"""Inline notices: neutral, info, warning, error (reason-box style) and success lines."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme import current
from ..theme.icons import paint as paint_icon

# kind -> (background, border, text, icon, icon colour)
KINDS = {
    "neutral": ("bg.sunken", "border.default", "text.secondary", "info", "text.tertiary"),
    "info": ("accent.subtle", "accent.border", "accent.onSubtle", "info", "accent"),
    "warn": ("warn.bg", "warn.border", "warn.fg", "warning", "warn.fg"),
    "error": ("fail.bgSoft", "fail.border", "fail.title", "cross", "fail.solid"),
    "success": ("pass.bg", "pass.border", "pass.fg", "check", "pass.fg"),
}


class _Icon(QWidget):
    def __init__(self, parent: Notice):
        super().__init__(parent)
        self.setFixedSize(QSize(16, 18))

    def paintEvent(self, _e) -> None:
        kind = KINDS[self.parent().kind]
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_icon(p, kind[3], t[kind[4]], QRectF(0, 1, 16, 16), 1.8)


class Notice(QWidget):
    """A rounded box with an icon, wrapped text and optional buttons on the right."""

    def __init__(self, text: str = "", kind: str = "neutral", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(6)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 10, 9)
        lay.setSpacing(10)
        icon_col = QVBoxLayout()
        icon_col.setContentsMargins(0, 0, 0, 0)
        icon_col.addWidget(_Icon(self))
        icon_col.addStretch(1)
        lay.addLayout(icon_col)
        lay.addWidget(self.label, 1)
        lay.addLayout(self._buttons)
        self.set(text, kind)

    def set(self, text: str, kind: str | None = None) -> None:
        if kind:
            self.kind = kind
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {current()[KINDS[self.kind][2]]}; background: transparent;")
        self.update()

    def text(self) -> str:
        return self.label.text()

    def add_button(self, button: QPushButton) -> QPushButton:
        self._buttons.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        return button

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.PaletteChange:
            self.set(self.label.text())

    def paintEvent(self, _e) -> None:
        bg, border, *_ = KINDS[self.kind]
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.c(border), 1))
        p.setBrush(t.c(bg))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
