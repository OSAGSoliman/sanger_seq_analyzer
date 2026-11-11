"""Eight metric tiles in one bordered strip; wraps to two rows of four when narrow (design spec 3.12)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QHelpEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ...presentation import Tile
from ..theme import current
from ..theme.fonts import caps_font, ui_font
from ..theme.icons import paint as paint_icon

ROW_H = 74
WRAP_BELOW = 1040


class MetricStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tiles: list[Tile] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(ROW_H)

    def set_tiles(self, tiles: list[Tile]) -> None:
        self.tiles = tiles
        self._relayout()
        self.update()

    def _columns(self) -> int:
        return len(self.tiles) // 2 if self.width() < WRAP_BELOW and len(self.tiles) > 4 else max(1, len(self.tiles))

    def _relayout(self) -> None:
        rows = 1 if not self.tiles else -(-len(self.tiles) // self._columns())
        self.setFixedHeight(ROW_H * rows)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def sizeHint(self) -> QSize:
        return QSize(8 * 128, self.height())

    def _rects(self) -> list[QRectF]:
        cols = self._columns()
        w = self.width() / cols
        return [QRectF((i % cols) * w, (i // cols) * ROW_H, w, ROW_H) for i in range(len(self.tiles))]

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            for tile, rect in zip(self.tiles, self._rects()):
                if rect.contains(e.position()) and tile.tooltip:
                    QToolTip.showText(e.globalPos(), tile.tooltip, self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(outer, 8, 8)
        p.fillPath(clip, t.c("bg.surface"))
        p.setClipPath(clip)
        rects = self._rects()
        for tile, rect in zip(self.tiles, rects):
            self._paint_tile(p, tile, rect)
        p.setPen(QPen(t.c("border.default"), 1))
        cols = self._columns()
        for i, rect in enumerate(rects):
            if i % cols:
                p.drawLine(rect.topLeft(), rect.bottomLeft())
            if i >= cols:
                p.drawLine(rect.topLeft(), rect.topRight())
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(outer, 8, 8)

    def _paint_tile(self, p: QPainter, tile: Tile, rect: QRectF) -> None:
        t = current()
        if tile.failed:
            p.fillRect(rect, t.c("fail.bgSoft"))
        x, y, w = rect.left() + 12, rect.top() + 9, rect.width() - 24
        label_color = "fail.fg" if tile.failed else "text.tertiary"
        value_color = "fail.fg" if tile.failed else ("text.disabled" if tile.provisional else "text.primary")
        suffix_color = "fail.fg" if tile.failed else ("text.disabled" if tile.provisional else "text.secondary")
        if tile.failed:
            paint_icon(p, "cross", t["fail.fg"], QRectF(x, y + 1, 12, 12), 2.2)
            x_label = x + 16
        else:
            x_label = x
        font = caps_font()
        p.setFont(font)
        p.setPen(t.c(label_color))
        fm = QFontMetrics(font)
        p.drawText(QRectF(x_label, y, w, 14), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(tile.label.upper(), Qt.TextElideMode.ElideRight, int(w - (x_label - x))))
        value_font = ui_font(18, 600, tabular=True)
        p.setFont(value_font)
        p.setPen(t.c(value_color))
        vfm = QFontMetrics(value_font)
        value = vfm.elidedText(tile.value, Qt.TextElideMode.ElideRight, int(w))
        p.drawText(QRectF(x, y + 16, w, 24), Qt.AlignmentFlag.AlignVCenter, value)
        if tile.suffix:
            sx = x + vfm.horizontalAdvance(value) + 4
            p.setFont(ui_font(12, 500, tabular=True))
            p.setPen(t.c(suffix_color))
            p.drawText(QRectF(sx, y + 19, max(0.0, x + w - sx), 20), Qt.AlignmentFlag.AlignVCenter, tile.suffix)
        if tile.hint:
            hint_font = ui_font(12)
            p.setFont(hint_font)
            p.setPen(t.c("fail.fg" if tile.failed else "text.tertiary"))
            hint = QFontMetrics(hint_font).elidedText(tile.hint, Qt.TextElideMode.ElideRight, int(w))
            p.drawText(QRectF(x, y + 40, w, 16), Qt.AlignmentFlag.AlignVCenter, hint)
