"""16 px stroke icons as inline SVG, tinted at runtime (design spec 5.2)."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "check": '<path d="M3.5 8.5l3 3 6-7"/>',
    "cross": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "dash": '<path d="M4 8h8"/>',
    "folder": '<path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9'
              'A1.5 1.5 0 0 1 2 11.5z"/>',
    "file": '<path d="M4 2h5l3 3v9H4z"/><path d="M9 2v3h3"/>',
    "play": '<path d="M5 3.5v9l7.5-4.5z"/>',
    "search": '<circle cx="7" cy="7" r="4"/><path d="M10 10l3.5 3.5"/>',
    "chevron-down": '<path d="M4 6l4 4 4-4"/>',
    "chevron-up": '<path d="M4 10l4-4 4 4"/>',
    "chevron-left": '<path d="M10 4l-4 4 4 4"/>',
    "chevron-right": '<path d="M6 4l4 4-4 4"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "minus": '<path d="M3 8h10"/>',
    "warning": '<path d="M8 2.5l6 10.5H2z"/><path d="M8 6.5v3M8 11.3v.2"/>',
    "info": '<circle cx="8" cy="8" r="6"/><path d="M8 7.2v4M8 4.8v.2"/>',
    "reset": '<path d="M3 8a5 5 0 1 0 1.5-3.5"/><path d="M3 2.5V5h2.5"/>',
    "copy": '<rect x="5" y="5" width="8.5" height="8.5" rx="1.5"/><path d="M3 10.5V3.5A1 1 0 0 1 4 2.5h6.5"/>',
    "external": '<path d="M9 2.5h4.5V7M13.5 2.5L7 9"/><path d="M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7'
                'a1 1 0 0 1 1-1h3"/>',
    "more": '<circle cx="3.5" cy="8" r=".6"/><circle cx="8" cy="8" r=".6"/><circle cx="12.5" cy="8" r=".6"/>',
    "fit": '<path d="M2.5 6V3h3M13.5 6V3h-3M2.5 10v3h3M13.5 10v3h-3"/>',
    "export": '<path d="M8 2.5v8M5 5.5l3-3 3 3"/><path d="M3 10v2.5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V10"/>',
    "list": '<path d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.1M2.5 8h.1M2.5 12h.1"/>',
    "wave": '<path d="M1.2 12c1.6 0 2-8 3.6-8s2 8 3.6 8 1.6-4.8 2.8-4.8 1.6 4.8 3.6 4.8"/>',
    "stop": '<rect x="4" y="4" width="8" height="8" rx="1"/>',
}


def svg(name: str, color: str, stroke: float = 1.6) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>')


@lru_cache(maxsize=512)
def _renderer(name: str, color: str, stroke: float) -> QSvgRenderer:
    return QSvgRenderer(QByteArray(svg(name, color, stroke).encode()))


def paint(painter: QPainter, name: str, color: str, rect: QRectF, stroke: float = 1.6) -> None:
    """Draw an icon into ``rect`` (vector, so it is sharp at any device pixel ratio)."""
    _renderer(name, color, stroke).render(painter, rect)


def pixmap(name: str, color: str, size: int = 16, dpr: float = 2.0, stroke: float = 1.6) -> QPixmap:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint(p, name, color, QRectF(0, 0, pm.width(), pm.height()), stroke)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 16, disabled: str | None = None) -> QIcon:
    ic = QIcon(pixmap(name, color, size))
    if disabled:
        ic.addPixmap(pixmap(name, disabled, size), QIcon.Mode.Disabled)
    return ic


_FILE_DIR: Path | None = None


def file_url(name: str, color: str) -> str:
    """Path of a tinted SVG file for QSS ``url()`` (forward slashes, as Qt expects)."""
    global _FILE_DIR
    if _FILE_DIR is None:
        _FILE_DIR = Path(tempfile.mkdtemp(prefix="sanger_seq_icons_"))
        atexit.register(shutil.rmtree, _FILE_DIR, True)
    path = _FILE_DIR / f"{name}-{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(svg(name, color, 1.8), encoding="utf-8")
    return path.as_posix()


def checkbox_url(checked: bool, fill: str, border: str, mark: str) -> str:
    """A 14 px checkbox indicator as an SVG file for QSS (Fusion's own box ignores the theme border)."""
    if _FILE_DIR is None:
        file_url("check", "#000000")  # creates the folder
    name = f"cb-{int(checked)}-{fill}-{border}-{mark}".replace("#", "")
    path = _FILE_DIR / f"{name}.svg"
    if not path.exists():
        tick = f'<path d="M3.5 7.2l2.3 2.3 4.7-5" fill="none" stroke="{mark}" stroke-width="1.8" ' \
               'stroke-linecap="round" stroke-linejoin="round"/>' if checked else ""
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14">'
                        f'<rect x="0.5" y="0.5" width="13" height="13" rx="3" fill="{fill}" stroke="{border}"/>'
                        f'{tick}</svg>', encoding="utf-8")
    return path.as_posix()
