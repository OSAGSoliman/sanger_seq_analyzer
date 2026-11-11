"""Item models and delegates of the QC pages (sample list, rules, flagged positions, live run table)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, QRectF, QSize, \
    QSortFilterProxyModel, Qt
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ...pipeline import SampleResult
from ...presentation import AlignmentData, Column, failure_categories, rule_status, sample_subtitle
from ...qc import QCSettings
from ...recall import RuleResult
from ..theme import current
from ..theme.fonts import ui_font
from ..widgets.badge import badge_size, paint_badge, paint_disc

SampleRole = Qt.ItemDataRole.UserRole + 1
SubtitleRole = Qt.ItemDataRole.UserRole + 2
SortRole = Qt.ItemDataRole.UserRole + 3
ColumnRole = Qt.ItemDataRole.UserRole + 4

SORTS = ("Status, then name", "Name", "Marks", "Mixtures", "Length")


class SampleListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples: list[SampleResult] = []
        self.settings = QCSettings()
        self._subtitles: list[str] = []
        self._search: list[str] = []

    def set_samples(self, samples: list[SampleResult], settings: QCSettings) -> None:
        self.beginResetModel()
        self.samples, self.settings = list(samples), settings
        self._subtitles = [sample_subtitle(s, settings) for s in self.samples]
        self._search = [" ".join([s.sample, *s.consensus.reasons, *failure_categories(s)]).lower()
                        for s in self.samples]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.samples)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        sr = self.samples[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return sr.sample
        if role == SampleRole:
            return sr
        if role == SubtitleRole:
            return self._subtitles[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            reasons = sr.consensus.reasons
            return f"{sr.sample}: " + ("; ".join(reasons) if reasons else self._subtitles[index.row()])
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{sr.sample}, {sr.consensus.status}, {self._subtitles[index.row()]}"
        return None

    def search_text(self, row: int) -> str:
        return self._search[row]

    def row_of(self, name: str) -> int:
        return next((i for i, s in enumerate(self.samples) if s.sample == name), -1)

    def as_tsv(self) -> str:
        """The sample table as tab-separated text (EX-5)."""
        head = ["sample", "status", "length", "ref_start", "ref_end", "mixtures", "mixture_pct", "N", "marks",
                "max_single_coverage_run", "forward", "reverse", "reasons"]
        rows = ["\t".join(head)]
        for s in self.samples:
            c = s.consensus
            rows.append("\t".join(map(str, [
                s.sample, c.status, c.length, c.ref_start or "", c.ref_end or "", c.mixtures,
                f"{c.mixture_fraction * 100:.2f}", c.n_count, c.marks, c.max_single_run,
                c.forward.status if c.forward else "MISSING", c.reverse.status if c.reverse else "MISSING",
                "; ".join(c.reasons)])))
        return "\n".join(rows) + "\n"


class SampleFilterProxy(QSortFilterProxyModel):
    """Status filter, text search over name, reasons and failure categories, and the sort modes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = ""  # "", "PASS" or "FAIL"
        self.text = ""
        self.mode = 0
        self.setDynamicSortFilter(True)

    def _refilter(self, change) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.10+
            self.beginFilterChange()
            change()
            self.endFilterChange()
        else:
            change()
            self.invalidateFilter()

    def set_status(self, status: str) -> None:
        self._refilter(lambda: setattr(self, "status", status))

    def set_text(self, text: str) -> None:
        self._refilter(lambda: setattr(self, "text", text.strip().lower()))

    def set_mode(self, mode: int) -> None:
        self.mode = mode
        self.invalidate()
        self.sort(0)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model: SampleListModel = self.sourceModel()
        sr = model.samples[row]
        if self.status and sr.consensus.status != self.status:
            return False
        return not self.text or self.text in model.search_text(row)

    def _key(self, sr: SampleResult):
        c = sr.consensus
        real = 0 if c.positions and not c.provisional else 1  # display-only metrics sort last
        if self.mode == 1:
            return (sr.sample.lower(),)
        if self.mode == 2:
            return real, -c.marks, sr.sample.lower()
        if self.mode == 3:
            return real, -c.mixtures, sr.sample.lower()
        if self.mode == 4:
            return real, -c.length, sr.sample.lower()
        return (0 if c.status == "FAIL" else 1), sr.sample.lower()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        model: SampleListModel = self.sourceModel()
        return self._key(model.samples[left.row()]) < self._key(model.samples[right.row()])


