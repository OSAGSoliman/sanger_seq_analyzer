"""Custom widgets paint in both themes; chromatogram tracks for every example read (AT-G14, AT-G18, AT-G19)."""

import time

import numpy as np
import pytest
from guihelpers import EXAMPLES, image_varies, select
from PySide6.QtTest import QTest

from sanger_seq_analyzer.gui.theme.manager import manager
from sanger_seq_analyzer.gui.widgets.badge import Badge, Chip
from sanger_seq_analyzer.gui.widgets.chromatogram import MARGIN, ChromatogramPanel, ChromatogramTrack
from sanger_seq_analyzer.gui.widgets.cutoff_meter import CutoffMeter
from sanger_seq_analyzer.gui.widgets.metric_strip import MetricStrip
from sanger_seq_analyzer.gui.widgets.notice import Notice
from sanger_seq_analyzer.gui.widgets.ratio_bar import RatioBar
from sanger_seq_analyzer.gui.widgets.reason_box import ReasonBox
from sanger_seq_analyzer.gui.widgets.segmented import Segmented
from sanger_seq_analyzer.gui.widgets.switch import Switch
from sanger_seq_analyzer.pipeline import run
from sanger_seq_analyzer.presentation import alignment, metric_tiles, reason_views
from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.simulate import make_read

S = QCSettings()
NAMES = ["BAD01", "BAD02", "BAD03", "BAD04", "BAD05", "BAD06", "BAD07", "BAD08", "BORDER01", "GOOD01", "GOOD02"]


def _widgets(example):
    meter = CutoffMeter()
    meter.set(0.7, 0.75, 20, "70.0% · 5.0 points short", True)
    ratio = RatioBar()
    ratio.set_counts(3, 8)
    strip = MetricStrip()
    strip.set_tiles(metric_tiles(example("BAD04"), S, 450))
    box = ReasonBox()
    box.set_reasons(reason_views(example("BAD04"), S))
    return [(Badge("pass", large=True), (80, 26)), (Badge("fail"), (60, 20)),
            (Chip("1 forward only", "warn"), (130, 22)),
            (Switch(True), (80, 20)), (Segmented(["All 11", "Pass 3", "Fail 8"]), (276, 28)), (meter, (208, 44)),
            (ratio, (140, 8)), (strip, (1100, 74)), (box, (1000, 120)), (Notice("Run cancelled", "neutral"), (400, 40))]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_widgets_paint(qapp, example, theme):
    manager().set_mode(theme)
    try:
        for widget, size in _widgets(example):
            assert image_varies(widget, size), type(widget).__name__
    finally:
        manager().set_mode("light")


def test_metric_strip_wraps(qapp, example):
    strip = MetricStrip()
    strip.set_tiles(metric_tiles(example("GOOD01"), S, 450))
    strip.show()
    strip.resize(1200, 74)
    assert strip.height() == 74
    strip.resize(900, 74)
    assert strip.height() == 148


def test_tooltips_of_custom_widgets(qapp, example, monkeypatch):
    """Hovering shows a tooltip without an error (a QHelpEvent has pos(), not position())."""
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QToolTip

    from sanger_seq_analyzer.gui.widgets.alignment_view import AlignmentView
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *a, **k: shown.append(a[1]))
    strip = MetricStrip()
    strip.set_tiles(metric_tiles(example("BAD04"), S, 450))
    strip.resize(1200, 74)
    strip.show()
    for rect in strip._rects():
        c = rect.center().toPoint()
        assert strip.event(QHelpEvent(QEvent.Type.ToolTip, c, strip.mapToGlobal(c)))
    assert len(shown) == sum(1 for t in strip.tiles if t.tooltip) > 0
    sr = example("BAD04")
    view = AlignmentView()
    view.set_data(alignment(sr, S), sr.consensus, S)
    view.resize(900, 200)
    view.show()
    view.viewportEvent(QHelpEvent(QEvent.Type.ToolTip, QPoint(120, 40), view.mapToGlobal(QPoint(120, 40))))
    track = ChromatogramTrack("F")
    track.set_sample(sr, alignment(sr, S), S)
    track.resize(900, 300)
    track.show()
    view_widget = track.view
    view_widget.viewportEvent(QHelpEvent(QEvent.Type.ToolTip, QPoint(200, 150),
                                         view_widget.mapToGlobal(QPoint(200, 150))))
    assert len(shown) > sum(1 for t in strip.tiles if t.tooltip)  # the trace tooltip was shown too


