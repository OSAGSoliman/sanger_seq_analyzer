"""State of the Fetch data page: a :class:`~sanger_seq_analyzer.fetch.session.FetchSession`
with Qt signals, remembered paths and a worker thread for reading the spreadsheets.

Reading large workbooks can take seconds, so :meth:`FetchController.load_sources`
reads them on a worker thread (``block=True`` reads in the caller, for tests).
Files are read again only when they changed on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ...fetch import linking, sources, tables, template
from ...fetch.linking import MEANS_COLUMNS
from ...fetch.session import FetchSession
from ...fetch.sources import SOURCE_FILES, SPECS
from ..persist import Store

log = logging.getLogger("sanger_seq_analyzer.gui")
FILE_FILTER = "Spreadsheets (*.xlsx *.xlsm *.xlsb *.xls *.ods *.csv *.tsv *.txt);;All files (*)"


class _Signals(QObject):
    done = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn, signals: _Signals):
        super().__init__()
        self.fn, self.signals = fn, signals

    def run(self) -> None:
        self.signals.done.emit(self.fn())


def _session_attr(name: str):
    return property(lambda self: getattr(self.session, name),
                    lambda self, value: setattr(self.session, name, value))


class FetchController(QObject):
    sourcesChanged = Signal()  # paths, tables or errors changed
    loadingChanged = Signal(bool)
    genotypingChanged = Signal()
    rowsChanged = Signal()  # linked rows rebuilt
    namesChanged = Signal()  # strain names regenerated (values changed in place)
    columnsChanged = Signal()

    paths = _session_attr("paths")
    tables = _session_attr("tables")
    errors = _session_attr("errors")
    virus = _session_attr("virus")
    columns = _session_attr("columns")
    genotyping = _session_attr("genotyping")
    genotyping_error = _session_attr("genotyping_error")
    linked = _session_attr("linked")
    rows = _session_attr("rows")

    def __init__(self, store: Store, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.session = FetchSession(store.get_str("fetch/virus", "MVs") or "MVs",
                                    store.get_list("fetch/columns") or list(MEANS_COLUMNS))
        for k in SPECS:
            self.session.paths[k] = store.get_str(f"fetch/{k}")
        self.loading = False
        self._pending_signals: _Signals | None = None
        self._after_load = None

    # --- source files ----------------------------------------------------------------------------------

    def missing_paths(self) -> list[str]:
        return self.session.missing_paths()

    def set_path(self, key: str, path: str) -> None:
        if self.session.set_path(key, path):
            self.store.set(f"fetch/{key}", self.session.paths[key])
            self.sourcesChanged.emit()

    def name_filter(self, key: str) -> str:
        """File-dialog filter showing the lab's files of this kind first, then every spreadsheet."""
        label, _example, patterns = SOURCE_FILES[key]
        globs = " ".join(f"{p}{suffix}" for p in patterns for suffix in tables.READABLE_SUFFIXES
                         if suffix not in (".txt", ".tsv"))
        return f"{label} ({globs});;{FILE_FILTER}"

    def newer_file(self, key: str) -> Path | None:
        """A more recently saved file of the same kind in the chosen file's folder (e.g. next week's sheet)."""
        return sources.newer_file(key, self.paths.get(key))

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
            self._apply(self.session.read_changed())
            if then:
                then()
            return
        self.loading = True
        self._after_load = then
        self.loadingChanged.emit(True)
        signals = _Signals()
        signals.done.connect(self._loaded)
        self._pending_signals = signals
        QThreadPool.globalInstance().start(_Task(self.session.read_changed, signals))

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
        self.session.apply(results)
        self.sourcesChanged.emit()

    def existing_names(self) -> list:
        return self.session.existing_names()

    # --- genotyping table ------------------------------------------------------------------------------

    def set_genotyping_text(self, text: str) -> bool:
        ok = self.session.set_genotyping_text(text)
        self.genotypingChanged.emit()
        return ok

    def set_genotyping_file(self, path: str | Path) -> bool:
        ok = self.session.set_genotyping_file(path)
        self.genotypingChanged.emit()
        return ok

    def set_genotyping_rows(self, columns: list[str], rows: list[dict]) -> None:
        """The table as edited in the window."""
        self.session.set_genotyping_rows(columns, rows)

    def genotyping_from_run(self, run, reviews) -> None:
        self.session.genotyping_from_run(run, reviews)
        self.genotypingChanged.emit()

    # --- linking ---------------------------------------------------------------------------------------

    def set_virus(self, virus: str) -> None:
        had_rows = bool(self.rows)
        if self.session.set_virus(virus):
            self.store.set("fetch/virus", self.virus)
            if had_rows:
                self.namesChanged.emit()

    def fetch(self) -> str:
        """Link the genotyping table to the loaded sources; returns a problem to show, or ""."""
        problem = self.session.fetch()
        if not problem:
            self.rowsChanged.emit()
        return problem

    def regenerate_names(self) -> None:
        self.session.regenerate_names()
        self.namesChanged.emit()

    def edit_cell(self, index: int, column: str, text: str) -> None:
        if self.session.edit_cell(index, column, text):
            self.namesChanged.emit()

    def all_columns(self) -> list[str]:
        return self.session.all_columns()

    def set_columns(self, columns: list[str]) -> None:
        self.session.set_columns(columns)
        self.store.set("fetch/columns", self.columns)
        self.columnsChanged.emit()

    def value(self, row: linking.MeansRow, column: str):
        return self.session.value(row, column)

    # --- saving ----------------------------------------------------------------------------------------

    def rows_to_save(self) -> list[linking.MeansRow]:
        return self.session.rows_to_save()

    def save_to_template(self) -> template.AppendResult:
        """Append the included rows (MeaNS columns) to the template; raises TableError."""
        result = self.session.save_to_template()
        self.sourcesChanged.emit()
        self.rowsChanged.emit()
        return result

    def export_workbook(self, path: str | Path) -> Path:
        return self.session.export_workbook(path)
