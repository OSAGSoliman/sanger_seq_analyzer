"""Render every screen of the desktop window to PNG files, headless.

    python scripts/screenshots.py OUTDIR [--theme light|dark|both] [--size 1440x900]

Uses a temporary settings file, analyses the bundled examples and drives the real
widgets into each state. Also copies the example report.html into OUTDIR.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sanger_seq_analyzer.gui.app import create_app  # noqa: E402
from sanger_seq_analyzer.gui.persist import Store  # noqa: E402
from sanger_seq_analyzer.gui.qc.examples import find_examples  # noqa: E402
from sanger_seq_analyzer.gui.qc.export_dialog import ExportDialog  # noqa: E402
from sanger_seq_analyzer.gui.shell import MainWindow  # noqa: E402
from sanger_seq_analyzer.gui.theme.manager import manager  # noqa: E402


def settle(ms: int = 60) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def run_and_wait(controller) -> None:
    loop = QEventLoop()
    controller.runFinished.connect(lambda _r: loop.quit())
    QTimer.singleShot(120_000, loop.quit)
    controller.start_run()
    loop.exec()


def select(tool, name: str, ref_number: int | None = None) -> None:
    tool.results.list.select_name(name)
    if ref_number is not None:
        detail = tool.results.detail
        data = tool.controller.alignment_for(detail.sr)
        column = next(c.index for c in data.columns if c.ref_number == ref_number)
        detail.goto(column)
    settle()


def render(outdir: Path, theme: str, size: tuple[int, int]) -> list[Path]:
    manager().set_mode(theme)
    suffix = "" if theme == "light" else "_dark"
    shots: list[Path] = []
    tmp = Path(tempfile.mkdtemp(prefix="sanger_shots_"))
    try:
        examples_copy = tmp / "examples"
        shutil.copytree(find_examples(), examples_copy)
        window = MainWindow(Store(tmp / "settings.ini"))
        window.resize(*size)
        window.show()
        tool = window.tools[0]
        c = tool.controller

        def shot(name: str, widget=None) -> None:
            settle()
            path = outdir / f"{name}{suffix}.png"
            (widget or window).grab().save(str(path))
            shots.append(path)

        shot("01_setup_empty")
        c.set_inputs([examples_copy])
        c.rescan_now()
        shot("02_setup_ready")

        run_and_wait(c)
        result = c.result
        running = tool.run_page
        names = [s.sample for s in result.samples]
        tool._run_started(names)
        for n, sr in enumerate(result.samples[:5], 1):
            running.running.progress(n, len(names), sr.sample)
            tool._sample_done(n, len(names), sr)
        running.running.progress(6, len(names), names[5])
        shot("03_running")
        tool._run_finished(result)

        select(tool, "GOOD01", 281)
        shot("04_results_GOOD01")
        select(tool, "BAD04", 178)
        shot("05_results_BAD04")
        select(tool, "BAD02")
        shot("06_results_BAD02")
        select(tool, "BAD07")
        shot("07_results_BAD07")

        window.show_page("settings")
        shot("08_settings")
        page = tool.settings
        page.controls["min_good_fraction"].setValue(70)
        page.advanced_toggle.setChecked(True)
        shot("09_settings_changed")
        page.controls["min_good_fraction"].setValue(75)
        page.advanced_toggle.setChecked(False)

        window.show_page("results")
        dialog = ExportDialog(c, window)
        dialog.show()
        shot("10_export_dialog", dialog)
        dialog.close()
        if theme == "light":
            report = tmp / "report"
            c.export(report, ["html"])
            shutil.copy(report / "report.html", outdir / "report.html")
        window.close()
    finally:
        QApplication.processEvents()
        shutil.rmtree(tmp, ignore_errors=True)
    return shots


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("outdir", type=Path)
    p.add_argument("--theme", choices=("light", "dark", "both"), default="light")
    p.add_argument("--size", default="1440x900", help="window size, e.g. 1440x900")
    args = p.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    size = tuple(int(v) for v in args.size.lower().split("x"))
    create_app([], "light")
    started = time.perf_counter()
    themes = ("light", "dark") if args.theme == "both" else (args.theme,)
    for theme in themes:
        for path in render(args.outdir, theme, size):
            print(path)
    print(f"done in {time.perf_counter() - started:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
