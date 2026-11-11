"""Run page: the setup form and the running view (design spec 1.5, 3.14, 3.15)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QProgressBar, QScrollArea, QSizePolicy, QStackedWidget, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...pipeline import RunResult, SampleResult
from ...presentation import plural, rules_used, scan_summary
from ...references import REFERENCES
from ...settings_schema import rule_set_label
from ..theme import current
from ..theme.fonts import mono_font, ui_font
from ..widgets.badge import Badge, Chip
from ..widgets.common import button, card, caps, divider, hbox, label, vbox
from ..widgets.notice import Notice
from ..widgets.path_field import PathField
from .controller import QcController
from .examples import find_examples
from .models import BadgeDelegate, LiveRunModel, PaddedDelegate

FASTA_FILTER = "FASTA files (*.fasta *.fa *.fas *.fna *.txt);;All files (*)"
AB1_FILTER = "Chromatograms (*.ab1 *.abi *.AB1 *.ABI);;All files (*)"


class FlowRow(QWidget):
    """Chips in a row that wraps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chips: list[QWidget] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_widgets(self, widgets: list[QWidget]) -> None:
        for w in self._chips:
            w.setParent(None)
            w.deleteLater()
        self._chips = widgets
        for w in widgets:
            w.setParent(self)
            w.show()
        self._place(self.width())
        self.updateGeometry()

    def _place(self, width: int) -> int:
        x = y = 0
        row_h = 0
        for w in self._chips:
            s = w.sizeHint()
            if x and x + s.width() > width:
                x, y = 0, y + row_h + 6
                row_h = 0
            w.setGeometry(x, y, s.width(), s.height())
            x += s.width() + 6
            row_h = max(row_h, s.height())
        return y + row_h

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(width)

    def sizeHint(self) -> QSize:
        return QSize(400, self.heightForWidth(max(self.width(), 400)))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place(self.width())


