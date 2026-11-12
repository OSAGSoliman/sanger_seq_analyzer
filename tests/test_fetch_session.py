"""The Qt-free Fetch data session, the template saving lock and the one-sample report page."""

import os
import time

import pytest

pytest.importorskip("openpyxl")

from fetch_builders import GENOTYPING_TSV, build  # noqa: E402

from sanger_seq_analyzer.fetch import sources, tables, template  # noqa: E402
from sanger_seq_analyzer.fetch.session import FetchSession  # noqa: E402


@pytest.fixture
def session(tmp_path):
    files = build(tmp_path / "files")
    s = FetchSession()
    for key in sources.SPECS:
        assert s.set_path(key, str(files[key]))
    s.load_sources()
    assert all(s.tables[k] is not None for k in sources.SPECS), s.errors
    assert s.set_genotyping_text(GENOTYPING_TSV)
    assert s.fetch() == ""
    return s, files


def test_session_links_edits_and_saves_without_qt(session):
    s, files = session
    assert len(s.rows) == 6 and len(s.rows_to_save()) == 4
    by = {r.accession: i for i, r in enumerate(s.rows)}
    assert s.rows[by["02SV269990003"]].values["WHO Strain Name"] == "MVs/Dubai.ARE/11.26/8"
    # editing a name input renumbers; editing the genotype does not
    assert s.edit_cell(by["02SV269990004"], "Admin Level 1", "Sharjah")
    assert s.rows[by["02SV269990004"]].values["WHO Strain Name"] == "MVs/Sharjah.ARE/11.26"
    assert not s.edit_cell(by["02SV269990004"], "Genotype", "D8")
    assert not s.edit_cell(by["02SV269990004"], "not a MeaNS column", "x")
    result = s.save_to_template()
    assert result.written == 4 and not s.rows_to_save()
    assert "MVs/Sharjah.ARE/11.26" in s.existing_names()  # the template was read again
    # nothing left to save
    with pytest.raises(tables.TableError, match="no rows are ticked"):
        s.save_to_template()
    assert len(s.workbook_sheets()) == 3


def test_virus_switch_renames(session):
    s, _files = session
    assert s.set_virus("RVs") and not s.set_virus("RVs") and not s.set_virus("XYZ")
    assert all(str(r.values["WHO Strain Name"]).startswith("RVs/") for r in s.rows_to_save())


def test_saving_lock_blocks_a_second_writer_and_clears_stale_locks(session):
    s, files = session
    path = files["template"]
    lock = template.saving_lock_path(path)
    with template.saving_lock(path):
        assert lock.is_file()
        with pytest.raises(tables.TableError, match="being saved by someone else"):
            s.save_to_template()
    assert not lock.exists()
    # a lock left behind by a crash is ignored after two minutes
    lock.write_text("crashed-pc 2026-01-01 00:00:00")
    old = time.time() - template.STALE_LOCK_SECONDS - 5
    os.utime(lock, (old, old))
    assert s.save_to_template().written == 4
    assert not lock.exists()


def test_backups_never_overwrite_each_other(tmp_path):
    folder = tmp_path / template.BACKUP_FOLDER
    folder.mkdir()
    path = tmp_path / "MeaNS Upload Template.xlsx"
    first = template._backup_name(folder, path)
    first.write_text("x")
    second = template._backup_name(folder, path)
    assert second != first and second.name.endswith(" (2).xlsx")


def test_newest_file_in_folder(tmp_path):
    a = tmp_path / "Weekly update on measles-rubella results 02112026.xlsx"
    b = tmp_path / "Weekly update on measles-rubella results 09112026.xlsx"
    for f, t in ((a, 1000), (b, 2000)):
        f.write_text("x")
        os.utime(f, (t, t))
    (tmp_path / "~$Weekly update on measles-rubella results 09112026.xlsx").write_text("lock")
    (tmp_path / "notes.xlsx").write_text("x")
    assert sources.newest_in_folder("daily", tmp_path) == b
    assert sources.newer_file("daily", a) == b and sources.newer_file("daily", b) is None
    assert sources.is_candidate("linelist", "UAE_EMRO Measles Rubella Reporting Tool Sep 2026.xlsb")


def test_render_sample_is_one_section(examples_run):
    from sanger_seq_analyzer.report_html import render_html, render_sample
    page = render_sample(examples_run, "GOOD01")
    assert page.count('class="card sample"') == 1 and 'id="summary"' not in page
    assert "<title>GOOD01 · Sanger QC</title>" in page and 'id="trace-data"' in page
    assert render_html(examples_run).count('class="card sample"') == len(examples_run.samples)
    with pytest.raises(KeyError):
        render_sample(examples_run, "NOPE")
