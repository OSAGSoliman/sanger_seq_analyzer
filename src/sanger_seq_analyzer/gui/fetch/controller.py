"""State of the Fetch data page: source files, the genotyping table, linked rows and saving.

Reading large workbooks can take seconds, so :meth:`FetchController.load_sources`
reads them on a worker thread (``block=True`` reads in the caller, for tests).
Files are read again only when they changed on disk.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ...fetch import linking, tables, template
from ...fetch.linking import DAILY, GENOTYPING, LINELIST, MASTERLIST, MEANS_COLUMNS, TEMPLATE, SourceSpec
from ..persist import Store

log = logging.getLogger("sanger_seq_analyzer.gui")
SPECS: dict[str, SourceSpec] = {s.key: s for s in (MASTERLIST, DAILY, LINELIST, TEMPLATE)}
# key -> (label, the lab's file name, name patterns used by Browse… and to spot a newer version in the same folder)
SOURCE_FILES = {
    "masterlist": ("Measles and rubella master list", "Measles and Rubella Positive Masterlist 2026",
                   ("*masterlist*",)),
    "daily": ("Measles daily sheet", "Weekly update on measles-rubella results 09112026",
              ("*measles*rubella*results*", "*daily*sheet*")),
    "linelist": ("MOHAP line list", "UAE_EMRO Measles Rubella Reporting Tool Sep 2026",
                 ("*reporting tool*", "*line*list*")),
    "template": ("MeaNS upload template", "MeaNS Upload Template 2025-2026", ("*means*template*",)),
}
FILE_FILTER = "Spreadsheets (*.xlsx *.xlsm *.xlsb *.xls *.ods *.csv *.tsv *.txt);;All files (*)"


def read_source(spec: SourceSpec, path: str | Path) -> tables.Table:
    return tables.read_table(path, spec.title, list(spec.expected), spec.match_column, combine=spec.combine)


class _Signals(QObject):
    done = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn, signals: _Signals):
        super().__init__()
        self.fn, self.signals = fn, signals

    def run(self) -> None:
        self.signals.done.emit(self.fn())


class FetchController(QObject):
    sourcesChanged = Signal()  # paths, tables or errors changed
    loadingChanged = Signal(bool)
    genotypingChanged = Signal()
    rowsChanged = Signal()  # linked rows rebuilt
    namesChanged = Signal()  # strain names regenerated (values changed in place)
    columnsChanged = Signal()

    def __init__(self, store: Store, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.paths: dict[str, str] = {k: store.get_str(f"fetch/{k}") for k in SPECS}
        self.tables: dict[str, tables.Table | None] = {k: None for k in SPECS}
        self.errors: dict[str, str] = {k: "" for k in SPECS}
        self._stamps: dict[str, tuple] = {}
        self.virus = store.get_str("fetch/virus", "MVs") or "MVs"
        saved = store.get_list("fetch/columns")
        self.columns: list[str] = saved or list(MEANS_COLUMNS)
        self.genotyping: tables.Table | None = None
        self.genotyping_error = ""
        self.linked: linking.Linked | None = None
        self.rows: list[linking.MeansRow] = []
        self.loading = False
        self._pending_signals: _Signals | None = None
        self._after_load = None

    # --- source files ----------------------------------------------------------------------------------

    def missing_paths(self) -> list[str]:
        return [k for k in SPECS if not self.paths.get(k)]

    def set_path(self, key: str, path: str) -> None:
        path = path.strip()
        if path == self.paths.get(key):
            return
        self.paths[key] = path
        self.store.set(f"fetch/{key}", path)
        self.tables[key], self.errors[key] = None, ""
        self._stamps.pop(key, None)
        self.sourcesChanged.emit()

    def name_filter(self, key: str) -> str:
        """File-dialog filter showing the lab's files of this kind first, then every spreadsheet."""
        label, _example, patterns = SOURCE_FILES[key]
        globs = " ".join(f"{p}{suffix}" for p in patterns for suffix in tables.READABLE_SUFFIXES
                         if suffix not in (".txt", ".tsv"))
        return f"{label} ({globs});;{FILE_FILTER}"

    def newer_file(self, key: str) -> Path | None:
        """A more recently saved file of the same kind in the chosen file's folder (e.g. next week's sheet)."""
        path = self.paths.get(key)
        if not path:
            return None
        current = Path(path)
        folder = current.parent
        try:
            current_time = current.stat().st_mtime if current.is_file() else -1.0
            entries = list(folder.iterdir())
        except OSError:
            return None
        patterns = SOURCE_FILES[key][2]
        best, best_time = None, current_time
        for f in entries:
            name = f.name.lower()
            if (name.startswith(("~$", ".")) or f.suffix.lower() not in tables.READABLE_SUFFIXES
                    or not any(fnmatch.fnmatch(name, p) for p in patterns)):
                continue
            try:
                if not f.is_file() or f.resolve() == current.resolve():
                    continue
                t = f.stat().st_mtime
            except OSError:
                continue
            if t > best_time:
                best, best_time = f, t
        return best

    def _stamp(self, path: str) -> tuple | None:
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)

    def _read_changed(self) -> dict[str, tuple]:
        """Read every source whose file changed; returns key -> (stamp, table or None, error)."""
        out = {}
        for key, spec in SPECS.items():
            path = self.paths.get(key)
            if not path:
                continue
            stamp = self._stamp(path)
            if stamp is not None and stamp == self._stamps.get(key) and self.tables.get(key) is not None:
                continue
            if stamp is None:
                out[key] = (None, None, f"file not found: {path}")
                continue
            try:
                out[key] = (stamp, read_source(spec, path), "")
            except tables.TableError as exc:
                out[key] = (stamp, None, str(exc))
            except Exception as exc:  # unexpected file content: report, do not crash the window
                log.exception("reading %s failed", path)
                out[key] = (stamp, None, f"{type(exc).__name__}: {exc}")
        return out

    def load_sources(self, block: bool = False, then=None) -> None:
        """Read changed source files (on a worker thread unless ``block``), then call ``then()``."""
        if self.loading:  # a read is under way (e.g. started when the page opened): run ``then`` after it
            if then is not None:
                previous = self._after_load

                def chained(prev=previous, nxt=then):
                    if prev:
                        prev()
                    nxt()
                self._after_load = chained
            return
        if block:
            self._apply(self._read_changed())
            if then:
                then()
            return
        self.loading = True
        self._after_load = then
        self.loadingChanged.emit(True)
        signals = _Signals()
        signals.done.connect(self._loaded)
        self._pending_signals = signals
        QThreadPool.globalInstance().start(_Task(self._read_changed, signals))

    @Slot(object)
    def _loaded(self, results: dict) -> None:
        self.loading = False
        self._pending_signals = None
        self._apply(results)
        self.loadingChanged.emit(False)
        then, self._after_load = self._after_load, None
        if then:
            then()

    def _apply(self, results: dict) -> None:
        for key, (stamp, table, error) in results.items():
            self.tables[key], self.errors[key] = table, error
            if stamp is not None and table is not None:
                self._stamps[key] = stamp
            else:
                self._stamps.pop(key, None)
        self.sourcesChanged.emit()

    def existing_names(self) -> list:
        t = self.tables.get("template")
        if t is None:
            return []
        col = t.column("WHO Strain Name")
        return [r.get(col) for r in t.rows] if col else []

    # --- genotyping table ------------------------------------------------------------------------------

    def set_genotyping_text(self, text: str) -> bool:
        try:
            self.genotyping = tables.text_table(text, GENOTYPING.title, list(GENOTYPING.expected),
                                                GENOTYPING.match_column, key_filter=tables.looks_like_accession)
            self.genotyping_error = ""
        except tables.TableError as exc:
            self.genotyping_error = f"{exc}. Copy the table from Excel including its header row (SN, Accession, ...)."
            self.genotypingChanged.emit()
            return False
        self.genotypingChanged.emit()
        return True

    def set_genotyping_file(self, path: str | Path) -> bool:
        try:
            self.genotyping = tables.read_table(path, GENOTYPING.title, list(GENOTYPING.expected),
                                                GENOTYPING.match_column, key_filter=tables.looks_like_accession)
            self.genotyping_error = ""
        except tables.TableError as exc:
            self.genotyping_error = str(exc)
            self.genotypingChanged.emit()
            return False
        self.genotypingChanged.emit()
        return True

    def set_genotyping_rows(self, columns: list[str], rows: list[dict]) -> None:
        """The table as edited in the window."""
        acc = tables.match_column("Accession", columns)
        self.genotyping = tables.Table(GENOTYPING.title, columns,
                                       [r for r in rows if tables.looks_like_accession(r.get(acc))])
        self.genotyping_error = ""

    def genotyping_from_run(self, run, reviews) -> None:
        self.genotyping = linking.genotyping_from_run(run, reviews)
        self.genotyping_error = ""
        self.genotypingChanged.emit()

    # --- linking ---------------------------------------------------------------------------------------

    def set_virus(self, virus: str) -> None:
        if virus in ("MVs", "RVs") and virus != self.virus:
            self.virus = virus
            self.store.set("fetch/virus", virus)
            if self.rows:
                self.regenerate_names()

    def fetch(self) -> str:
        """Link the genotyping table to the loaded sources; returns a problem to show, or ""."""
        if self.genotyping is None or not self.genotyping.rows:
            return "Paste or load the genotyping table first."
        if self.tables.get("masterlist") is None:
            return "The masterlist is needed to find each patient (choose it under Source files)."
        self.linked = linking.link(self.genotyping, self.tables.get("masterlist"), self.tables.get("daily"),
                                   self.tables.get("linelist"))
        self.rows = linking.build_rows(self.linked, self.virus, self.existing_names())
        self.rowsChanged.emit()
        return ""

    def regenerate_names(self) -> None:
        linking.assign_strain_names(self.rows, self.virus, self.existing_names())
        self.namesChanged.emit()

    def all_columns(self) -> list[str]:
        return self.linked.all_columns() if self.linked else []

    def set_columns(self, columns: list[str]) -> None:
        self.columns = list(columns) or list(MEANS_COLUMNS)
        self.store.set("fetch/columns", self.columns)
        self.columnsChanged.emit()

    def value(self, row: linking.MeansRow, column: str):
        if column in row.values:
            return row.values[column]
        if self.linked is None:
            return None
        return self.linked.merged(row.sample).get(column)

    # --- saving ----------------------------------------------------------------------------------------

    def rows_to_save(self) -> list[linking.MeansRow]:
        return [r for r in self.rows if r.include]

    def save_to_template(self) -> template.AppendResult:
        """Append the included rows (MeaNS columns) to the template; raises TableError."""
        path = self.paths.get("template")
        if not path:
            raise tables.TableError("choose the MeaNS upload template under Source files first")
        rows = self.rows_to_save()
        if not rows:
            raise tables.TableError("no rows are ticked to save")
        missing = [r.accession for r in rows if not str(r.values.get("WHO Strain Name") or "").strip()]
        if missing:
            raise tables.TableError("these rows have no WHO strain name: " + ", ".join(missing))
        result = template.append_rows(path, [{c: r.values.get(c, "") for c in MEANS_COLUMNS} for r in rows],
                                      list(MEANS_COLUMNS))
        saved = {tables.norm_key(r.values.get("Accession")) for r in rows} if result.written else set()
        skipped = {s.split(":", 1)[0] for s in result.skipped}
        for r in rows:
            if tables.norm_key(r.values.get("Accession")) in saved and r.accession not in skipped:
                r.include = False
                r.strain_manual = True  # keep the saved name even if names are regenerated later
        self._stamps.pop("template", None)
        self.load_sources(block=True)  # the next names continue after the ones just saved
        self.rowsChanged.emit()
        return result

    def export_workbook(self, path: str | Path) -> Path:
        cols = list(self.columns)
        rows = [[self.value(r, c) for c in cols] for r in self.rows]
        sheets = [("MeaNS", cols, rows)]
        if self.linked is not None:
            all_cols = self.linked.all_columns()
            sheets.append(("All columns", all_cols,
                           [[self.linked.merged(s).get(c) for c in all_cols] for s in self.linked.samples]))
            sheets.append(("Matching", ["Accession", "Result", "Notes"],
                           [[s.accession, s.status(), "; ".join(s.notes)] for s in self.linked.samples]))
        return template.write_workbook(path, sheets)
