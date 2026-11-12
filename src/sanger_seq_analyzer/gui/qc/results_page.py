"""Results page: summary strip, sample list and the sample detail (design spec 1.4, 3.8-3.12, 3.17, 3.18)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QObject, QRectF, QSize, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QStackedWidget, QTableView, QToolButton,
    QVBoxLayout, QWidget,
)

from ... import review
from ...pipeline import RunResult, SampleResult
from ...presentation import (
    fasta_text, metric_tiles, not_analysed_count, plural, reason_views, run_context, skipped_rows, verdict_sentence,
)
from ..theme import current
from ..theme.fonts import mono_font, ui_font
from ..widgets.alignment_view import AlignmentView
from ..widgets.badge import Badge, Chip
from ..widgets.chromatogram import ChromatogramPanel
from ..widgets.common import ThinSplitter, button, hbox, icon_button, label, themed_icon
from ..widgets.metric_strip import MetricStrip
from ..widgets.notice import Notice
from ..widgets.ratio_bar import RatioBar
from ..widgets.reason_box import ReasonBox
from ..widgets.segmented import Segmented
from .controller import QcController
from .models import (
    FLAG_FILTERS, SORTS, ColumnRole, FlaggedPositionsModel, PaddedDelegate, RuleChecklistModel, SampleDelegate,
    SampleFilterProxy, SampleListModel, SampleRole, SortRole,
)

LEGEND = (("A", "diff", "differs from reference"), ("Y", "mix", "mixture"), ("N", "n", "no call"),
          ("A", "mark", "marked"), ("-", "gap", "gap"), ("T", "stop", "stop codon"))


class SelectionModel(QObject):
    """The selected alignment column, shared by every view of a sample."""

    changed = Signal(object)  # int or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.column: int | None = None

    def set(self, column: int | None) -> None:
        if column != self.column:
            self.column = column
            self.changed.emit(column)


class _Legend(QWidget):
    """Swatches of the alignment highlights (design spec 3.17)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_stop = False
        self.setFixedHeight(22)

    def sizeHint(self) -> QSize:
        return QSize(560, 22)

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        x = 0.0
        for letter, key, text in LEGEND:
            if key == "stop" and not self.show_stop:
                continue
            cell = QRectF(x, 3, 16, 16)
            if key == "mark":
                p.fillRect(QRectF(x + 1, 17, 14, 2), t.c("hl.mark"))
                color = t.c("text.primary")
            elif key == "stop":
                p.fillRect(cell, t.c("hl.stop.bg"))
                p.fillRect(QRectF(x, 3, 16, 2), t.c("hl.stop.rule"))
                p.fillRect(QRectF(x, 17, 16, 2), t.c("hl.stop.rule"))
                color = t.c("hl.stop.fg")
            else:
                p.fillRect(cell, t.c(f"hl.{key}.bg"))
                color = t.c(f"hl.{key}.fg")
            p.setFont(mono_font(12, 700))
            p.setPen(color)
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, letter)
            p.setFont(ui_font(12))
            p.setPen(t.c("text.secondary"))
            w = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QRectF(x + 20, 0, w + 2, 22), Qt.AlignmentFlag.AlignVCenter, text)
            x += 20 + w + 14


