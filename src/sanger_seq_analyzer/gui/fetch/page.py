"""Fetch data page: source files, the genotyping table, and the linked MeaNS rows.

1. Source files: the masterlist, the daily sheet, the MOHAP line list and the MeaNS
   upload template (chosen once, remembered).
2. Genotyping results: pasted from Excel, loaded from a file, or filled from the QC run.
3. Fetch: every accession is matched to the files; the MeaNS table can be edited,
   columns chosen, exported, and the ticked rows appended to the template.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHeaderView, QListWidget,
    QLineEdit, QListWidgetItem, QMessageBox, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...fetch import tables
from ...fetch.linking import COMPUTED, DATE_COLUMNS, GENOTYPING, MEANS_COLUMNS
from ...presentation import plural
from ..theme import current
from ..widgets.common import button, card, divider, hbox, label, vbox
from ..widgets.notice import Notice
from ..widgets.path_field import PathField
from ..widgets.segmented import Segmented
from .controller import FILE_FILTER, SOURCE_FILES, SPECS, FetchController

SOURCE_HINTS = {
    "masterlist": "matched on ACCESSION",
    "daily": "every sheet is combined; matched on MRN (reporting facility)",
    "linelist": "matched on LabID; .xlsb (Excel Binary Workbook) is fine",
    "template": "existing WHO strain names; confirmed rows are added here (.xlsx or .xlsm)",
}
ROW_FILTERS = ("All", "To check", "Not in every file", "Ticked to save")
NAME_INPUTS = {"Admin Level 1", "Case Onset Date", "Collection Date", "to upload?"}


class GenotypingTable(QTableWidget):
    """Editable genotyping table; Ctrl+V pastes a range copied from Excel."""

    pasted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(0, len(GENOTYPING.expected), parent)
        self.setHorizontalHeaderLabels(list(GENOTYPING.expected))
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setMinimumHeight(220)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

    def keyPressEvent(self, e) -> None:
        if e.matches(QKeySequence.StandardKey.Paste):
            self.pasted.emit(QGuiApplication.clipboard().text())
            return
        super().keyPressEvent(e)

    def set_table(self, table: tables.Table | None) -> None:
        cols = list(GENOTYPING.expected)
        if table is not None:
            cols += [c for c in table.columns if not tables.match_column(c, cols)]
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)
        rows = table.rows if table is not None else []
        self.setRowCount(len(rows))
        mapping = {c: (table.column(c) if table is not None else None) for c in cols}
        for r, row in enumerate(rows):
            for c, col in enumerate(cols):
                actual = mapping[col]
                self.setItem(r, c, QTableWidgetItem(tables.as_text(row.get(actual)) if actual else ""))
        self.resizeColumnsToContents()

    def rows(self) -> tuple[list[str], list[dict]]:
        cols = [self.horizontalHeaderItem(c).text() for c in range(self.columnCount())]
        out = []
        for r in range(self.rowCount()):
            out.append({cols[c]: (self.item(r, c).text().strip() if self.item(r, c) else "")
                        for c in range(self.columnCount())})
        return cols, out


class ColumnsDialog(QDialog):
    """Tick the columns of the MeaNS table; drag to reorder."""

    def __init__(self, chosen: list[str], available: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose columns")
        self.resize(520, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Tick the columns to show, export and keep. Drag to change the order. Only MeaNS "
                            "columns are written to the upload template.", "secondary", wrap=True))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        order = list(chosen) + [c for c in [*MEANS_COLUMNS, *available] if c not in chosen]
        for c in order:
            item = QListWidgetItem(c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if c in chosen else Qt.CheckState.Unchecked)
            if c in MEANS_COLUMNS:
                item.setToolTip("MeaNS column")
            self.list.addItem(item)
        lay.addWidget(self.list, 1)
        reset = button("MeaNS default", size="small")
        reset.clicked.connect(self.reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addLayout(hbox(reset, None, buttons))

    def reset(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in MEANS_COLUMNS else Qt.CheckState.Unchecked)

    def chosen(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class FetchPage(QWidget):
    """The page itself. ``qc`` is the QC controller (for "Use QC results" and the reference)."""

    def __init__(self, controller: FetchController, qc=None, parent=None):
        super().__init__(parent)
        self.setProperty("page", True)
        self.c = controller
        self.qc = qc
        self._filling = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("page", True)
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(18)
        lay.addLayout(vbox(label("Fetch data", "pageTitle"),
                           label("Link genotyping results to the masterlist, the daily sheet and the MOHAP line list, "
                                 "then prepare the MeaNS upload rows.", "secondary"), spacing=4))
        lay.addWidget(self._sources_card())
        lay.addWidget(self._genotyping_card())
        lay.addWidget(self._results_card())
        lay.addStretch(1)
        c = self.c
        c.sourcesChanged.connect(self.refresh_sources)
        c.loadingChanged.connect(self._loading)
        c.genotypingChanged.connect(self._genotyping_changed)
        c.rowsChanged.connect(self.refresh_results)
        c.namesChanged.connect(self.refresh_results)
        c.columnsChanged.connect(self.refresh_results)
        self.refresh_sources()
        self.refresh_results()

    # --- construction -----------------------------------------------------------------------------------

    def _sources_card(self) -> QFrame:
        frame, lay = card(margins=(24, 20, 24, 20), spacing=10)
        lay.addWidget(label("Source files", "cardTitle"))
        self.sources_notice = Notice("Choose these files once; the app remembers them and reads them again "
                                     "when they change.", "neutral")
        lay.addWidget(self.sources_notice)
        self.fields: dict[str, PathField] = {}
        self.source_status: dict[str, object] = {}
        self.newer: dict[str, object] = {}
        for i, key in enumerate(SPECS):
            if i:
                lay.addWidget(divider())
            title_text, example, _patterns = SOURCE_FILES[key]
            field = PathField(example)
            field.setToolTip(f"The lab's file: {example}. {SOURCE_HINTS[key][:1].upper()}{SOURCE_HINTS[key][1:]}.")
            field.setFullText(self.c.paths.get(key, ""))
            field.pathEdited.connect(lambda text, k=key: self._path_edited(k, text))
            browse = button("Browse…", icon_name="folder")
            browse.clicked.connect(lambda _=False, k=key: self.browse(k))
            status = label("", "hint", wrap=True)
            newer = button("", "link")
            newer.clicked.connect(lambda _=False, k=key: self.use_newer(k))
            newer.hide()
            self.fields[key] = field
            self.source_status[key] = status
            self.newer[key] = newer
            title = label(title_text, None)
            title.setMinimumWidth(260)
            lay.addLayout(hbox(title, field, browse))
            row = hbox(status, newer, spacing=12)
            row.setStretch(0, 1)
            lay.addLayout(row)
        self.reload = button("Reload files", icon_name="reset", size="small")
        self.reload.clicked.connect(lambda: self.c.load_sources())
        self.loading_label = label("", "secondary")
        lay.addLayout(hbox(self.reload, self.loading_label, None))
        return frame

    def _genotyping_card(self) -> QFrame:
        frame, lay = card(margins=(24, 20, 24, 20), spacing=10)
        lay.addWidget(label("Genotyping results", "cardTitle"))
        lay.addWidget(label("Paste the table from Excel (with its header row: SN, Accession, PCR Result, ct value, "
                            "Genotyping Result, COMMENTS, FINAL), load it from a file, or start from the QC run. "
                            "Cells can be edited.", "secondary", wrap=True))
        paste = button("Paste from clipboard", icon_name="copy", size="small")
        paste.clicked.connect(lambda: self.paste(QGuiApplication.clipboard().text()))
        load = button("Load file…", icon_name="file", size="small")
        load.clicked.connect(self.load_genotyping)
        self.from_run = button("Use QC results", icon_name="list", size="small",
                               tooltip="One row per analysed sample: PASS, or FAIL REINJECT FORWARD/REVERSE/BOTH "
                                       "STRANDS (fill in genotype, PCR result and Ct)")
        self.from_run.clicked.connect(self.use_run)
        add = button("Add row", icon_name="plus", size="small")
        add.clicked.connect(lambda: self.geno.insertRow(self.geno.rowCount()))
        remove = button("Remove rows", icon_name="minus", size="small")
        remove.clicked.connect(self.remove_rows)
        clear = button("Clear", icon_name="reset", size="small")
        clear.clicked.connect(self.clear_genotyping)
        lay.addLayout(hbox(paste, load, self.from_run, None, add, remove, clear))
        self.geno = GenotypingTable()
        self.geno.pasted.connect(self.paste)
        lay.addWidget(self.geno)
        self.geno_notice = Notice("", "error")
        self.geno_notice.hide()
        lay.addWidget(self.geno_notice)
        self.virus = QComboBox()
        self.virus.addItem("Measles (MVs)", "MVs")
        self.virus.addItem("Rubella (RVs)", "RVs")
        self.virus.setCurrentIndex(0 if self.c.virus == "MVs" else 1)
        self.virus.currentIndexChanged.connect(lambda i: self.c.set_virus(self.virus.itemData(i)))
        self.fetch_button = button("Fetch patient data", "primary", icon_name="play", size="large")
        self.fetch_button.clicked.connect(self.fetch)
        self.fetch_status = label("", "secondary", wrap=True)
        self.virus.setMinimumWidth(170)
        self.virus.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.fetch_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row = hbox(label("Strain names for", "secondary"), self.virus, 16, self.fetch_button, self.fetch_status,
                   spacing=10)
        row.setStretch(4, 1)
        lay.addLayout(row)
        return frame

    def _results_card(self) -> QFrame:
        frame, lay = card(margins=(24, 20, 24, 20), spacing=10)
        self.results_frame = frame
        self.switch = Segmented(["MeaNS table", "All columns", "Matching"], equal=False, height=28)
        self.columns_button = button("Columns…", icon_name="list", size="small")
        self.columns_button.clicked.connect(self.choose_columns)
        self.names_button = button("Regenerate strain names", icon_name="reset", size="small",
                                   tooltip="Number the rows to upload again after the names in the template")
        self.names_button.clicked.connect(self.c.regenerate_names)
        self.export_button = button("Export to Excel…", icon_name="export", size="small")
        self.export_button.clicked.connect(self.export)
        self.save_button = button("Save to MeaNS template…", "primary", icon_name="check")
        self.save_button.clicked.connect(self.save)
        lay.addLayout(hbox(label("Results", "cardTitle"), 12, self.switch, None, self.columns_button,
                           self.names_button, self.export_button, self.save_button))
        self.results_summary = label("", "secondary", wrap=True)
        lay.addWidget(self.results_summary)
        self.row_filter = Segmented([*ROW_FILTERS], equal=False, height=28)
        self.row_filter.changed.connect(lambda _i: self.apply_row_filter())
        self.row_search = QLineEdit()
        self.row_search.setPlaceholderText("Find accession, MRN, name…")
        self.row_search.setClearButtonEnabled(True)
        self.row_search.setMinimumWidth(260)
        self.row_search.setMaximumWidth(360)
        self.row_search.textChanged.connect(lambda _t: self.apply_row_filter())
        self.shown_label = label("", "secondary")
        lay.addLayout(hbox(label("Show", "secondary"), self.row_filter, 12, self.row_search, self.shown_label, None))
        self.stack = QStackedWidget()
        self.means = QTableWidget()
        self.means.verticalHeader().setDefaultSectionSize(26)
        self.means.setMinimumHeight(360)
        self.means.itemChanged.connect(self._means_edited)
        self.allcols = self._readonly_table()
        self.matching = self._readonly_table()
        for w in (self.means, self.allcols, self.matching):
            self.stack.addWidget(w)
        self.switch.changed.connect(self.stack.setCurrentIndex)
        lay.addWidget(self.stack)
        self.save_notice = Notice("", "success")
        self.open_template = button("Open template", icon_name="external", size="small")
        self.open_template.clicked.connect(self._open_template)
        self.save_notice.add_button(self.open_template)
        self.save_notice.hide()
        lay.addWidget(self.save_notice)
        return frame

    @staticmethod
    def _readonly_table() -> QTableWidget:
        t = QTableWidget()
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(26)
        t.setMinimumHeight(360)
        return t

    # --- source files -----------------------------------------------------------------------------------

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if any(self.c.paths.values()) and not self.c.loading:
            self.c.load_sources()  # cheap when nothing changed

    def browse(self, key: str) -> None:
        start = self.c.paths.get(key) or self.c.store.get_str("dialogs/fetch") or str(Path.home())
        title = SOURCE_FILES[key][0]
        path, _ = QFileDialog.getOpenFileName(self, f"Choose the {title} ({SOURCE_FILES[key][1]})", start,
                                              self.c.name_filter(key))
        if path:
            self.c.store.set("dialogs/fetch", str(Path(path).parent))
            self.fields[key].setFullText(path)
            self._path_edited(key, path)

    def use_newer(self, key: str) -> None:
        newer = self.c.newer_file(key)
        if newer is not None:
            self.fields[key].setFullText(str(newer))
            self._path_edited(key, str(newer))

    def _path_edited(self, key: str, text: str) -> None:
        self.c.set_path(key, text)
        if text.strip():
            self.c.load_sources()

    def refresh_sources(self) -> None:
        t = current()
        for key, status in self.source_status.items():
            if self.fields[key].fullText() != self.c.paths.get(key, ""):
                self.fields[key].setFullText(self.c.paths.get(key, ""))
            table, error, path = self.c.tables.get(key), self.c.errors.get(key), self.c.paths.get(key)
            if error:
                status.setText(error)
                status.setStyleSheet(f"color: {t['fail.fg']};")
            elif table is not None:
                extra = f" · skipped sheets: {', '.join(table.skipped_sheets)}" if table.skipped_sheets else ""
                warn = f" · {'; '.join(table.warnings)}" if table.warnings else ""
                status.setText(f"✓ {table.summary()}{extra}{warn}")
                status.setStyleSheet(f"color: {t['pass.fg']};")
            else:
                status.setText(f"Not chosen · {SOURCE_HINTS[key]}" if not path else "Not read yet")
                status.setStyleSheet("")
            self.fields[key].setInvalid(bool(error))
            newer = self.c.newer_file(key) if path else None
            self.newer[key].setVisible(newer is not None)
            if newer is not None:
                self.newer[key].setText(f"Newer file in this folder: {newer.name} — use it")
                self.newer[key].setToolTip(str(newer))
        missing = self.c.missing_paths()
        self.sources_notice.setVisible(bool(missing))
        if missing:
            names = ", ".join(SOURCE_FILES[k][0] for k in missing)
            self.sources_notice.set(f"Choose these files once (the app remembers them): {names}.", "neutral")

    def _loading(self, on: bool) -> None:
        self.loading_label.setText("Reading files…" if on else "")
        self.reload.setEnabled(not on)
        self.fetch_button.setEnabled(not on)

    # --- genotyping table -------------------------------------------------------------------------------

    def paste(self, text: str) -> None:
        if not text.strip():
            self._geno_problem("The clipboard is empty. Copy the table in Excel first (including the header row).")
            return
        if self.c.set_genotyping_text(text):
            self.fetch_status.setText(f"{plural(len(self.c.genotyping.rows), 'accession')} pasted")

    def load_genotyping(self) -> None:
        start = self.c.store.get_str("dialogs/fetch") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Load the genotyping table", start, FILE_FILTER)
        if path:
            self.c.store.set("dialogs/fetch", str(Path(path).parent))
            self.c.set_genotyping_file(path)

    def use_run(self) -> None:
        run = self.qc.result if self.qc is not None else None
        if run is None:
            self._geno_problem("Run the QC analysis first (Run page), or paste the table.")
            return
        self.c.genotyping_from_run(run, self.qc.reviews)
        self.fetch_status.setText(f"{plural(len(run.samples), 'sample')} from the QC run: fill in the genotypes")

    def remove_rows(self) -> None:
        for r in sorted({i.row() for i in self.geno.selectedIndexes()}, reverse=True):
            self.geno.removeRow(r)

    def clear_genotyping(self) -> None:
        self.geno.setRowCount(0)
        self.c.genotyping = None
        self.geno_notice.hide()

    def _geno_problem(self, text: str) -> None:
        self.geno_notice.set(text, "error")
        self.geno_notice.show()

    def _genotyping_changed(self) -> None:
        if self.c.genotyping_error:
            self._geno_problem(self.c.genotyping_error)
            return
        self.geno_notice.hide()
        self.geno.set_table(self.c.genotyping)

    # --- fetch and results ------------------------------------------------------------------------------

    def fetch(self) -> None:
        cols, rows = self.geno.rows()
        if not any(r.get("Accession") for r in rows):
            self._geno_problem("The table has no accession numbers yet.")
            return
        self.c.set_genotyping_rows(cols, rows)
        self.geno_notice.hide()
        self.save_notice.hide()
        self.fetch_status.setText("Reading files…")
        self.c.load_sources(then=self._fetch_now)

    def fetch_now(self) -> str:
        """Fetch with the files already read (tests call this after ``load_sources(block=True)``)."""
        cols, rows = self.geno.rows()
        self.c.set_genotyping_rows(cols, rows)
        return self._fetch_now()

    def _fetch_now(self) -> str:
        problem = self.c.fetch()
        if problem:
            self.fetch_status.setText(problem)
        else:
            self.switch.setIndex(0)
        return problem

    def refresh_results(self) -> None:
        has = bool(self.c.rows)
        self.results_frame.setVisible(has)
        if not has:
            return
        rows = self.c.rows
        complete = sum(1 for r in rows if all(r.sample.found.values()))
        to_save = len(self.c.rows_to_save())
        problems = sum(1 for r in rows if r.problems or r.sample.notes)
        self.results_summary.setText(
            f"{plural(len(rows), 'accession')} · {complete} found in every file · "
            f"{plural(problems, 'row')} to check (orange; Show › To check lists them) · {to_save} ticked to save")
        self.fetch_status.setText(f"Fetched {plural(len(rows), 'accession')}")
        self._fill_means()
        self._fill_all()
        self._fill_matching()
        self.apply_row_filter()
        self.save_button.setEnabled(to_save > 0 and bool(self.c.paths.get("template")))
        self.save_button.setToolTip("" if self.c.paths.get("template") else "Choose the MeaNS upload template first")

    def row_matches(self, row, mode: int, text: str) -> bool:
        if mode == 1 and not (row.problems or row.sample.notes):
            return False
        if mode == 2 and all(row.sample.found.values()):
            return False
        if mode == 3 and not row.include:
            return False
        if text:
            merged = self.c.linked.merged(row.sample) if self.c.linked else {}
            hay = " ".join(tables.as_text(v) for v in [*row.values.values(), *merged.values()]).casefold()
            return text in hay
        return True

    def apply_row_filter(self) -> None:
        """Show only the rows of the chosen kind (and matching the search) in all three views."""
        rows = self.c.rows
        counts = [len(rows), sum(1 for r in rows if r.problems or r.sample.notes),
                  sum(1 for r in rows if not all(r.sample.found.values())), sum(1 for r in rows if r.include)]
        self.row_filter.setLabels([f"{name} {n}" for name, n in zip(ROW_FILTERS, counts)])
        mode, text = self.row_filter.index(), self.row_search.text().strip().casefold()
        shown = 0
        for i, row in enumerate(rows):
            ok = self.row_matches(row, mode, text)
            shown += ok
            for table in (self.means, self.allcols, self.matching):
                if i < table.rowCount():
                    table.setRowHidden(i, not ok)
        self.shown_label.setText(f"{shown} of {len(rows)} shown" if shown != len(rows) else "")

    def _fill_means(self) -> None:
        self._filling = True
        t = current()
        cols = self.c.columns
        table = self.means
        table.clear()
        table.setColumnCount(len(cols) + 1)
        table.setHorizontalHeaderLabels(["Save", *cols])
        for i, col in enumerate(cols, 1):
            item = table.horizontalHeaderItem(i)
            item.setToolTip(COMPUTED.get(col, "MeaNS column" if col in MEANS_COLUMNS else "From the source files"))
        table.setRowCount(len(self.c.rows))
        warn = QBrush(t.c("warn.bg"))
        for r, row in enumerate(self.c.rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if row.include else Qt.CheckState.Unchecked)
            table.setItem(r, 0, check)
            notes = [*row.problems, *row.sample.notes]
            for c, col in enumerate(cols, 1):
                item = QTableWidgetItem(tables.as_text(self.c.value(row, col)))
                tip = row.sources.get(col, "")
                if col not in MEANS_COLUMNS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if notes:
                    item.setBackground(warn)
                    tip = "\n".join(filter(None, [tip, *notes]))
                item.setToolTip(tip)
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        self._filling = False

    def _fill_all(self) -> None:
        cols = self.c.all_columns()
        self.allcols.clear()
        self.allcols.setColumnCount(len(cols))
        self.allcols.setHorizontalHeaderLabels(cols)
        samples = self.c.linked.samples if self.c.linked else []
        self.allcols.setRowCount(len(samples))
        for r, s in enumerate(samples):
            merged = self.c.linked.merged(s)
            for c, col in enumerate(cols):
                self.allcols.setItem(r, c, QTableWidgetItem(tables.as_text(merged.get(col))))
        self.allcols.resizeColumnsToContents()

    def _fill_matching(self) -> None:
        t = current()
        cols = ["Accession", "Masterlist", "Daily sheet", "Line list", "Line list matched by", "Notes"]
        self.matching.clear()
        self.matching.setColumnCount(len(cols))
        self.matching.setHorizontalHeaderLabels(cols)
        rows = self.c.rows
        self.matching.setRowCount(len(rows))
        for r, row in enumerate(rows):
            s = row.sample
            found = s.found
            values = [s.accession] + ["✓" if found[k] else "✗" for k in ("masterlist", "daily", "linelist")] + \
                     [s.linelist_by, "; ".join([*s.notes, *row.problems])]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if v == "✗":
                    item.setForeground(QBrush(t.c("fail.fg")))
                elif v == "✓":
                    item.setForeground(QBrush(t.c("pass.fg")))
                self.matching.setItem(r, c, item)
        self.matching.horizontalHeader().setStretchLastSection(True)
        self.matching.resizeColumnsToContents()

    def _means_edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        row = self.c.rows[item.row()]
        if item.column() == 0:
            row.include = item.checkState() == Qt.CheckState.Checked
            self.refresh_results()
            return
        col = self.c.columns[item.column() - 1]
        if col not in MEANS_COLUMNS:
            return
        text = item.text().strip()
        if col in DATE_COLUMNS:
            row.values[col] = tables.parse_date(text) or text
        elif col == "TAT":
            row.values[col] = int(text) if text.lstrip("-").isdigit() else text
        else:
            row.values[col] = text
        if col == "WHO Strain Name":
            row.strain_manual = bool(text)
        if col == "to upload?":
            row.include = text.lower() in ("yes", "y")
        if col in NAME_INPUTS or col == "WHO Strain Name":
            self.c.regenerate_names()

    def choose_columns(self) -> None:
        dialog = ColumnsDialog(self.c.columns, self.c.all_columns(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.c.set_columns(dialog.chosen())

    def export(self) -> None:
        start = self.c.store.get_str("dialogs/fetch") or str(Path.home())
        default = str(Path(start) / f"MeaNS rows {date.today():%Y-%m-%d}.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "Export to Excel", default, "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            self.c.export_workbook(path)
        except tables.TableError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        self.save_notice.set(f"Exported to {path}", "success")
        self.open_template.hide()
        self.save_notice.show()

    def save(self, confirm: bool = True) -> bool:
        rows = self.c.rows_to_save()
        path = self.c.paths.get("template")
        if not rows or not path:
            return False
        if confirm:
            names = "\n".join(f"{r.values.get('WHO Strain Name') or '(no name)'}  ·  {r.accession}" for r in rows[:25])
            more = f"\n… and {len(rows) - 25} more" if len(rows) > 25 else ""
            answer = QMessageBox.question(
                self, "Save to MeaNS template",
                f"Add {plural(len(rows), 'row')} to {Path(path).name}?\n\n{names}{more}\n\nA backup copy of the "
                f"template is saved first in the 'MeaNS backups' folder next to it.")
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            result = self.c.save_to_template()
        except tables.TableError as exc:
            self.save_notice.set(str(exc), "error")
            self.open_template.hide()
            self.save_notice.show()
            return False
        parts = [f"{plural(result.written, 'row')} added to {result.path.name} (sheet {result.sheet}, rows "
                 f"{result.first_row}-{result.last_row})" if result.written else "No rows added"]
        if result.backup:
            parts.append(f"backup: {result.backup}")
        if result.skipped:
            parts.append("skipped: " + "; ".join(result.skipped))
        self.save_notice.set(". ".join(parts) + ".", "success" if result.written else "warn")
        self.open_template.show()
        self.save_notice.show()
        return result.written > 0

    def _open_template(self) -> None:
        path = self.c.paths.get("template")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save_state(self) -> None:
        pass
