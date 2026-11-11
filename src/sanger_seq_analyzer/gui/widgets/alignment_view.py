"""Reference / consensus / read alignment in wrapped blocks (design spec 3.17).

All drawing happens in the viewport's ``paintEvent`` and only for the blocks in
view. The widget emits ``columnSelected`` when the user picks a column; the
results page calls :meth:`set_selected` to move the selection programmatically.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip

from ...presentation import AlignmentData, Column, tooltip_lines
from ...qc import QCSettings
from ...recall import SampleCall
from ..theme import current
from ..theme.fonts import mono_font, ui_font

CELL_W, CELL_H = 16, 18
LABEL_W = 60
RULER_H = 14
BLOCK_GAP = 8
PAD_X, PAD_Y = 12, 8


class AlignmentView(QAbstractScrollArea):
    columnSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: AlignmentData | None = None
        self.call: SampleCall | None = None
        self.settings = QCSettings()
        self.selected: int | None = None
        self.hover: int | None = None
        self.show_reads = True
        self.dots = False
        self.per_block = 60
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._font = mono_font(13)
        self._bold = mono_font(13, 700)
        self._small = ui_font(10, tabular=True)
        self._label_font = ui_font(11)

    # --- data -------------------------------------------------------------------------------------------

    def set_data(self, data: AlignmentData | None, call: SampleCall | None, settings: QCSettings) -> None:
        self.data, self.call, self.settings = data, call, settings
        self.selected = None
        self.hover = None
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self.viewport().update()

    def set_show_reads(self, on: bool) -> None:
        self.show_reads = on
        self._relayout()
        self.viewport().update()

    def set_dots(self, on: bool) -> None:
        self.dots = on
        self.viewport().update()

    def set_selected(self, column: int | None, ensure_visible: bool = True) -> None:
        self.selected = column
        if column is not None and ensure_visible:
            self.ensure_visible(column)
        self.viewport().update()

    # --- geometry ---------------------------------------------------------------------------------------

    def _rows(self) -> list[str]:
        rows = ["ref", "cons"]
        if self.show_reads and self.data is not None:
            rows += [d for d in ("F", "R") if d in self.data.col_to_raw]
        return rows

    def _block_height(self) -> int:
        return RULER_H + CELL_H * len(self._rows()) + BLOCK_GAP

    def _n_blocks(self) -> int:
        n = len(self.data.columns) if self.data else 0
        return -(-n // self.per_block) if n else 0

    def _relayout(self) -> None:
        room = (self.viewport().width() - LABEL_W - 2 * PAD_X) // CELL_W
        self.per_block = max(30, min(120, room // 10 * 10))
        total = PAD_Y * 2 + self._n_blocks() * self._block_height() - (BLOCK_GAP if self._n_blocks() else 0)
        sb = self.verticalScrollBar()
        sb.setRange(0, max(0, total - self.viewport().height()))
        sb.setPageStep(self.viewport().height())
        sb.setSingleStep(CELL_H)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def sizeHint(self) -> QSize:
        return QSize(900, 230)

    def _cell_rect(self, column: int, row: str) -> QRectF:
        block, i = divmod(column, self.per_block)
        rows = self._rows()
        y = PAD_Y + block * self._block_height() + RULER_H + rows.index(row) * CELL_H - self.verticalScrollBar().value()
        return QRectF(PAD_X + LABEL_W + i * CELL_W, y, CELL_W, CELL_H)

    def column_at(self, pos: QPointF) -> int | None:
        if self.data is None:
            return None
        y = pos.y() + self.verticalScrollBar().value() - PAD_Y
        block, within = divmod(int(y), self._block_height())
        if within < RULER_H or within >= self._block_height() - BLOCK_GAP or block < 0:
            return None
        i = int((pos.x() - PAD_X - LABEL_W) // CELL_W)
        if not 0 <= i < self.per_block:
            return None
        col = block * self.per_block + i
        return col if col < len(self.data.columns) else None

    def ensure_visible(self, column: int) -> None:
        block = column // self.per_block
        top = PAD_Y + block * self._block_height()
        bottom = top + self._block_height() - BLOCK_GAP
        sb = self.verticalScrollBar()
        if top < sb.value() or bottom > sb.value() + self.viewport().height():
            sb.setValue(max(0, top - PAD_Y))

    # --- painting ---------------------------------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), t.c("bg.surface"))
        if not self.data or not self.data.columns:
            p.setFont(ui_font(12))
            p.setPen(t.c("text.tertiary"))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                       "No assembly: no read matched the reference.")
            return
        bh = self._block_height()
        scroll = self.verticalScrollBar().value()
        first = max(0, (scroll - PAD_Y) // bh)
        last = min(self._n_blocks() - 1, (scroll + self.viewport().height() - PAD_Y) // bh)
        for block in range(first, last + 1):
            self._paint_block(p, block, PAD_Y + block * bh - scroll)

    def _paint_block(self, p: QPainter, block: int, top: float) -> None:
        t = current()
        cols = self.data.columns[block * self.per_block:(block + 1) * self.per_block]
        rows = self._rows()
        x0 = PAD_X + LABEL_W
        # ruler and codon band
        p.setFont(self._small)
        p.setPen(t.c("text.tertiary"))
        for i, c in enumerate(cols):
            x = x0 + i * CELL_W
            if c.ref_number and c.ref_number % 10 == 0:
                p.drawText(QRectF(x - 40, top - 1, 40 + CELL_W - 1, RULER_H - 3),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(c.ref_number))
            if self.data.coding and c.codon:
                p.fillRect(QRectF(x, top + RULER_H - 3, CELL_W, 3), t.c("hl.codon.a" if c.codon % 2 else "hl.codon.b"))
        # labels
        first_ref = next((c.ref_number for c in cols if c.ref_number), None)
        labels = {"ref": f"Ref {first_ref}" if first_ref else "Ref", "cons": "Cons", "F": "F", "R": "R"}
        p.setFont(self._label_font)
        for r, row in enumerate(rows):
            y = top + RULER_H + r * CELL_H
            p.setPen(t.c("text.tertiary"))
            p.drawText(QRectF(PAD_X, y, LABEL_W - 4, CELL_H), Qt.AlignmentFlag.AlignVCenter, labels[row])
        for i, c in enumerate(cols):
            x = x0 + i * CELL_W
            for r, row in enumerate(rows):
                self._paint_cell(p, c, row, QRectF(x, top + RULER_H + r * CELL_H, CELL_W, CELL_H))

    def _paint_cell(self, p: QPainter, c: Column, row: str, rect: QRectF) -> None:
        t = current()
        selected = c.index == self.selected
        if row == "ref":
            text, color, bold = c.ref_base, "text.tertiary", False
            if c.ref_base == "-":
                p.fillRect(rect, t.c("hl.gap.bg"))
                color = "hl.gap.fg"
            if selected:
                p.fillRect(rect, t.c("accent.subtle"))
                color = "text.primary"
            elif c.index == self.hover:
                p.fillRect(rect, t.c("bg.hover"))
            self._text(p, rect, text, color, bold)
            return
        if row == "cons":
            fill, color, bold = self._cons_style(c)
            text = c.cons_base
            if fill:
                p.fillRect(rect, t.c(fill))
            elif c.index == self.hover:
                p.fillRect(rect, t.c("bg.hover"))
            if self.dots and not fill and c.cons_base == c.ref_base:
                text, color = "·", "text.tertiary"
            self._text(p, rect, text, color, bold)
            if "stop" in c.flags:
                rule = t.c("hl.stop.rule")
                p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 2), rule)
                p.fillRect(QRectF(rect.left(), rect.bottom() - 2, rect.width(), 2), rule)
            elif c.marks:
                p.fillRect(QRectF(rect.left() + 1, rect.bottom() - 2, rect.width() - 2, 2), t.c("hl.mark"))
            if selected:
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(QPen(t.c("accent"), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 2, 2)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            return
        cell = c.reads.get(row)
        if cell is None:
            return
        if selected:
            p.fillRect(rect, t.c("accent.subtle"))
        elif c.index == self.hover:
            p.fillRect(rect, t.c("bg.hover"))
        if cell.base == "-":
            p.fillRect(rect.adjusted(0, 1, 0, -1), t.c("hl.gap.bg"))
            self._text(p, rect, "-", "hl.gap.fg", False)
        elif cell.censored:
            self._text(p, rect, cell.base.lower(), "text.disabled", False)
        else:
            good = cell.quality >= self.settings.min_quality
            self._text(p, rect, cell.base, "text.secondary" if good else "text.tertiary", False)

    def _cons_style(self, c: Column) -> tuple[str | None, str, bool]:
        """Fill, text colour and weight; precedence stop > N > mixture > differs > gap."""
        for flag, key in (("stop", "stop"), ("n", "n"), ("mixture", "mix"), ("diff", "diff"), ("deletion", "gap")):
            if flag in c.flags:
                return f"hl.{key}.bg", f"hl.{key}.fg", key != "gap"
        return None, "text.primary", False

    def _text(self, p: QPainter, rect: QRectF, text: str, color: str, bold: bool) -> None:
        p.setFont(self._bold if bold else self._font)
        p.setPen(current().c(color))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    # --- interaction ------------------------------------------------------------------------------------

    def viewportEvent(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            col = self.column_at(QPointF(e.pos()))
            if col is not None and self.data:
                QToolTip.showText(e.globalPos(), "\n".join(
                    tooltip_lines(self.data.columns[col], self.data, self.settings, self.call)), self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        col = self.column_at(e.position())
        if col != self.hover:
            self.hover = col
            self.viewport().update()

    def leaveEvent(self, e) -> None:
        self.hover = None
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        col = self.column_at(e.position())
        if col is not None:
            self.setFocus()
            self._select(col)

    def _select(self, col: int) -> None:
        if self.data is None or not self.data.columns:
            return
        col = max(0, min(len(self.data.columns) - 1, col))
        self.set_selected(col)
        self.columnSelected.emit(col)

    def step_flagged(self, forward: bool) -> None:
        if not self.data or not self.data.flagged:
            return
        cur = self.selected if self.selected is not None else (-1 if forward else len(self.data.columns))
        if forward:
            nxt = next((c for c in self.data.flagged if c > cur), self.data.flagged[0])
        else:
            nxt = next((c for c in reversed(self.data.flagged) if c < cur), self.data.flagged[-1])
        self._select(nxt)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self.data is None or not self.data.columns:
            return super().keyPressEvent(e)
        key, mods = e.key(), e.modifiers()
        cur = self.selected if self.selected is not None else -1
        step = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
        if mods & Qt.KeyboardModifier.ControlModifier:
            step = 3
        if key == Qt.Key.Key_Right:
            self._select(cur + step)
        elif key == Qt.Key.Key_Left:
            self._select(max(0, cur - step))
        elif key == Qt.Key.Key_Down:
            self._select(cur + self.per_block)
        elif key == Qt.Key.Key_Up:
            self._select(max(0, cur - self.per_block))
        elif key == Qt.Key.Key_Home:
            self._select(0)
        elif key == Qt.Key.Key_End:
            self._select(len(self.data.columns) - 1)
        elif key == Qt.Key.Key_BracketRight:
            self.step_flagged(True)
        elif key == Qt.Key.Key_BracketLeft:
            self.step_flagged(False)
        else:
            super().keyPressEvent(e)

    def flagged_position(self) -> tuple[int, int]:
        """(1-based index of the selection among flagged columns or 0, number flagged)."""
        if not self.data:
            return 0, 0
        flagged = self.data.flagged
        return (flagged.index(self.selected) + 1 if self.selected in flagged else 0), len(flagged)
