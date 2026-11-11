"""Design tokens shared by the desktop window and the HTML report (design spec 2.1-2.3).

Plain data, no Qt: ``palette(theme)`` returns one flat dict of colour strings
(``#RRGGBB`` or ``rgba(r,g,b,a)``) per theme, so the stylesheet, the
custom-painted widgets and the report CSS all use the same values.
"""

from __future__ import annotations

import re

LIGHT = {
    "bg.window": "#F3F5F7", "bg.surface": "#FFFFFF", "bg.sunken": "#F7F8FA", "bg.hover": "#EEF2F6",
    "bg.selected": "#E3EDF9", "bg.pressed": "#E3E8ED",
    "border.default": "#D9DEE4", "border.subtle": "#EEF1F4", "border.strong": "#BCC4CE", "border.hover": "#98A1AD",
    "text.primary": "#1A2129", "text.secondary": "#4A5563", "text.tertiary": "#667080", "text.disabled": "#98A1AD",
    "text.onAccent": "#FFFFFF",
    "accent": "#1F5FA8", "accent.hover": "#194E8C", "accent.pressed": "#143F73", "accent.subtle": "#E8F0FA",
    "accent.onSubtle": "#173F70", "accent.ghostPressed": "#D6E4F5", "accent.border": "#A9C6E8",
    "primary.disabled": "#BCC4CE",
    "pass.fg": "#17663A", "pass.bg": "#E3F3E8", "pass.border": "#9CD0AE", "pass.solid": "#2E8B57",
    "fail.fg": "#A3231C", "fail.bg": "#FBE9E7", "fail.bgSoft": "#FDF3F2", "fail.border": "#E9A8A2",
    "fail.solid": "#B3261E", "fail.title": "#7A1510", "fail.bar": "#D0312D", "fail.text": "#4A2A28",
    "fail.hint": "#6B4A47", "fail.buttonBorder": "#C9837D", "fail.pressed": "#F5D5D2",
    "warn.fg": "#5C3D00", "warn.bg": "#FFF4D6", "warn.border": "#E0B040",
    "tooltip.bg": "#1A2129", "tooltip.fg": "#FFFFFF",
    "switch.off": "#98A1AD", "track": "#E6EAEE", "running.row": "#F2F7FD",
}

DARK = {
    "bg.window": "#15181C", "bg.surface": "#1D2126", "bg.sunken": "#171A1F", "bg.hover": "#262B32",
    "bg.selected": "#1E3550", "bg.pressed": "#2D333B",
    "border.default": "#323943", "border.subtle": "#262B32", "border.strong": "#4A5462", "border.hover": "#5C6673",
    "text.primary": "#E6E9ED", "text.secondary": "#B3BCC7", "text.tertiary": "#8C97A5", "text.disabled": "#5C6673",
    "text.onAccent": "#0F1F33",
    "accent": "#6FA8E8", "accent.hover": "#8BBAEE", "accent.pressed": "#5A93D4", "accent.subtle": "#1B2C40",
    "accent.onSubtle": "#BBD6F5", "accent.ghostPressed": "#22384F", "accent.border": "#2F4F75",
    "primary.disabled": "#3A424D",
    "pass.fg": "#7BD49B", "pass.bg": "#17301F", "pass.border": "#2F6A45", "pass.solid": "#3FA56B",
    "fail.fg": "#F0938C", "fail.bg": "#3A1C1A", "fail.bgSoft": "#2B1716", "fail.border": "#7A3530",
    "fail.solid": "#D9534A", "fail.title": "#FFC9C4", "fail.bar": "#D9534A", "fail.text": "#E8C9C6",
    "fail.hint": "#C9A5A1", "fail.buttonBorder": "#8A4A44", "fail.pressed": "#4A2422",
    "warn.fg": "#F2CF7A", "warn.bg": "#33290F", "warn.border": "#7A5F1A",
    "tooltip.bg": "#E6E9ED", "tooltip.fg": "#15181C",
    "switch.off": "#5C6673", "track": "#2D333B", "running.row": "#1B2633",
}

BASES = {
    "light": {"A": "#2E8B3D", "C": "#1F62C4", "G": "#20242A", "T": "#D0312D", "N": "#8A5A00"},
    "dark": {"A": "#5CC46B", "C": "#6EA6F5", "G": "#E6E9ED", "T": "#F2766F", "N": "#E0B040"},
}