class _Panel(QFrame):
    """A bordered surface panel (cards cannot clip children, so the border is painted)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.c("border.default"), 1))
        p.setBrush(t.c("bg.surface"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


class DetailPanel(_Panel):
    """Alignment · Rules · Flagged · Sequence, switched by a segmented control (plan D7)."""

    def __init__(self, selection: SelectionModel, parent=None):
        super().__init__(parent)
        self.selection = selection
        self.sr: SampleResult | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        head = QWidget()
        head.setFixedHeight(40)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(10)
        self.switch = Segmented(["Alignment", "Rules", "Flagged (0)", "Sequence"], equal=False, height=26)
        hl.addWidget(self.switch)
        hl.addStretch(1)
        self.dots = QCheckBox("Matches as dots")
        self.reads = QCheckBox("Reads")
        self.reads.setChecked(True)
        self.prev = icon_button("chevron-left", "Previous flagged position ([)")
        self.next = icon_button("chevron-right", "Next flagged position (])")
        self.step_label = label("", "secondary")
        self.flag_filter = QComboBox()
        self.flag_filter.addItems(FLAG_FILTERS)
        self.copy_seq = button("Copy sequence", size="small", icon_name="copy")
        self.copy_fasta = button("Copy FASTA", size="small", icon_name="copy")
        for w in (self.dots, self.reads, self.prev, self.step_label, self.next, self.flag_filter, self.copy_seq,
                  self.copy_fasta):
            hl.addWidget(w)
        lay.addWidget(head)
        self.legend_row = QWidget()
        lr = QHBoxLayout(self.legend_row)
        lr.setContentsMargins(12, 0, 12, 4)
        self.legend = _Legend()
        lr.addWidget(self.legend)
        lr.addStretch(1)
        lay.addWidget(self.legend_row)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        self.alignment = AlignmentView()
        self.rules_model = RuleChecklistModel(self)
        self.rules = self._table(self.rules_model)
        self.rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, w in ((1, 80), (2, 210), (3, 220), (4, 100)):
            self.rules.horizontalHeader().resizeSection(col, w)
        self.flagged_model = FlaggedPositionsModel(self)
        self.flagged_proxy = QSortFilterProxyModel(self)
        self.flagged_proxy.setSourceModel(self.flagged_model)
        self.flagged_proxy.setSortRole(SortRole)
        self.flagged = self._table(self.flagged_proxy)
        self.flagged.setSortingEnabled(True)
        self.flagged.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.flagged.horizontalHeader().setStretchLastSection(True)
        for col, w in enumerate((60, 70, 50, 70, 50, 70, 120, 120)):
            self.flagged.horizontalHeader().resizeSection(col, w)
        seq = QWidget()
        sl = QVBoxLayout(seq)
        sl.setContentsMargins(12, 4, 12, 10)
        self.seq_title = label("", "hint")
        self.seq_text = QPlainTextEdit()
        self.seq_text.setReadOnly(True)
        self.seq_text.setFont(mono_font(12))
        sl.addWidget(self.seq_title)
        sl.addWidget(self.seq_text, 1)
        for w in (self.alignment, self.rules, self.flagged, seq):
            self.stack.addWidget(w)

        self.switch.changed.connect(self._switched)
        self.dots.toggled.connect(self.alignment.set_dots)
        self.reads.toggled.connect(self.alignment.set_show_reads)
        self.prev.clicked.connect(lambda: self.alignment.step_flagged(False))
        self.next.clicked.connect(lambda: self.alignment.step_flagged(True))
        self.flag_filter.currentIndexChanged.connect(self.flagged_model.apply_filter)
        self.rules.clicked.connect(self._row_goto)
        self.flagged.clicked.connect(self._row_goto)
        self.copy_seq.clicked.connect(self._copy_sequence)
        self.copy_fasta.clicked.connect(self._copy_fasta)
        copy = QShortcut(QKeySequence.StandardKey.Copy, self.seq_text)
        copy.setContext(Qt.ShortcutContext.WidgetShortcut)
        copy.activated.connect(self._copy_sequence)
        self._switched(0)

    def _table(self, model) -> QTableView:
        view = QTableView()
        view.setModel(model)
        view.setItemDelegate(PaddedDelegate(view))
        view.verticalHeader().hide()
        view.verticalHeader().setDefaultSectionSize(30)
        view.setShowGrid(False)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        view.horizontalHeader().setHighlightSections(False)
        return view

    def _switched(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        on_alignment, on_flagged, on_seq = index == 0, index == 2, index == 3
        for w in (self.dots, self.reads, self.prev, self.next, self.step_label):
            w.setVisible(on_alignment)
        self.legend_row.setVisible(on_alignment)
        self.flag_filter.setVisible(on_flagged)
        self.copy_seq.setVisible(on_seq)
        self.copy_fasta.setVisible(on_seq)

    def set_sample(self, sr: SampleResult, data, settings) -> None:
        self.sr = sr
        self.alignment.set_data(data, sr.consensus, settings)
        self.rules_model.set_rules(sr.consensus.rules)
        self.flagged_model.set_data(data)
        labels = self.switch.labels()
        labels[2] = f"Flagged ({len(self.flagged_model._all)})"
        self.switch.setLabels(labels)
        self.legend.show_stop = bool(sr.consensus.stop_records)
        self.legend.update()
        c = sr.consensus
        if c.sequence:
            what = "Consensus (FASTA)" if c.status == "PASS" else "Assembly, not exported (sample failed)"
            self.seq_title.setText(f"{what} · {c.length} bp")
            self.seq_text.setPlainText(fasta_text(sr))
        else:
            self.seq_title.setText("No assembly for this sample")
            self.seq_text.setPlainText("")
        self.copy_seq.setEnabled(bool(c.sequence))
        self.copy_fasta.setEnabled(bool(c.sequence))
        self.update_stepper()

    def update_stepper(self) -> None:
        i, n = self.alignment.flagged_position()
        if n:
            self.step_label.setText(f"Flagged {i} of {n}")
        elif self.flagged_model._all:
            # The stepper skips bulk single-coverage marks (design 3.17); the Flagged tab still lists them.
            self.step_label.setText("Only single-coverage marks")
        else:
            self.step_label.setText("No flagged positions")
        self.prev.setEnabled(n > 0)
        self.next.setEnabled(n > 0)

    def _row_goto(self, index: QModelIndex) -> None:
        column = index.data(ColumnRole)
        if column is not None:
            self.selection.set(column)

    def _copy_sequence(self) -> None:
        if self.sr is not None:
            QGuiApplication.clipboard().setText(self.sr.consensus.sequence)

    def _copy_fasta(self) -> None:
        if self.sr is not None:
            QGuiApplication.clipboard().setText(fasta_text(self.sr))


class SampleDetail(QWidget):
    """Header, reason box, metric strip and the alignment / chromatogram splitter."""

    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.c = controller
        self.sr: SampleResult | None = None
        self.selection = SelectionModel(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        self.name = label("", "sampleName")
        self.badge = Badge("pass", large=True)
        self.verdict = label("", "secondary")
        self.verdict.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.copy = button("Copy consensus FASTA", size="small", icon_name="copy")
        self.copy.clicked.connect(self._copy_fasta)
        header = hbox(self.name, self.badge, 4, self.verdict, self.copy, spacing=10)
        header.setStretch(3, 1)
        lay.addLayout(header)
        # Reviewer decision: confirm the status, or change it; exports use the decision.
        self.review_state = label("", "secondary")
        self.review_state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.confirm_pass = button("Confirm PASS", size="small", icon_name="check",
                                   tooltip="Mark this sample as PASS for export (overrides the rules)")
        self.confirm_fail = button("Confirm FAIL", size="small", icon_name="cross",
                                   tooltip="Mark this sample as FAIL for export (overrides the rules)")
        self.undo_review = button("Undo", "link", tooltip="Forget the decision for this sample")
        self.auto_next = QCheckBox("Go to next sample after confirming")
        self.confirm_pass.clicked.connect(lambda: self._review("PASS"))
        self.confirm_fail.clicked.connect(lambda: self._review("FAIL"))
        self.undo_review.clicked.connect(lambda: self._review(None))
        review_row = hbox(label("Review", "caps"), self.confirm_pass, self.confirm_fail, 6, self.review_state,
                          self.undo_review, self.auto_next, spacing=8)
        review_row.setStretch(4, 1)
        lay.addLayout(review_row)
        self.reasons = ReasonBox()
        lay.addWidget(self.reasons)
        self.metrics = MetricStrip()
        lay.addWidget(self.metrics)
        self.split = ThinSplitter(Qt.Orientation.Vertical, line=False)
        self.split.setHandleWidth(12)
        self.detail = DetailPanel(self.selection)
        self.detail.setMinimumHeight(230)
        chrom_panel = _Panel()
        cl = QVBoxLayout(chrom_panel)
        cl.setContentsMargins(1, 1, 1, 1)
        self.chrom = ChromatogramPanel()
        cl.addWidget(self.chrom)
        self.chrom_frame = chrom_panel
        self.split.addWidget(self.detail)
        self.split.addWidget(chrom_panel)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)
        self.split.setSizes([230, 420])
        lay.addWidget(self.split, 1)

        self.selection.changed.connect(self._selected)
        self.detail.alignment.columnSelected.connect(self.selection.set)
        self.chrom.columnClicked.connect(self.selection.set)
        self.reasons.gotoColumn.connect(self.goto)
        self.reasons.showRead.connect(self.show_read)
        self.reasons.copyText.connect(self._copy_text)
        self.reasons.openFolder.connect(self.open_input_folder)

    def set_sample(self, sr: SampleResult) -> None:
        self.sr = sr
        result = self.c.result
        settings = result.settings
        c = sr.consensus
        self.name.setText(sr.sample)
        self.refresh_review()
        self.verdict.setText(verdict_sentence(sr))
        self.verdict.setToolTip(verdict_sentence(sr))
        self.copy.setText("Copy consensus FASTA" if c.status == "PASS" else "Copy assembly FASTA")
        self.copy.setVisible(bool(c.sequence))
        self.reasons.set_reasons(reason_views(sr, settings))
        self.metrics.set_tiles(metric_tiles(sr, settings, len(result.reference)))
        data = self.c.alignment_for(sr) if c.positions else None
        self.selection.column = None
        self.detail.set_sample(sr, data, settings)
        self.chrom.set_sample(sr, data, settings)
        first = data.flagged[0] if data and data.flagged else None
        if first is not None:
            self.selection.set(first)
        else:
            self.chrom.select_column(None)

    reviewed = Signal(str, object)  # sample, "PASS" / "FAIL" / None

    def refresh_review(self) -> None:
        """Badge and review bar for the reviewer's decision on the shown sample."""
        sr = self.sr
        if sr is None:
            return
        status = self.c.status_of(sr)
        self.badge.set("pass" if status == "PASS" else "fail")
        state = review.review_label(sr, self.c.reviews)
        if state == "confirmed":
            text = f"✓ Confirmed {status}"
        elif state == "changed":
            text = f"✓ Changed to {status} by reviewer (rules: {sr.consensus.status})"
        else:
            text = f"Not reviewed · rules: {sr.consensus.status}"
        self.review_state.setText(text)
        self.review_state.setToolTip(text)
        self.confirm_pass.setEnabled(self.c.reviews.get(sr.sample) != "PASS")
        self.confirm_fail.setEnabled(self.c.reviews.get(sr.sample) != "FAIL")
        self.undo_review.setVisible(bool(state))

    def _review(self, status) -> None:
        if self.sr is not None:
            self.reviewed.emit(self.sr.sample, status)

    def _selected(self, column) -> None:
        self.detail.alignment.set_selected(column)
        self.chrom.select_column(column)
        self.detail.update_stepper()

    def goto(self, column: int) -> None:
        self.detail.switch.setIndex(0)
        self.selection.column = None
        self.selection.set(column)
        self.detail.alignment.setFocus()

    def show_read(self, direction: str) -> None:
        track = self.chrom.tracks[direction]
        scroll = self._scroll_area()
        if scroll is not None:
            scroll.ensureWidgetVisible(track)
        track.view.setFocus()

    def _scroll_area(self) -> QScrollArea | None:
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        return w

    def open_input_folder(self) -> None:
        if self.sr is None:
            return
        path = self.sr.forward_path or self.sr.reverse_path
        folder = path.parent if path else (self.c.inputs[0] if self.c.inputs else None)
        if folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _copy_fasta(self) -> None:
        if self.sr is not None:
            QGuiApplication.clipboard().setText(fasta_text(self.sr))

    def _copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)


