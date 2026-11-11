"""System fonts at the design's pixel sizes (design spec 2.2)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QFont, QFontDatabase

from ...design_tokens import TYPE

MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "JetBrains Mono", "DejaVu Sans Mono",
                   "Liberation Mono")
_WEIGHTS = {400: QFont.Weight.Normal, 500: QFont.Weight.Medium, 600: QFont.Weight.DemiBold, 700: QFont.Weight.Bold}


@lru_cache(maxsize=1)
def mono_family() -> str:
    available = set(QFontDatabase.families())
    for name in MONO_CANDIDATES:
        if name in available:
            return name
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def _tabular(font: QFont) -> QFont:
    if hasattr(font, "setFeature") and hasattr(QFont, "Tag"):  # Qt 6.7+
        try:
            font.setFeature(QFont.Tag("tnum"), 1)
        except (TypeError, ValueError):
            pass
    return font


def ui_font(size: int = 13, weight: int = 400, tabular: bool = False) -> QFont:
    font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
    font.setPixelSize(size)
    font.setWeight(_WEIGHTS.get(weight, QFont.Weight.Normal))
    return _tabular(font) if tabular else font


def mono_font(size: int = 12, weight: int = 400) -> QFont:
    font = QFont(mono_family())
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(size)
    font.setWeight(_WEIGHTS.get(weight, QFont.Weight.Normal))
    return font


def role_font(role: str, mono: bool = False, tabular: bool = False) -> QFont:
    size, weight = TYPE[role]
    return mono_font(size, weight) if mono else ui_font(size, weight, tabular)


def caps_font() -> QFont:
    """11 px / 600 uppercase label with 0.04 em letter spacing (QSS cannot do letter spacing)."""
    font = ui_font(11, 600)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
    return font
