"""Batch robustness: unreadable files, analysis errors, pairing, tags, cancel and cached re-runs (B7, B10-B13)."""

import shutil
import time

import numpy as np
import pytest
from builders import EXAMPLES, REF, readqc

from sanger_seq_analyzer import pipeline
from sanger_seq_analyzer.abi import read_ab1
from sanger_seq_analyzer.pipeline import ChromCache, run, scan, split_name, validate_tags
from sanger_seq_analyzer.qc import QCSettings, qc_read
from sanger_seq_analyzer.recall import call_sample
from sanger_seq_analyzer.simulate import quality_profile, simulate_read, write_ab1


def _good_pair(folder, name):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", folder / f"{name}_F.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_R.ab1", folder / f"{name}_R.ab1")


# --- B7: unreadable file -----------------------------------------------------------------------------------

@pytest.mark.parametrize("both", [True, False])
def test_unreadable_reverse_always_fails(tmp_path, both):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "UNR_F.ab1")
    (tmp_path / "UNR_R.ab1").write_bytes(b"this is not an ab1 file")
    st = QCSettings(require_both_directions=both, max_marks=10**6, max_single_coverage_run=10**6)
    res = run([tmp_path], REF, st)
    c = res.samples[0].consensus
    assert c.status == "FAIL" and "reverse read unreadable (UNR_R.ab1)" in c.reasons, c.reasons
    assert str(tmp_path / "UNR_R.ab1") in res.errors
    assert res.samples[0].reverse_path == tmp_path / "UNR_R.ab1"


# --- B10: an exception for one sample never aborts the batch -------------------------------------------

def test_all_n_read_does_not_raise():
    rq = readqc("N" * 200)
    c = call_sample("n", {"F": rq, "R": rq}, REF)
    assert c.status == "FAIL"
    assert "forward read does not match the reference (no alignment)" in c.reasons


def test_analysis_error_is_recorded_and_batch_continues(tmp_path, monkeypatch):
    _good_pair(tmp_path, "A1")
    _good_pair(tmp_path, "B1")
    real = pipeline.call_sample

    def boom(name, *a, **kw):
        if name == "A1":
            raise RuntimeError("synthetic failure")
        return real(name, *a, **kw)

    monkeypatch.setattr(pipeline, "call_sample", boom)
    res = run([tmp_path], REF)
    a, b = res.samples
    assert a.consensus.status == "FAIL" and a.consensus.reasons == ["analysis error: RuntimeError: synthetic failure"]
    assert "Traceback" in a.error and a.consensus.error
    assert b.consensus.status == "PASS"
    assert any("synthetic failure" in v for v in res.errors.values())


def test_corrupt_files_do_not_stop_the_batch(tmp_path):
    _good_pair(tmp_path, "OK")
    (tmp_path / "BAD_F.ab1").write_bytes(b"ABIF garbage")
    (tmp_path / "BAD_R.ab1").write_bytes(b"\x00" * 10)
    res = run([tmp_path], REF)
    status = {s.sample: s.consensus.status for s in res.samples}
    assert status == {"BAD": "FAIL", "OK": "PASS"}


# --- B13: files without quality values ----------------------------------------------------------------

def test_file_without_qualities_has_a_clear_reason(tmp_path):
    rng = np.random.default_rng(2)
    calls, q, peaks, traces = simulate_read("ACGT" * 100, quality_profile(400, rng), rng)
    write_ab1(tmp_path / "NOQ_F.ab1", "NOQ", calls, [], peaks, traces)
    chrom = read_ab1(tmp_path / "NOQ_F.ab1")
    assert not chrom.has_qualities
    assert qc_read(chrom).reasons == ["file has no quality values"]


def test_traces_are_int32():
    chrom = read_ab1(EXAMPLES / "good/GOOD01_F.ab1")
    assert all(chrom.traces[b].dtype == np.int32 for b in "ACGT")


# --- B11: pairing --------------------------------------------------------------------------------------