class _EmptyFilter(QWidget):
    clear = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addWidget(label("No samples match", "secondary"), 0, Qt.AlignmentFlag.AlignHCenter)
        b = button("Clear filter", "ghost", size="small")
        b.clicked.connect(self.clear.emit)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(2)


class SampleListPanel(QWidget):
    """Search, status filter, sort, the list itself and the "Files not analysed" footer (design spec 3.9)."""

    sampleSelected = Signal(object)

    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setProperty("surface", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.model = SampleListModel(self)
        self.model.reviews = controller.reviews
        self.proxy = SampleFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(12, 12, 12, 12)
        tl.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter samples")
        self.search.setClearButtonEnabled(True)
        self._search_icon = QAction(self.search)
        themed_icon(self._search_icon, "search", "text.tertiary")
        self.search.addAction(self._search_icon, QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter_text)
        tl.addWidget(self.search)
        self.segments = Segmented(["All 0", "Pass 0", "Fail 0"])
        self.segments.changed.connect(self._filter_status)
        tl.addWidget(self.segments)
        self.sort = QComboBox()
        self.sort.addItems([f"Sort: {s}" for s in SORTS])
        self.sort.currentIndexChanged.connect(self._sort)
        tl.addWidget(self.sort)
        lay.addWidget(top)
        lay.addWidget(_hline())
        self.stack = QStackedWidget()
        self.view = QListView()
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(SampleDelegate(self.view))
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        copy = QShortcut(QKeySequence.StandardKey.Copy, self.view)
        copy.setContext(Qt.ShortcutContext.WidgetShortcut)
        copy.activated.connect(lambda: QGuiApplication.clipboard().setText(self.model.as_tsv()))
        self.empty = _EmptyFilter()
        self.empty.clear.connect(self.clear_filter)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)
        lay.addWidget(_hline())
        self.footer = QToolButton()
        self.footer.setCheckable(True)
        self.footer.setProperty("variant", "flat")
        self.footer.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.footer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.footer.setFixedHeight(36)
        self.footer.toggled.connect(self._toggle_files)
        foot = QWidget()
        foot.setProperty("sunkenBand", True)
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(6, 0, 6, 0)
        fl.setSpacing(0)
        fl.addWidget(self.footer)
        self.files = QListWidget()
        self.files.setMaximumHeight(180)
        self.files.hide()
        fl.addWidget(self.files)
        lay.addWidget(foot)
        self._update_footer_icon(False)

    def set_run(self, run: RunResult) -> None:
        self.model.set_samples(run.samples, run.settings)
        self.proxy.sort(0)
        self.refresh_counts()
        rows = skipped_rows(run)
        self.footer.setText(f"Files not analysed ({len(rows)})")
        self.files.clear()
        for row in rows:
            item = QListWidgetItem(f"{row.name} — {row.cause}")
            item.setToolTip(str(row.path))
            item.setFont(ui_font(12))
            self.files.addItem(item)
        self._update_empty()

    def refresh_counts(self) -> None:
        run = self.c.result
        if run is None:
            return
        n_pass, n_fail = review.count(run, self.c.reviews, "PASS"), review.count(run, self.c.reviews, "FAIL")
        self.segments.setLabels([f"All {len(run.samples)}", f"Pass {n_pass}", f"Fail {n_fail}"])

    def select_next(self) -> bool:
        """Move to the next sample in list order; False at the end."""
        row = self.view.currentIndex().row()
        if 0 <= row < self.proxy.rowCount() - 1:
            self.view.setCurrentIndex(self.proxy.index(row + 1, 0))
            return True
        return False

    def _update_footer_icon(self, open_: bool) -> None:
        themed_icon(self.footer, "chevron-up" if open_ else "chevron-down", "text.secondary", 14)

    def _toggle_files(self, on: bool) -> None:
        self.files.setVisible(on)
        self._update_footer_icon(on)

    def open_files(self) -> None:
        self.footer.setChecked(True)

    def _filter_text(self, text: str) -> None:
        self.proxy.set_text(text)
        self._update_empty()

    def _filter_status(self, index: int) -> None:
        self.proxy.set_status(["", "PASS", "FAIL"][index])
        self._update_empty()

    def _sort(self, index: int) -> None:
        self.proxy.set_mode(index)

    def clear_filter(self) -> None:
        self.search.clear()
        self.segments.setIndex(0)

    def _update_empty(self) -> None:
        self.stack.setCurrentWidget(self.empty if self.proxy.rowCount() == 0 and self.model.rowCount() else self.view)

    def select_name(self, name: str) -> bool:
        row = self.model.row_of(name)
        if row < 0:
            return False
        index = self.proxy.mapFromSource(self.model.index(row))
        if not index.isValid():
            self.clear_filter()
            index = self.proxy.mapFromSource(self.model.index(row))
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index)
        return True

    def select_first(self) -> None:
        """The first failing sample in list order, else the first sample."""
        for r in range(self.proxy.rowCount()):
            sr = self.proxy.index(r, 0).data(SampleRole)
            if self.model.status(sr) == "FAIL":
                self.view.setCurrentIndex(self.proxy.index(r, 0))
                return
        if self.proxy.rowCount():
            self.view.setCurrentIndex(self.proxy.index(0, 0))

    def visible_names(self) -> list[str]:
        return [self.proxy.index(r, 0).data() for r in range(self.proxy.rowCount())]

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            self.sampleSelected.emit(current.data(SampleRole))

    def _context_menu(self, pos) -> None:
        index = self.view.indexAt(pos)
        if not index.isValid():
            return
        sr: SampleResult = index.data(SampleRole)
        menu = QMenu(self)
        clip = QGuiApplication.clipboard()
        menu.addAction("Copy sample name", lambda: clip.setText(sr.sample))
        act = menu.addAction("Copy consensus", lambda: clip.setText(sr.consensus.sequence))
        act.setEnabled(bool(sr.consensus.sequence))
        menu.addAction("Copy table (tab-separated)", lambda: clip.setText(self.model.as_tsv()))
        menu.addSeparator()
        menu.addAction("Confirm PASS", lambda: self.c.set_review(sr.sample, "PASS"))
        menu.addAction("Confirm FAIL", lambda: self.c.set_review(sr.sample, "FAIL"))
        act = menu.addAction("Clear review", lambda: self.c.set_review(sr.sample, None))
        act.setEnabled(sr.sample in self.c.reviews)
        menu.addSeparator()
        for direction, label_text in (("F", "forward"), ("R", "reverse")):
            path = sr.path(direction)
            act = menu.addAction(f"Show {label_text} file in folder",
                                 lambda p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent))))
            act.setEnabled(path is not None)
        menu.exec(self.view.viewport().mapToGlobal(pos))


