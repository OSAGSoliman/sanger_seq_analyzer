"""On/off switch followed by the words On / Off, so state is not colour-only (design spec 3.4)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from ..theme import current


class _Track(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(36, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        return QSize(36, 20)

    def paintEvent(self, _event) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        if not self.isEnabled():
            track = t.c("border.default")
        else:
            track = t.c("accent") if on else t.c("switch.off")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        knob_x = 18 if on else 2
        p.setBrush(t.c("bg.surface") if t.dark and not on else Qt.GlobalColor.white)
        p.drawEllipse(QRectF(knob_x, 2, 16, 16))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(t.c("accent"), 2))
            p.drawRoundedRect(QRectF(1, 1, 34, 18), 9, 9)


class Switch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.track = _Track(self)
        self.track.setChecked(checked)
        self.text = QLabel()
        self.text.setProperty("role", "secondary")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.track)
        lay.addWidget(self.text)
        lay.addStretch(1)
        self.track.toggled.connect(self._changed)
        self._changed(checked, emit=False)

    def _changed(self, on: bool, emit: bool = True) -> None:
        self.text.setText("On" if on else "Off")
        self.setAccessibleName("On" if on else "Off")
        if emit:
            self.toggled.emit(on)

    def isChecked(self) -> bool:
        return self.track.isChecked()

    def setChecked(self, on: bool) -> None:
        self.track.setChecked(on)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.track.update()