# Alignment highlights: background, text (and rule colour where one is drawn).
HIGHLIGHTS = {
    "light": {
        "diff.bg": "#CFE3FB", "diff.fg": "#0B3D7A", "mix.bg": "#FFE08A", "mix.fg": "#5C3D00",
        "n.bg": "#F5B5B0", "n.fg": "#7A1510", "gap.bg": "#E6EAEE", "gap.fg": "#4A5563", "mark": "#E07B00",
        "stop.bg": "#FBE9E7", "stop.fg": "#A3231C", "stop.rule": "#D0312D",
        "codon.a": "#E6EAEE", "codon.b": "#BCC4CE",
    },
    "dark": {
        "diff.bg": "#1F3F66", "diff.fg": "#BBD6F5", "mix.bg": "#5C4A12", "mix.fg": "#FFE9A8",
        "n.bg": "#6A2622", "n.fg": "#FFD3CF", "gap.bg": "#2D333B", "gap.fg": "#B3BCC7", "mark": "#F2A341",
        "stop.bg": "#3A1C1A", "stop.fg": "#F0938C", "stop.rule": "#D9534A",
        "codon.a": "#2D333B", "codon.b": "#4A5462",
    },
}

CHROM = {
    "light": {
        "qual.good": "#9AAFC4", "qual.bad": "#D0312D", "qual.line": "#4A5563",
        "trim.fill": "rgba(102,112,128,0.20)", "trim.edge": "#4A5563",
        "sel.fill": "rgba(31,95,168,0.10)", "sel.edge": "rgba(31,95,168,0.60)",
        "stop.fill": "rgba(208,49,45,0.07)", "stop.edge": "rgba(208,49,45,0.55)",
        "mini.ground": "#E6EAEE", "mini.aligned": "#A9C6E8", "mini.hatch": "#98A1AD",
        "mini.view": "#1A2129", "mini.viewFill": "rgba(255,255,255,0.55)",
    },
    "dark": {
        "qual.good": "#5F7892", "qual.bad": "#D9534A", "qual.line": "#B3BCC7",
        "trim.fill": "rgba(140,151,165,0.18)", "trim.edge": "#8C97A5",
        "sel.fill": "rgba(111,168,232,0.16)", "sel.edge": "rgba(111,168,232,0.70)",
        "stop.fill": "rgba(217,83,74,0.12)", "stop.edge": "rgba(217,83,74,0.70)",
        "mini.ground": "#2D333B", "mini.aligned": "#2F4F75", "mini.hatch": "#5C6673",
        "mini.view": "#E6E9ED", "mini.viewFill": "rgba(0,0,0,0.30)",
    },
}

# Type scale (design spec 2.2): role -> (pixel size, weight)
TYPE = {
    "page_title": (22, 600), "sample_name": (20, 600), "dialog_title": (17, 600), "reason_title": (15, 600),
    "card_title": (14, 600), "panel_title": (13, 600), "body": (13, 400), "button": (13, 500),
    "list_name": (13, 600), "hint": (12, 400), "caps": (11, 600), "badge": (11, 700), "badge_large": (13, 700),
    "metric": (18, 600), "metric_unit": (12, 500), "sequence": (13, 400), "ruler": (10, 400),
    "chrom_letter": (12, 700), "chrom_ruler": (9, 400), "path": (12, 400),
}

THEMES = ("light", "dark")


def palette(theme: str = "light") -> dict[str, str]:
    """Every colour token of one theme in a flat dict (``base.A``, ``hl.diff.bg``, ``chrom.qual.good``...)."""
    core = LIGHT if theme == "light" else DARK
    out = dict(core)
    out.update({f"base.{k}": v for k, v in BASES[theme].items()})
    out.update({f"hl.{k}": v for k, v in HIGHLIGHTS[theme].items()})
    out.update({f"chrom.{k}": v for k, v in CHROM[theme].items()})
    return out


def parse_color(value: str) -> tuple[int, int, int, float]:
    """``#RRGGBB`` or ``rgba(r,g,b,a)`` -> (r, g, b, alpha)."""
    if value.startswith("#"):
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 1.0
    m = re.fullmatch(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", value.replace(" ", ""))
    if not m:
        raise ValueError(f"not a colour: {value}")
    return int(m[1]), int(m[2]), int(m[3]), float(m[4])


def _luminance(value: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b, _ = parse_color(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two opaque colours."""
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)