class SummaryStrip(QFrame):
    """Counts, ratio bar, reference and rules text, and the page actions (design spec 3.8)."""

    newRun = Signal()
    openReport = Signal()
    export = Signal()
    showFiles = Signal()
    confirmAll = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("summary")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(10)
        self.passed = Badge("pass", "0 PASS", large=True)
        self.failed = Badge("fail", "0 FAIL", large=True)
        self.total = label("", "secondary")
        self.ratio = RatioBar()
        self.context = label("", "secondary")
        self.context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.stale = Chip("Computed with different settings", "warn")
        self.stale.setToolTip("Settings changed after this run. Run the analysis again to apply them.")
        self.files = QToolButton()
        self.files.setProperty("variant", "flat")
        self.files.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files.clicked.connect(self.showFiles.emit)
        self.reviewed = label("", "secondary")
        self.confirm_all = button("Confirm all…", icon_name="check",
                                  tooltip="Confirm every sample not reviewed yet with the status the rules gave")
        self.confirm_all.clicked.connect(self.confirmAll.emit)
        new_run = button("New run")
        new_run.clicked.connect(self.newRun.emit)
        self.report = button("Open report", icon_name="external")
        self.report.clicked.connect(self.openReport.emit)
        export = button("Export…", "primary", icon_name="export")
        export.clicked.connect(self.export.emit)
        for w in (self.passed, self.failed, self.total):
            lay.addWidget(w)
        lay.addSpacing(6)
        lay.addWidget(self.ratio)
        lay.addSpacing(6)
        lay.addWidget(self.context, 1)
        lay.addWidget(self.stale)
        lay.addWidget(self.files)
        lay.addWidget(self.reviewed)
        lay.addWidget(self.confirm_all)
        lay.addWidget(new_run)
        lay.addWidget(self.report)
        lay.addWidget(export)

    def set_run(self, run: RunResult, stale: bool, report: Path | None, reviews: dict | None = None) -> None:
        reviews = reviews or {}
        n_pass, n_fail = review.count(run, reviews, "PASS"), review.count(run, reviews, "FAIL")
        n_reviewed = review.reviewed_count(run, reviews)
        self.reviewed.setText(f"{n_reviewed} of {len(run.samples)} reviewed")
        self.confirm_all.setEnabled(n_reviewed < len(run.samples))
        self.passed.set("pass", f"{n_pass} PASS")
        self.failed.set("fail", f"{n_fail} FAIL")
        self.total.setText(f"of {plural(len(run.samples), 'sample')}")
        self.ratio.set_counts(n_pass, n_fail)
        context = run_context(run)
        n_files = not_analysed_count(run)
        if n_files:
            context = context.rsplit(" · ", 1)[0]
            self.files.setText(f"{plural(n_files, 'file')} not analysed")
            themed_icon(self.files, "warning", "warn.fg", 14)
            self.files.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.files.setStyleSheet(f"color: {current()['warn.fg']};")
        self.files.setVisible(bool(n_files))
        self.context.setText(context)
        self.context.setToolTip(context)
        self.stale.setVisible(stale)
        self.report.setEnabled(report is not None)
        self.report.setToolTip(str(report) if report else "No report.html yet: use Export… to write one")


