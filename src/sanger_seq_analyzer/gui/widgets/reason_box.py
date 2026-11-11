"""The reason box under a failing sample's header (design spec 3.11)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...presentation import ReasonView
from ..theme import current
from ..theme.fonts import caps_font, ui_font
from .badge import paint_disc
from .common import button


class _Disc(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)

    def paintEvent(self, _e) -> None:
        paint_disc(QPainter(self), QRectF(0, 0, 28, 28), "fail", solid=True)


class _Rule(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, _e) -> None:
        QPainter(self).fillRect(self.rect(), current().c("fail.border"))


class ReasonBox(QWidget):
    """One box per sample listing every reason; buttons emit the navigation signals."""

    gotoColumn = Signal(int)
    showRead = Signal(str)
    copyText = Signal(str)
    openFolder = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._views: list[ReasonView] = []
        self._labels: list[tuple[QLabel, str]] = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)
        disc_col = QVBoxLayout()
        disc_col.addWidget(_Disc(self))
        disc_col.addStretch(1)
        outer.addLayout(disc_col)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)

    def set_reasons(self, views: list[ReasonView]) -> None:
        self._views = views
        self._labels = []
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear(item.layout())
        for i, v in enumerate(views):
            if i:
                self.body.addWidget(_Rule())
            self.body.addLayout(self._reason_row(v))
        self._recolor()
        self.setVisible(bool(views))

    def _text(self, text: str, token: str, font, wrap: bool = True) -> QLabel:
        lab = QLabel(text)
        lab.setFont(font)
        lab.setWordWrap(wrap)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._labels.append((lab, token))
        return lab

    def _reason_row(self, v: ReasonView) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self._text(v.title, "fail.title", ui_font(15, 600), wrap=False))
        family = self._text(v.family.upper(), "fail.fg", caps_font(), wrap=False)
        head.addWidget(family, 0, Qt.AlignmentFlag.AlignBaseline)
        head.addStretch(1)
        col.addLayout(head)
        if v.explanation:
            col.addWidget(self._text(v.explanation, "fail.text", ui_font(13)))
        if v.hint:
            col.addWidget(self._text(v.hint, "fail.hint", ui_font(12)))
        row.addLayout(col, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        if v.goto_column is not None and v.goto_label:
            b = button(v.goto_label, "danger", size="small")
            b.clicked.connect(lambda _=False, c=v.goto_column: self.gotoColumn.emit(c))
            buttons.addWidget(b)
        if v.action == "show_read" and v.read:
            b = button(f"Show {'forward' if v.read == 'F' else 'reverse'} read", "danger", size="small")
            b.clicked.connect(lambda _=False, d=v.read: self.showRead.emit(d))
            buttons.addWidget(b)
        if v.action == "open_folder":
            b = button("Open input folder", "danger", size="small")
            b.clicked.connect(self.openFolder.emit)
            buttons.addWidget(b)
        copy = button("Copy reason", "danger", size="small")
        copy.clicked.connect(lambda _=False, v=v: self.copyText.emit(f"{v.title}. {v.explanation}".strip()))
        buttons.addWidget(copy)
        wrap = QVBoxLayout()
        wrap.addLayout(buttons)
        wrap.addStretch(1)
        row.addLayout(wrap)
        return row

    def _recolor(self) -> None:
        t = current()
        for lab, token in self._labels:
            lab.setStyleSheet(f"color: {t[token]}; background: transparent;")

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.PaletteChange:
            self._recolor()

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.c("fail.border"), 1))
        p.setBrush(t.c("fail.bgSoft"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
