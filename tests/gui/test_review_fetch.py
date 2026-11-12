"""Clear button, reviewer confirmation, export scope and the Fetch data page."""

from datetime import date

import pytest
from guihelpers import EXAMPLES, run_examples, select
from PySide6.QtWidgets import QMessageBox

from sanger_seq_analyzer.gui.qc.export_dialog import ExportDialog

pytest.importorskip("openpyxl")


def _yes(monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes)
    return asked


def test_clear_button_clears_inputs_results_and_reviews(window, monkeypatch):
    tool = window.tools[0]
    setup = tool.run_page.setup
    assert not setup.clear_button.isEnabled()
    run_examples(window, [EXAMPLES / "good"])
    c = tool.controller
    c.set_review("GOOD01", "FAIL")
    assert setup.clear_button.isEnabled()
    # the Clear button sits right after Run analysis
    footer = setup.run_button.parentWidget().layout()
    assert footer.indexOf(setup.clear_button) == footer.indexOf(setup.run_button) + 1
    asked = _yes(monkeypatch)
    setup.clear_button.click()
    assert "review of 1 sample" in asked[0][2]
    assert c.inputs == [] and c.result is None and c.reviews == {}
    assert not window.tabs["results"].isEnabled() and not setup.clear_button.isEnabled()


