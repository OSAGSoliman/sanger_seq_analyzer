"""Small helpers shared by the pages: labels by role, cards, buttons, themed icons, thin splitters."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSplitter, QSplitterHandle, QToolButton,
    QVBoxLayout, QWidget,
)

from ..theme import current, manager
from ..theme.fonts import caps_font
from ..theme.icons import icon

_themed: weakref.WeakSet = weakref.WeakSet()


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed (design spec 5.3)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value) -> None:
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


def label(text: str = "", role: str | None = None, *, wrap: bool = False, selectable: bool = False) -> QLabel:
    w = QLabel(text)
    if role == "caps":
        w.setFont(caps_font())
        w.setText(text.upper())
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    if selectable:
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def caps(text: str) -> QLabel:
    return label(text, "caps")


def card(*, margins=(20, 16, 20, 16), spacing: int = 12, layout: str = "v") -> tuple[QFrame, QLayout]:
    frame = QFrame()
    frame.setProperty("card", True)
    lay = QVBoxLayout(frame) if layout == "v" else QHBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return frame, lay


def divider(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("vdivider" if vertical else "divider", True)
    return line


def themed_icon(button: QAbstractButton | QAction, name: str, token: str = "text.primary", size: int = 16) -> None:
    """Give a button an icon in a theme colour that follows theme changes."""
    button._icon_spec = (name, token, size)
    _themed.add(button)
    _apply_icon(button)


def _apply_icon(button: QAbstractButton) -> None:
    name, token, size = button._icon_spec
    t = current()
    button.setIcon(icon(name, t[token], size, disabled=t["text.disabled"]))
    if hasattr(button, "setIconSize"):  # QAction has no icon size
        button.setIconSize(QSize(size, size))


def _refresh_icons(_theme) -> None:
    for button in list(_themed):
        try:
            _apply_icon(button)
        except RuntimeError:  # deleted on the C++ side
            pass


manager().changed.connect(_refresh_icons)


def button(text: str = "", variant: str = "secondary", *, icon_name: str | None = None, size: str | None = None,
           tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if variant != "secondary":
        b.setProperty("variant", variant)
    if size:
        b.setProperty("size", size)
    if icon_name:
        token = {"primary": "text.onAccent", "ghost": "accent", "danger": "fail.title"}.get(variant, "text.primary")
        themed_icon(b, icon_name, token, 14 if size == "small" else 16)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def icon_button(icon_name: str, tooltip: str, token: str = "text.primary") -> QToolButton:
    b = QToolButton()
    b.setProperty("variant", "icon")
    b.setToolTip(tooltip)
    b.setAutoRaise(False)
    b.setFixedSize(26, 26)
    themed_icon(b, icon_name, token)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class _ThinHandle(QSplitterHandle):
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        color = current().c("border.default")
        if self.orientation() == Qt.Orientation.Horizontal:
            p.fillRect(self.width() // 2, 0, 1, self.height(), color)
        else:
            p.fillRect(0, self.height() // 2, self.width(), 1, color)


class ThinSplitter(QSplitter):
    """A splitter drawn as a 1 px line with a wider transparent grab area (design spec 5.1)."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None, line: bool = True):
        super().__init__(orientation, parent)
        self._line = line
        self.setHandleWidth(7)
        self.setChildrenCollapsible(False)

    def createHandle(self) -> QSplitterHandle:
        return _ThinHandle(self.orientation(), self) if self._line else super().createHandle()


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay
