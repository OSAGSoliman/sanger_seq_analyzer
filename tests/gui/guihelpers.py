"""Helpers for the desktop window tests (kept out of conftest so they can be imported by name)."""

from pathlib import Path

from PySide6.QtCore import QElapsedTimer
from PySide6.QtTest import QTest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def wait_until(predicate, timeout_ms: int = 20000) -> None:
    timer = QElapsedTimer()
    timer.start()
    while not predicate():
        if timer.elapsed() > timeout_ms:
            raise AssertionError("condition not reached in time")
        QTest.qWait(10)


def make_window(store_path: Path):
    from sanger_seq_analyzer.gui.persist import Store
    from sanger_seq_analyzer.gui.shell import MainWindow
    w = MainWindow(Store(store_path))
    w.resize(1440, 900)
    w.show()
    return w


def close_window(w, qapp) -> None:
    for tool in w.tools:
        tool.controller.shutdown()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def run_examples(window, inputs=None) -> None:
    c = window.tools[0].controller
    c.set_inputs(inputs or [EXAMPLES])
    c.set_export_options(auto_export=False)
    c.rescan_now()
    done = []
    c.runFinished.connect(lambda r: done.append(r))
    assert c.start_run(), c.run_blocked_reason()
    wait_until(lambda: bool(done))


def select(tool, name: str):
    assert tool.results.list.select_name(name)
    QTest.qWait(1)
    return tool.results.detail


def column_of(tool, ref_number: int) -> int:
    data = tool.controller.alignment_for(tool.results.detail.sr)
    return next(c.index for c in data.columns if c.ref_number == ref_number)


def image_varies(widget, size=None) -> bool:
    if size is not None:
        widget.resize(*size)
    QTest.qWait(1)
    image = widget.grab().toImage()
    if size is not None:
        # The grab covers the widget's actual size, which can exceed the request when its
        # minimum size is larger (e.g. wider system fonts on Windows).
        dpr = image.devicePixelRatio()
        actual = widget.size()
        assert actual.width() >= size[0] and actual.height() >= size[1]
        assert (image.width(), image.height()) == (round(actual.width() * dpr), round(actual.height() * dpr))
    first = image.pixel(0, 0)
    step = max(1, image.width() // 97)
    return any(image.pixel(x, y) != first for x in range(0, image.width(), step)
               for y in range(0, image.height(), max(1, image.height() // 31)))
