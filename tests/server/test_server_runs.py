"""QC runs through the web pages: upload, preview, run, review (two users), export, viewer pages."""

import io
import re
import zipfile
from pathlib import Path

import pytest

from sanger_seq_analyzer.pipeline import run as pipeline_run

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TOKEN_RE = re.compile(r'name="draft" value="([^"]+)"')


def ab1_files(*folders):
    files = []
    for folder in folders:
        for p in sorted((EXAMPLES / folder).glob("*.ab1")):
            files.append(("files", (p.name, p.read_bytes(), "application/octet-stream")))
    return files


def start_run(user, files, name="Plate 1", **fields):
    r = user.post("/runs/draft", files=files)
    assert r.status_code == 200, r.text
    m = TOKEN_RE.search(r.text)
    assert m, r.text
    data = {"draft": m.group(1), "name": name, "reference": "measles-n450", **fields}
    r = user.post("/runs", data=data, headers={"HX-Request": "true"})
    assert r.status_code == 204, r.text
    return int(r.headers["HX-Redirect"].rsplit("/", 1)[1]), m.group(1)


@pytest.fixture
def analyst(make_user):
    return make_user("analyst1", "analyst")


@pytest.fixture
def finished_run(app, analyst):
    run_id, _ = start_run(analyst, ab1_files("good", "bad"))
    return run_id


def samples_of(app, run_id):
    store = app.state.ctx.services["runs"]
    return {s["sample"]: s for s in store.samples(run_id)}


def test_upload_preview_and_run(app, analyst):
    r = analyst.post("/runs/draft", files=ab1_files("good", "bad") + [("files", ("notes.txt", b"x", "text/plain")),
                                                                        ("files", ("fake_216.ab1", b"nope", "x"))])
    assert "11</b> samples" in r.text and "10 with both reads" in r.text
    assert "notes.txt: not an .ab1 file" in r.text and "fake_216.ab1: not a trace file" in r.text
    assert "Only one read: BAD07" in r.text
    run_id, token = start_run(analyst, ab1_files("good", "bad"))
    store = app.state.ctx.services["runs"]
    row = store.get(run_id)
    assert row["status"] == "done" and row["n_samples"] == 11
    expected = pipeline_run([EXAMPLES], "measles-n450")
    got = samples_of(app, run_id)
    assert {s.sample: s.consensus.status for s in expected.samples} == {k: v["status"] for k, v in got.items()}
    out = store.output(run_id)
    assert (out / "consensus.fasta").read_text().count(">") == 3 and (out / "report.html").is_file()
    page = analyst.get(f"/runs/{run_id}")
    assert "GOOD01" in page.text and "0 of 11 reviewed" in page.text
    assert analyst.get("/runs").text.count(f'href="/runs/{run_id}"') == 1
    # the draft is gone once used
    r = analyst.post("/runs", data={"draft": token, "reference": "measles-n450"})
    assert r.status_code == 400


def test_sample_viewer_is_sandboxed(app, analyst, finished_run):
    idx = samples_of(app, finished_run)["GOOD01"]["idx"]
    r = analyst.get(f"/runs/{finished_run}/samples/{idx}")
    assert 'sandbox="allow-scripts"' in r.text and "Confirm PASS" in r.text
    v = analyst.get(f"/runs/{finished_run}/samples/{idx}/view")
    assert v.status_code == 200 and "GOOD01" in v.text and 'id="summary"' not in v.text
    csp = v.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'sha256-" in csp
    full = analyst.get(f"/runs/{finished_run}/report")
    assert "sandbox allow-scripts" in full.headers["content-security-policy"] and full.text.count('class="card sample"') == 11


