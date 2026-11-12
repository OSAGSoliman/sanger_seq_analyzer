"""Fetch data in the browser: the lab files, the genotyping table, the MeaNS rows, saving."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from ...fetch import sources, tables
from ...fetch.linking import COMPUTED, MEANS_COLUMNS
from .. import audit, paths
from ..auth import Session
from ..fetch_service import FetchError, FetchService, Workspace
from ..web import ServerContext, is_htmx, render, require
from . import WebTool

SHOW = (("all", "All"), ("check", "To check"), ("missing", "Not in every file"), ("ticked", "Ticked to save"))
STATE_NAMES = {"pass": "PASS", "fail": "FAIL", "check": "To check"}


def _svc(ctx: ServerContext) -> FetchService:
    return ctx.services["fetch"]


def start(ctx: ServerContext) -> None:
    ctx.services["fetch"] = FetchService(ctx.db, ctx.config.data_dir, ctx.config.tmp_dir, ctx.config.max_sheet_mb)


def _ws(ctx: ServerContext, sess: Session) -> Workspace:
    return _svc(ctx).workspace(sess.user, ctx.accounts.prefs(sess.user.id))


def _back(request: Request, message: str = "", ws: Workspace | None = None):
    if ws is not None and message:
        ws.message = message
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": "/fetch"})
    return RedirectResponse("/fetch", status_code=303)


def _row_tags(row) -> str:
    tags = [row.state]
    if not all(row.sample.found.values()):
        tags.append("missing")
    if row.include:
        tags.append("ticked")
    return " ".join(tags)


def _results(ctx: ServerContext, ws: Workspace) -> dict:
    s = ws.session
    rows = s.rows
    if not rows:
        return {"has_rows": False}
    states = [r.state for r in rows]
    all_cols = s.all_columns()
    means = []
    for i, r in enumerate(rows):
        notes = [*r.problems, *r.sample.notes]
        if r.state == "check" and not notes:
            notes = ["no genotype in the genotyping table"]
        means.append({"i": i, "row": r, "tags": _row_tags(r), "notes": notes,
                      "cells": [(c, tables.as_text(s.value(r, c)), c in MEANS_COLUMNS, r.sources.get(c, ""))
                                for c in s.columns]})
    allrows = [[tables.as_text(s.linked.merged(r.sample).get(c)) for c in all_cols] for r in rows] if s.linked else []
    counts = {"all": len(rows), "check": states.count("check"),
              "missing": sum(1 for r in rows if not all(r.sample.found.values())),
              "ticked": sum(1 for r in rows if r.include)}
    return {"has_rows": True, "means": means, "columns": s.columns, "all_columns": all_cols, "allrows": allrows,
            "n_pass": states.count("pass"), "n_fail": states.count("fail"), "n_check": states.count("check"),
            "complete": sum(1 for r in rows if all(r.sample.found.values())), "counts": counts,
            "computed": COMPUTED, "means_columns": MEANS_COLUMNS, "rows": rows}


def router(ctx: ServerContext) -> APIRouter:
    r = APIRouter(prefix="/fetch")
    analyst, admin = require("analyst"), require("admin")

    def page(request: Request, sess: Session, ws: Workspace, status_code: int = 200, **extra):
        svc = _svc(ctx)
        chosen = svc.load(ws)
        cards = []
        for key, (label, example, _patterns) in sources.SOURCE_FILES.items():
            s = ws.session
            t = s.tables.get(key)
            folder = svc.folders().get(key, {})
            cards.append({"key": key, "label": label, "example": example, "chosen": chosen.get(key),
                          "candidates": svc.candidates(key), "pinned": ws.pins.get(key, ""),
                          "error": s.errors.get(key, ""), "rows": len(t.rows) if t else 0,
                          "sheets": len(t.sheets) if t is not None and getattr(t, "sheets", None) else 0,
                          "folder": folder})
        g = ws.session.genotyping
        message, ws.message = ws.message, ""
        runs = ctx.services["runs"].list(limit=30)
        return render(request, "fetch.html", status_code=status_code, cards=cards, ws=ws,
                      genotyping=g, geno_rows=g.rows if g else [], geno_cols=g.columns if g else [],
                      geno_error=ws.session.genotyping_error, message=message, roots=paths.roots(ctx.db),
                      is_admin=sess.user.can("admin"), runs=[x for x in runs if x["status"] == "done"],
                      changed=svc.changed_since_fetch(ws) if ws.session.rows else [], show=SHOW,
                      state_names=STATE_NAMES, virus=ws.session.virus, **_results(ctx, ws), **extra)

    @r.get("")
    def fetch_page(request: Request, sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            return page(request, sess, ws)

    # --- source files --------------------------------------------------------------------------------------

    @r.post("/folder/{key}")
    def set_folder(request: Request, key: str, root: str = Form(""), path: str = Form(""),
                   sess: Session = Depends(admin)):
        ws = _ws(ctx, sess)
        try:
            _svc(ctx).set_folder(key, root, path, sess.user)
        except (FetchError, paths.PathError) as exc:
            return _back(request, f"error:{exc}", ws)
        return _back(request, "Folder saved.", ws)

    @r.post("/pin/{key}")
    def pin(request: Request, key: str, candidate: str = Form(""), sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        if key in sources.SPECS:
            if candidate:
                ws.pins[key] = candidate
            else:
                ws.pins.pop(key, None)
        return _back(request)

    @r.post("/upload/{key}")
    def upload(request: Request, key: str, file: UploadFile = File(...), force: str = Form(""),
               sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        data = file.file.read(_svc(ctx).max_bytes + 1)
        try:
            msg = _svc(ctx).upload(key, file.filename or "file", data, sess.user, force=force == "1")
        except FetchError as exc:
            return _back(request, f"error:{exc}", ws)
        ws.pins.pop(key, None)  # use the new file
        return _back(request, msg, ws)

    @r.get("/template/{version}")
    def download_template(version: int, sess: Session = Depends(analyst)):
        path = _svc(ctx).version_file(version)
        if path is None or not path.is_file():
            raise HTTPException(404, "No such template version.")
        audit.record(ctx.db, sess.user, "template.downloaded", f"version {version}")
        return FileResponse(path, filename=f"MeaNS Upload Template v{version}{path.suffix}",
                            media_type="application/octet-stream")

    # --- genotyping table ------------------------------------------------------------------------------

    @r.post("/genotyping")
    def paste(request: Request, text: str = Form(""), sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            ok = ws.session.set_genotyping_text(text)
        return _back(request, "" if ok else "error:" + ws.session.genotyping_error, ws)

    @r.post("/genotyping/file")
    def genotyping_file(request: Request, file: UploadFile = File(...), sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        svc = _svc(ctx)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in tables.READABLE_SUFFIXES:
            return _back(request, "error:Choose an Excel, .csv or .txt file.", ws)
        svc.tmp.mkdir(parents=True, exist_ok=True)
        tmp = svc.tmp / f"geno-{time.time_ns()}{suffix}"
        tmp.write_bytes(file.file.read(svc.max_bytes))
        try:
            with ws.lock:
                ok = ws.session.set_genotyping_file(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        return _back(request, "" if ok else "error:" + ws.session.genotyping_error, ws)

    @r.post("/genotyping/clear")
    def clear(request: Request, sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            ws.session.clear_genotyping()
            ws.session.rows, ws.session.linked = [], None
        return _back(request)

    @r.post("/from-run")
    def from_run_form(request: Request, run: int = Form(...), sess: Session = Depends(analyst)):
        return from_run(request, run, sess)

    @r.post("/from-run/{run_id}")
    def from_run(request: Request, run_id: int, sess: Session = Depends(analyst)):
        store = ctx.services["runs"]
        row = store.get(run_id)
        if row is None or row["status"] != "done":
            raise HTTPException(404, "This run is not finished.")
        result = store.result(run_id)
        ws = _ws(ctx, sess)
        with ws.lock:
            ws.session.genotyping_from_run(result, store.decisions(run_id))
        audit.record(ctx.db, sess.user, "fetch.from_run", f"run {run_id}")
        return _back(request, f"Genotyping table filled from run {run_id} ({row['name']}): PASS rows need their "
                              "genotype typed in.", ws)

    # --- fetch and the MeaNS table ---------------------------------------------------------------------

    @r.post("/run")
    def fetch_now(request: Request, virus: str = Form("MVs"), sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            if ws.session.set_virus(virus):
                ctx.accounts.set_prefs(sess.user.id, fetch_virus=ws.session.virus)
            problem = _svc(ctx).fetch(ws, ctx.today())
        if problem:
            return _back(request, "error:" + problem, ws)
        rows = ws.session.rows
        audit.record(ctx.db, sess.user, "fetch.fetched", "", accessions=len(rows))
        return _back(request, "", ws)

    def results_fragment(request: Request, sess: Session, ws: Workspace):
        return render(request, "_fetch_results.html", ws=ws, show=SHOW, state_names=STATE_NAMES,
                      changed=_svc(ctx).changed_since_fetch(ws), **_results(ctx, ws))

    @r.post("/cell")
    def edit_cell(request: Request, row: int = Form(...), col: str = Form(...), value: str = Form(""),
                  sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            if not 0 <= row < len(ws.session.rows):
                raise HTTPException(409, "The table changed. Reload the page.")
            ws.session.edit_cell(row, col, value)
            return results_fragment(request, sess, ws)

    @r.post("/include")
    def include(request: Request, row: int = Form(...), on: str = Form(""), sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            if not 0 <= row < len(ws.session.rows):
                raise HTTPException(409, "The table changed. Reload the page.")
            ws.session.set_include(row, on == "1")
            return results_fragment(request, sess, ws)

    @r.post("/columns")
    def columns(request: Request, cols: list[str] = Form(default=[]), reset: str = Form(""),
                sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            ws.session.set_columns([] if reset else cols)
            ctx.accounts.set_prefs(sess.user.id, fetch_columns=ws.session.columns)
        return _back(request)

    @r.post("/regenerate")
    def regenerate(request: Request, sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            _svc(ctx).load(ws)
            ws.session.regenerate_names()
        return _back(request, "Strain names numbered again after the names in the template.", ws)

    @r.post("/export")
    def export(request: Request, sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        svc = _svc(ctx)
        svc.tmp.mkdir(parents=True, exist_ok=True)
        out = svc.tmp / f"export-{time.time_ns()}.xlsx"
        with ws.lock:
            if not ws.session.rows:
                raise HTTPException(400, "Fetch first.")
            ws.session.export_workbook(out)
        data = out.read_bytes()
        out.unlink(missing_ok=True)
        audit.record(ctx.db, sess.user, "fetch.exported", "", rows=len(ws.session.rows))
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="MeaNS rows {time.strftime("%Y-%m-%d")}.xlsx"'})

    @r.post("/save/preview")
    def save_preview(request: Request, sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            try:
                names, token, cand = _svc(ctx).save_preview(ws)
            except (FetchError, tables.TableError) as exc:
                return render(request, "_fetch_save.html", error=str(exc), names=[], token="", cand=None)
        return render(request, "_fetch_save.html", error="", names=names, token=token, cand=cand, renumbered=False)

    @r.post("/save")
    def save(request: Request, token: str = Form(""), names: list[str] = Form(default=[]),
             sess: Session = Depends(analyst)):
        ws = _ws(ctx, sess)
        with ws.lock:
            try:
                out = _svc(ctx).save(ws, sess.user, token, names)
            except (FetchError, tables.TableError) as exc:
                return render(request, "_fetch_save.html", error=str(exc), names=[], token="", cand=None)
        if "renumbered" in out:
            cand = _svc(ctx).chosen("template", ws)
            return render(request, "_fetch_save.html", error="", names=out["renumbered"], token=out["token"],
                          cand=cand, renumbered=True)
        result = out["saved"]
        where = Path(out["target"]).name if out["kind"] == "folder" else "the server's template (new version)"
        msg = f"{result.written} row{'s' if result.written != 1 else ''} added to {where}"
        if result.backup:
            msg += f"; backup in 'MeaNS backups' ({Path(result.backup).name})"
        if result.skipped:
            msg += ". Not added (already there): " + "; ".join(result.skipped)
        return _back(request, msg + ".", ws)

    return r


TOOL = WebTool("fetch", "Fetch data", "/fetch", router, min_role="analyst", start=start)
