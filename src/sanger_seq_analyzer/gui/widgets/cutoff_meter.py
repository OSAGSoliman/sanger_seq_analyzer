"""The "Bases ≥ Q20" meter with the cutoff tick (design spec 3.18)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import current
from ..theme.fonts import ui_font


class CutoffMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value, self.cutoff, self.min_quality = 0.0, 0.75, 20
        self.text = ""
        self.failed = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(44)

    def sizeHint(self) -> QSize:
        return QSize(200, 44)

    def set(self, value: float, cutoff: float, min_quality: int, text: str, failed: bool) -> None:
        self.value, self.cutoff, self.min_quality, self.text, self.failed = value, cutoff, min_quality, text, failed
        self.setAccessibleName(f"Bases at or above Q{min_quality}: {text}, cutoff {cutoff:.0%}")
        self.update()

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        p.setFont(ui_font(12))
        p.setPen(t.c("text.tertiary"))
        p.drawText(QRectF(0, 0, w, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"Bases ≥ Q{self.min_quality}")
        p.setFont(ui_font(12, 600, tabular=True))
        p.setPen(t.c("fail.fg" if self.failed else "text.primary"))
        p.drawText(QRectF(0, 0, w, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.text)
        bar = QRectF(0, 22, w, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.c("track"))
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(t.c("fail.bar" if self.failed else "pass.solid"))
        p.drawRoundedRect(QRectF(0, 22, max(6.0, w * min(self.value, 1.0)), 6), 3, 3)
        x = w * self.cutoff
        p.fillRect(QRectF(x - 1, 19, 2, 12), t.c("text.primary"))
        p.setFont(ui_font(10))
        p.setPen(t.c("text.tertiary"))
        label = f"cutoff {self.cutoff:.0%}"
        p.drawText(QRectF(x - 60, 31, 120, 13), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)