class ResultsPage(QWidget):
    newRunRequested = Signal()
    exportRequested = Signal()

    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.setProperty("page", True)
        self.c = controller
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.summary = SummaryStrip()
        lay.addWidget(self.summary)
        self.notice = Notice("", "error")
        self.notice.hide()
        notice_wrap = QWidget()
        nl = QVBoxLayout(notice_wrap)
        nl.setContentsMargins(16, 10, 16, 0)
        nl.addWidget(self.notice)
        self.notice_wrap = notice_wrap
        notice_wrap.hide()
        lay.addWidget(notice_wrap)
        self.split = ThinSplitter(Qt.Orientation.Horizontal)
        self.list = SampleListPanel(controller)
        self.list.setMinimumWidth(240)
        self.list.setMaximumWidth(420)
        self.split.addWidget(self.list)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail = SampleDetail(controller)
        self.scroll.setWidget(self.detail)
        self.split.addWidget(self.scroll)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([300, 1100])
        lay.addWidget(self.split, 1)
        self.list.sampleSelected.connect(self.detail.set_sample)
        self.summary.newRun.connect(self.newRunRequested.emit)
        self.summary.export.connect(self.exportRequested.emit)
        self.summary.openReport.connect(self.open_report)
        self.summary.showFiles.connect(self.list.open_files)
        self.summary.confirmAll.connect(self.confirm_all)
        self.detail.reviewed.connect(self._review)
        controller.reviewsChanged.connect(self._reviews_changed)
        controller.settingsChanged.connect(self.refresh_summary)
        controller.exported.connect(lambda _paths: self.refresh_summary())
        self._restore()

    def _restore(self) -> None:
        store = self.c.store
        for key, splitter in (("ui/results_splitter", self.split), ("ui/detail_splitter", self.detail.split)):
            state = store.get_bytes(key)
            if state is not None:
                splitter.restoreState(state)

    def save_state(self) -> None:
        self.c.store.set("ui/results_splitter", self.split.saveState())
        self.c.store.set("ui/detail_splitter", self.detail.split.saveState())
        self.c.store.set("ui/sort", self.list.sort.currentIndex())
        self.c.store.set("ui/matches_as_dots", self.detail.detail.dots.isChecked())
        self.c.store.set("ui/show_reads", self.detail.detail.reads.isChecked())
        self.c.store.set("ui/link_tracks", self.detail.chrom.link.isChecked())
        self.c.store.set("ui/review_auto_next", self.detail.auto_next.isChecked())

    def restore_view_options(self) -> None:
        s = self.c.store
        self.list.sort.setCurrentIndex(max(0, min(len(SORTS) - 1, s.get_int("ui/sort", 0))))
        self.detail.detail.dots.setChecked(s.get_bool("ui/matches_as_dots", False))
        self.detail.detail.reads.setChecked(s.get_bool("ui/show_reads", True))
        self.detail.chrom.link.setChecked(s.get_bool("ui/link_tracks", True))
        self.detail.auto_next.setChecked(s.get_bool("ui/review_auto_next", False))

    def report_path(self) -> Path | None:
        paths = self.c.last_export or {}
        if "html" in paths and paths["html"].is_file():
            return paths["html"]
        out = self.c.output_path()
        candidate = out / "report.html" if out else None
        return candidate if candidate is not None and candidate.is_file() and self.c.last_export is not None else None

    def show_run(self, run: RunResult, keep: str | None = None) -> None:
        self.list.set_run(run)
        self.refresh_summary()
        if not (keep and self.list.select_name(keep)):
            self.list.select_first()

    def show_notice(self, text: str) -> None:
        self.notice.set(text, "error")
        self.notice.show()
        self.notice_wrap.show()

    def hide_notice(self) -> None:
        self.notice_wrap.hide()

    def refresh_summary(self) -> None:
        if self.c.result is not None:
            self.summary.set_run(self.c.result, self.c.results_stale(), self.report_path(), self.c.reviews)

    def _review(self, sample: str, status) -> None:
        self.c.set_review(sample, status)
        if status is not None and self.detail.auto_next.isChecked():
            self.list.select_next()

    def _reviews_changed(self, names: list[str]) -> None:
        self.list.model.review_changed(names)
        self.list.refresh_counts()
        self.list._update_empty()
        self.refresh_summary()
        if self.detail.sr is not None and self.detail.sr.sample in names:
            self.detail.refresh_review()

    def confirm_all(self) -> None:
        run = self.c.result
        if run is None:
            return
        todo = len(run.samples) - review.reviewed_count(run, self.c.reviews)
        if not todo:
            return
        answer = QMessageBox.question(self, "Confirm all", f"Confirm the {plural(todo, 'sample')} not reviewed yet "
                                      "with the status the rules gave? Samples you already reviewed keep your "
                                      "decision.")
        if answer == QMessageBox.StandardButton.Yes:
            self.c.confirm_unreviewed()

    def open_report(self) -> None:
        path = self.report_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def current_sample(self) -> str | None:
        return self.detail.sr.sample if self.detail.sr else None


def _hline() -> QWidget:
    line = QFrame()
    line.setProperty("divider", True)
    return line

