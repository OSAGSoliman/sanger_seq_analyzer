"""Assembly and rejection rules on hand-built and simulated reads (audit B5, B6; FR 3.1)."""

import numpy as np
import pytest
from builders import FLANK5, REF, amplicon, amplicon_pair, custom_read, readqc

from sanger_seq_analyzer.pipeline import run
from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.recall import (
    GAP, AlignedRead, Obs, Position, SampleCall, _apply_rules, _number, assemble, vote,
)
from sanger_seq_analyzer.simulate import revcomp

S = QCSettings()
SMALL_REF = "ATGGCAGCAGCAGCAGCAGCAGCA"


def o(base, uncalled="", ratio=0.0, q=40):
    return Obs(base, q, uncalled, ratio)


def aligned(cols, inserts=None, direction="F", strand="+"):
    return AlignedRead(readqc("A" * 30), direction, strand, min(cols), max(cols) + 1, 1.0, cols, inserts or {})


def clean(seq, q=50, start=0):
    return {start + i: Obs(b, q, "", 0.0, i) for i, b in enumerate(seq)}


# --- FR 3.1 vote truth table, plus instrument IUPAC calls -----------------------------------------------

@pytest.mark.parametrize("obs, expected", [
    ([o("A", "G", .3)], "R"),
    ([o("A", "G", .3), o("A")], "A"),
    ([o("A", "G", .2), o("A", "G", .2)], "R"),
    ([o("A"), o("G")], "R"),
    ([o("A", "G", .1999), o("A", "G", .1999)], "A"),
    ([o("A", "G", .3), o("A", "G", .3), o("A")], "R"),
    ([o("A", "G", .3), o("A"), o("A")], "A"),
    ([o("A", "G", .3), o("A", "G", .3), o("A"), o("A")], "A"),
    ([o("A", "G", .3)] * 3 + [o("A")], "R"),
    ([o("A"), o("C"), o("G")], "N"),
    ([o("R")], "R"),
    ([o("R"), o("R")], "R"),
    ([o("R"), o("A")], "A"),
    ([o("Y", q=30), o("C", "T", .4)], "Y"),
])
def test_vote_truth_table(obs, expected):
    assert vote(obs, S) == expected


# --- B5: an insertion seen in one read only -------------------------------------------------------------

def test_insertion_in_one_read_is_not_a_frameshift(tmp_path):
    amp = amplicon()
    at = FLANK5 + 200
    rng = np.random.default_rng(4)
    custom_read(tmp_path / "X_F.ab1", (amp[:at] + "A" + amp[at:])[:800], rng)
    custom_read(tmp_path / "X_R.ab1", revcomp(amp[100:960]), rng)
    c = run([tmp_path], REF).samples[0].consensus
    assert c.status == "PASS", c.reasons
    assert c.length == 450 and any("insertion in some reads" in p.marks for p in c.positions)


def test_insertion_in_one_read_marks_the_anchor():
    f = clean(SMALL_REF)
    r = clean(SMALL_REF)
    pos = assemble([aligned(f, {10: [Obs("T", 50, read_index=11)]}), aligned(r, direction="R")], S)
    assert all(p.ref_pos is not None for p in pos)
    assert "insertion in some reads" in pos[10].marks


def test_insertion_in_both_reads_is_kept():
    f, r = clean(SMALL_REF), clean(SMALL_REF)
    pos = assemble([aligned(f, {10: [Obs("T", 50)]}), aligned(r, {10: [Obs("T", 45)]}, direction="R")], S)
    ins = [p for p in pos if p.ref_pos is None]
    assert len(ins) == 1 and ins[0].base == "T" and "insertion" in ins[0].marks
    assert ins[0].anchor == 10 and ins[0].ins_offset == 1 and set(ins[0].obs) == {"F", "R"}


# --- B6: censored bases against gaps --------------------------------------------------------------------

def test_censored_base_opposite_gap_is_not_a_deletion():
    f, r = clean(SMALL_REF), clean(SMALL_REF)
    f[10] = Obs("C", 5, read_index=10)
    r[10] = Obs(GAP, 0)
    pos = assemble([aligned(f), aligned(r, direction="R")], S)
    assert pos[10].base != GAP


def test_censored_inserted_base_in_single_coverage_is_dropped():
    f, r = clean(SMALL_REF), clean(SMALL_REF[:8])
    pos = assemble([aligned(f, {10: [Obs("T", 5)]}), aligned(r, direction="R")], S)
    assert not [p for p in pos if p.ref_pos is None]
    assert "insertion in some reads" in pos[10].marks


def test_deletion_needs_a_majority_of_gaps():
    f, r = clean(SMALL_REF), clean(SMALL_REF)
    f[5] = Obs(GAP, 0)
    pos = assemble([aligned(f), aligned(r, direction="R")], S)
    assert pos[5].base == SMALL_REF[5] and "deletion in some reads" in pos[5].marks
    r[5] = Obs(GAP, 0)
    pos = assemble([aligned(f), aligned(r, direction="R")], S)
    assert pos[5].base == GAP and pos[5].marks == ["deletion"]


# --- single-coverage run boundary (FR 3.1) ---------------------------------------------------------------

def _single_run(coverages_quals):
    positions = [Position(i, "A", cov, q) for i, (cov, q) in enumerate(coverages_quals)]
    _number(positions, "A" * len(positions))
    call = SampleCall("x", "FAIL", "", {}, positions=positions)
    rules = _apply_rules(call, QCSettings(check_stop_codons=False, require_full_reference=False), len(positions))
    return call.max_single_run, rules["single_coverage"].passed


@pytest.mark.parametrize("pattern, expected", [
    ([(2, 50)] * 5 + [(1, 30)] * 3 + [(2, 50)] * 5, (3, True)),
    ([(2, 50)] * 5 + [(1, 30)] * 4 + [(2, 50)] * 5, (4, False)),
    ([(2, 50)] * 2 + [(1, 30)] * 2 + [(1, 40)] + [(1, 30)] * 2 + [(2, 50)] * 2, (2, True)),
])
def test_single_coverage_boundary(pattern, expected):
    assert _single_run(pattern) == expected


# --- stop codons (FR 3.1) --------------------------------------------------------------------------------

def _junction_stop_deletion():
    for i in range(1, 440):
        if i % 3 == 0:
            continue
        t = REF[:i] + REF[i + 3:]
        codons = [t[k:k + 3] for k in range(0, len(t) - 2, 3)]
        stops = [n for n, c in enumerate(codons, 1) if c in {"TAA", "TAG", "TGA"}]
        if stops:
            return t, stops[0]
    raise AssertionError("no junction stop found")


def test_stop_codon_in_inframe_insertion(tmp_path):
    amplicon_pair(tmp_path, REF[:180] + "TAG" + REF[180:])
    c = run([tmp_path], REF).samples[0].consensus
    assert c.status == "FAIL" and c.stop_codons and any("stop codon" in r for r in c.reasons)
    assert not any("bad insert" in r for r in c.reasons)


def test_stop_codon_across_inframe_deletion(tmp_path):
    target, codon = _junction_stop_deletion()
    amplicon_pair(tmp_path, target)
    c = run([tmp_path], REF).samples[0].consensus
    assert codon in c.stop_codons, (codon, c.stop_codons, c.reasons)


def test_ambiguous_stop_is_not_flagged(tmp_path):
    amplicon_pair(tmp_path, REF[:177] + "TAR" + REF[180:])
    c = run([tmp_path], REF).samples[0].consensus
    assert c.stop_codons == [], c.reasons
    assert revcomp("TAR") == "YTA"