def test_scan_reports_a_reason_for_every_file(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for n in ["S1_F_A01.ab1", "S1_F_B02.ab1", "S1_R_A02.ab1", "a/X_F.ab1", "b/X_F.ab1", "a/X_R.ab1",
              "._S1_F_A01.ab1", "notag.ab1", "S2_F.txt"]:
        (tmp_path / n).write_bytes(b"x")
    sr = scan([tmp_path, tmp_path / "S1_R_A02.ab1", tmp_path / "S2_F.txt", tmp_path / "nope"])
    assert set(sr.pairs) == {"S1", "X"}
    assert sr.pairs["S1"] == {"F": tmp_path / "S1_F_A01.ab1", "R": tmp_path / "S1_R_A02.ab1"}
    reasons = {s.path.name: (s.reason, s.detail) for s in sr.skipped}
    assert reasons["S1_F_B02.ab1"] == ("duplicate", "duplicate forward for S1 (used: S1_F_A01.ab1)")
    assert reasons["X_F.ab1"][0] == "duplicate"
    assert reasons["._S1_F_A01.ab1"][0] == "hidden"
    assert reasons["notag.ab1"][0] == "no_tag"
    assert reasons["S2_F.txt"][0] == "not_ab1"
    assert reasons["nope"][0] == "missing"
    assert "S1_R_A02.ab1" not in reasons  # given twice (folder + file): one entry, no duplicate
    assert sr.n_files == 7


def test_scan_header_check(tmp_path):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "S_F.ab1")
    (tmp_path / "S_R.ab1").write_bytes(b"not abif")
    assert scan([tmp_path], check_headers=True).bad_header == [tmp_path / "S_R.ab1"]


def test_hidden_sample_is_not_created(tmp_path):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "._S1_F.ab1")
    assert run([tmp_path], REF).samples == []


# --- B12: tags are literal text and validated ---------------------------------------------------------

def test_tags_are_literal():
    assert split_name("S1_(F", ("(F",), ("R",)) == ("S1", "F")
    assert split_name("S1_F", ("F+",), ("R",)) == ("S1_F", None)
    assert split_name("S1_F", (), ()) == ("S1_F", None)


@pytest.mark.parametrize("fwd, rev, fragment", [
    ((), ("R",), "forward tag"),
    (("F",), (), "reverse tag"),
    (("F",), ("f",), "both lists"),
    (("F_1",), ("R",), "separator"),
])
def test_validate_tags(fwd, rev, fragment):
    problems = validate_tags(fwd, rev)
    assert problems and any(fragment in p for p in problems), problems


def test_default_tags_are_valid():
    assert validate_tags(pipeline.FORWARD_TAGS, pipeline.REVERSE_TAGS) == []


# --- RN-4 / RN-7: cancel and cached re-analysis -------------------------------------------------------

def test_cancel_after_three_samples():
    done = []
    res = run([EXAMPLES], REF, should_cancel=lambda: len(done) >= 3,
              on_sample=lambda n, total, sr: done.append(sr.sample))
    assert len(res.samples) == 3 and res.cancelled and res.planned == 11


def test_progress_and_on_sample_order():
    events = []
    run([EXAMPLES / "good"], REF, progress=lambda n, t, name: events.append(("p", n, t, name)),
        on_sample=lambda n, t, sr: events.append(("s", n, t, sr.sample)))
    assert events[:2] == [("p", 1, 3, "BORDER01"), ("s", 1, 3, "BORDER01")]
    assert len(events) == 6


def test_rerun_uses_cache_and_new_settings(monkeypatch):
    cache = ChromCache()
    first = run([EXAMPLES], REF, cache=cache)
    bad02 = next(s for s in first.samples if s.sample == "BAD02").consensus
    assert bad02.reasons[0].startswith("reverse read failed QC")

    def no_disk(path):
        raise AssertionError(f"read_ab1 called for {path}")

    monkeypatch.setattr(pipeline, "read_ab1", no_disk)
    second = run([EXAMPLES], REF, QCSettings(min_good_fraction=0.70), cache=cache)
    bad02 = next(s for s in second.samples if s.sample == "BAD02").consensus
    assert not any("reverse read failed QC" in r for r in bad02.reasons), bad02.reasons


def test_run_metadata():
    res = run([EXAMPLES / "good"], "measles-n450")
    assert res.reference_info.name == "measles-n450" and len(res.reference) == 450
    assert res.started <= res.finished and res.version and not res.cancelled
    assert res.inputs == [EXAMPLES / "good"] and res.planned == 3


def test_performance_96_samples(tmp_path):
    for i in range(96):
        shutil.copy(EXAMPLES / "good/GOOD02_F.ab1", tmp_path / f"P{i:03d}_F.ab1")
        shutil.copy(EXAMPLES / "good/GOOD02_R.ab1", tmp_path / f"P{i:03d}_R.ab1")
    t = time.perf_counter()
    res = run([tmp_path], REF)
    assert time.perf_counter() - t < 15
    assert all(s.consensus.status == "PASS" for s in res.samples)
