"""PASS/FAIL ratio bar of the results summary strip (design spec 3.8)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from ..theme import current


class RatioBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.passed = self.failed = 0
        self.setFixedSize(QSize(140, 8))

    def set_counts(self, passed: int, failed: int) -> None:
        self.passed, self.failed = passed, failed
        self.setToolTip(f"{passed} pass, {failed} fail")
        self.update()

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 4, 4)
        p.setClipPath(clip)
        total = self.passed + self.failed
        if not total:
            p.fillRect(QRectF(0, 0, w, h), t.c("track"))
            return
        pass_w = w * self.passed / total
        gap = 2 if self.passed and self.failed else 0
        p.fillRect(QRectF(0, 0, pass_w - gap / 2, h), t.c("pass.solid"))
        p.fillRect(QRectF(pass_w + gap / 2, 0, w - pass_w - gap / 2, h), t.c("fail.bar"))
