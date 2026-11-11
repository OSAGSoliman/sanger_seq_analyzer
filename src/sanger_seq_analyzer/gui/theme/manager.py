"""Applies the light or dark theme and follows the operating system's colour scheme."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from .qss import build_stylesheet
from .tokens import Theme

MODES = ("system", "light", "dark")


def _palette(t: Theme) -> QPalette:
    pal = QPalette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    for r, key in [(role.Window, "bg.window"), (role.Base, "bg.surface"), (role.AlternateBase, "bg.sunken"),
                   (role.WindowText, "text.primary"), (role.Text, "text.primary"), (role.ButtonText, "text.primary"),
                   (role.Button, "bg.surface"), (role.Highlight, "accent"), (role.HighlightedText, "text.onAccent"),
                   (role.ToolTipBase, "tooltip.bg"), (role.ToolTipText, "tooltip.fg"), (role.Link, "accent"),
                   (role.PlaceholderText, "text.tertiary"), (role.Mid, "border.strong"),
                   (role.Midlight, "border.default"),
                   (role.Dark, "border.strong"), (role.Light, "bg.surface"), (role.BrightText, "fail.fg")]:
        pal.setColor(r, t.c(key))
    for r in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(group.Disabled, r, t.c("text.disabled"))
    if hasattr(role, "Accent"):
        pal.setColor(role.Accent, t.c("accent"))
    return pal


class ThemeManager(QObject):
    """Holds the current :class:`Theme`; ``changed`` fires after it was applied to the application."""

    changed = Signal(object)

    def __init__(self, mode: str = "system"):
        super().__init__()
        self.mode = mode if mode in MODES else "system"
        self.theme = Theme(self._resolve())
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_changed)

    def _resolve(self) -> str:
        if self.mode != "system":
            return self.mode
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else Qt.ColorScheme.Unknown
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in MODES else "system"
        self.apply()

    def _system_changed(self, *_):
        if self.mode == "system":
            self.apply()

    def apply(self) -> None:
        self.theme = Theme(self._resolve())
        app = QApplication.instance()
        if app is not None:
            app.setPalette(_palette(self.theme))
            app.setStyleSheet(build_stylesheet(self.theme))
        self.changed.emit(self.theme)


_manager: ThemeManager | None = None


def manager() -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def install(mode: str = "system") -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager(mode)
    else:
        _manager.mode = mode
    _manager.apply()
    return _manager


def current() -> Theme:
    return manager().theme
