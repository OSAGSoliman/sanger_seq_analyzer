"""Start-up order: nothing may touch the application or its style hints before QApplication exists.

On macOS the Cocoa platform plugin reports the system colour scheme while QApplication is still
being constructed. v0.3.0 had connected to that signal at import time and restyled the half-built
application from it, which crashed the app at launch (segfault in QApplication::setStyleSheet).
The offscreen platform used here never emits that signal, so these tests check the rule instead.
"""

import os
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(r"""
    import importlib, pkgutil, sys
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    real_hints = QGuiApplication.styleHints

    def guarded_hints():
        if QApplication.instance() is None:
            raise SystemExit("FAIL: styleHints() used before the QApplication exists")
        return real_hints()

    QGuiApplication.styleHints = staticmethod(guarded_hints)

    import sanger_seq_analyzer.gui as gui
    for mod in pkgutil.walk_packages(gui.__path__, gui.__name__ + "."):
        if not mod.name.endswith(".__main__"):  # __main__ starts the app
            importlib.import_module(mod.name)

    from sanger_seq_analyzer.gui.theme import current, manager
    current()
    m = manager()
    m._system_changed()  # a scheme change before the app exists must be ignored
    assert not m.installed

    from sanger_seq_analyzer.gui.app import create_app
    app = create_app([], None)
    assert m.installed and manager() is m

    applied = []
    real_apply = m.apply
    m.apply = lambda: (applied.append(1), real_apply())
    m._system_changed()
    m._system_changed()
    assert applied == [], "restyled synchronously from the platform signal"
    app.processEvents()
    assert applied == [1], applied  # once, on the next event-loop pass
    print("OK")
""")


def test_nothing_touches_qt_before_the_application_exists():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0 and out.stdout.strip().endswith("OK"), out.stdout + out.stderr