@pytest.mark.parametrize("name", NAMES)
def test_every_read_paints(qapp, example, name):
    sr = example(name)
    data = alignment(sr, S) if sr.consensus.positions else None
    for direction in ("F", "R"):
        track = ChromatogramTrack(direction)
        track.set_sample(sr, data, S)
        if track.chrom is None:
            assert (name, direction) == ("BAD07", "R") and track.missing
            assert image_varies(track, (900, 64))
            continue
        track.show()
        assert image_varies(track, (900, 220)), (name, direction)
        track.close()


def test_read_without_traces_paints(qapp, tmp_path, monkeypatch):
    import shutil
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "NT_F.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_R.ab1", tmp_path / "NT_R.ab1")
    result = run([tmp_path], "measles-n450")
    sr = result.samples[0]
    sr.forward_chrom.traces = {}
    track = ChromatogramTrack("F")
    track.set_sample(sr, alignment(sr, S), S)
    track.show()
    assert track.view.read.traces is None and "No trace data" in track.qc.problems.text()
    assert image_varies(track, (900, max(220, track.minimumHeight())))


def test_zoom_fit_and_scroll_ends(tool):
    detail = select(tool, "GOOD02")
    chrom = detail.chrom
    detail.goto(10)
    for step in (1, 1, 1, -1, -1, -1, -1, -1):
        chrom.zoom_step(step)
    assert chrom.zoom == 4
    chrom.fit_read()
    view = chrom.tracks["F"].view
    assert view.content_width() <= view.viewport().width() + 2  # the whole read fits
    chrom.zoom_step(1)
    sb = view.horizontalScrollBar()
    for value in (sb.maximum(), sb.minimum()):
        sb.setValue(value)
        left, right = view.x_of(0), view.x_of(view.read.n_points)
        assert left <= MARGIN + 1 and right >= view.viewport().width() - MARGIN - 1  # never scrolled past an end
    chrom.reset_zoom()
    assert chrom.zoom == 14 and chrom.tracks["F"].selected_column == 10


def test_reverse_complement_toggle(tool):
    detail = select(tool, "GOOD01")
    track = detail.chrom.tracks["R"]
    detail.goto(280)
    raw = track.selected_raw
    seq = track.chrom.sequence
    comp = {"A": "T", "C": "G", "G": "C", "T": "A"}
    track.view.scroll_to_sample(0, 0)
    assert track.first_visible_call() == comp[seq[-1]]
    track.set_reverse_complement(False)
    track.view.scroll_to_sample(0, 0)
    assert track.first_visible_call() == seq[0]
    assert track.selected_raw == raw and track.selected_column == 280
    track.set_reverse_complement(True)


def test_long_read_paints_fast(qapp, tmp_path):
    rng = np.random.default_rng(3)
    template = "".join(rng.choice(list("ACGT"), 1500))
    make_read(tmp_path / "L_F.ab1", "L", template, rng)
    make_read(tmp_path / "L_R.ab1", "L", template, rng)
    sr = run([tmp_path], template).samples[0]
    panel = ChromatogramPanel()
    panel.resize(1200, 420)
    panel.show()
    panel.set_sample(sr, alignment(sr, S), S)
    view = panel.tracks["F"].view
    QTest.qWait(1)
    timings = []
    for zoom in (14, 4):
        panel.set_zoom(zoom)
        start = time.perf_counter()
        view.viewport().repaint()
        timings.append(time.perf_counter() - start)
    assert max(timings) < 0.2, timings