def test_reviews_conflicts_and_confirm_remaining(app, analyst, make_user, finished_run):
    other = make_user("analyst2", "analyst")
    idx = samples_of(app, finished_run)["BORDER01"]["idx"]
    url = f"/runs/{finished_run}/samples/{idx}/review"
    r = analyst.post(url, data={"decision": "FAIL", "expected": "0"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "✓ FAIL" in r.text and r.headers.get("HX-Trigger") == "sample-reviewed"
    # the second reviewer's page still showed "not reviewed": refused, and told who changed it
    r = other.post(url, data={"decision": "PASS", "expected": "0"}, headers={"HX-Request": "true"})
    assert r.status_code == 409 and "Someone else changed this sample" in r.text and "Analyst1" in r.text
    store = app.state.ctx.services["runs"]
    latest = store.reviews(finished_run)[idx]
    r = other.post(url, data={"decision": "PASS", "expected": str(latest["id"])}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and store.decisions(finished_run)["BORDER01"] == "PASS"
    # confirm the rest with the rules' status
    analyst.post(f"/runs/{finished_run}/confirm-remaining")
    decisions = store.decisions(finished_run)
    assert len(decisions) == 11 and decisions["BORDER01"] == "PASS" and decisions["BAD01"] == "FAIL"
    assert "11 of 11 reviewed" in analyst.get(f"/runs/{finished_run}").text
    actions = [e["action"] for e in __import__("sanger_seq_analyzer.server.audit", fromlist=["x"]).entries(
        app.state.ctx.db, "review")]
    assert actions.count("review.set") == 2 and "review.confirm_remaining" in actions


def test_export_applies_reviews(app, analyst, make_user, finished_run):
    viewer = make_user("viewer1", "viewer")
    idx = samples_of(app, finished_run)["GOOD01"]["idx"]
    analyst.post(f"/runs/{finished_run}/samples/{idx}/review", data={"decision": "FAIL", "expected": "0"})
    r = viewer.post(f"/runs/{finished_run}/export", data={"scope": "pass", "files": ["consensus", "samples_csv"]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["consensus.fasta", "sample_summary.csv"]
    fasta = z.read("consensus.fasta").decode()
    assert fasta.count(">") == 2 and "GOOD01" not in fasta  # confirmed FAIL by the reviewer
    r = viewer.post(f"/runs/{finished_run}/export", data={"scope": "selected", "chosen": ["GOOD02"],
                                                          "files": ["consensus"]})
    assert zipfile.ZipFile(io.BytesIO(r.content)).read("consensus.fasta").decode().count(">") == 1
    # viewers look and download, but do not start runs or review
    assert viewer.post("/runs/draft", files=ab1_files("good")).status_code == 403
    assert viewer.post(f"/runs/{finished_run}/samples/{idx}/review", data={"decision": "PASS"}).status_code == 403


def test_run_from_a_shared_folder(app, admin, analyst, tmp_path):
    share = tmp_path / "share" / "2026-11-12 plate"
    share.mkdir(parents=True)
    for p in (EXAMPLES / "good").glob("*.ab1"):
        (share / p.name.replace("_F", "_216").replace("_R", "_214")).write_bytes(p.read_bytes())
    admin.post("/admin/folders", data={"name": "Sequencer", "path": str(tmp_path / "share")})
    root = app.state.ctx.db.get_setting("roots")[0]["id"]
    listing = analyst.get(f"/runs/browse?root={root}&path=")
    assert "2026-11-12 plate" in listing.text
    assert "outside" in analyst.get(f"/runs/browse?root={root}&path=../..").text or \
        "not allowed" in analyst.get(f"/runs/browse?root={root}&path=../..").text
    r = analyst.post("/runs/draft", data={"root": root, "path": "2026-11-12 plate"})
    assert "3</b> samples" in r.text and "3 with both reads" in r.text
    token = TOKEN_RE.search(r.text).group(1)
    r = analyst.post("/runs", data={"draft": token, "reference": "measles-n450"}, headers={"HX-Request": "true"})
    run_id = int(r.headers["HX-Redirect"].rsplit("/", 1)[1])
    assert {s["status"] for s in samples_of(app, run_id).values()} == {"PASS"}


def test_settings_reference_upload_and_lab_defaults(app, admin, analyst):
    # a rule changed for one run; an uploaded reference FASTA
    from sanger_seq_analyzer.references import REFERENCES
    fasta = ">myref N450 copy\n" + REFERENCES["measles-n450"].sequence + "\n"
    r = analyst.post("/runs/draft", files=ab1_files("good"))
    token = TOKEN_RE.search(r.text).group(1)
    r = analyst.post("/runs", data={"draft": token, "reference": "upload", "s_min_good_fraction": "99"},
                     files={"reference_file": ("myref.fasta", fasta.encode(), "text/plain")},
                     headers={"HX-Request": "true"})
    assert r.status_code == 204, r.text
    run_id = int(r.headers["HX-Redirect"].rsplit("/", 1)[1])
    store = app.state.ctx.services["runs"]
    import json
    config = json.loads(store.get(run_id)["config"])
    assert config["reference"]["name"] == "myref" and config["settings"]["min_good_fraction"] == 0.99
    assert config["settings"]["call_mixtures"] is True  # unchanged booleans keep the default
    # a bad value is refused with the reason
    r = analyst.post("/runs/draft", files=ab1_files("good"))
    token = TOKEN_RE.search(r.text).group(1)
    r = analyst.post("/runs", data={"draft": token, "reference": "measles-n450", "s_min_quality": "500"})
    assert r.status_code == 400 and "Minimum base quality" in r.text
    # lab defaults
    assert analyst.get("/admin/defaults").status_code == 403
    r = admin.post("/admin/defaults", data={"reference": "measles-n450", "forward_tags": "216, F",
                                            "reverse_tags": "214, R", "s_min_good_fraction": "80"})
    assert "Saved" in r.text
    from sanger_seq_analyzer.server.runs import lab_defaults
    d = lab_defaults(app.state.ctx.db)
    assert d.settings.min_good_fraction == 0.8 and d.forward_tags == ("216", "F")
    draft = analyst.post("/runs/draft", files=ab1_files("good")).text  # the next run starts from them
    assert 'name="s_min_good_fraction" value="80.0"' in draft and 'value="216, F"' in draft


def test_cancel_and_delete(app, admin, analyst, finished_run):
    assert analyst.post(f"/runs/{finished_run}/delete").status_code == 403
    r = admin.post(f"/runs/{finished_run}/delete")
    assert r.status_code == 303
    assert admin.get(f"/runs/{finished_run}").status_code == 404
    assert not app.state.ctx.services["runs"].folder(finished_run).exists()


def test_queued_runs_resume_after_a_restart(app, analyst, cfg):
    from conftest import InlineExecutor

    from sanger_seq_analyzer.server.app import create_app
    store = app.state.ctx.services["runs"]

    class Later:  # accepts jobs but never runs them (the server stops first)
        def submit(self, fn, *a, **k):
            pass

    app.state.ctx.services["jobs"].executor = Later()
    run_id, _ = start_run(analyst, ab1_files("good"))
    assert store.get(run_id)["status"] == "queued"
    from fastapi.testclient import TestClient
    again = create_app(cfg, executor=InlineExecutor())
    with TestClient(again):  # startup resumes queued runs
        pass
    assert again.state.ctx.services["runs"].get(run_id)["status"] == "done"
