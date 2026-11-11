"""Application setup, error handling, logging and the ``--self-test`` entry used by CI on packaged builds."""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import platform
import sys
import tempfile
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .. import __version__

log = logging.getLogger("sanger_seq_analyzer")


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sanger-seq-gui", add_help=True)
    p.add_argument("--self-test", metavar="OUTDIR", help="run the bundled examples headless and exit 0/1")
    p.add_argument("--theme", choices=("system", "light", "dark"), help="colour scheme for this session")
    p.add_argument("--quit-after", type=float, metavar="SECONDS",
                   help="open the window on the native platform and quit after SECONDS (launch check in CI)")
    return p.parse_args(argv)


def setup_logging(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "sanger-seq-analyzer.log"
    if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return path


class _MainThreadGC:
    """Run Python's cyclic garbage collector only on the GUI thread.

    Left automatic, a collection can start inside the analysis worker thread and
    destroy Qt widgets that belong to the GUI thread, which crashes Qt. Automatic
    collection is switched off and the same generational thresholds are checked
    from a timer in the GUI thread instead. Reference counting still frees almost
    everything immediately.
    """

    def __init__(self, interval_ms: int = 500):
        from PySide6.QtCore import QTimer

        self.thresholds = gc.get_threshold()
        gc.disable()
        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)

    def check(self) -> None:
        g0, g1, g2 = gc.get_count()
        if g0 > self.thresholds[0]:
            gc.collect(0)
            if g1 > self.thresholds[1]:
                gc.collect(1)
                if g2 > self.thresholds[2]:
                    gc.collect(2)

    def stop(self) -> None:
        self.timer.stop()
        gc.enable()


_gc: _MainThreadGC | None = None


def create_app(argv: list[str] | None = None, theme: str | None = None):
    """The QApplication with names, style, theme and error handling; reuses an existing instance."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from .persist import APPLICATION, ORGANIZATION
    from .shell import log_folder
    from .theme.manager import install

    QCoreApplication.setOrganizationName(ORGANIZATION)
    QCoreApplication.setApplicationName(APPLICATION)
    QCoreApplication.setApplicationVersion(__version__)
    app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "sanger-seq-gui", *(argv or [])])
    app.setStyle("Fusion")
    global _gc
    if _gc is None:
        _gc = _MainThreadGC()
    install(theme or "system")
    setup_logging(log_folder())
    _install_error_handling()
    return app


class _ErrorRelay:
    """Shows unexpected exceptions in a dialog; exceptions from other threads are relayed to the GUI thread."""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class Relay(QObject):
            error = Signal(str, str)

        self.relay = Relay()
        self.relay.error.connect(self.show)
        self.showing = False

    def show(self, message: str, details: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        if self.showing or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        self.showing = True
        box = QMessageBox(QMessageBox.Icon.Critical, "Unexpected error",
                          f"Something went wrong: {message}\n\nThe details were written to the log file "
                          "(Help > Open log folder).")
        box.setDetailedText(details)
        box.exec()
        self.showing = False


_relay: _ErrorRelay | None = None


def _install_error_handling() -> None:
    global _relay
    if _relay is not None:
        return
    _relay = _ErrorRelay()

    def hook(exc_type, exc, tb):
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("unhandled exception\n%s", details)
        _relay.relay.error.emit(f"{exc_type.__name__}: {exc}", details)

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook


def release_gc() -> None:
    """Give garbage collection back to Python (tests that keep running after the window is gone)."""
    global _gc
    if _gc is not None:
        _gc.stop()
        _gc = None


def run_app(argv: list[str] | None = None) -> int:
    args = _parse(list(sys.argv[1:] if argv is None else argv))
    if args.self_test:
        return self_test(Path(args.self_test))
    app = create_app([], args.theme)
    from .persist import Store
    from .shell import MainWindow
    store = Store()
    if args.theme is None:
        from .theme.manager import manager
        manager().set_mode(store.get_str("ui/theme", "system"))
    window = MainWindow(store)
    window.show()
    if args.quit_after is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(args.quit_after * 1000), app.quit)
    return app.exec()


def self_test(outdir: Path) -> int:
    """Build the real window, analyse the bundled examples, export everything; write self_test.json (AT-G31)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    outdir.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "version": __version__, "python": platform.python_version(), "platform": sys.platform}
    started = time.perf_counter()
    try:
        from PySide6 import __version__ as qt_version
        from PySide6.QtCore import QEventLoop, QTimer

        from ..report import EXPORT_FILES
        from .persist import Store
        from .qc.examples import find_examples
        from .shell import MainWindow

        report["qt"] = qt_version
        create_app([], "light")
        examples = find_examples()
        report["examples"] = str(examples)
        if examples is None:
            raise RuntimeError("bundled examples not found")
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(Store(Path(tmp) / "self_test.ini"))
            window.show()
            tool = window.tools[0]
            c = tool.controller
            export_dir = outdir / "export"
            c.set_inputs([examples])
            c.set_output_dir(str(export_dir))
            c.set_export_options(auto_export=True, write_html=True)
            c.rescan_now()
            loop = QEventLoop()
            c.runFinished.connect(lambda _r: loop.quit())
            QTimer.singleShot(180_000, loop.quit)
            if not c.start_run():
                raise RuntimeError(f"run did not start: {c.run_blocked_reason()}")
            loop.exec()
            result = c.result
            if result is None:
                raise RuntimeError("the run did not finish")
            for name in [s.sample for s in result.samples]:  # paint every sample once
                tool.results.list.select_name(name)
                window.grab()
            window.grab().save(str(outdir / "self_test.png"))
            written = sorted(p.name for p in export_dir.iterdir())
            report.update({"samples": len(result.samples), "pass": result.count("PASS"),
                           "fail": result.count("FAIL"), "files": written, "errors": result.errors,
                           "export_error": c.last_export_error})
            expected = sorted(f.filename for f in EXPORT_FILES)
            report["ok"] = (result.count("PASS"), result.count("FAIL")) == (3, 8) and written == expected
            window.close()
    except Exception as exc:  # the JSON is the only output a windowed build can give
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    report["seconds"] = round(time.perf_counter() - started, 2)
    (outdir / "self_test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
