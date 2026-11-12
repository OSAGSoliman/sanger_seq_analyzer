"""QC runs in the browser: new run (upload or shared folder) → preview → run → review → export."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from ... import review
from ...pipeline import AB1_SUFFIXES, FORWARD_TAGS, REVERSE_TAGS
from ...qc import QCSettings
from ...references import REFERENCES
from ...report import EXPORT_FILES
from ...report_assets import JS as REPORT_JS
from ...settings_schema import FIELDS, GROUPS, format_display, from_display, settings_from_dict, settings_to_dict, to_display
from .. import audit, paths
from ..auth import Session
from ..jobs import JobQueue
from ..runs import (
    Conflict, LabDefaults, RunError, RunStore, builtin_reference, check_settings, is_abif, lab_defaults, preview,
    reference_from_fasta, reference_json, safe_part,
)
from ..web import ServerContext, is_htmx, render, require
from . import WebTool

DRAFT_HOURS = 24
_script_hash = base64.b64encode(hashlib.sha256(REPORT_JS.encode("utf-8")).digest()).decode()
# The sample viewer and the full report run the report's own script only, sandboxed (no cookies, no requests).
VIEWER_CSP = (f"default-src 'none'; script-src 'sha256-{_script_hash}'; style-src 'unsafe-inline'; img-src data:; "
              "frame-ancestors 'self'; base-uri 'none'; form-action 'none'")


def _store(ctx: ServerContext) -> RunStore:
    return ctx.services["runs"]


def _jobs(ctx: ServerContext) -> JobQueue:
    return ctx.services["jobs"]


def start(ctx: ServerContext) -> None:
    store = RunStore(ctx.db, ctx.config.runs_dir)
    ctx.services["runs"] = store
    ctx.services["jobs"] = JobQueue(store, ctx.services.get("executor"))
    resumed = ctx.services["jobs"].resume()
    if resumed:
        ctx.services["log"] = f"resumed runs {resumed}"
    _clean_drafts(ctx)


def stop(ctx: ServerContext) -> None:
    jobs = ctx.services.get("jobs")
    if jobs:
        jobs.stop()


def _drafts(ctx: ServerContext) -> Path:
    return ctx.config.tmp_dir / "drafts"


def _clean_drafts(ctx: ServerContext) -> None:
    folder = _drafts(ctx)
    if not folder.exists():
        return
    limit = time.time() - DRAFT_HOURS * 3600
    for d in folder.iterdir():
        try:
            if d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


def _draft(ctx: ServerContext, token: str, user) -> Path:
    if not token or not token.replace("-", "").replace("_", "").isalnum() or len(token) > 64:
        raise HTTPException(400, "The upload was not found. Add the files again.")
    folder = _drafts(ctx) / token
    try:
        owner = json.loads((folder / "owner.json").read_text())
    except (OSError, ValueError):
        raise HTTPException(400, "The upload has expired. Add the files again.") from None
    if owner.get("user") != user.id:
        raise HTTPException(403, "This upload belongs to someone else.")
    return folder


def _settings_from_form(form, defaults: LabDefaults) -> tuple[QCSettings, list[str]]:
    values, problems = {}, []
    for meta in FIELDS:
        key = f"s_{meta.name}"
        if meta.kind == "bool":  # the page sends a hidden "0" before each check box
            raw = form.get(key)
            values[meta.name] = getattr(defaults.settings, meta.name) if raw is None else raw == "1"
            continue
        raw = (form.get(key) or "").strip()
        if raw == "":
            values[meta.name] = getattr(defaults.settings, meta.name)
            continue
        try:
            values[meta.name] = from_display(meta, float(raw))
        except ValueError:
            problems.append(f"{meta.label}: '{raw}' is not a number")
    return QCSettings(**values), problems


def _tags(text: str, fallback) -> tuple[str, ...]:
    tags = tuple(t.strip() for t in (text or "").split(",") if t.strip())
    return tags or tuple(fallback)


def _settings_groups(settings: QCSettings):
    groups = []
    for key, title in GROUPS.items():
        fields = [(m, to_display(m, getattr(settings, m.name)), format_display(m, getattr(settings, m.name)))
                  for m in FIELDS if m.group == key]
        groups.append((title, fields))
    return groups


def _effective(sample, rv) -> str:
    return rv["decision"] if rv and rv.get("decision") else sample["status"]


def _sample_rows(store: RunStore, run_id: int):
    reviews = store.reviews(run_id)
    rows = []
    for s in store.samples(run_id):
        rv = reviews.get(s["idx"])
        rv = rv if rv and rv.get("decision") else None
        rows.append({"idx": s["idx"], "sample": s["sample"], "status": s["status"], "review": rv,
                     "effective": _effective(s, rv), "reasons": json.loads(s["reasons"]),
                     "reads": json.loads(s["reads"]), "length": s["length"]})
    return rows


def router(ctx: ServerContext) -> APIRouter:
    r = APIRouter()
    viewer, analyst, admin = require("viewer"), require("analyst"), require("admin")
    cfg = ctx.config

    # --- list ----------------------------------------------------------------------------------------------

    @r.get("/runs")
    def runs_page(request: Request, mine: int = 0, q: str = "", sess: Session = Depends(viewer)):
        rows = _store(ctx).list(sess.user.id if mine else None, q.strip())
        busy = any(row["status"] in ("queued", "running") for row in rows)
        return render(request, "runs_list.html", runs=rows, mine=mine, q=q, busy=busy,
                      can_run=sess.user.can("analyst"))

    # --- new run -------------------------------------------------------------------------------------------

    @r.get("/runs/new")
    def new_run(request: Request, sess: Session = Depends(analyst)):
        d = lab_defaults(ctx.db)
        return render(request, "run_new.html", roots=paths.roots(ctx.db), groups=_settings_groups(d.settings),
                      defaults=d, references=REFERENCES, max_files=cfg.max_files_per_run, max_mb=cfg.max_ab1_mb,
                      fields=FIELDS)

    @r.get("/runs/browse")
    def browse(request: Request, root: str = "", path: str = "", sess: Session = Depends(analyst)):
        try:
            base = paths.root(ctx.db, root)
            dirs, files = paths.list_folder(base, path, AB1_SUFFIXES)
        except paths.PathError as exc:
            return HTMLResponse(f'<div class="notice error">{_esc(str(exc))}</div>')
        crumbs = [p for p in path.strip("/").split("/") if p]
        return render(request, "_folder_browser.html", root=base, path=path.strip("/"), dirs=dirs, files=files,
                      crumbs=crumbs)

    @r.post("/runs/draft")
    def make_draft(request: Request, files: list[UploadFile] = File(default=[]), root: str = Form(""),
                   path: str = Form(""), sess: Session = Depends(analyst)):
        _clean_drafts(ctx)
        token = secrets.token_urlsafe(16)
        folder = _drafts(ctx) / token
        target = folder / "files"
        target.mkdir(parents=True)
        (folder / "owner.json").write_text(json.dumps({"user": sess.user.id, "at": time.time()}))
        notes, source = [], ""
        try:
            if root:
                base = paths.root(ctx.db, root)
                src = paths.resolve(base, path)
                found = [p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in AB1_SUFFIXES
                         and not any(part.startswith((".", "~$")) for part in p.relative_to(src).parts)]
                if len(found) > cfg.max_files_per_run:
                    raise RunError(f"The folder has {len(found)} trace files; a run takes at most "
                                   f"{cfg.max_files_per_run}. Choose a smaller folder.")
                for p in found:
                    if p.stat().st_size > cfg.max_ab1_mb * 1_000_000:
                        notes.append(f"{p.name}: larger than {cfg.max_ab1_mb} MB, left out")
                        continue
                    dest = target.joinpath(*[safe_part(x) for x in p.relative_to(src).parts])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                source = f"folder {base.name}/{path.strip('/')}"
            else:
                uploads = [f for f in files if f.filename]
                if not uploads:
                    raise RunError("Choose .ab1 files or a folder first.")
                if len(uploads) > cfg.max_files_per_run:
                    raise RunError(f"{len(uploads)} files were chosen; a run takes at most {cfg.max_files_per_run}.")
                for f in uploads:
                    name = safe_part(f.filename)
                    if Path(name).suffix.lower() not in AB1_SUFFIXES:
                        notes.append(f"{name}: not an .ab1 file, left out")
                        continue
                    data = f.file.read(cfg.max_ab1_mb * 1_000_000 + 1)
                    if len(data) > cfg.max_ab1_mb * 1_000_000:
                        notes.append(f"{name}: larger than {cfg.max_ab1_mb} MB, left out")
                        continue
                    if not is_abif(data):
                        notes.append(f"{name}: not a trace file (no ABIF header), left out")
                        continue
                    dest = target / name
                    if dest.exists():
                        notes.append(f"{name}: chosen twice, the second copy was left out")
                        continue
                    dest.write_bytes(data)
                source = "upload"
            d = lab_defaults(ctx.db)
            scan = preview(target, d.forward_tags, d.reverse_tags)
        except (RunError, paths.PathError) as exc:
            shutil.rmtree(folder, ignore_errors=True)
            return render(request, "_draft.html", error=str(exc), token="", scan=None, notes=[], source="")
        (folder / "source.txt").write_text(source, encoding="utf-8")
        return render(request, "_draft.html", error="", token=token, scan=scan, notes=notes, source=source,
                      n_files=sum(1 for p in target.rglob("*") if p.is_file()), defaults=d,
                      groups=_settings_groups(d.settings), references=REFERENCES)

    @r.post("/runs")
    async def start_run(request: Request, sess: Session = Depends(analyst)):
        form = await request.form()
        reference_upload = form.get("reference_file")
        data = await reference_upload.read() if getattr(reference_upload, "filename", "") else b""
        return await run_in_threadpool(_start_run, request, sess, form, data,
                                       getattr(reference_upload, "filename", "") or "")

    def _start_run(request: Request, sess: Session, form, ref_data: bytes, ref_name: str):
        folder = _draft(ctx, str(form.get("draft", "")), sess.user)
        d = lab_defaults(ctx.db)
        settings, problems = _settings_from_form(form, d)
        forward = _tags(str(form.get("forward_tags", "")), d.forward_tags)
        reverse = _tags(str(form.get("reverse_tags", "")), d.reverse_tags)
        try:
            if form.get("reference") == "upload":
                if not ref_data:
                    raise RunError("Choose the reference FASTA file.")
                ref = reference_from_fasta(ref_data, ref_name, folder)
            else:
                ref = builtin_reference(str(form.get("reference") or d.reference))
        except RunError as exc:
            problems.append(str(exc))
            ref = None
        if ref is not None:
            problems += check_settings(settings, forward, reverse, ref.length)
        if problems:
            return HTMLResponse("".join(f'<div class="notice error">{_esc(p)}</div>' for p in problems),
                                status_code=400, headers={"HX-Retarget": "#start-problems", "HX-Reswap": "innerHTML"})
        files = folder / "files"
        if not any(files.rglob("*")):
            raise HTTPException(400, "The upload has no trace files. Add the files again.")
        config = {"settings": settings_to_dict(settings), "forward_tags": list(forward),
                  "reverse_tags": list(reverse), "reference": reference_json(ref)}
        source = (folder / "source.txt").read_text(encoding="utf-8") if (folder / "source.txt").exists() else ""
        name = str(form.get("name", "")).strip() or time.strftime("Run %Y-%m-%d %H:%M")
        run_id = _store(ctx).create(sess.user, name, files, config, source)
        shutil.rmtree(folder, ignore_errors=True)
        _jobs(ctx).submit(run_id)
        url = f"/runs/{run_id}"
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url, status_code=303)

    # --- one run ---------------------------------------------------------------------------------------------

    def _run_or_404(run_id: int):
        row = _store(ctx).get(run_id)
        if row is None:
            raise HTTPException(404, "This run does not exist (or was deleted).")
        return row

    @r.get("/runs/{run_id}")
    def run_page(request: Request, run_id: int, sess: Session = Depends(viewer)):
        store = _store(ctx)
        row = _run_or_404(run_id)
        info = store.info(run_id)
        config = json.loads(row["config"])
        rows = _sample_rows(store, run_id) if row["status"] == "done" else []
        reviewed = sum(1 for x in rows if x["review"])
        settings_lines = _settings_groups(settings_from_dict(config["settings"])[0])
        return render(request, "run.html", run=row, info=info, config=config, samples=rows, reviewed=reviewed,
                      can_review=sess.user.can("analyst"), is_admin=sess.user.can("admin"),
                      export_files=EXPORT_FILES, scopes=review.SCOPES, progress=_jobs(ctx).progress.get(run_id),
                      settings_groups=settings_lines,
                      n_pass=sum(1 for x in rows if x["effective"] == "PASS"),
                      n_fail=sum(1 for x in rows if x["effective"] == "FAIL"))

    @r.get("/runs/{run_id}/status")
    def run_status(request: Request, run_id: int, sess: Session = Depends(viewer)):
        row = _run_or_404(run_id)
        if row["status"] not in ("queued", "running"):
            return Response(status_code=200, headers={"HX-Refresh": "true"})
        return render(request, "_run_status.html", run=row, progress=_jobs(ctx).progress.get(run_id))

    @r.post("/runs/{run_id}/cancel")
    def cancel_run(request: Request, run_id: int, sess: Session = Depends(analyst)):
        _run_or_404(run_id)
        _jobs(ctx).cancel(run_id)
        audit.record(ctx.db, sess.user, "run.cancel_requested", f"run {run_id}")
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    @r.post("/runs/{run_id}/delete")
    def delete_run(request: Request, run_id: int, sess: Session = Depends(admin)):
        row = _run_or_404(run_id)
        if row["status"] in ("queued", "running"):
            raise HTTPException(409, "Cancel the run before deleting it.")
        _store(ctx).delete(run_id, sess.user)
        return RedirectResponse("/runs", status_code=303)

    @r.get("/runs/{run_id}/samples/{idx}")
    def sample_detail(request: Request, run_id: int, idx: int, sess: Session = Depends(viewer)):
        return _detail(request, sess, run_id, idx)

    def _detail(request, sess, run_id, idx, message="", conflict=None, status_code=200, advance=False):
        store = _store(ctx)
        run = _run_or_404(run_id)
        s = store.sample(run_id, idx)
        if s is None:
            raise HTTPException(404, "No such sample in this run.")
        rv = store.reviews(run_id).get(idx)
        rows = _sample_rows(store, run_id)
        return render(request, "_sample_detail.html", status_code=status_code, run=run, s=s, rv=rv,
                      reads=json.loads(s["reads"]), reasons=json.loads(s["reasons"]),
                      can_review=sess.user.can("analyst"), message=message, conflict=conflict,
                      row=next(x for x in rows if x["idx"] == idx), reviewed=sum(1 for x in rows if x["review"]),
                      total=len(rows), n_pass=sum(1 for x in rows if x["effective"] == "PASS"),
                      n_fail=sum(1 for x in rows if x["effective"] == "FAIL"), oob=is_htmx(request),
                      advance=advance)

    @r.get("/runs/{run_id}/samples/{idx}/view")
    def sample_view(request: Request, run_id: int, idx: int, sess: Session = Depends(viewer)):
        page = _store(ctx).sample_page(run_id, idx)
        if page is None:
            raise HTTPException(404, "No page for this sample.")
        return HTMLResponse(page, headers={"Content-Security-Policy": VIEWER_CSP, "Cache-Control": "private, max-age=600"})

    @r.get("/runs/{run_id}/report")
    def full_report(request: Request, run_id: int, sess: Session = Depends(viewer)):
        path = _store(ctx).output(run_id) / "report.html"
        if not path.is_file():
            raise HTTPException(404, "This run has no report.")
        audit.record(ctx.db, sess.user, "run.report_viewed", f"run {run_id}")
        return HTMLResponse(path.read_bytes(), headers={"Content-Security-Policy": VIEWER_CSP + "; sandbox allow-scripts"})

    @r.post("/runs/{run_id}/samples/{idx}/review")
    def set_review(request: Request, run_id: int, idx: int, decision: str = Form(""), expected: int = Form(0),
                   sess: Session = Depends(analyst)):
        _run_or_404(run_id)
        try:
            changed = _store(ctx).set_review(run_id, idx, decision or None, sess.user, expected)
        except Conflict as exc:
            return _detail(request, sess, run_id, idx, conflict=exc.latest, status_code=409)
        except RunError as exc:
            raise HTTPException(400, str(exc)) from None
        return _detail(request, sess, run_id, idx, message="saved" if changed["changed"] else "",
                       advance=bool(decision))

    @r.post("/runs/{run_id}/confirm-remaining")
    def confirm_remaining(request: Request, run_id: int, sess: Session = Depends(analyst)):
        _run_or_404(run_id)
        _store(ctx).confirm_remaining(run_id, sess.user)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @r.post("/runs/{run_id}/export")
    async def export(request: Request, run_id: int, sess: Session = Depends(viewer)):
        form = await request.form()
        return await run_in_threadpool(_export, run_id, sess, form.getlist("files"), str(form.get("scope", "all")),
                                       [str(x) for x in form.getlist("chosen")])

    def _export(run_id: int, sess: Session, files: list, scope: str, chosen: list[str]):
        row = _run_or_404(run_id)
        if row["status"] != "done":
            raise HTTPException(409, "The run has not finished.")
        try:
            data, n = _store(ctx).export_zip(run_id, [str(f) for f in files], scope, chosen, cfg.tmp_dir)
        except RunError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(ctx.db, sess.user, "run.exported", f"run {run_id}", scope=scope, samples=n,
                     files=[str(f) for f in files])
        name = safe_part(f"{row['name']} {scope}").replace(" ", "_")
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="run{run_id}_{name}.zip"'})

    # --- admin: lab defaults ------------------------------------------------------------------------------

    @r.get("/admin/defaults")
    def defaults_page(request: Request, sess: Session = Depends(admin)):
        from .admin import ADMIN_PAGES
        d = lab_defaults(ctx.db)
        return render(request, "admin_defaults.html", d=d, groups=_settings_groups(d.settings), references=REFERENCES,
                      admin_pages=ADMIN_PAGES, error="", saved=False)

    @r.post("/admin/defaults")
    async def save_defaults(request: Request, sess: Session = Depends(admin)):
        from .admin import ADMIN_PAGES
        form = await request.form()
        current = lab_defaults(ctx.db)
        settings, problems = _settings_from_form(form, current)
        forward = _tags(str(form.get("forward_tags", "")), FORWARD_TAGS)
        reverse = _tags(str(form.get("reverse_tags", "")), REVERSE_TAGS)
        ref = str(form.get("reference", current.reference))
        if ref not in REFERENCES:
            problems.append("Unknown reference.")
        else:
            problems += check_settings(settings, forward, reverse, len(REFERENCES[ref].sequence))
        d = LabDefaults(settings, forward, reverse, ref if ref in REFERENCES else current.reference)
        if not problems:
            ctx.db.set_setting("qc_defaults", d.to_json())
            audit.record(ctx.db, sess.user, "settings.lab_defaults", "QC rules", reference=d.reference,
                         forward=list(forward), reverse=list(reverse))
        return render(request, "admin_defaults.html", d=d, groups=_settings_groups(d.settings), references=REFERENCES,
                      admin_pages=ADMIN_PAGES, error="; ".join(problems), saved=not problems,
                      status_code=400 if problems else 200)

    return r


def _esc(text: str) -> str:
    import html
    return html.escape(text)


TOOL = WebTool("qc", "Runs", "/runs", router, start=start, stop=stop)
