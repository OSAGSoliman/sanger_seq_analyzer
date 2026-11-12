"""Mouse, keyboard and tooltip events on every custom widget raise no Python errors.

An exception inside a Qt event handler does not fail a normal test (Qt prints it and
carries on), but the packaged app shows it to the user, as with the metric-tile tooltip
crash in 0.4.0. This test sends hover, tooltip, wheel, click, drag, context-menu and key
events to the app's own widgets and the item views, and fails on any exception.
"""

import random
import sys
import traceback

import pytest
import shiboken6
from guihelpers import EXAMPLES, run_examples, select
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QDesktopServices, QHelpEvent, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QApplication, QDialog, QWidget

KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End,
        Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight, Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_Return)
NONE, LEFT = Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton
NOMOD = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def caught(monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb))))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a, **k: True)

    def dismiss():  # a right-click menu or dialog would otherwise wait for a real click
        for w in (QApplication.activePopupWidget(), QApplication.activeModalWidget()):
            if w is not None:
                w.reject() if isinstance(w, QDialog) else w.close()
    timer = QTimer()
    timer.timeout.connect(dismiss)
    timer.start(20)
    yield errors
    timer.stop()


def _mouse(kind, target, p, button, buttons):
    QApplication.sendEvent(target, QMouseEvent(kind, QPointF(p), QPointF(target.mapToGlobal(p)), button, buttons,
                                               NOMOD))


def _poke(widget, rnd: random.Random, clicks: bool) -> None:
    targets = [widget] + ([widget.viewport()] if isinstance(widget, QAbstractScrollArea) else [])
    for t in targets:
        if not shiboken6.isValid(t) or not t.isVisible() or t.width() < 2 or t.height() < 2:
            continue
        for p in (t.rect().center(), QPoint(rnd.randrange(t.width()), rnd.randrange(t.height()))):
            g = t.mapToGlobal(p)
            QApplication.sendEvent(t, QHelpEvent(QEvent.Type.ToolTip, p, g))
            _mouse(QEvent.Type.MouseMove, t, p, NONE, NONE)
            for mods in (NOMOD, Qt.KeyboardModifier.ControlModifier):
                QApplication.sendEvent(t, QWheelEvent(QPointF(p), QPointF(g), QPoint(0, 0), QPoint(0, -120), NONE,
                                                      mods, Qt.ScrollPhase.NoScrollPhase, False))
            QApplication.sendEvent(t, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, p, g))
            if clicks:
                _mouse(QEvent.Type.MouseButtonPress, t, p, LEFT, LEFT)
                q = QPoint(min(t.width() - 1, p.x() + 30), p.y())
                _mouse(QEvent.Type.MouseMove, t, q, NONE, LEFT)
                _mouse(QEvent.Type.MouseButtonRelease, t, q, LEFT, NONE)
                _mouse(QEvent.Type.MouseButtonDblClick, t, p, LEFT, LEFT)
                _mouse(QEvent.Type.MouseButtonRelease, t, p, LEFT, NONE)
        QApplication.sendEvent(t, QEvent(QEvent.Type.Leave))
        for key in KEYS:
            for mods in (NOMOD, Qt.KeyboardModifier.ShiftModifier):
                QApplication.sendEvent(t, QKeyEvent(QEvent.Type.KeyPress, key, mods))
                QApplication.sendEvent(t, QKeyEvent(QEvent.Type.KeyRelease, key, mods))
    QApplication.processEvents()


def _poke_page(page, rnd, clicks=True) -> None:
    for w in [page, *page.findChildren(QWidget)]:
        mod = type(w).__module__ or ""
        if shiboken6.isValid(w) and w.isVisible() and (mod.startswith("sanger_seq_analyzer")
                                                        or isinstance(w, QAbstractItemView)):
            _poke(w, rnd, clicks)


def test_results_page_survives_mouse_keyboard_and_tooltips(window, caught):
    run_examples(window, [EXAMPLES])
    tool = window.tools[0]
    rnd = random.Random(7)
    for name in ("BAD04", "BAD07", "GOOD01"):  # stop codon, missing read, pass
        detail = select(tool, name)
        for view in range(4):  # Alignment, Rules, Flagged, Sequence
            detail.detail.switch.setIndex(view)
            QApplication.processEvents()
            _poke_page(window.pages["results"], rnd, clicks=view == 0)
    assert not caught, caught[0]


def test_fetch_page_survives_mouse_keyboard_and_tooltips(window, caught, tmp_path):
    from fetch_builders import GENOTYPING_TSV, build
    files = build(tmp_path / "files")
    tool = window.tools[0]
    window.show_page("fetch")
    rnd = random.Random(7)
    _poke_page(window.pages["fetch"], rnd)
    fc = tool.fetch_controller
    for key in ("masterlist", "daily", "linelist", "template"):
        fc.set_path(key, str(files[key]))
    fc.load_sources(block=True)
    tool.fetch.paste(GENOTYPING_TSV)
    tool.fetch.fetch_now()
    for view in range(3):
        tool.fetch.switch.setIndex(view)
        for show in range(4):
            tool.fetch.row_filter.setIndex(show)
            QApplication.processEvents()
        _poke_page(window.pages["fetch"], rnd, clicks=False)
    assert not caught, caught[0]