class SampleDelegate(QStyledItemDelegate):
    """48 px rows: status disc, name, secondary line, status word (design spec 3.9)."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 48)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        t = current()
        sr: SampleResult = index.data(SampleRole)
        rect = QRectF(option.rect)
        p.save()
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(rect, t.c("bg.selected"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            p.fillRect(rect, t.c("bg.hover"))
        p.fillRect(QRectF(rect.left(), rect.bottom(), rect.width(), 1), t.c("border.subtle"))
        status = sr.consensus.status
        kind = "pass" if status == "PASS" else "fail"
        paint_disc(p, QRectF(rect.left() + 12, rect.center().y() - 10, 20, 20), kind)
        word_font = ui_font(11, 700)
        word_font.setLetterSpacing(word_font.SpacingType.PercentageSpacing, 103)
        word_w = QFontMetrics(word_font).horizontalAdvance(status)
        p.setFont(word_font)
        p.setPen(t.c("pass.fg" if kind == "pass" else "fail.fg"))
        p.drawText(QRectF(rect.right() - 12 - word_w, rect.top(), word_w, rect.height()),
                   Qt.AlignmentFlag.AlignVCenter, status)
        x = rect.left() + 42
        w = rect.right() - 12 - word_w - 10 - x
        p.setFont(ui_font(13, 600))
        p.setPen(t.c("text.primary"))
        p.drawText(QRectF(x, rect.top() + 6, w, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(ui_font(13, 600)).elidedText(sr.sample, Qt.TextElideMode.ElideRight, int(w)))
        sub_font = ui_font(12)
        p.setFont(sub_font)
        p.setPen(t.c("text.tertiary"))
        sub = QFontMetrics(sub_font).elidedText(index.data(SubtitleRole), Qt.TextElideMode.ElideRight, int(w))
        p.drawText(QRectF(x, rect.top() + 24, w, 17), Qt.AlignmentFlag.AlignVCenter, sub)
        p.restore()


class RuleChecklistModel(QAbstractTableModel):
    """Every rule with observed value, limit and result (SD-3)."""

    HEADERS = ("Rule", "Type", "Observed", "Limit", "Result")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rules: list[RuleResult] = []

    def set_rules(self, rules: list[RuleResult]) -> None:
        self.beginResetModel()
        self.rules = list(rules)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rules)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section].upper()
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rules[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return [r.label, "Lab" if r.family == "lab" else "RECall", r.observed, r.limit, rule_status(r)][col]
        if role == Qt.ItemDataRole.ForegroundRole:
            t = current()
            if col == 4:
                return t.c({True: "pass.fg", False: "fail.fg", None: "text.tertiary"}[r.passed])
            if col == 1:
                return t.c("text.tertiary")
            return t.c("text.disabled") if r.passed is None and col == 2 else None
        if role == Qt.ItemDataRole.FontRole and col in (0, 4):
            return ui_font(13, 600 if col == 4 else 500)
        if role == ColumnRole:
            return r.column
        if role == Qt.ItemDataRole.ToolTipRole and r.passed is None:
            return "Not applied: a lab read rule failed, so RECall rules were not evaluated."
        return None


FLAG_FILTERS = ("All flagged", "Marked", "Mixture", "Differs", "Indel", "N", "Stop codon")
_FILTER_TEST = {
    1: lambda c: bool(c.marks), 2: lambda c: "mixture" in c.flags, 3: lambda c: "diff" in c.flags,
    4: lambda c: bool({"deletion", "insertion"} & c.flags), 5: lambda c: "n" in c.flags,
    6: lambda c: "stop" in c.flags,
}


class FlaggedPositionsModel(QAbstractTableModel):
    """Every flagged or marked column with per-read detail (SD-5..SD-7); virtualised, so 450 rows are cheap."""

    HEADERS = ("Cons", "Ref", "Base", "Ref base", "Cov", "Mean Q", "Forward", "Reverse", "Why")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: list[Column] = []
        self._all: list[Column] = []
        self.filter = 0

    def set_data(self, data: AlignmentData | None) -> None:
        self._all = [c for c in data.columns if c.marks or c.flags - {"single"}] if data else []
        self.apply_filter(self.filter)

    def apply_filter(self, index: int) -> None:
        self.beginResetModel()
        self.filter = index
        test = _FILTER_TEST.get(index)
        self.columns = [c for c in self._all if test is None or test(c)]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section].upper()
        return None

    @staticmethod
    def _read(c: Column, d: str) -> str:
        cell = c.reads.get(d)
        if cell is None:
            return "–"
        if cell.base == "-":
            return "gap"
        text = f"{cell.base} Q{cell.quality}"
        if cell.uncalled and cell.ratio >= 0.05:
            text += f" · {cell.uncalled} {cell.ratio:.0%}"
        return text

    def _why(self, c: Column) -> str:
        parts = []
        for flag, word in (("stop", "stop codon"), ("diff", "differs"), ("mixture", "mixture"), ("n", "N"),
                           ("deletion", "deletion"), ("insertion", "insertion")):
            if flag in c.flags and word not in c.marks:
                parts.append(word)
        return "; ".join(parts + c.marks)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        c = self.columns[index.row()]
        col = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, SortRole):
            ref = c.ref_number if c.ref_number else f"ins {c.anchor_number}"
            values = [c.cons_index or "–", ref, c.cons_base, c.ref_base, c.coverage, round(c.mean_quality, 1),
                      self._read(c, "F"), self._read(c, "R"), self._why(c)]
            value = values[col]
            if role == SortRole:
                return c.index if col in (0, 1) else value
            return str(value)
        if role == ColumnRole:
            return c.index
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (0, 1, 4, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class LiveRunModel(QAbstractTableModel):
    """Rows of the running view: finished samples with status and reason, then the current and queued ones."""

    HEADERS = ("Sample", "Status", "Reason")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.names: list[str] = []
        self.results: dict[str, SampleResult] = {}
        self.current = ""

    def start(self, names: list[str]) -> None:
        self.beginResetModel()
        self.names, self.results, self.current = list(names), {}, ""
        self.endResetModel()

    def set_current(self, name: str) -> None:
        self.current = name
        self._changed(name)

    def add_result(self, sr: SampleResult) -> None:
        self.results[sr.sample] = sr
        self._changed(sr.sample)

    def _changed(self, name: str) -> None:
        if name in self.names:
            row = self.names.index(name)
            self.dataChanged.emit(self.index(row, 0), self.index(row, 2))
        self.layoutChanged.emit()

    def state(self, row: int) -> tuple[str, str]:
        """(badge kind, reason text) for a row."""
        name = self.names[row]
        sr = self.results.get(name)
        if sr is not None:
            c = sr.consensus
            return ("pass" if c.status == "PASS" else "fail"), ("; ".join(c.reasons) if c.reasons else
                                                                f"{c.length} bp, no rule triggered")
        if name == self.current:
            return "running", "Trimming, aligning and calling"
        return "queued", ""

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.names)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section].upper()
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, reason = self.state(index.row())
        if role == Qt.ItemDataRole.DisplayRole:
            return [self.names[index.row()], kind, reason[:1].upper() + reason[1:]][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 2:
            return current().c("text.secondary")
        if role == Qt.ItemDataRole.BackgroundRole and kind == "running":
            return current().c("running.row")
        if role == Qt.ItemDataRole.FontRole and index.column() == 0:
            return ui_font(13, 600)
        return None


class PaddedDelegate(QStyledItemDelegate):
    """Table cells with the 12 px side padding of the header and the model's row background."""

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        if bg is not None and not option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(option.rect, bg)
        padded = QStyleOptionViewItem(option)
        padded.rect = option.rect.adjusted(12, 0, -8, 0)
        super().paint(p, padded, index)


class BadgeDelegate(QStyledItemDelegate):
    """Paints the status column of the live run table as a pill."""

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        kind = index.data()
        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        if bg is not None:
            p.fillRect(option.rect, bg)
        if kind == "queued":
            p.save()
            p.setFont(ui_font(12))
            p.setPen(current().c("text.tertiary"))
            p.drawText(QRectF(option.rect).adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Queued")
            p.restore()
            return
        text = {"pass": "PASS", "fail": "FAIL", "running": "Analysing…"}[kind]
        size = badge_size(kind, text)
        paint_badge(p, option.rect.left() + 12, option.rect.center().y() - size.height() / 2 + 1, kind, text)
