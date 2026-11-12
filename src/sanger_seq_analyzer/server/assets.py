"""The colour variables of the web pages, made from the same design tokens as the desktop app."""

from __future__ import annotations

from functools import cache

from ..report_assets import _variables


@cache
def theme_css() -> str:
    return (f":root{{{_variables('light')}}}\n"
            f"@media (prefers-color-scheme: dark){{:root{{{_variables('dark')}}}}}\n")
