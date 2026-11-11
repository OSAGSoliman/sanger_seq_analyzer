"""Qt colours for one theme."""

from __future__ import annotations

from functools import cached_property

from PySide6.QtGui import QColor

from ...design_tokens import palette, parse_color


class Theme:
    """A light or dark palette; ``theme.c("accent")`` gives a QColor, ``theme["accent"]`` the CSS string."""

    def __init__(self, name: str):
        self.name = name
        self.tokens = palette(name)
        self._colors: dict[str, QColor] = {}

    @property
    def dark(self) -> bool:
        return self.name == "dark"

    def __getitem__(self, key: str) -> str:
        return self.tokens[key]

    def c(self, key: str) -> QColor:
        color = self._colors.get(key)
        if color is None:
            r, g, b, a = parse_color(self.tokens[key])
            color = QColor(r, g, b, round(a * 255))
            self._colors[key] = color
        return color

    def base(self, letter: str) -> QColor:
        """Trace / letter colour of a base; IUPAC codes and N use the "other" colour."""
        return self.c(f"base.{letter}" if letter in "ACGT" else "base.N")

    @cached_property
    def qss_tokens(self) -> dict[str, str]:
        """Tokens with dots replaced so they can be used with ``str.format``."""
        return {k.replace(".", "_"): v for k, v in self.tokens.items()}
