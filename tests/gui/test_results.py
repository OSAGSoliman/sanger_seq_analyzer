"""Results page against the expected example results (AT-G08..AT-G12, AT-G21)."""

import pytest
from guihelpers import select
from PySide6.QtGui import QGuiApplication

EXPECTED = {
    # name: (badge, number of reasons, first reason title, length tile, marks tile)
    "GOOD01": ("PASS", 0, None, "450", "1"),
    "GOOD02": ("PASS", 0, None, "447", "4"),
    "BORDER01": ("PASS", 0, None, "450", "0"),
    "BAD01": ("FAIL", 2, "Forward read failed QC", "–", "–"),
    "BAD02": ("FAIL", 1, "Reverse read failed QC", "450", "450"),
    "BAD03": ("FAIL", 1, "Reverse read does not match the reference (no alignment)", "450", "450"),
    "BAD04": ("FAIL", 1, "Stop codon at codon 60", "450", "0"),
    "BAD05": ("FAIL", 1, "Bad deletion: 1 bp deletion at reference position 301 (frameshift)", "449", "1"),
    "BAD06": ("FAIL", 1, "Too many mixtures: 28 (6.2% > 3.5%)", "450", "28"),
    "BAD07": ("FAIL", 1, "Reverse read missing", "450", "450"),
    "BAD08": ("FAIL", 1, "113 marked positions (fail at >= 100)", "450", "113"),
}


def test_summary_counts(tool):
    s = tool.results.summary
    assert (s.passed.text(), s.failed.text(), s.total.text()) == ("3 PASS", "8 FAIL", "of 11 samples")
    assert "Reference measles-n450 · 450 bp · rules: defaults" in s.context.text()


@pytest.mark.parametrize("name", EXPECTED)
def test_sample_detail(tool, name):
    badge, n_reasons, title, length, marks = EXPECTED[name]
    detail = select(tool, name)
    assert detail.name.text() == name and detail.badge.text() == badge
    assert len(detail.reasons._views) == n_reasons
    if title:
        assert detail.reasons._views[0].title == title
    tiles = {t.label: t.value for t in detail.metrics.tiles}
    assert (tiles["Length"], tiles["Marks"]) == (length, marks)


def test_rule_checklist(tool):
    detail = select(tool, "GOOD01")
    model = detail.detail.rules_model
    assert all(model.index(r, 4).data() == "Pass" for r in range(model.rowCount()))
    detail = select(tool, "BAD04")
    failed = [(model.index(r, 0).data(), model.index(r, 2).data()) for r in range(model.rowCount())
              if model.index(r, 4).data() == "Fail"]
    assert failed == [("No stop codon", "codon 60")]


def test_filter_and_search(tool):
    panel = tool.results.list
    try:
        panel.segments.setIndex(2)
        assert len(panel.visible_names()) == 8
        panel.segments.setIndex(1)
        assert panel.visible_names() == ["BORDER01", "GOOD01", "GOOD02"]
        panel.segments.setIndex(0)
        panel.search.setText("BAD0")
        assert len(panel.visible_names()) == 8
        panel.search.setText("stop codon")
        assert panel.visible_names() == ["BAD04"]
        panel.search.setText("frameshift")
        assert panel.visible_names() == ["BAD05"]
        panel.search.setText("zzz")
        assert panel.stack.currentWidget() is panel.empty
    finally:
        panel.clear_filter()
    assert len(panel.visible_names()) == 11


def test_sort_by_marks(tool):
    panel = tool.results.list
    try:
        panel.sort.setCurrentIndex(2)
        names = panel.visible_names()
        assert names[0] == "BAD08" and names.index("BAD06") < names.index("GOOD02")
    finally:
        panel.sort.setCurrentIndex(0)
    assert panel.visible_names()[0] == "BAD01"


def test_flagged_marks_table(tool):
    detail = select(tool, "GOOD02").detail
    detail.flag_filter.setCurrentIndex(1)  # "Marked"
    proxy = detail.flagged_proxy
    refs = [proxy.index(r, 1).data() for r in range(proxy.rowCount())]
    assert refs == ["201", "202", "203", "401"]
    row_401 = refs.index("401")
    assert proxy.index(row_401, 2).data() == "R" and "mark area; low average quality" in proxy.index(row_401, 8).data()
    detail.flag_filter.setCurrentIndex(0)


def test_copy_consensus(tool):
    detail = select(tool, "GOOD01")
    detail.detail._copy_sequence()
    text = QGuiApplication.clipboard().text()
    assert text == detail.sr.consensus.sequence and len(text) == 450
    detail._copy_fasta()
    assert QGuiApplication.clipboard().text().startswith(">GOOD01 ref=1-450")


def test_copy_table(tool):
    tool.results.list.view.setFocus()
    text = tool.results.list.model.as_tsv()
    assert text.splitlines()[0].startswith("sample\tstatus") and len(text.splitlines()) == 12


def test_copy_reads_when_there_is_no_assembly(tool):
    d = select(tool, "BAD01").detail  # both reads too short to assemble
    d.switch.setIndex(3)
    assert not d.copy_seq.isEnabled() and d.copy_fasta.isEnabled() and d.copy_fasta.text() == "Copy reads FASTA"
    d._copy_fasta()
    text = QGuiApplication.clipboard().text()
    assert text.startswith(">BAD01_F forward read, trimmed, as sequenced") and "\n>BAD01_R reverse read" in text
    d = select(tool, "GOOD01").detail
    assert d.copy_seq.isEnabled() and d.copy_fasta.text() == "Copy FASTA"


def test_ctrl_c_and_right_click_copy_table_rows(tool):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from sanger_seq_analyzer.gui.widgets.table_copy import copy_menu
    view = select(tool, "GOOD01").detail.rules
    n = view.model().rowCount()
    view.selectAll()
    QGuiApplication.clipboard().setText("")
    QApplication.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier))
    lines = QGuiApplication.clipboard().text().splitlines()
    assert len(lines) == n and all(line.count("\t") == 4 for line in lines)  # 5 columns
    menu = copy_menu(view, view.visualRect(view.model().index(0, 0)).center())
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts == ["Copy", "Copy with headers", f"Copy all shown rows ({n}) with headers"]
    assert all(a.isEnabled() for a in menu.actions())
    menu.actions()[1].trigger()
    assert QGuiApplication.clipboard().text().upper().startswith("RULE\tTYPE\tOBSERVED\tLIMIT\tRESULT\n")
