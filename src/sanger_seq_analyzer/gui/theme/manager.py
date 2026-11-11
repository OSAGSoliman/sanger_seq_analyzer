"""Applies the light or dark theme and follows the operating system's colour scheme."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
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
    """Holds the current :class:`Theme`; ``changed`` fires after it was applied to the application.

    It may be created before the QApplication (modules ask for colours at import time), so it
    touches the application and its style hints only once :func:`install` has run, after the
    QApplication is fully constructed. On macOS the platform plugin reports the system colour
    scheme while QApplication is still being constructed; restyling the half-built application
    from that signal crashed the app at launch.
    """

    changed = Signal(object)

    def __init__(self, mode: str = "system"):
        super().__init__()
        self.mode = mode if mode in MODES else "system"
        self.installed = False  # True once install() ran with a constructed QApplication
        self._pending = False
        self.theme = Theme(self._resolve())

    def _resolve(self) -> str:
        if self.mode != "system":
            return self.mode
        if not self.installed:
            return "light"  # no application yet: do not query the platform
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else Qt.ColorScheme.Unknown
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def attach(self) -> None:
        """Start following the system colour scheme (call once the QApplication exists)."""
        if self.installed:
            return
        self.installed = True
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_changed)

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in MODES else "system"
        self.apply()

    def _system_changed(self, *_):
        # Never restyle from inside Qt's own platform callbacks; do it on the next event-loop pass.
        if self.mode == "system" and self.installed and not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._apply_pending)

    def _apply_pending(self) -> None:
        self._pending = False
        if self.mode == "system":
            self.apply()

    def apply(self) -> None:
        self.theme = Theme(self._resolve())
        app = QApplication.instance()
        if app is not None and self.installed:
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
    """Apply the theme to the (fully constructed) QApplication and follow the system scheme."""
    global _manager
    if QApplication.instance() is None:
        raise RuntimeError("install() needs a QApplication")
    if _manager is None:
        _manager = ThemeManager(mode)
    else:
        _manager.mode = mode if mode in MODES else "system"
    _manager.attach()
    _manager.apply()
    return _manager


def current() -> Theme:
    return manager().theme
