"""Fetch data through the web pages: shared folders and uploads, linking, colours, editing, saving
safely with several users, template versions."""

import io
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("openpyxl")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # fetch_builders

from fetch_builders import GENOTYPING_TSV, build  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from sanger_seq_analyzer.fetch import template  # noqa: E402

TOKEN_RE = re.compile(r'name="token" value="([^"]*)"')
NAMES_RE = re.compile(r'name="names" value="([^"]*)"')


@pytest.fixture
def lab(app, admin, tmp_path):
    """The lab share: the four files in one folder the server may write."""
    share = tmp_path / "share"
    files = build(share)
    admin.post("/admin/folders", data={"name": "Measles", "path": str(share), "writable": "1"})
    root = app.state.ctx.db.get_setting("roots")[0]["id"]
    for key in ("masterlist", "daily", "linelist", "template"):
        r = admin.post(f"/fetch/folder/{key}", data={"root": root, "path": ""})
        assert r.status_code == 303
    # the synthetic files are named differently from the lab's: rename to match the lab's patterns
    renames = {"daily": "Weekly update on measles-rubella results 09112026.xlsx",
               "linelist": "UAE_EMRO Measles Rubella Reporting Tool Sep 2026.xlsx"}
    for key, name in renames.items():
        files[key] = files[key].rename(share / name)
    return share, files


def fetched(user):
    r = user.post("/fetch/genotyping", data={"text": GENOTYPING_TSV})
    assert r.status_code == 303
    r = user.post("/fetch/run", data={"virus": "MVs"})
    assert r.status_code == 303
    return user.get("/fetch").text


def save(user):
    preview = user.post("/fetch/save/preview").text
    token = TOKEN_RE.search(preview).group(1)
    names = NAMES_RE.findall(preview)
    return user.post("/fetch/save", data={"token": token, "names": names}), names, preview


def test_sources_from_the_share_and_fetch(app, lab, make_user):
    analyst = make_user("analyst1", "analyst")
    page = analyst.get("/fetch").text
    assert "Measles and Rubella Positive Masterlist 2026.xlsx" in page and "2 sheets" in page
    assert "Weekly update on measles-rubella results 09112026.xlsx" in page
    page = fetched(analyst)
    assert "6 accessions" in page
    assert '<span class="badge b-PASS">1 pass</span>' in page and '<span class="badge b-FAIL">2 fail</span>' in page
    assert page.count('class="st-fail"') == 2 and page.count('class="st-pass"') == 1 and page.count('class="st-check"') == 3
    assert "MVs/Dubai.ARE/11.26/8" in page and "Al Ain" in page
    # a viewer has no Fetch data (patient data)
    assert make_user("viewer1", "viewer").get("/fetch").status_code == 403


def test_edit_cells_and_columns(app, lab, make_user):
    analyst = make_user("analyst1", "analyst")
    fetched(analyst)
    ws = app.state.ctx.services["fetch"].workspaces[app.state.ctx.accounts.by_name("analyst1").id]
    i = next(n for n, r in enumerate(ws.session.rows) if r.accession == "02SV269990004")
    r = analyst.post("/fetch/cell", data={"row": str(i), "col": "Admin Level 1", "value": "Sharjah"},
                     headers={"HX-Request": "true"})
    assert r.status_code == 200 and 'id="results"' in r.text and "MVs/Sharjah.ARE/11.26" in r.text
    r = analyst.post("/fetch/include", data={"row": str(i)}, headers={"HX-Request": "true"})  # untick
    assert not ws.session.rows[i].include
    analyst.post("/fetch/columns", data={"cols": ["Accession", "WHO Strain Name", "MRN"]})
    page = analyst.get("/fetch").text
    assert ws.session.columns == ["Accession", "WHO Strain Name", "MRN"]
    assert app.state.ctx.accounts.prefs(ws_user(app, "analyst1"))["fetch_columns"][-1] == "MRN"
    analyst.post("/fetch/columns", data={"reset": "1"})
    assert len(ws.session.columns) == 19 and page


def ws_user(app, name):
    return app.state.ctx.accounts.by_name(name).id


def test_save_to_the_shared_template_with_backup(app, lab, make_user):
    share, files = lab
    analyst = make_user("analyst1", "analyst")
    fetched(analyst)
    r, names, preview = save(analyst)
    assert "on the lab share (a backup copy is made first)" in preview
    assert r.status_code == 303
    page = analyst.get("/fetch").text
    assert "4 rows added to MeaNS Upload Template.xlsx" in page and "MeaNS backups" in page
    ws = load_workbook(files["template"]).active
    saved = {ws.cell(row=x, column=1).value for x in range(1, ws.max_row + 1)}
    assert set(names) <= saved
    assert list((share / "MeaNS backups").iterdir())
    from sanger_seq_analyzer.server import audit
    entry = audit.entries(app.state.ctx.db, "template.saved")[0]
    assert "02SV269990003" in entry["details"] and "Test Patient" not in entry["details"]


