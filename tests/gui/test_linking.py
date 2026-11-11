"""Alignment <-> chromatogram linking and keyboard navigation (AT-G15, AT-G16, AT-G17, AT-G27)."""

from guihelpers import column_of, select
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest


def _visible(track, raw):
    view = track.view
    r = view.read
    x = view.x_of(r.peaks[r.raw_to_disp(raw)])
    return 0 <= x <= view.viewport().width()


def test_alignment_selects_the_base_in_both_reads(tool):
    detail = select(tool, "GOOD02")
    detail.goto(column_of(tool, 204))
    f, r = detail.chrom.tracks["F"], detail.chrom.tracks["R"]
    assert f.selected_raw == 371 and int(f.chrom.peak_locations[371]) == 4464
    assert r.selected_raw == 588 and int(r.chrom.peak_locations[588]) == 7068
    assert _visible(f, 371) and _visible(r, 588)
    assert detail.chrom.chip.text().startswith("Ref 204")


def test_click_in_a_trace_selects_the_column(tool):
    detail = select(tool, "GOOD02")
    view = detail.chrom.tracks["F"].view
    view.scroll_to_sample(4452)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(view.x_of(4452)), view.viewport().height() // 2))
    column = detail.selection.column
    assert tool.controller.alignment_for(detail.sr).columns[column].ref_number == 200
    assert detail.detail.alignment.selected == column


def test_deletion_shows_a_gap_marker(tool):
    detail = select(tool, "GOOD02")
    detail.goto(column_of(tool, 201))
    f = detail.chrom.tracks["F"]
    lo, hi = f.view.selection
    peaks = f.view.read.peaks
    assert peaks[370] < (lo + hi) / 2 < peaks[371]
    assert detail.chrom.tracks["R"].view.selection is not None


def test_keyboard_navigation(tool):
    results = tool.results
    view = results.list.view
    select(tool, "BAD01")
    view.setFocus()
    before = view.currentIndex().row()
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert view.currentIndex().row() == before + 1 and results.detail.sr.sample == "BAD02"
    detail = select(tool, "GOOD02")
    alignment = detail.detail.alignment
    detail.goto(10)
    QTest.keyClick(alignment, Qt.Key.Key_Right)
    assert detail.selection.column == 11
    QTest.keyClick(alignment, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert detail.selection.column == 21
    QTest.keyClick(alignment, Qt.Key.Key_BracketRight)
    data = tool.controller.alignment_for(detail.sr)
    assert detail.selection.column == next(c for c in data.flagged if c > 21)
    assert "Flagged" in detail.detail.step_label.text()
