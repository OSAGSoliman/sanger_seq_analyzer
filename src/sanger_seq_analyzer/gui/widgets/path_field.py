"""Editable path field in monospace that elides the middle while it does not have focus (design 3.2)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QLineEdit

from .common import set_prop


class PathField(QLineEdit):
    """``fullText()`` is the real path; the shown text may be elided. ``pathEdited`` fires on Enter or focus out."""

    pathEdited = Signal(str)

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setProperty("mono", True)
        self.setPlaceholderText(placeholder)
        self.editingFinished.connect(self._finished)

    def fullText(self) -> str:
        return self._full if not self.hasFocus() else self.text()

    def setFullText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._show()

    def setInvalid(self, invalid: bool) -> None:
        set_prop(self, "invalid", invalid)

    def _show(self) -> None:
        self.blockSignals(True)
        if self.hasFocus():
            self.setText(self._full)
        else:
            room = self.width() - 26
            fm = QFontMetrics(self.font())
            self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(room, 40)))
            self.setCursorPosition(0)
        self.blockSignals(False)

    def _finished(self) -> None:
        if self.hasFocus() and self.text() != self._full:
            self._full = self.text()
            self.setToolTip(self._full)
            self.pathEdited.emit(self._full)

    def focusInEvent(self, e: QFocusEvent) -> None:
        super().focusInEvent(e)
        self._show()

    def focusOutEvent(self, e: QFocusEvent) -> None:
        if self.text() != self._full:
            self._full = self.text()
            self.setToolTip(self._full)
            self.pathEdited.emit(self._full)
        super().focusOutEvent(e)
        self._show()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        if not self.hasFocus():
            self._show()
