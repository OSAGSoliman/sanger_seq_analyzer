"""Fixtures for the desktop window tests: offscreen Qt, a temporary settings file per window."""

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")

from guihelpers import close_window, make_window, run_examples  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)


@pytest.fixture(scope="session")
def qapp():
    from sanger_seq_analyzer.gui.app import create_app
    app = create_app([], "light")
    yield app
    # Delete every widget while the application still exists; widgets collected after it is gone crash Qt.
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    from sanger_seq_analyzer.gui.app import release_gc
    release_gc()


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Fail instead of hanging: a modal dialog in a headless test waits forever for a click.

    Unexpected dialogs answer No/Cancel and fail the test at teardown. Tests that expect a
    dialog patch it themselves (their monkeypatch.setattr runs after this one).
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

    opened = []

    def record(kind, result):
        def fake(*args, **kwargs):
            opened.append((kind, [a for a in args if isinstance(a, str)][:1]))
            return result
        return fake

    no = QMessageBox.StandardButton.No
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, record(f"QMessageBox.{name}", no))
    monkeypatch.setattr(QMessageBox, "about", record("QMessageBox.about", None))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", record("QFileDialog.getExistingDirectory", ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", record("QFileDialog.getOpenFileName", ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", record("QFileDialog.getOpenFileNames", ([], "")))
    monkeypatch.setattr(QDialog, "exec", record("QDialog.exec", 0))
    yield
    assert not opened, f"unexpected modal dialog(s) in a headless test: {opened}"


@pytest.fixture
def window(qapp, tmp_path):
    w = make_window(tmp_path / "settings.ini")
    yield w
    close_window(w, qapp)


@pytest.fixture(scope="session")
def results_window(qapp, tmp_path_factory):
    """One window with the examples analysed, shared by read-only result tests."""
    w = make_window(tmp_path_factory.mktemp("results") / "settings.ini")
    run_examples(w)
    yield w
    close_window(w, qapp)


@pytest.fixture
def tool(results_window):
    return results_window.tools[0]