class _ReferenceDelegate(QStyledItemDelegate):
    """Reference items: name in monospace plus the description in the secondary colour."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 30)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        t = current()
        p.save()
        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            p.fillRect(option.rect, t.c("bg.selected"))
        name, desc = index.data(Qt.ItemDataRole.UserRole + 1) or (index.data(), "")
        rect = QRectF(option.rect).adjusted(10, 0, -10, 0)
        p.setFont(mono_font(12, 500))
        p.setPen(t.c("text.primary"))
        p.drawText(rect, Qt.AlignmentFlag.AlignVCenter, name)
        nw = QFontMetrics(mono_font(12, 500)).horizontalAdvance(name)
        p.setFont(ui_font(12))
        p.setPen(t.c("text.secondary"))
        rest = QRectF(rect.left() + nw + 8, rect.top(), rect.width() - nw - 8, rect.height())
        p.drawText(rest, Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(ui_font(12)).elidedText(f"· {desc}", Qt.TextElideMode.ElideRight, int(rest.width())))
        p.restore()


def _step(n: int, title: str) -> QHBoxLayout:
    circle = QLabel(str(n))
    circle.setProperty("role", "step")
    circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return hbox(circle, label(title, "cardTitle"), None, spacing=10)


class SetupView(QWidget):
    """The setup form (inputs, reference, output) and the rules card."""

    runRequested = Signal()
    editSettings = Signal()

    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.c = controller
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("page", True)
        scroll.setWidget(page)
        center = QHBoxLayout(page)
        center.setContentsMargins(24, 32, 24, 32)
        column = QWidget()
        column.setMaximumWidth(1120)
        center.addStretch(1)
        center.addWidget(column, 100)
        center.addStretch(1)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        lay.addLayout(vbox(label("New run", "pageTitle"),
                           label("Choose a folder of .ab1 chromatograms. Each sample needs a forward and a reverse "
                                 "read.", "secondary"), spacing=4))
        self.notice = Notice("", "neutral")
        self.notice.hide()
        self.copy_details = button("Copy details", "danger", size="small", icon_name="copy")
        self.notice.add_button(self.copy_details)
        self.copy_details.clicked.connect(self._copy_details)
        self._details = ""
        lay.addWidget(self.notice)
        row = QHBoxLayout()
        row.setSpacing(20)
        row.addWidget(self._form_card(), 1)
        row.addWidget(self._rules_card(), 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(row)
        lay.addStretch(1)

        c = self.c
        c.inputsChanged.connect(self.refresh_inputs)
        c.scanChanged.connect(self.refresh_scan)
        c.referenceChanged.connect(self.refresh_reference)
        c.settingsChanged.connect(self.refresh_rules)
        c.settingsChanged.connect(self.refresh_reference)
        c.outputChanged.connect(self.refresh_output)
        c.validityChanged.connect(self.refresh_footer)
        self.refresh_all()

    # --- construction -----------------------------------------------------------------------------------

    def _form_card(self) -> QFrame:
        frame, lay = card(margins=(0, 0, 0, 0), spacing=0)
        lay.addWidget(self._inputs_section())
        lay.addWidget(divider())
        lay.addWidget(self._reference_section())
        lay.addWidget(divider())
        lay.addWidget(self._output_section())
        footer = QFrame()
        footer.setProperty("band", True)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(24, 14, 24, 14)
        fl.setSpacing(14)
        self.run_button = button("Run analysis", "primary", icon_name="play", size="large")
        self.run_button.clicked.connect(self.runRequested.emit)
        self.status = label("", "secondary", wrap=True)
        fl.addWidget(self.run_button)
        fl.addWidget(self.status, 1)
        lay.addWidget(footer)
        return frame

    def _section(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        return w, lay

    def _inputs_section(self) -> QWidget:
        w, lay = self._section()
        lay.addLayout(_step(1, "Input folder"))
        self.input_field = PathField("Folder with .ab1 files")
        self.input_field.pathEdited.connect(self._input_edited)
        browse = button("Browse…", icon_name="folder")
        browse.clicked.connect(self.browse_folder)
        add = button("Add files…", icon_name="plus")
        add.clicked.connect(self.browse_files)
        lay.addLayout(hbox(self.input_field, browse, add))
        self.sources = QWidget()
        self.sources_lay = QVBoxLayout(self.sources)
        self.sources_lay.setContentsMargins(0, 0, 0, 0)
        self.sources_lay.setSpacing(2)
        lay.addWidget(self.sources)
        self.summary = QFrame()
        self.summary.setProperty("sunken", True)
        sl = QVBoxLayout(self.summary)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.setSpacing(8)
        self.summary_head = label("", None)
        self.summary_head.setFont(ui_font(13, 500))
        self.summary_warn = Notice("", "warn")
        self.chips = FlowRow()
        self.hint = label("", "hint", wrap=True)
        self.hint.setTextFormat(Qt.TextFormat.RichText)
        self.examples_button = button("Load example data", "ghost", icon_name="file", size="small")
        self.examples_button.clicked.connect(self.load_examples)
        self.pairing_toggle = button("Show pairing", "link")
        self.pairing_toggle.setCheckable(True)
        self.pairing_toggle.toggled.connect(self._toggle_pairing)
        self.pairing = QTableWidget(0, 4)
        self.pairing.setHorizontalHeaderLabels(["SAMPLE", "FORWARD", "REVERSE", "PROBLEM"])
        self.pairing.verticalHeader().hide()
        self.pairing.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pairing.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pairing.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pairing.setMaximumHeight(220)
        self.pairing.hide()
        sl.addLayout(hbox(self.summary_head, None, self.pairing_toggle))
        sl.addWidget(self.summary_warn)
        sl.addWidget(self.chips)
        sl.addWidget(self.hint)
        sl.addWidget(self.pairing)
        sl.addLayout(hbox(self.examples_button, None))
        lay.addWidget(self.summary)
        return w

    def _reference_section(self) -> QWidget:
        w, lay = self._section()
        lay.addLayout(_step(2, "Reference"))
        self.reference = QComboBox()
        self.reference.setItemDelegate(_ReferenceDelegate(self.reference))
        self.reference.setMinimumWidth(320)
        self.reference.activated.connect(self._reference_chosen)
        custom = button("Custom FASTA…", icon_name="file")
        custom.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        custom.clicked.connect(self.browse_reference)
        self.custom_chip = Chip("Custom", "accent")
        row = hbox(self.reference, self.custom_chip, custom)
        row.setStretch(0, 1)
        lay.addLayout(row)
        self.record = QComboBox()
        self.record.activated.connect(self._record_chosen)
        self.record_row = QWidget()
        self.record_row.setLayout(hbox(label("Record", "secondary"), self.record, None))
        lay.addWidget(self.record_row)
        self.ref_chips = FlowRow()
        lay.addWidget(self.ref_chips)
        self.ref_hint = label("", "hint", wrap=True)
        lay.addWidget(self.ref_hint)
        self.ref_notice = Notice("", "warn")
        lay.addWidget(self.ref_notice)
        return w

    def _output_section(self) -> QWidget:
        w, lay = self._section()
        lay.addLayout(_step(3, "Output folder"))
        self.output_field = PathField("Folder for the result files")
        self.output_field.pathEdited.connect(self.c.set_output_dir)
        browse = button("Browse…", icon_name="folder")
        browse.clicked.connect(self.browse_output)
        lay.addLayout(hbox(self.output_field, browse))
        self.auto_export = QCheckBox("Write output files when the run finishes")
        self.auto_export.toggled.connect(lambda on: self.c.set_export_options(auto_export=on))
        self.write_html = QCheckBox("Write HTML report")
        self.write_html.toggled.connect(lambda on: self.c.set_export_options(write_html=on))
        lay.addLayout(hbox(self.auto_export, 16, self.write_html, None))
        self.output_hint = label("", "hint", wrap=True)
        lay.addWidget(self.output_hint)
        return w

    def _rules_card(self) -> QFrame:
        frame, lay = card(margins=(20, 18, 20, 18), spacing=10)
        frame.setMinimumWidth(320)
        frame.setMaximumWidth(380)
        self.rules_chip = Chip("Defaults")
        lay.addLayout(hbox(label("Rules for this run", "cardTitle"), None, self.rules_chip))
        self.rules_body = QVBoxLayout()
        self.rules_body.setSpacing(4)
        lay.addLayout(self.rules_body)
        edit = button("Edit settings", "ghost", size="small")
        edit.clicked.connect(self.editSettings.emit)
        lay.addLayout(hbox(edit, None))
        return frame

    # --- refresh ----------------------------------------------------------------------------------------

    def refresh_all(self) -> None:
        self.refresh_inputs()
        self.refresh_scan()
        self.refresh_reference()
        self.refresh_output()
        self.refresh_rules()
        self.refresh_footer()

    def refresh_inputs(self) -> None:
        inputs = self.c.inputs
        self.input_field.setFullText(str(inputs[0]) if inputs else "")
        while self.sources_lay.count():
            item = self.sources_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if len(inputs) > 1:
            for path in inputs:
                remove = button("Remove", "link")
                remove.clicked.connect(lambda _=False, p=path: self.c.remove_input(p))
                name = label(str(path), "mono")
                name.setToolTip(str(path))
                row = QWidget()
                row.setLayout(hbox(name, None, remove))
                self.sources_lay.addWidget(row)
            clear = button("Clear all", "link")
            clear.clicked.connect(self.c.clear_inputs)
            row = QWidget()
            row.setLayout(hbox(label(f"{len(inputs)} sources", "hint"), None, clear))
            self.sources_lay.addWidget(row)
        self.sources.setVisible(len(inputs) > 1)

    def refresh_scan(self) -> None:
        sr = self.c.scan_result
        naming = ("Sample name = file name before the last direction tag. "
                  f"<span style='font-family:{mono_font().family()}'>BAD04_F.ab1</span> + "
                  f"<span style='font-family:{mono_font().family()}'>BAD04_R.ab1</span> → BAD04. "
                  "You can also drop a folder onto this window.")
        self.hint.setText(naming)
        self.examples_button.setVisible(not self.c.inputs and find_examples() is not None)
        if sr is None:
            self.summary_head.hide()
            self.summary_warn.hide()
            self.chips.set_widgets([])
            self.chips.hide()
            self.pairing_toggle.hide()
            self.pairing.hide()
            return
        s = scan_summary(sr)
        self.summary_head.setText(s.headline)
        self.summary_head.show()
        self.summary_warn.set(s.warning, "warn")
        self.summary_warn.setVisible(bool(s.warning))
        self.chips.set_widgets([Chip(text, kind) for text, kind in s.chips])
        self.chips.show()
        self.pairing_toggle.setVisible(bool(sr.pairs or sr.skipped))
        self._fill_pairing(sr)

    def _fill_pairing(self, sr) -> None:
        rows = []
        for name, slot in sr.pairs.items():
            missing = [d for d in ("F", "R") if d not in slot]
            problem = f"{'reverse' if missing == ['R'] else 'forward'} missing" if missing else ""
            rows.append((name, slot.get("F"), slot.get("R"), problem))
        for s in sr.skipped:
            if s.reason != "hidden":
                rows.append((s.sample or "–", s.path if s.reason != "missing" else None, None, s.text))
        self.pairing.setRowCount(len(rows))
        for i, (name, f, r, problem) in enumerate(rows):
            for j, value in enumerate((name, f.name if f else "–", r.name if r else "–", problem)):
                item = QTableWidgetItem(value)
                if j in (1, 2):
                    item.setFont(mono_font(12))
                if j == 3 and problem:
                    item.setForeground(current().c("warn.fg"))
                self.pairing.setItem(i, j, item)

    def _toggle_pairing(self, on: bool) -> None:
        self.pairing.setVisible(on)
        self.pairing_toggle.setText("Hide pairing" if on else "Show pairing")

    def refresh_reference(self) -> None:
        c = self.c
        self.reference.blockSignals(True)
        self.reference.clear()
        for name, ref in REFERENCES.items():
            self.reference.addItem(name, f"builtin:{name}")
            self.reference.setItemData(self.reference.count() - 1, (name, ref.description.split(":")[0]),
                                       Qt.ItemDataRole.UserRole + 1)
        for path in c.custom_references:
            self.reference.addItem(Path(path).name, f"file:{path}")
            self.reference.setItemData(self.reference.count() - 1, (Path(path).name, str(Path(path).parent)),
                                       Qt.ItemDataRole.UserRole + 1)
        index = self.reference.findData(c.reference_spec)
        self.reference.setCurrentIndex(max(0, index))
        self.reference.blockSignals(False)
        self.custom_chip.setVisible(c.reference_spec.startswith("file:"))
        self.record_row.setVisible(bool(c.reference_records))
        if c.reference_records:
            self.record.blockSignals(True)
            self.record.clear()
            self.record.addItem("Choose a record…", "")
            for rid in c.reference_records:
                self.record.addItem(rid, rid)
            self.record.blockSignals(False)
        ref = c.reference
        chips = []
        if ref is not None:
            frame = c.settings.frame
            chips = [Chip(f"{ref.length:,} bp")]
            coding = c.settings.check_stop_codons or c.settings.check_frameshifts
            if coding:
                chips += [Chip(f"{(ref.length - frame + 1) // 3:,} codons"),
                          Chip(f"Codon 1 starts at position {frame}")]
            else:
                chips.append(Chip("Non-coding target"))
            chips.append(Chip("Built-in" if ref.builtin else "Custom"))
            desc = ref.description.split(": ", 1)[-1] if ref.builtin else ref.description
            parts = [desc[:1].upper() + desc[1:], ref.source] if desc else [ref.source]
            self.ref_hint.setText(" · ".join(parts))
        self.ref_hint.setVisible(ref is not None)
        self.ref_chips.set_widgets(chips)
        warnings = c.reference_warnings()
        if c.reference_error:
            self.ref_notice.set(c.reference_error, "error")
        elif warnings:
            self.ref_notice.set(" ".join(w[:1].upper() + w[1:] + "." for w in warnings), "warn")
        self.ref_notice.setVisible(bool(c.reference_error or warnings))

    def refresh_output(self) -> None:
        path = self.c.output_path()
        self.output_field.setFullText(str(path) if path else "")
        self.auto_export.setChecked(self.c.auto_export)
        self.write_html.setChecked(self.c.write_html)
        files = "consensus.fasta, trimmed_reads.fasta, 3 CSV files, run_info.json" + (
            ", report.html" if self.c.write_html else "")
        self.output_hint.setText(f"Written when the run finishes: {files}. Existing files with these names are "
                                 "replaced." if self.c.auto_export else
                                 "Nothing is written automatically; use Export… on the Results page.")
        self.write_html.setEnabled(self.c.auto_export)

    def refresh_rules(self) -> None:
        s = self.c.settings
        label_text = rule_set_label(s)
        self.rules_chip.set("Defaults" if label_text == "defaults" else label_text.capitalize(),
                            "neutral" if label_text == "defaults" else "warn")
        _clear_layout(self.rules_body)
        t = current()
        for i, (group, rows) in enumerate(rules_used(s)):
            if i:
                self.rules_body.addSpacing(8)
            self.rules_body.addWidget(caps(group if i == 0 else "RECall rules · Woods et al. 2012"))
            for row in rows:
                key = label(row.label, "secondary")
                value = QLabel(row.value)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setFont(ui_font(13, 600 if row.changed else 500, tabular=True))
                if row.changed:
                    value.setStyleSheet(f"color: {t['accent']};")
                self.rules_body.addLayout(hbox(key, None, value))
        self.refresh_footer()

    def refresh_footer(self) -> None:
        reason = self.c.run_blocked_reason()
        self.run_button.setEnabled(not reason and not self.c.running)
        self.input_field.setInvalid(bool(self.c.inputs) and any(f == "inputs" for f, _ in self.c.problems()))
        if reason:
            self.status.setText(reason)
        else:
            n = self.c.scan_result.complete + sum(
                1 for slot in self.c.scan_result.pairs.values() if len(slot) == 1) if self.c.scan_result else 0
            self.status.setText(f"{plural(n, 'sample')} ready · rules: {rule_set_label(self.c.settings)}")

    # --- actions ----------------------------------------------------------------------------------------

    def _last_dir(self, key: str) -> str:
        return self.c.store.get_str(f"dialogs/{key}") or str(Path.home())

    def _remember_dir(self, key: str, path: str) -> None:
        self.c.store.set(f"dialogs/{key}", str(Path(path).parent if Path(path).is_file() else path))

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the folder with .ab1 files", self._last_dir("inputs"))
        if folder:
            self._remember_dir("inputs", folder)
            self.c.set_inputs([folder])

    def browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add .ab1 files", self._last_dir("inputs"), AB1_FILTER)
        if files:
            self._remember_dir("inputs", files[0])
            self.c.add_inputs(files)

    def _input_edited(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.c.clear_inputs()
        elif Path(text).exists():
            self.c.set_inputs([text] + [str(p) for p in self.c.inputs[1:]])
        else:
            self.show_notice(f"Input not found: {text}", "warn")

    def browse_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a reference FASTA", self._last_dir("reference"),
                                              FASTA_FILTER)
        if path:
            self._remember_dir("reference", path)
            self.c.set_reference(f"file:{path}")

    def _reference_chosen(self, index: int) -> None:
        self.c.set_reference(self.reference.itemData(index))

    def _record_chosen(self, index: int) -> None:
        rid = self.record.itemData(index)
        if rid:
            self.c.set_reference(self.c.reference_spec, rid)

    def browse_output(self) -> None:
        start = str(self.c.output_path() or self._last_dir("output"))
        folder = QFileDialog.getExistingDirectory(self, "Choose the output folder", start)
        if folder:
            self._remember_dir("output", folder)
            self.c.set_output_dir(folder)

    def load_examples(self) -> None:
        path = find_examples()
        if path is not None:
            self.c.set_inputs([path])

    def show_notice(self, text: str, kind: str = "neutral", details: str = "") -> None:
        self._details = details or text
        self.notice.set(text, kind)
        self.copy_details.setVisible(kind == "error")
        self.notice.show()

    def hide_notice(self) -> None:
        self.notice.hide()

    def _copy_details(self) -> None:
        QGuiApplication.clipboard().setText(self._details)


class RunningView(QWidget):
    """Progress card, live sample table and the "This run" card (design spec 3.14)."""

    cancelRequested = Signal()

    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.c = controller
        self.model = LiveRunModel(self)
        self.total = 0
        self.done = 0
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 32, 24, 32)
        column = QWidget()
        column.setMaximumWidth(1120)
        outer.addStretch(1)
        outer.addWidget(column, 100)
        outer.addStretch(1)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        progress, pl = card(margins=(24, 22, 24, 22), spacing=10)
        self.caption = caps("Reading files…")
        self.current = label("", "pageTitle")
        self.cancel = button("Cancel run", icon_name="stop")
        self.cancel.clicked.connect(self._cancel_clicked)
        pl.addLayout(hbox(vbox(self.caption, self.current, spacing=4), None, self.cancel))
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        pl.addWidget(self.bar)
        self.finished_text = label("", "secondary")
        self.pass_badge, self.fail_badge = Badge("pass", "0 PASS"), Badge("fail", "0 FAIL")
        pl.addLayout(hbox(self.finished_text, 8, self.pass_badge, self.fail_badge, None))
        pl.addWidget(label("Cancelling stops after the current sample. No output files are written for a cancelled "
                           "run.", "hint"))
        lay.addWidget(progress)
        row = QHBoxLayout()
        row.setSpacing(20)
        table_card, tl = card(margins=(0, 0, 0, 0), spacing=0)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegate(PaddedDelegate(self.table))
        self.table.setItemDelegateForColumn(1, BadgeDelegate(self.table))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.resizeSection(0, 110)
        header.resizeSection(1, 120)
        header.setStretchLastSection(True)
        tl.addWidget(self.table)
        row.addWidget(table_card, 1)
        info, il = card(margins=(20, 18, 20, 18), spacing=6)
        info.setMinimumWidth(320)
        info.setMaximumWidth(380)
        il.addWidget(label("This run", "cardTitle"))
        self.info = QVBoxLayout()
        self.info.setSpacing(4)
        il.addLayout(self.info)
        il.addStretch(1)
        row.addWidget(info)
        lay.addLayout(row, 1)

    def start(self, names: list[str]) -> None:
        self.total, self.done = len(names), 0
        self.passed = self.failed = 0
        self.model.start(names)
        self.bar.setRange(0, max(1, self.total))
        self.bar.setValue(0)
        self.caption.setText("READING FILES…")
        self.current.setText("")
        self.cancel.setEnabled(True)
        self.cancel.setText("Cancel run")
        self._update_counts()
        self._fill_info()

    def _fill_info(self) -> None:
        _clear_layout(self.info)
        c = self.c
        sr = c.scan_result
        ref = c.reference
        coding = c.settings.check_stop_codons or c.settings.check_frameshifts
        items = [
            ("Input folder", ", ".join(str(p) for p in c.inputs), True),
            ("", f"{plural(sr.n_files, '.ab1 file')} · {plural(len(sr.pairs), 'sample')}" if sr else "", False),
            ("Reference", f"{ref.name} · {ref.length:,} bp" + (f" · {ref.length // 3:,} codons" if coding else ""),
             False),
            ("Output folder", str(c.output_path()) if c.auto_export else "Not written automatically", True),
            ("Rules", f"{rule_set_label(c.settings).capitalize()} · read cutoff {c.settings.min_good_fraction:.0%} "
                      f"≥ Q{c.settings.min_quality} · RECall (Woods et al. 2012)", False),
        ]
        for title, value, mono in items:
            if title:
                self.info.addSpacing(6)
                self.info.addWidget(caps(title))
            v = label(value, "mono" if mono else "secondary", wrap=True)
            self.info.addWidget(v)

    def progress(self, n: int, total: int, name: str) -> None:
        self.caption.setText(f"ANALYSING SAMPLE {n} OF {total}")
        self.current.setText(name)
        self.model.set_current(name)

    def sample_done(self, n: int, total: int, sr: SampleResult) -> None:
        self.done = n
        if sr.consensus.status == "PASS":
            self.passed += 1
        else:
            self.failed += 1
        self.model.add_result(sr)
        self.bar.setValue(n)
        self._update_counts()

    def _update_counts(self) -> None:
        self.finished_text.setText(f"{self.done} of {self.total} finished")
        self.pass_badge.set("pass", f"{self.passed} PASS")
        self.fail_badge.set("fail", f"{self.failed} FAIL")

    def _cancel_clicked(self) -> None:
        if self.done > self.total / 2:
            answer = QMessageBox.question(self, "Cancel run", f"{self.done} of {self.total} samples are finished. "
                                          "Cancel the run? No output files are written for a cancelled run.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.cancel.setEnabled(False)
        self.cancel.setText("Cancelling…")
        self.cancelRequested.emit()


class RunPage(QWidget):
    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.setProperty("page", True)
        self.c = controller
        self.stack = QStackedWidget()
        self.setup = SetupView(controller)
        self.running = RunningView(controller)
        self.stack.addWidget(self.setup)
        self.stack.addWidget(self.running)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self.running.cancelRequested.connect(controller.cancel_run)

    def show_running(self, names: list[str]) -> None:
        self.setup.hide_notice()
        self.running.start(names)
        self.stack.setCurrentWidget(self.running)

    def show_setup(self) -> None:
        self.stack.setCurrentWidget(self.setup)

    def run_ended(self, result: RunResult | None) -> None:
        self.show_setup()
        if result is not None and result.cancelled:
            self.setup.show_notice(f"Run cancelled after {plural(len(result.samples), 'sample')}. No output files "
                                   "were written.", "neutral")


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())