def test_two_users_saving_get_different_numbers(app, lab, make_user):
    a, b = make_user("analyst1", "analyst"), make_user("analyst2", "analyst")
    fetched(a)
    fetched(b)
    preview_a, preview_b = a.post("/fetch/save/preview").text, b.post("/fetch/save/preview").text
    names_a, names_b = NAMES_RE.findall(preview_a), NAMES_RE.findall(preview_b)
    assert names_a == names_b  # both previews numbered after the same template
    r = a.post("/fetch/save", data={"token": TOKEN_RE.search(preview_a).group(1), "names": names_a})
    assert r.status_code == 303
    # b confirms the old preview: nothing is written, the names are renumbered and shown again
    r = b.post("/fetch/save", data={"token": TOKEN_RE.search(preview_b).group(1), "names": names_b})
    assert r.status_code == 200 and "Someone saved to the template since you opened this" in r.text
    renamed = NAMES_RE.findall(r.text)
    # the same accessions are in the template already: they are skipped, never written twice
    r = b.post("/fetch/save", data={"token": TOKEN_RE.search(r.text).group(1), "names": renamed})
    assert r.status_code == 303
    assert "already" in b.get("/fetch").text


def test_excel_lock_and_saving_lock_block_the_save(app, lab, make_user):
    share, files = lab
    analyst = make_user("analyst1", "analyst")
    fetched(analyst)
    lock = share / ("~$" + files["template"].name)
    lock.write_text("osoliman")
    r, _names, _ = save(analyst)
    assert r.status_code == 200 and "is open in Excel" in r.text
    lock.unlink()
    with template.saving_lock(files["template"]):  # e.g. the desktop app saving right now
        r, _names, _ = save(analyst)
        assert "being saved by someone else" in r.text
    r, _names, _ = save(analyst)
    assert r.status_code == 303


def test_uploaded_sources_and_template_versions(app, make_user, tmp_path):
    files = build(tmp_path / "files")
    analyst = make_user("analyst1", "analyst")
    for key in ("masterlist", "daily", "linelist", "template"):
        r = analyst.post(f"/fetch/upload/{key}", files={"file": (files[key].name, files[key].read_bytes(), "x")})
        assert r.status_code == 303
    page = analyst.get("/fetch").text
    assert "uploaded by Analyst1" in page and "is now template version 1" in page or "server copy" in page
    fetched(analyst)
    r, names, _ = save(analyst)
    assert r.status_code == 303 and "new version" in analyst.get("/fetch").text
    svc = app.state.ctx.services["fetch"]
    v2 = svc.version_file(2)
    saved = {c.value for c in load_workbook(v2).active["A"]}
    assert set(names) <= saved
    d = analyst.get("/fetch/template/2")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    # an older download (version 1) misses the saved names: refused unless forced
    old = svc.version_file(1).read_bytes()
    r = analyst.post("/fetch/upload/template", files={"file": ("MeaNS Upload Template.xlsx", old, "x")})
    assert "does not have 4 strain names" in analyst.get("/fetch").text
    r = analyst.post("/fetch/upload/template", files={"file": ("MeaNS Upload Template.xlsx", old, "x")},
                     data={"force": "1"})
    assert "template version 3" in analyst.get("/fetch").text
    # not a spreadsheet
    analyst.post("/fetch/upload/masterlist", files={"file": ("x.exe", b"MZ", "x")})
    assert "choose an Excel workbook" in analyst.get("/fetch").text


def test_genotyping_from_a_run_and_from_a_file(app, make_user, tmp_path):
    analyst = make_user("analyst1", "analyst")
    from test_server_runs import ab1_files, start_run
    run_id, _ = start_run(analyst, ab1_files("bad"))
    r = analyst.post(f"/fetch/from-run/{run_id}")
    assert r.status_code == 303
    page = analyst.get("/fetch").text
    assert f"filled from run {run_id}" in page and "FAIL REINJECT REVERSE STRAND" in page
    analyst.post("/fetch/genotyping/clear")
    files = build(tmp_path / "f")
    r = analyst.post("/fetch/genotyping/file", files={"file": ("genotyping.tsv", files["genotyping"].read_bytes(), "x")})
    assert "6 accessions" in analyst.get("/fetch").text


def test_export_workbook(app, lab, make_user):
    analyst = make_user("analyst1", "analyst")
    fetched(analyst)
    r = analyst.post("/fetch/export")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["MeaNS", "All columns", "Matching"]
