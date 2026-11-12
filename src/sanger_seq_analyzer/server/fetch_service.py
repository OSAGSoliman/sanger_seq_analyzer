"""Fetch data on the server: where each lab file comes from, a shared cache of the parsed
tables, one workspace per user, MeaNS template versions, and saving safely.

Each source (masterlist, daily sheet, line list, MeaNS template) has candidates, newest first:
  * files in the shared folder the admin chose for it, matching the lab's file names;
  * versions uploaded through the browser (data/sources/<key>/), with who uploaded them;
  * for the template, the versions the server keeps (data/templates/vNNNN.xlsx).
The newest is used unless the user pins another one.

Saving to the template is one locked step: the template is read again, and when it changed
since the user's preview (someone else saved), the names are renumbered and shown again
instead of being written.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..fetch import sources, tables, template
from ..fetch.linking import MEANS_COLUMNS
from ..fetch.session import FetchSession
from . import audit, paths
from .db import Database, now, parse_time

log = logging.getLogger("sanger_seq_analyzer.server.fetch")
SOURCES_KEY = "fetch_sources"  # setting: {key: {"root": id, "path": "sub/folder"}}
WORKSPACE_IDLE = 2 * 3600
UPLOAD_SUFFIXES = (".xlsx", ".xlsm", ".xlsb", ".xls", ".ods", ".csv", ".tsv", ".txt")


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class Candidate:
    id: str  # "f:<file name>" (shared folder), "u:<upload id>" (uploaded), "v:<version>" (template version)
    kind: str  # "folder", "upload" or "version"
    name: str
    path: Path
    when: float  # modified or uploaded (epoch seconds)
    by: str = ""

    @property
    def label(self) -> str:
        stamp = datetime.fromtimestamp(self.when).strftime("%d %b %Y %H:%M")
        where = {"folder": "shared folder", "upload": f"uploaded by {self.by}",
                 "version": f"server copy, saved by {self.by}"}[self.kind]
        return f"{self.name} · {stamp} · {where}"


@dataclass
class Workspace:
    session: FetchSession
    pins: dict[str, str] = field(default_factory=dict)  # source key -> candidate id
    used: dict[str, str] = field(default_factory=dict)  # source key -> candidate id + stamp used in the fetch
    seen: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    message: str = ""


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FetchService:
    def __init__(self, db: Database, data_dir: Path, tmp_dir: Path, max_sheet_mb: int = 100, cache_size: int = 8):
        self.db = db
        self.uploads_dir = data_dir / "sources"
        self.versions_dir = data_dir / "templates"
        self.tmp = tmp_dir / "fetch"
        self.max_bytes = max_sheet_mb * 1_000_000
        self._cache: OrderedDict[tuple, tuple] = OrderedDict()
        self._cache_size = cache_size
        self._cache_lock = threading.Lock()
        self._read_locks: dict[tuple, threading.Lock] = {}
        self.save_lock = threading.Lock()
        self.workspaces: dict[int, Workspace] = {}
        self._ws_lock = threading.Lock()

    # --- where the files are -----------------------------------------------------------------------------

    def folders(self) -> dict[str, dict]:
        return self.db.get_setting(SOURCES_KEY, {}) or {}

    def set_folder(self, key: str, root_id: str, sub: str, user) -> None:
        if key not in sources.SPECS:
            raise FetchError("unknown source")
        data = self.folders()
        if root_id:
            root = paths.root(self.db, root_id)
            paths.resolve(root, sub)  # refuses paths outside the root
            data[key] = {"root": root_id, "path": sub.strip().strip("/")}
        else:
            data.pop(key, None)
        self.db.set_setting(SOURCES_KEY, data)
        audit.record(self.db, user, "fetch.folder_set", key, root=root_id, path=sub)

    def folder_of(self, key: str) -> tuple[paths.Root, Path] | None:
        spec = self.folders().get(key)
        if not spec:
            return None
        try:
            root = paths.root(self.db, spec["root"])
            return root, paths.resolve(root, spec.get("path", ""))
        except paths.PathError:
            return None

    def candidates(self, key: str, limit: int = 8) -> list[Candidate]:
        out: list[Candidate] = []
        found = self.folder_of(key)
        if found:
            _root, folder = found
            try:
                entries = [f for f in folder.iterdir() if sources.is_candidate(key, f.name) and f.is_file()]
            except OSError:
                entries = []
            for f in sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                out.append(Candidate(f"f:{f.name}", "folder", f.name, f, f.stat().st_mtime))
        with self.db.read() as con:
            for row in con.execute("SELECT s.*, u.display_name AS who FROM source_uploads s LEFT JOIN users u "
                                   "ON u.id = s.uploaded_by WHERE key = ? ORDER BY s.id DESC LIMIT ?", (key, limit)):
                when = parse_time(row["uploaded_at"])
                out.append(Candidate(f"u:{row['id']}", "upload", row["name"], self.uploads_dir / key / row["stored"],
                                     when.timestamp() if when else 0, row["who"] or ""))
            if key == "template":
                for row in con.execute("SELECT t.*, u.display_name AS who FROM template_versions t LEFT JOIN users u "
                                       "ON u.id = t.created_by ORDER BY version DESC LIMIT ?", (limit,)):
                    when = parse_time(row["created_at"])
                    out.append(Candidate(f"v:{row['version']}", "version", row["name"],
                                         self.versions_dir / row["stored"], when.timestamp() if when else 0,
                                         row["who"] or ""))
        if key == "template":  # older uploads of the template live on as versions
            out = [c for c in out if c.kind != "upload"]
        return sorted(out, key=lambda c: c.when, reverse=True)

    def chosen(self, key: str, ws: Workspace | None) -> Candidate | None:
        options = self.candidates(key)
        pin = ws.pins.get(key) if ws else None
        if pin:
            for c in options:
                if c.id == pin:
                    return c
        return options[0] if options else None

    # --- the shared cache of parsed tables -----------------------------------------------------------------

    def table(self, key: str, cand: Candidate) -> tuple[tables.Table | None, str, tuple | None]:
        """(table, error, stamp) for a candidate file; read once and shared by every user."""
        stamp = sources.stamp(cand.path)
        if stamp is None:
            return None, f"file not found: {cand.name}", None
        cache_key = (key, *stamp)
        with self._cache_lock:
            hit = self._cache.get(cache_key)
            if hit is not None:
                self._cache.move_to_end(cache_key)
                return (*hit, stamp)
            lock = self._read_locks.setdefault(cache_key, threading.Lock())
        with lock:  # two users asking for the same new file: read it once
            with self._cache_lock:
                hit = self._cache.get(cache_key)
            if hit is None:
                hit = self._read(key, cand)
                with self._cache_lock:
                    self._cache[cache_key] = hit
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                    self._read_locks.pop(cache_key, None)
        return (*hit, stamp)

    def _read(self, key: str, cand: Candidate) -> tuple[tables.Table | None, str]:
        """Read a local copy: Excel may be saving the file on the share right now."""
        self.tmp.mkdir(parents=True, exist_ok=True)
        local = self.tmp / f"read-{threading.get_ident()}-{time.time_ns()}{cand.path.suffix.lower()}"
        try:
            for attempt in range(2):
                try:
                    shutil.copy2(cand.path, local)
                except OSError as exc:
                    return None, f"{cand.name} could not be read: {exc.strerror or exc}"
                _stamp, table, error = sources.read_one(key, local)
                if table is not None or attempt or "BadZipFile" not in error and "zip" not in error.lower():
                    return table, error.replace(local.name, cand.name)
                time.sleep(1.0)  # caught half-saved: try once more
            return None, "unreadable"
        finally:
            local.unlink(missing_ok=True)

    # --- uploads -----------------------------------------------------------------------------------------

    def upload(self, key: str, filename: str, data: bytes, user, force: bool = False) -> str:
        """Keep an uploaded version of a source; returns a message. Raises FetchError."""
        if key not in sources.SPECS:
            raise FetchError("unknown source")
        name = Path(filename.replace("\\", "/")).name.strip() or "file"
        suffix = Path(name).suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            raise FetchError(f"{name}: choose an Excel workbook (.xlsx, .xlsm, .xlsb, .xls), .ods or .csv file.")
        if len(data) > self.max_bytes:
            raise FetchError(f"{name} is larger than {self.max_bytes // 1_000_000} MB.")
        if suffix in (".xlsx", ".xlsm"):
            self._check_zip(data, name)
        if key == "template":
            return self._upload_template(name, data, user, force)
        folder = self.uploads_dir / key
        folder.mkdir(parents=True, exist_ok=True)
        stored = f"{datetime.now(UTC):%Y%m%dT%H%M%S}_{hashlib.sha256(data).hexdigest()[:8]}{suffix}"
        (folder / stored).write_bytes(data)
        # parse now, so a broken file is refused at once
        cand = Candidate("new", "upload", name, folder / stored, time.time())
        table, error = self._read(key, cand)
        if table is None:
            (folder / stored).unlink(missing_ok=True)
            raise FetchError(error or f"{name} could not be read")
        with self.db.tx() as con:
            con.execute("INSERT INTO source_uploads(key, name, stored, size, sha256, uploaded_by, uploaded_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)", (key, name, stored, len(data), hashlib.sha256(data).hexdigest(),
                                                        user.id, now()))
            audit.record(self.db, user, "fetch.uploaded", key, con=con, file=name, rows=len(table.rows))
        return f"{name} uploaded: {len(table.rows)} rows."

    def _check_zip(self, data: bytes, name: str) -> None:
        """Refuse workbooks that would unpack to something huge (a zip bomb)."""
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                total = sum(i.file_size for i in z.infolist())
        except zipfile.BadZipFile:
            raise FetchError(f"{name} is not a valid Excel workbook.") from None
        if total > 20 * self.max_bytes:
            raise FetchError(f"{name} unpacks to {total // 1_000_000} MB; that is too large.")

    def _latest_version(self):
        with self.db.read() as con:
            return con.execute("SELECT * FROM template_versions ORDER BY version DESC LIMIT 1").fetchone()

    def _add_version(self, name: str, source: Path, user, reason: str, con=None) -> int:
        latest = self._latest_version()
        version = (latest["version"] + 1) if latest else 1
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        stored = f"v{version:04d}{source.suffix.lower()}"
        shutil.copy2(source, self.versions_dir / stored)
        sql = ("INSERT INTO template_versions(version, name, stored, sha256, created_by, created_at, reason) "
               "VALUES(?, ?, ?, ?, ?, ?, ?)")
        args = (version, name, stored, file_sha(self.versions_dir / stored), user.id, now(), reason)
        if con is not None:
            con.execute(sql, args)
        else:
            with self.db.tx() as c:
                c.execute(sql, args)
        return version

    def _upload_template(self, name: str, data: bytes, user, force: bool) -> str:
        suffix = Path(name).suffix.lower()
        if suffix not in (".xlsx", ".xlsm"):
            raise FetchError(f"{name}: the MeaNS template must be an .xlsx or .xlsm workbook, so rows can be added "
                             "to it. Save it as .xlsx in Excel first.")
        self.tmp.mkdir(parents=True, exist_ok=True)
        incoming = self.tmp / f"upload-{time.time_ns()}{suffix}"
        incoming.write_bytes(data)
        try:
            _st, table, error = sources.read_one("template", incoming)
            if table is None:
                raise FetchError(error or f"{name} could not be read as the MeaNS template")
            with self.save_lock:
                latest = self._latest_version()
                if latest is not None and not force:
                    _s, current, _e = sources.read_one("template", self.versions_dir / latest["stored"])
                    have = {tables.norm_key(n) for n in sources.strain_names(current)} - {""}
                    new = {tables.norm_key(n) for n in sources.strain_names(table)} - {""}
                    missing = have - new
                    if missing:
                        raise FetchError(f"{name} does not have {len(missing)} strain names that the server's "
                                         f"current template has (e.g. {sorted(missing)[0]}). It may be an older "
                                         "download. Upload it anyway only if those rows were removed on purpose.")
                version = self._add_version(name, incoming, user, "upload")
            audit.record(self.db, user, "template.uploaded", f"version {version}", file=name,
                         names=len(sources.strain_names(table)), forced=force)
            return f"{name} is now template version {version} ({len(table.rows)} rows)."
        finally:
            incoming.unlink(missing_ok=True)

    # --- workspaces --------------------------------------------------------------------------------------

    def workspace(self, user, prefs: dict | None = None) -> Workspace:
        with self._ws_lock:
            limit = time.time() - WORKSPACE_IDLE
            for uid in [u for u, w in self.workspaces.items() if w.seen < limit]:
                del self.workspaces[uid]  # the patient rows of an idle workspace are dropped
            ws = self.workspaces.get(user.id)
            if ws is None:
                prefs = prefs or {}
                ws = Workspace(FetchSession(prefs.get("fetch_virus", "MVs"), prefs.get("fetch_columns") or None))
                self.workspaces[user.id] = ws
            ws.seen = time.time()
            return ws

    def load(self, ws: Workspace) -> dict[str, Candidate | None]:
        """Put the chosen version of every source into the workspace (from the shared cache)."""
        chosen = {}
        for key in sources.SPECS:
            cand = self.chosen(key, ws)
            chosen[key] = cand
            if cand is None:
                ws.session.set_table(key, None, "")
                continue
            table, error, stamp = self.table(key, cand)
            ws.session.set_table(key, table, error, stamp)
        return chosen

    def changed_since_fetch(self, ws: Workspace) -> list[str]:
        """Sources whose chosen file changed after the user's last fetch."""
        out = []
        for key, used in ws.used.items():
            cand = self.chosen(key, ws)
            now_id = f"{cand.id}|{sources.stamp(cand.path)}" if cand else ""
            if now_id != used:
                out.append(key)
        return out

    def fetch(self, ws: Workspace, today) -> str:
        chosen = self.load(ws)
        problem = ws.session.fetch(today)
        if not problem:
            ws.used = {k: f"{c.id}|{sources.stamp(c.path)}" for k, c in chosen.items() if c is not None}
        return problem

    # --- saving to the template --------------------------------------------------------------------------

    def save_preview(self, ws: Workspace) -> tuple[list[tuple[str, str]], str, Candidate]:
        """Rows to save with their (renumbered if needed) names, and the template's sha256 as the token."""
        cand = self.chosen("template", ws)
        if cand is None:
            raise FetchError("There is no MeaNS template yet: upload it, or ask an admin to set its folder.")
        table, error, stamp = self.table("template", cand)
        if table is None:
            raise FetchError(error)
        if stamp != ws.session.stamps.get("template"):
            ws.session.set_table("template", table, "", stamp)
            ws.session.regenerate_names()
        rows, _values = ws.session.template_rows()
        return [(r.accession, str(r.values.get("WHO Strain Name"))) for r in rows], file_sha(cand.path), cand

    def save(self, ws: Workspace, user, token: str, confirmed: list[str]) -> dict:
        """The locked save. Returns {"saved": AppendResult, ...} or {"renumbered": [(accession, name)], ...}."""
        with self.save_lock:
            cand = self.chosen("template", ws)
            if cand is None:
                raise FetchError("There is no MeaNS template.")
            sha_before = file_sha(cand.path)
            if sha_before != token:
                # someone saved meanwhile (here, or in Excel, or with the desktop app): renumber, write nothing
                self.tmp.mkdir(parents=True, exist_ok=True)
                fresh, error, stamp = self.table("template", cand)
                if fresh is None:
                    raise FetchError(error)
                ws.session.set_table("template", fresh, "", stamp)
                ws.session.regenerate_names()
                rows, _ = ws.session.template_rows()
                return {"renumbered": [(r.accession, str(r.values.get("WHO Strain Name"))) for r in rows],
                        "token": sha_before}
            rows, values = ws.session.template_rows()
            names = [str(r.values.get("WHO Strain Name")) for r in rows]
            if names != list(confirmed):
                raise FetchError("The rows changed after you confirmed. Open Save again.")
            if cand.kind == "folder":
                result = template.append_rows(cand.path, values, list(MEANS_COLUMNS))
                target = cand.path
            else:
                self.tmp.mkdir(parents=True, exist_ok=True)
                work = self.tmp / f"save-{time.time_ns()}{cand.path.suffix}"
                shutil.copy2(cand.path, work)
                try:
                    result = template.append_rows(work, values, list(MEANS_COLUMNS), backup=False)
                    version = self._add_version(cand.name, work, user, "save") if result.written else None
                finally:
                    work.unlink(missing_ok=True)
                target = self.versions_dir / f"v{version:04d}{cand.path.suffix}" if version else cand.path
                if version:
                    ws.pins.pop("template", None)  # follow the new version
            # read it back: the names must be there
            _s, check, error = sources.read_one("template", target)
            saved_names = {tables.norm_key(n) for n in sources.strain_names(check)} if check else set()
            skipped = {s.split(":", 1)[0] for s in result.skipped}  # already in the template
            missing = [str(r.values.get("WHO Strain Name")) for r in rows if r.accession not in skipped
                       and tables.norm_key(r.values.get("WHO Strain Name")) not in saved_names]
            if result.written and missing:
                raise FetchError(f"After saving, {len(missing)} names are not in the template (e.g. {missing[0]}). "
                                 "Check the file and the backup.")
            ws.session.mark_saved(rows, result)
            fresh_cand = self.chosen("template", ws)
            if fresh_cand is not None:
                table, error, stamp = self.table("template", fresh_cand)
                ws.session.set_table("template", table, error, stamp)
            audit.record(self.db, user, "template.saved", cand.name, rows=result.written,
                         accessions=[r.accession for r in rows], names=names, skipped=result.skipped,
                         sha_before=sha_before, sha_after=file_sha(target), backup=str(result.backup or ""))
            return {"saved": result, "target": target, "kind": cand.kind}

    def version_file(self, version: int) -> Path | None:
        with self.db.read() as con:
            row = con.execute("SELECT stored FROM template_versions WHERE version = ?", (version,)).fetchone()
        return self.versions_dir / row["stored"] if row else None
