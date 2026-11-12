"""QC runs on the server: inputs, the analysis job, what each run keeps, reviews and exports.

A run never changes once it has finished. It keeps:
  runs/<id>/inputs/                 the .ab1 files as uploaded or copied from a shared folder
  runs/<id>/run.json                settings, tags, reference (sequence and sha256), app version,
                                    files that were not used and why
  runs/<id>/output/                 consensus.fasta, the CSV/JSON files and report.html, written once
  runs/<id>/output/samples/N.html.gz  one page per sample (the viewer next to the review buttons)
and a row per sample in the database (status, reasons, reads), so lists and the Fetch data
genotyping table never need the analysis again. Exports that apply reviewer decisions
recompute the run from its inputs (a second or two), in a small in-memory cache.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import re
import shutil
import threading
import traceback
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .. import __version__, review
from ..pipeline import FORWARD_TAGS, REVERSE_TAGS, RunResult, run, scan, validate_tags
from ..qc import QCSettings
from ..references import REFERENCES, ReferenceError, ReferenceInfo, fasta_records, from_sequence
from ..report import EXPORT_FILES, write_outputs
from ..report_html import render_sample
from ..settings_schema import errors as setting_errors
from ..settings_schema import settings_from_dict, settings_to_dict, validate
from . import audit
from .db import Database, now

log = logging.getLogger("sanger_seq_analyzer.server.runs")
DEFAULTS_KEY = "qc_defaults"
DEFAULT_REFERENCE = "measles-n450"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ ()+-]")


class RunError(Exception):
    """A problem to show to the user."""


class Conflict(Exception):
    """Someone else changed the review in the meantime."""

    def __init__(self, latest):
        super().__init__("changed by someone else")
        self.latest = latest


@dataclass
class LabDefaults:
    settings: QCSettings
    forward_tags: tuple[str, ...]
    reverse_tags: tuple[str, ...]
    reference: str  # built-in name

    def to_json(self) -> dict:
        return {"settings": settings_to_dict(self.settings), "forward_tags": list(self.forward_tags),
                "reverse_tags": list(self.reverse_tags), "reference": self.reference}


def lab_defaults(db: Database) -> LabDefaults:
    data = db.get_setting(DEFAULTS_KEY, {}) or {}
    settings, _problems = settings_from_dict(data.get("settings", {}))
    ref = data.get("reference", DEFAULT_REFERENCE)
    return LabDefaults(settings, tuple(data.get("forward_tags", FORWARD_TAGS)),
                       tuple(data.get("reverse_tags", REVERSE_TAGS)), ref if ref in REFERENCES else DEFAULT_REFERENCE)


def check_settings(settings: QCSettings, forward, reverse, reference_length: int) -> list[str]:
    problems = [p.message for p in setting_errors(validate(settings, reference_length))]
    problems += validate_tags(tuple(forward), tuple(reverse))
    return problems


def builtin_reference(name: str) -> ReferenceInfo:
    ref = REFERENCES.get(name)
    if ref is None:
        raise RunError(f"unknown built-in reference {name!r}")
    return ReferenceInfo(ref.name, ref.description, ref.source, ref.sequence, builtin=True)


def reference_from_fasta(data: bytes, filename: str, folder: Path) -> ReferenceInfo:
    """The first record of an uploaded FASTA (never a path typed by the user)."""
    if len(data) > 5_000_000:
        raise RunError("The reference FASTA is too large (5 MB at most).")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "reference.fasta"
    tmp.write_bytes(data)
    try:
        records = fasta_records(tmp)
    except (OSError, ValueError) as exc:
        raise RunError(f"The reference could not be read: {exc}") from None
    finally:
        tmp.unlink(missing_ok=True)
    if not records:
        raise RunError("No FASTA record found in the reference file (it must start with a '>' line).")
    rid, desc, seq = records[0]
    try:
        return from_sequence(seq, name=rid or Path(filename).stem, description=desc, source=f"uploaded {filename}")
    except ReferenceError as exc:
        raise RunError(str(exc)) from None


def reference_json(info: ReferenceInfo) -> dict:
    return {"name": info.name, "description": info.description, "source": info.source, "sequence": info.sequence,
            "sha256": info.sha256, "builtin": info.builtin}


def reference_info(data: dict) -> ReferenceInfo:
    return ReferenceInfo(data["name"], data.get("description", ""), data.get("source", ""), data["sequence"],
                         builtin=bool(data.get("builtin")))


def safe_part(name: str) -> str:
    """A file or folder name safe to create on Windows and Linux (keeps _216/_214 tags)."""
    name = _SAFE_NAME.sub("_", Path(name.replace("\\", "/")).name).strip(" .")
    return name[:150] or "file"


def is_abif(head: bytes) -> bool:
    return head[:4] == b"ABIF"


class RunStore:
    def __init__(self, db: Database, runs_dir: Path, cache_size: int = 3):
        self.db = db
        self.dir = runs_dir
        self._cache: OrderedDict[int, RunResult] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_size = cache_size
        self._compute_lock = threading.Lock()

    # --- folders -----------------------------------------------------------------------------------------

    def folder(self, run_id: int) -> Path:
        return self.dir / str(int(run_id))

    def inputs(self, run_id: int) -> Path:
        return self.folder(run_id) / "inputs"

    def output(self, run_id: int) -> Path:
        return self.folder(run_id) / "output"

    def info(self, run_id: int) -> dict:
        try:
            return json.loads((self.folder(run_id) / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # --- creating ----------------------------------------------------------------------------------------

    def create(self, user, name: str, files_dir: Path, config: dict, source: str) -> int:
        """A queued run from the .ab1 files in ``files_dir`` (moved into the run)."""
        n_files = sum(1 for p in files_dir.rglob("*") if p.is_file())
        with self.db.tx() as con:
            cur = con.execute("INSERT INTO runs(name, created_by, created_at, status, app_version, config, n_files) "
                              "VALUES(?, ?, ?, 'queued', ?, ?, ?)",
                              (name.strip()[:120] or "Run", user.id, now(), __version__, json.dumps(config), n_files))
            run_id = cur.lastrowid
            audit.record(self.db, user, "run.created", f"run {run_id}", con=con, name=name, files=n_files, source=source)
        folder = self.folder(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(files_dir), str(self.inputs(run_id)))
        self._write_info(run_id, {"config": config, "source": source})
        return run_id

    def _write_info(self, run_id: int, values: dict) -> None:
        data = self.info(run_id)
        data.update(values)
        path = self.folder(run_id) / "run.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)

    # --- reading -----------------------------------------------------------------------------------------

    def get(self, run_id: int):
        with self.db.read() as con:
            return con.execute("SELECT r.*, u.display_name AS creator FROM runs r LEFT JOIN users u "
                               "ON u.id = r.created_by WHERE r.id = ?", (run_id,)).fetchone()

    def list(self, user_id: int | None = None, text: str = "", limit: int = 200):
        sql = ("SELECT r.*, u.display_name AS creator, "
               "(SELECT COUNT(*) FROM (SELECT idx, decision FROM reviews v WHERE v.run_id = r.id AND v.id IN "
               " (SELECT MAX(id) FROM reviews WHERE run_id = r.id GROUP BY idx)) WHERE decision IS NOT NULL) "
               "AS n_reviewed FROM runs r LEFT JOIN users u ON u.id = r.created_by WHERE 1 = 1")
        args: list = []
        if user_id:
            sql += " AND r.created_by = ?"
            args.append(user_id)
        if text:
            sql += " AND (r.name LIKE ? OR r.id IN (SELECT run_id FROM run_samples WHERE sample LIKE ?))"
            args += [f"%{text}%", f"%{text}%"]
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(limit)
        with self.db.read() as con:
            return con.execute(sql, args).fetchall()

    def samples(self, run_id: int):
        with self.db.read() as con:
            return con.execute("SELECT * FROM run_samples WHERE run_id = ? ORDER BY idx", (run_id,)).fetchall()

    def sample(self, run_id: int, idx: int):
        with self.db.read() as con:
            return con.execute("SELECT * FROM run_samples WHERE run_id = ? AND idx = ?", (run_id, idx)).fetchone()

    def sample_page(self, run_id: int, idx: int) -> bytes | None:
        path = self.output(run_id) / "samples" / f"{int(idx)}.html.gz"
        try:
            return gzip.decompress(path.read_bytes())
        except OSError:
            return None

    # --- running -----------------------------------------------------------------------------------------

    def queued(self) -> list[int]:
        with self.db.read() as con:
            return [r["id"] for r in con.execute("SELECT id FROM runs WHERE status IN ('queued', 'running') "
                                                 "ORDER BY id")]

    def _set_status(self, run_id: int, status: str, **values) -> None:
        cols = ", ".join(f"{k} = ?" for k in values)
        with self.db.tx() as con:
            con.execute(f"UPDATE runs SET status = ?{', ' + cols if cols else ''} WHERE id = ?",
                        (status, *values.values(), run_id))

    def _analyse(self, run_id: int, progress=None, should_cancel=None) -> RunResult:
        config = json.loads(self.get(run_id)["config"])
        settings, _ = settings_from_dict(config["settings"])
        return run([self.inputs(run_id)], reference_info(config["reference"]), settings,
                   tuple(config["forward_tags"]), tuple(config["reverse_tags"]), progress, should_cancel=should_cancel)

    def execute(self, run_id: int, progress=None, should_cancel=None) -> None:
        """The analysis job: run, write the outputs and the per-sample pages, record the summary."""
        row = self.get(run_id)
        if row is None or row["status"] not in ("queued", "running"):
            return
        self._set_status(run_id, "running", started_at=now(), error="")
        try:
            result = self._analyse(run_id, progress, should_cancel)
            if result.cancelled:
                self._set_status(run_id, "cancelled", finished_at=now())
                audit.record(self.db, None, "run.cancelled", f"run {run_id}")
                return
            out = self.output(run_id)
            if out.exists():
                shutil.rmtree(out)
            write_outputs(result, out, html_report=True)
            pages = out / "samples"
            pages.mkdir(exist_ok=True)
            rows = []
            for idx, sr in enumerate(result.samples):
                (pages / f"{idx}.html.gz").write_bytes(gzip.compress(render_sample(result, sr.sample).encode("utf-8"),
                                                                     compresslevel=6))
                rows.append(self._snapshot(run_id, idx, sr))
            self._write_info(run_id, {
                "skipped": [{"file": s.path.name, "reason": s.text} for s in result.skipped if s.reason != "hidden"],
                "errors": {Path(k).name: v for k, v in result.errors.items()},
                "reference": {"name": result.reference_info.name if result.reference_info else "",
                              "sha256": result.reference_info.sha256 if result.reference_info else ""},
                "version": __version__})
            n_pass = sum(1 for sr in result.samples if sr.consensus.status == "PASS")
            with self.db.tx() as con:
                con.execute("DELETE FROM run_samples WHERE run_id = ?", (run_id,))
                con.executemany("INSERT INTO run_samples(run_id, idx, sample, status, reasons, reads, length, "
                                "consensus_sha) VALUES(?, ?, ?, ?, ?, ?, ?, ?)", rows)
                con.execute("UPDATE runs SET status = 'done', finished_at = ?, n_samples = ?, n_pass = ?, n_fail = ? "
                            "WHERE id = ?", (now(), len(result.samples), n_pass, len(result.samples) - n_pass, run_id))
                audit.record(self.db, None, "run.finished", f"run {run_id}", con=con, samples=len(result.samples),
                             passed=n_pass)
            self._remember(run_id, result)
        except Exception as exc:  # report it on the run page; the details go to the log
            log.error("run %s failed:\n%s", run_id, traceback.format_exc())
            self._set_status(run_id, "failed", finished_at=now(), error=f"{type(exc).__name__}: {exc}"[:2000])
            audit.record(self.db, None, "run.failed", f"run {run_id}", error=type(exc).__name__)

    @staticmethod
    def _snapshot(run_id: int, idx: int, sr) -> tuple:
        c = sr.consensus
        reads = {}
        for d in ("F", "R"):
            path, qc = sr.path(d), c.reads.get(d)
            reads[d] = {"file": path.name if path else "", "status": qc.status if qc else "MISSING",
                        "reasons": list(qc.reasons) if qc else ([sr.read_errors[d]] if d in sr.read_errors else [])}
        sha = hashlib.sha256(c.sequence.encode("ascii")).hexdigest() if c.sequence else ""
        return (run_id, idx, sr.sample, c.status, json.dumps(list(c.reasons)), json.dumps(reads), c.length, sha)

    # --- the analysis result (for exports that apply reviews) --------------------------------------------

    def _remember(self, run_id: int, result: RunResult) -> None:
        with self._cache_lock:
            self._cache[run_id] = result
            self._cache.move_to_end(run_id)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def result(self, run_id: int) -> RunResult:
        with self._cache_lock:
            if run_id in self._cache:
                self._cache.move_to_end(run_id)
                return self._cache[run_id]
        with self._compute_lock:  # one recompute at a time (memory)
            result = self._analyse(run_id)
        self._remember(run_id, result)
        stored = {r["sample"]: r["consensus_sha"] for r in self.samples(run_id)}
        changed = [sr.sample for sr in result.samples
                   if stored.get(sr.sample, "") != (hashlib.sha256(sr.consensus.sequence.encode("ascii")).hexdigest()
                                                    if sr.consensus.sequence else "")]
        result.recomputed_changes = changed  # type: ignore[attr-defined]
        return result

    # --- reviews -----------------------------------------------------------------------------------------

    def reviews(self, run_id: int) -> dict[int, dict]:
        """Latest review per sample index: {idx: {id, decision, user, at}} (decision None = cleared)."""
        with self.db.read() as con:
            rows = con.execute("SELECT v.*, u.display_name AS reviewer FROM reviews v LEFT JOIN users u "
                               "ON u.id = v.user_id WHERE v.id IN (SELECT MAX(id) FROM reviews WHERE run_id = ? "
                               "GROUP BY idx)", (run_id,)).fetchall()
        return {r["idx"]: {"id": r["id"], "decision": r["decision"], "user": r["reviewer"] or "", "at": r["at"]}
                for r in rows}

    def decisions(self, run_id: int) -> dict[str, str]:
        """{sample name: "PASS"/"FAIL"} for review.select / review.apply."""
        names = {r["idx"]: r["sample"] for r in self.samples(run_id)}
        return {names[i]: v["decision"] for i, v in self.reviews(run_id).items() if v["decision"] and i in names}

    def set_review(self, run_id: int, idx: int, decision: str | None, user, expected: int) -> dict:
        """Record a decision; ``expected`` is the review id the page showed (0 = none). Raises Conflict."""
        if decision not in ("PASS", "FAIL", None):
            raise RunError("unknown decision")
        with self.db.tx() as con:
            sample = con.execute("SELECT sample FROM run_samples WHERE run_id = ? AND idx = ?", (run_id, idx)).fetchone()
            if sample is None:
                raise RunError("no such sample")
            latest = con.execute("SELECT v.*, u.display_name AS reviewer FROM reviews v LEFT JOIN users u "
                                 "ON u.id = v.user_id WHERE run_id = ? AND idx = ? ORDER BY v.id DESC LIMIT 1",
                                 (run_id, idx)).fetchone()
            if (latest["id"] if latest else 0) != expected:
                raise Conflict({"decision": latest["decision"], "user": latest["reviewer"], "at": latest["at"]}
                               if latest else None)
            previous = latest["decision"] if latest else None
            if previous == decision:
                return {"changed": False}
            con.execute("INSERT INTO reviews(run_id, idx, decision, previous, user_id, at) VALUES(?, ?, ?, ?, ?, ?)",
                        (run_id, idx, decision, previous, user.id, now()))
            audit.record(self.db, user, "review.set", f"run {run_id}", con=con, sample=sample["sample"],
                         old=previous, new=decision)
        return {"changed": True}

    def confirm_remaining(self, run_id: int, user) -> int:
        """Confirm every sample still without a decision with the status the rules gave."""
        with self.db.tx() as con:
            rows = con.execute(
                "SELECT s.idx, s.sample, s.status FROM run_samples s WHERE s.run_id = ? AND NOT EXISTS ("
                " SELECT 1 FROM reviews v WHERE v.run_id = s.run_id AND v.idx = s.idx AND v.id = ("
                "  SELECT MAX(id) FROM reviews WHERE run_id = s.run_id AND idx = s.idx) AND v.decision IS NOT NULL)",
                (run_id,)).fetchall()
            stamp = now()
            con.executemany("INSERT INTO reviews(run_id, idx, decision, previous, user_id, at) "
                            "VALUES(?, ?, ?, NULL, ?, ?)", [(run_id, r["idx"], r["status"], user.id, stamp)
                                                             for r in rows])
            if rows:
                audit.record(self.db, user, "review.confirm_remaining", f"run {run_id}", con=con, samples=len(rows))
        return len(rows)

    # --- export ------------------------------------------------------------------------------------------

    def export_zip(self, run_id: int, keys: list[str], scope: str, chosen: list[str], tmp: Path) -> tuple[bytes, int]:
        """The chosen files for the chosen samples, with reviewer decisions applied, as a zip."""
        known = {f.key for f in EXPORT_FILES}
        keys = [k for k in keys if k in known] or [f.key for f in EXPORT_FILES]
        if scope not in review.SCOPES:
            raise RunError("unknown scope")
        result = self.result(run_id)
        decisions = self.decisions(run_id)
        names = review.select(result, decisions, scope, chosen)
        if not names:
            raise RunError("No sample matches this choice.")
        tmp.mkdir(parents=True, exist_ok=True)
        folder = Path(tmp) / f"export-{run_id}-{threading.get_ident()}"
        if folder.exists():
            shutil.rmtree(folder)
        try:
            write_outputs(review.apply(result, decisions, names), folder, files=keys)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(folder.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(folder).as_posix())
            return buf.getvalue(), len(names)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def delete(self, run_id: int, user) -> None:
        with self.db.tx() as con:
            con.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            audit.record(self.db, user, "run.deleted", f"run {run_id}", con=con)
        shutil.rmtree(self.folder(run_id), ignore_errors=True)
        with self._cache_lock:
            self._cache.pop(run_id, None)


def preview(folder: Path, forward, reverse):
    """Pairing of the files in ``folder`` before a run: (pairs, skipped rows)."""
    return scan([folder], tuple(forward), tuple(reverse), check_headers=True)
