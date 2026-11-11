"""Segmented control: equal segments in one bordered box, one selected (design spec 3.5)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import current
from ..theme.fonts import ui_font


class Segmented(QWidget):
    changed = Signal(int)

    def __init__(self, labels: list[str], parent=None, height: int = 28, equal: bool = True):
        super().__init__(parent)
        self._labels = list(labels)
        self._index = 0
        self._hover = -1
        self._equal = equal
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding if equal else QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Fixed)

    def setLabels(self, labels: list[str]) -> None:
        self._labels = list(labels)
        self.updateGeometry()
        self.update()

    def labels(self) -> list[str]:
        return list(self._labels)

    def index(self) -> int:
        return self._index

    def setIndex(self, i: int, emit: bool = True) -> None:
        if 0 <= i < len(self._labels) and i != self._index:
            self._index = i
            self.update()
            if emit:
                self.changed.emit(i)

    def _widths(self) -> list[float]:
        if self._equal:
            return [self.width() / len(self._labels)] * len(self._labels)
        fm = QFontMetrics(ui_font(12, 600))
        return [fm.horizontalAdvance(t) + 24 for t in self._labels]

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(ui_font(12, 600))
        widths = [fm.horizontalAdvance(t) + 24 for t in self._labels]
        w = max(widths) * len(widths) if self._equal else sum(widths)
        return QSize(int(w), self.height())

    def _at(self, x: float) -> int:
        edge = 0.0
        for i, w in enumerate(self._widths()):
            edge += w
            if x < edge:
                return i
        return len(self._labels) - 1

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        i = self._at(e.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setIndex(self._at(e.position().x()))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Left:
            self.setIndex(max(0, self._index - 1))
        elif e.key() == Qt.Key.Key_Right:
            self.setIndex(min(len(self._labels) - 1, self._index + 1))
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _event) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        widths = self._widths()
        total = sum(widths)
        outer = QRectF(0.5, 0.5, total - 1, self.height() - 1)
        clip = QPainterPath()
        clip.addRoundedRect(outer, 6, 6)
        p.fillPath(clip, t.c("bg.surface"))
        p.setClipPath(clip)
        x = 0.0
        for i, (label, w) in enumerate(zip(self._labels, widths)):
            seg = QRectF(x, 0, w, self.height())
            if i == self._index:
                p.fillRect(seg, t.c("accent.subtle"))
            elif i == self._hover and self.isEnabled():
                p.fillRect(seg, t.c("bg.hover"))
            if i:
                p.fillRect(QRectF(x, 0, 1, self.height()), t.c("border.default"))
            selected = i == self._index
            p.setFont(ui_font(12, 600 if selected else 500))
            p.setPen(t.c("accent" if selected else "text.secondary") if self.isEnabled() else t.c("text.disabled"))
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, label)
            x += w
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(t.c("accent") if self.hasFocus() else t.c("border.strong"), 2 if self.hasFocus() else 1))
        p.drawRoundedRect(outer, 6, 6)
