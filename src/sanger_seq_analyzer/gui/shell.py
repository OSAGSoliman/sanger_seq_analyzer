"""Main window: header with tool page tabs, the page stack, menus and the close guard (design spec 1.1-1.3)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, \
    QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QSizePolicy, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__
from .persist import Store
from .theme import current, manager
from .theme.icons import paint as paint_icon
from .tools import ToolUI, available_tools
from .widgets.common import label, themed_icon

TITLE = f"Sanger Seq Analyzer {__version__}"
CITATION = ("Woods CK, Brumme CJ, Liu TF, et al. Automating HIV drug resistance genotyping with RECall, a freely "
            "accessible sequence analysis tool. J Clin Microbiol. 2012;50(6):1936-1942.")
DIFFERENCES = [
    "Reads are paired by file name (forward and reverse tags); RECall groups any number of primers.",
    "The lab's read rule is applied first: at least 75% of bases at Q20 or better, trimmed length 100 bp.",
    "A sample needs a forward and a reverse read, on the expected strands.",
    "Two-base IUPAC calls written by the sequencer count as two observations.",
    "An insertion or deletion seen in only one of two reads is not called; the position is marked.",
    "Codons and stop codons are counted on the reference fragment (codon 1 = first base unless the frame is set).",
]


class _Brand(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_icon(p, "wave", current()["accent"], QRectF(0, 0, 20, 20), 1.8)


class _DropArea(QWidget):
    """Central widget that accepts folders and files dropped anywhere on the window (IN-3)."""

    def __init__(self, on_drop):
        super().__init__()
        self._on_drop = on_drop
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self._on_drop(paths)


def log_folder() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base or Path.home() / ".sanger_seq_analyzer") / "logs"


class MainWindow(QMainWindow):
    def __init__(self, store: Store | None = None):
        super().__init__()
        self.store = store or Store()
        self.setWindowTitle(TITLE)
        self.setMinimumSize(1100, 700)
        self.resize(1440, 900)
        self.tabs: dict[str, QToolButton] = {}
        self.pages: dict[str, QWidget] = {}
        self.tools: list[ToolUI] = []
        central = _DropArea(self.drop_paths)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header())
        self.stack = QStackedWidget()
        self.stack.setObjectName("pages")
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        for spec in available_tools():
            self._add_tool(spec.factory(self, self.store))
        self._build_menus()
        for i, key in enumerate(list(self.tabs)[:9], 1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            shortcut.activated.connect(lambda k=key: self.show_page(k))
        geometry = self.store.get_bytes("ui/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        for tool in self.tools:
            if hasattr(tool, "start"):
                tool.start()
        self.show_page(next(iter(self.tabs)))

    # --- header and pages -------------------------------------------------------------------------------

    def _header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(48)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 12, 0)
        hl.setSpacing(8)
        hl.addWidget(_Brand())
        hl.addWidget(label("Sanger Seq Analyzer", "brand"))
        hl.addWidget(label(__version__, "hint"))
        hl.addSpacing(16)
        self.tab_bar = QHBoxLayout()
        self.tab_bar.setSpacing(4)
        hl.addLayout(self.tab_bar)
        hl.addStretch(1)
        self.context = QLabel("")
        self.context.setProperty("role", "hint")
        self.context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.context.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self.context, 2)
        self.more = QToolButton()
        self.more.setProperty("variant", "flat")
        self.more.setToolTip("Menu")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setFixedSize(QSize(32, 32))
        self.more.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        themed_icon(self.more, "more", "text.secondary")
        hl.addWidget(self.more)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        return header

    def _add_tool(self, tool: ToolUI) -> None:
        self.tools.append(tool)
        for key, title, widget in tool.pages():
            tab = QToolButton()
            tab.setText(title)
            tab.setProperty("role", "tab")
            tab.setCheckable(True)
            tab.setFixedHeight(48)
            tab.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.tab_group.addButton(tab)
            self.tab_bar.addWidget(tab)
            self.tabs[key] = tab
            self.pages[key] = widget
            self.stack.addWidget(widget)
        tool.contextTextChanged.connect(self._set_context)
        tool.pageEnabledChanged.connect(self.set_page_enabled)
        tool.pageRequested.connect(self.show_page)

    def show_page(self, key: str) -> None:
        if key not in self.pages or not self.tabs[key].isEnabled():
            return
        self.tabs[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])

    def current_page(self) -> str:
        widget = self.stack.currentWidget()
        return next(k for k, w in self.pages.items() if w is widget)

    def set_page_enabled(self, key: str, enabled: bool) -> None:
        if key in self.tabs:
            self.tabs[key].setEnabled(enabled)
            if not enabled and self.current_page() == key:
                self.show_page(next(k for k, t in self.tabs.items() if t.isEnabled()))

    def _set_context(self, text: str) -> None:
        self.context.setToolTip(text)
        fm = self.context.fontMetrics()
        self.context.setText(fm.elidedText(text, Qt.TextElideMode.ElideMiddle, max(120, self.context.width())))
        self._context_full = text

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if getattr(self, "_context_full", None):
            self._set_context(self._context_full)

    # --- menus ------------------------------------------------------------------------------------------

    def _build_menus(self) -> None:
        actions: dict[str, QAction] = {}
        for tool in self.tools:
            actions.update(tool.actions())
        for a in actions.values():
            self.addAction(a)  # shortcuts work with the menu bar hidden

        def section(menu: QMenu, keys: list[str]) -> None:
            for k in keys:
                if k == "-":
                    menu.addSeparator()
                elif k in actions:
                    menu.addAction(actions[k])

        file_menu = QMenu("&File", self)
        section(file_menu, ["add_folder", "add_files", "load_examples", "-", "export", "-"])
        quit_action = file_menu.addAction("Quit", self.close)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        self.addAction(quit_action)
        run_menu = QMenu("&Run", self)
        section(run_menu, ["run", "cancel"])
        view_menu = QMenu("&View", self)
        section(view_menu, ["find", "-", "zoom_in", "zoom_out", "zoom_reset", "-"])
        theme_menu = view_menu.addMenu("Theme")
        group = QActionGroup(self)
        mode = self.store.get_str("ui/theme", "system")
        for key, text in (("system", "Follow system"), ("light", "Light"), ("dark", "Dark")):
            a = theme_menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(key == mode)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            group.addAction(a)
        help_menu = QMenu("&Help", self)
        about = help_menu.addAction("About Sanger Seq Analyzer", self.about)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        help_menu.addAction("Open log folder", self.open_logs)
        help_menu.addSeparator()
        help_menu.addAction("Restore factory settings…", self.restore_factory)
        menus = [file_menu, run_menu, view_menu, help_menu]
        bar = self.menuBar()
        for m in menus:
            bar.addMenu(m)
        if sys.platform == "darwin":
            self.more.hide()
        else:
            bar.setVisible(False)
            popup = QMenu(self)
            for m in menus:
                popup.addMenu(m)
            self.more.setMenu(popup)

    def set_theme(self, mode: str) -> None:
        self.store.set("ui/theme", mode)
        manager().set_mode(mode)

    def about(self) -> None:
        tool = self.tools[0]
        rules = ""
        if hasattr(tool, "controller"):
            from ..presentation import rule_summary
            rules = f"<p><b>Rules in force</b><br>{rule_summary(tool.controller.settings)}</p>"
        diffs = "".join(f"<li>{d}</li>" for d in DIFFERENCES)
        QMessageBox.about(self, "About Sanger Seq Analyzer",
                          f"<h3>Sanger Seq Analyzer {__version__}</h3>"
                          "<p>Quality control and forward/reverse consensus of Sanger reads with the lab's read "
                          "cutoff and the RECall rules.</p>"
                          f"{rules}<p><b>Differences from RECall</b></p><ul>{diffs}</ul>"
                          f"<p><b>Rule source</b><br>{CITATION}</p>"
                          "<p>See README.md for the full rule list.</p>")

    def open_logs(self) -> None:
        folder = log_folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def restore_factory(self) -> None:
        answer = QMessageBox.question(self, "Restore factory settings",
                                      "Forget every remembered folder, reference, rule setting and window layout? "
                                      "The change takes effect the next time the app starts.")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.clear()
            self._factory_reset = True

    # --- drag and drop, closing -------------------------------------------------------------------------

    def drop_paths(self, paths: list[Path]) -> None:
        for tool in self.tools:
            if hasattr(tool, "accept_drop"):
                tool.accept_drop(paths)
                break

    def closeEvent(self, e: QCloseEvent) -> None:
        if not all(tool.can_close() for tool in self.tools):
            e.ignore()
            return
        if not getattr(self, "_factory_reset", False):
            for tool in self.tools:
                tool.save_state()
            self.store.set("ui/geometry", self.saveGeometry())
        self.store.sync()
        e.accept()