def test_review_overrides_status_and_export_scopes(window, tmp_path, monkeypatch):
    run_examples(window, [EXAMPLES])
    tool = window.tools[0]
    c = tool.controller
    detail = select(tool, "BAD02")
    assert "Not reviewed" in detail.review_state.text()
    detail.confirm_pass.click()  # override FAIL -> PASS
    assert c.reviews["BAD02"] == "PASS" and "Changed to PASS" in detail.review_state.text()
    assert "1 of 11 reviewed" in tool.results.summary.reviewed.text()
    assert tool.results.list.segments.labels()[1] == "Pass 4"
    select(tool, "GOOD01").confirm_pass.click()
    assert "Confirmed PASS" in tool.results.detail.review_state.text()

    _yes(monkeypatch)
    tool.results.confirm_all()
    assert len(c.reviews) == 11

    dialog = ExportDialog(c, window)
    dialog.folder.setFullText(str(tmp_path / "pass"))
    dialog.set_scope("pass")
    assert dialog.sample_names() == ["BAD02", "BORDER01", "GOOD01", "GOOD02"]
    assert dialog.export(confirm=False)
    summary = (tmp_path / "pass" / "sample_summary.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(summary) == 5 and summary[0].endswith("automated_status,reviewed")
    bad02 = next(line for line in summary if line.startswith("BAD02"))
    assert bad02.startswith("BAD02,PASS") and bad02.endswith("FAIL,yes")
    assert "reviewer changed the status from FAIL to PASS" in bad02

    dialog.folder.setFullText(str(tmp_path / "fail"))
    dialog.set_scope("fail")
    assert dialog.export(confirm=False)
    assert (tmp_path / "fail" / "consensus.fasta").read_text(encoding="utf-8") == ""
    assert (tmp_path / "fail" / "sample_summary.csv").read_text(encoding="utf-8-sig").count("\n") == 8

    dialog.folder.setFullText(str(tmp_path / "chosen"))
    dialog.set_scope("selected")
    assert not dialog.export_button.isEnabled()  # nothing ticked yet
    dialog.set_chosen(["GOOD02", "BAD05"])
    assert dialog.export(confirm=False)
    html = (tmp_path / "chosen" / "report.html").read_text(encoding="utf-8")
    assert "GOOD02" in html and "BAD05" in html and "GOOD01" not in html
    dialog.close()


def test_report_filter_hides_sample_sections(results_window, tmp_path):
    c = results_window.tools[0].controller
    c.export(tmp_path, ["html"])
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert 'class="card sample"' in html and 'data-status="FAIL" aria-label="BAD01"' in html
    assert 'data-filter="PASS"' in html and "sec.style.display" in html


def test_files_named_216_and_214_pair(window, tmp_path):
    """216 = MeV216 forward, 214 = MeV214 reverse; the pair passes (reads on the expected strands)."""
    import shutil
    for name, target in (("GOOD01_F", "02SV260016292_216"), ("GOOD01_R", "02SV260016292_214")):
        shutil.copy(EXAMPLES / "good" / f"{name}.ab1", tmp_path / f"{target}.ab1")
    c = window.tools[0].controller
    c.set_inputs([tmp_path])
    c.rescan_now()
    assert list(c.scan_result.pairs) == ["02SV260016292"]
    assert c.scan_result.pairs["02SV260016292"]["F"].name == "02SV260016292_216.ab1"
    assert c.scan_result.pairs["02SV260016292"]["R"].name == "02SV260016292_214.ab1"
    run_examples(window, [tmp_path])
    assert c.result.samples[0].consensus.status == "PASS", c.result.samples[0].consensus.reasons


@pytest.mark.parametrize("old", [(["F", "FWD", "FOR", "FORWARD"], ["R", "REV", "REVERSE"]),
                                 (["F", "FWD", "FOR", "FORWARD", "214", "MeV214"],
                                  ["R", "REV", "REVERSE", "216", "MeV216"])])  # 0.4.0 had them swapped
def test_stored_old_default_tags_are_upgraded(qapp, tmp_path, old):
    from guihelpers import close_window, make_window

    from sanger_seq_analyzer.gui.persist import Store
    store = Store(tmp_path / "s.ini")
    store.set("qc/forward_tags", old[0])
    store.set("qc/reverse_tags", old[1])
    store.sync()
    w = make_window(tmp_path / "s.ini")
    c = w.tools[0].controller
    assert "216" in c.forward_tags and "214" in c.reverse_tags
    close_window(w, qapp)


def test_fetch_page_links_files_and_saves_to_template(window, tmp_path, monkeypatch):
    from fetch_builders import GENOTYPING_TSV, build
    from openpyxl import load_workbook
    files = build(tmp_path / "files")
    tool = window.tools[0]
    page, fc = tool.fetch, tool.fetch_controller
    window.show_page("fetch")
    assert window.current_page() == "fetch"
    assert [t.text() for t in window.tabs.values()] == ["Run", "Results", "Fetch data", "Settings"]
    assert page.sources_notice.isVisibleTo(page)  # first visit: asks for the files
    for key in ("masterlist", "daily", "linelist", "template"):
        fc.set_path(key, str(files[key]))
    fc.load_sources(block=True)
    assert not page.sources_notice.isVisibleTo(page)
    assert "2 sheets" in page.source_status["daily"].text()

    page.paste(GENOTYPING_TSV)
    assert page.geno.rowCount() == 6
    assert page.fetch_now() == ""
    assert page.results_frame.isVisibleTo(page)
    assert page.means.columnCount() == 1 + 19
    names = {fc.rows[r].accession: page.means.item(r, 1).text() for r in range(page.means.rowCount())}
    assert names["02SV269990003"] == "MVs/Dubai.ARE/11.26/8"
    assert page.matching.rowCount() == 6 and page.allcols.columnCount() > 60

    # editing Admin Level 1 renames the strain
    r4 = next(i for i, r in enumerate(fc.rows) if r.accession == "02SV269990004")
    admin = 1 + fc.columns.index("Admin Level 1")
    page.means.item(r4, admin).setText("Sharjah")
    assert fc.rows[r4].values["WHO Strain Name"] == "MVs/Sharjah.ARE/11.26"

    # export the table, then save the ticked rows to the template
    out = fc.export_workbook(tmp_path / "rows.xlsx")
    assert load_workbook(out).sheetnames == ["MeaNS", "All columns", "Matching"]
    _yes(monkeypatch)
    assert page.save()
    assert "4 rows added" in page.save_notice.text() and "backup" in page.save_notice.text()
    ws = load_workbook(files["template"]).active
    saved = [ws.cell(row=r, column=1).value for r in range(9, 13)]
    assert "MVs/Sharjah.ARE/11.26" in saved and "MVs/Dubai.ARE/11.26/8" in saved
    assert not fc.rows_to_save()  # saved rows are unticked
    # a later fetch continues after the saved names
    page.fetch_now()
    by = {r.accession: r for r in fc.rows}
    assert by["02SV269990003"].values["WHO Strain Name"] == "MVs/Dubai.ARE/11.26/9"


def test_fetch_page_file_boxes_filter_and_newer_file(window, tmp_path):
    import os
    import time

    from fetch_builders import GENOTYPING_TSV, build
    files = build(tmp_path / "files")
    tool = window.tools[0]
    page, fc = tool.fetch, tool.fetch_controller
    # the boxes show the lab's file names before anything is chosen
    assert page.fields["masterlist"].placeholderText() == "Measles and Rubella Positive Masterlist 2026"
    assert page.fields["daily"].placeholderText() == "Weekly update on measles-rubella results 09112026"
    assert page.fields["linelist"].placeholderText() == "UAE_EMRO Measles Rubella Reporting Tool Sep 2026"
    assert page.fields["template"].placeholderText() == "MeaNS Upload Template 2025-2026"
    assert "*reporting tool*.xlsb" in fc.name_filter("linelist")
    # next week's daily sheet in the same folder is offered
    old = tmp_path / "files" / "Weekly update on measles-rubella results 02112026.xlsx"
    os.replace(files["daily"], old)
    for key in ("masterlist", "linelist", "template"):
        fc.set_path(key, str(files[key]))
    fc.set_path("daily", str(old))
    fc.load_sources(block=True)
    assert not page.newer["daily"].isVisibleTo(page)
    new = tmp_path / "files" / "Weekly update on measles-rubella results 09112026.xlsx"
    new.write_bytes(old.read_bytes())
    later = time.time() + 60
    os.utime(new, (later, later))
    page.refresh_sources()
    assert page.newer["daily"].isVisibleTo(page) and new.name in page.newer["daily"].text()
    page.use_newer("daily")
    assert fc.paths["daily"] == str(new)
    from guihelpers import wait_until
    wait_until(lambda: not fc.loading)  # the new file is read on a worker thread
    assert fc.tables["daily"] is not None and not page.newer["daily"].isVisibleTo(page)

    page.paste(GENOTYPING_TSV)
    assert page.geno.rowCount() == 6  # the MeV rows are not shown
    page.fetch_now()
    assert page.row_filter.labels() == ["All 6", "To check 3", "Not in every file 1", "Ticked to save 4"]

    def shown(table):
        return [table.item(r, 0).text() for r in range(table.rowCount()) if not table.isRowHidden(r)]
    page.row_filter.setIndex(2)
    assert shown(page.matching) == ["02SV269990006"] and len(shown(page.means)) == 1 and len(shown(page.allcols)) == 1
    assert page.shown_label.text() == "1 of 6 shown"
    page.row_filter.setIndex(1)
    assert len(shown(page.matching)) == 3
    page.row_filter.setIndex(0)
    page.row_search.setText("100004")  # an MRN
    assert shown(page.matching) == ["02SV269990004"]
    page.row_search.clear()
    assert len(shown(page.matching)) == 6


def test_fetch_from_qc_results(window, tmp_path):
    run_examples(window, [EXAMPLES / "bad"])
    tool = window.tools[0]
    tool.fetch.use_run()
    cols, rows = tool.fetch.geno.rows()
    comments = {r["Accession"]: r["COMMENTS"] for r in rows}
    assert comments["BAD02"] == "FAIL REINJECT REVERSE STRAND"
    assert tool.fetch.fetch_now().startswith("The masterlist is needed")


def test_fetch_page_reports_unreadable_files(window, tmp_path):
    bad = tmp_path / "broken.xlsx"
    bad.write_text("not a workbook")
    fc = window.tools[0].fetch_controller
    fc.set_path("masterlist", str(bad))
    fc.load_sources(block=True)
    assert "could not be opened" in fc.errors["masterlist"]
    assert "could not be opened" in window.tools[0].fetch.source_status["masterlist"].text()
    assert date.today()  # keep the import used


def test_fetch_button_reads_files_on_a_worker_thread(window, tmp_path):
    from fetch_builders import GENOTYPING_TSV, build
    from guihelpers import wait_until
    files = build(tmp_path / "files")
    tool = window.tools[0]
    page, fc = tool.fetch, tool.fetch_controller
    for key in ("masterlist", "daily", "linelist", "template"):
        fc.set_path(key, str(files[key]))
    page.paste(GENOTYPING_TSV)
    fc.load_sources()  # as when the page opens
    assert not page.fetch_button.isEnabled() and page.loading_label.text() == "Reading files…"
    page.fetch()  # e.g. Enter pressed while that read is still running: fetches once it is done
    wait_until(lambda: not fc.loading and bool(fc.rows))
    assert page.fetch_button.isEnabled()
    assert len(fc.rows) == 6 and page.results_frame.isVisibleTo(page)
