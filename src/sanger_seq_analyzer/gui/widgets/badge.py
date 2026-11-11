"""Status pills, status discs and chips (design spec 3.6, 3.7, 3.9). Status is always icon + word."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import current
from ..theme.fonts import ui_font
from ..theme.icons import paint as paint_icon

# kind -> (fg, bg, border, icon)
STYLES = {
    "pass": ("pass.fg", "pass.bg", "pass.border", "check"),
    "fail": ("fail.fg", "fail.bg", "fail.border", "cross"),
    "missing": ("text.secondary", "bg.window", "border.default", "dash"),
    "queued": ("text.tertiary", "bg.window", "border.default", None),
    "running": ("accent", "accent.subtle", "accent.border", None),
    "warn": ("warn.fg", "warn.bg", "warn.border", "warning"),
    "neutral": ("text.secondary", "bg.surface", "border.default", None),
    "accent": ("accent.onSubtle", "accent.subtle", "accent.border", None),
}
WORDS = {"pass": "PASS", "fail": "FAIL", "missing": "MISSING", "queued": "Queued", "running": "Analysing…"}


def kind_for_status(status: str) -> str:
    return {"PASS": "pass", "FAIL": "fail", "MISSING": "missing"}.get(status, "neutral")


def _badge_font(large: bool):
    font = ui_font(13 if large else 11, 700)
    font.setLetterSpacing(font.SpacingType.PercentageSpacing, 103)
    return font


def _badge_metrics(kind: str, large: bool) -> tuple[int, int, int, int]:
    """(height, left padding, icon size or 0, right padding)."""
    has_icon = STYLES[kind][3] is not None
    if large:
        return 26, 8 if has_icon else 12, 14 if has_icon else 0, 12
    return 20, 6 if has_icon else 8, 12 if has_icon else 0, 8


def badge_size(kind: str, text: str, large: bool = False) -> QSize:
    h, left, icon, right = _badge_metrics(kind, large)
    text_w = QFontMetrics(_badge_font(large)).horizontalAdvance(text)
    return QSize(left + (icon + 4 if icon else 0) + text_w + right, h)


def paint_badge(p: QPainter, x: float, y: float, kind: str, text: str, large: bool = False) -> None:
    """Draw a pill with its top-left corner at (x, y)."""
    t = current()
    fg, bg, border, icon = STYLES[kind]
    size = badge_size(kind, text, large)
    h, left, s, _ = _badge_metrics(kind, large)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(x + 0.5, y + 0.5, size.width() - 1, h - 1)
    p.setPen(QPen(t.c(border), 1))
    p.setBrush(t.c(bg))
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    tx = rect.left() + left
    if icon:
        paint_icon(p, icon, t[fg], QRectF(tx, rect.center().y() - s / 2, s, s), 2.2)
        tx += s + 4
    p.setFont(_badge_font(large))
    p.setPen(t.c(fg))
    p.drawText(QRectF(tx, rect.top(), rect.right() - tx, rect.height()), Qt.AlignmentFlag.AlignVCenter, text)
    p.restore()


class Badge(QWidget):
    """Pill with icon and text; ``large`` is the 26 px variant used in headers and summaries."""

    def __init__(self, kind: str = "pass", text: str | None = None, large: bool = False, parent=None):
        super().__init__(parent)
        self._large = large
        self._kind, self._text = kind, ""
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set(kind, text)

    def set(self, kind: str, text: str | None = None) -> None:
        self._kind = kind
        self._text = text if text is not None else WORDS.get(kind, kind.upper())
        self.setAccessibleName(self._text)
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def kind(self) -> str:
        return self._kind

    def sizeHint(self) -> QSize:
        return badge_size(self._kind, self._text, self._large)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        h = self.sizeHint().height()
        paint_badge(QPainter(self), 0, (self.height() - h) / 2, self._kind, self._text, self._large)


class StatusDisc(QWidget):
    """20 px disc with a check or cross, used in the sample list."""

    def __init__(self, kind: str = "pass", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(20, 20)

    def paintEvent(self, _event) -> None:
        paint_disc(QPainter(self), QRectF(0, 0, 20, 20), self.kind)


def paint_disc(p: QPainter, rect: QRectF, kind: str, solid: bool = False) -> None:
    t = current()
    fg, bg, border, icon = STYLES[kind]
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if solid:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.c("fail.solid" if kind == "fail" else "pass.solid"))
        icon_color = "#FFFFFF"
    else:
        p.setPen(QPen(t.c(border), 1))
        p.setBrush(t.c(bg))
        icon_color = t[fg]
    p.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))
    if icon:
        s = rect.width() * 0.6
        paint_icon(p, icon, icon_color, QRectF(rect.center().x() - s / 2, rect.center().y() - s / 2, s, s), 2.4)


class Chip(QWidget):
    """22 px information chip; ``kind="warn"`` adds a warning triangle (design spec 3.7)."""

    def __init__(self, text: str = "", kind: str = "neutral", parent=None):
        super().__init__(parent)
        self._text, self._kind = text, kind
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set(self, text: str, kind: str | None = None) -> None:
        self._text = text
        if kind:
            self._kind = kind
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(ui_font(12, 500 if self._kind == "accent" else 400))
        icon_w = 16 if self._kind == "warn" else 0
        return QSize(16 + icon_w + fm.horizontalAdvance(self._text), 22)

    def paintEvent(self, _event) -> None:
        t = current()
        fg, bg, border, icon = STYLES.get(self._kind, STYLES["neutral"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(t.c(border), 1))
        p.setBrush(t.c(bg))
        p.drawRoundedRect(rect, 4, 4)
        x = 8.0
        if self._kind == "warn":
            paint_icon(p, "warning", t[fg], QRectF(x - 1, rect.center().y() - 6.5, 13, 13), 1.6)
            x += 15
        p.setFont(ui_font(12, 500 if self._kind == "accent" else 400))
        p.setPen(t.c(fg if self._kind != "neutral" else "text.secondary"))
        p.drawText(QRectF(x, 0, self.width() - x - 6, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
