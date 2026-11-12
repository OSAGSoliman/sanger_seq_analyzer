"""Regression tests for samples that used to PASS although they should not (audit B1-B4)."""

import re
import shutil

import numpy as np
import pytest
from builders import EXAMPLES, FLANK5, REF, alt_base, amplicon_pair, random_seq

from sanger_seq_analyzer.pipeline import run
from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.report import write_outputs
from sanger_seq_analyzer.simulate import revcomp


def call(folder, settings=None):
    res = run([folder], REF, settings)
    assert len(res.samples) == 1
    return res, res.samples[0].consensus


# --- B1: partial coverage of the reference ----------------------------------------------------------------

def _partial_amplicon():
    """Reference 101-351 between 40 bp random flanks."""
    rng = np.random.default_rng(11)
    return random_seq(rng, 40) + REF[100:351] + random_seq(rng, 40)


def test_partial_coverage_fails(tmp_path):
    amp = _partial_amplicon()
    amplicon_pair(tmp_path / "in", amp=amp, f_slice=(0, len(amp)), r_slice=(0, len(amp)))
    res, c = call(tmp_path / "in")
    assert c.status == "FAIL"
    assert c.reasons[0].startswith("incomplete coverage:"), c.reasons
    # A flank base may pair by chance next to the reads' true ends, so allow two positions of slack either way.
    assert re.fullmatch(r"incomplete coverage: reference 1-(9[89]|10[01]), (349|35[0-4])-450 not covered by both reads "
                        r"\(whole reference required\)", c.reasons[0]), c.reasons
    paths = write_outputs(res, tmp_path / "out", html_report=False)
    assert paths["consensus"].read_text(encoding="utf-8") == ""


def test_partial_coverage_allowed_when_switched_off(tmp_path):
    amp = _partial_amplicon()
    amplicon_pair(tmp_path, amp=amp, f_slice=(0, len(amp)), r_slice=(0, len(amp)))
    _, c = call(tmp_path, QCSettings(require_full_reference=False))
    assert c.status == "PASS", c.reasons
    assert 99 <= c.ref_start <= 103 and 349 <= c.ref_end <= 353


# --- B2: variants in the first or last reference positions ----------------------------------------------

@pytest.mark.parametrize("positions", [(1,), (2,), (449,), (450,), (1, 2)])
def test_terminal_snp_is_kept(tmp_path, positions):
    target = list(REF)
    for p in positions:
        target[p - 1] = alt_base(REF[p - 1])
    target = "".join(target)
    amplicon_pair(tmp_path, target)
    _, c = call(tmp_path)
    assert c.status == "PASS", c.reasons
    assert c.length == 450 and (c.ref_start, c.ref_end) == (1, 450)
    assert c.sequence == target


def test_terminal_deletion_is_a_frameshift(tmp_path):
    amplicon_pair(tmp_path, REF[:2] + REF[3:])
    _, c = call(tmp_path)
    assert c.status == "FAIL" and any(r.startswith("bad deletion") for r in c.reasons), c.reasons


def test_three_terminal_snps_fail_on_coverage(tmp_path):
    target = list(REF)
    for p in (1, 4, 7):
        target[p - 1] = alt_base(REF[p - 1])
    amplicon_pair(tmp_path, "".join(target))
    _, c = call(tmp_path)
    assert c.status == "FAIL"
    assert c.reasons[0].startswith("incomplete coverage: reference 1 not covered"), c.reasons


# --- B3: direction from the file name versus strand from the alignment ----------------------------------

def test_duplicate_file_as_forward_and_reverse_fails(tmp_path):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "DUP_F.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "DUP_R.ab1")
    _, c = call(tmp_path)
    assert c.status == "FAIL"
    assert any("same strand" in r for r in c.reasons), c.reasons


def test_swapped_names_fail(tmp_path):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "SWAP_R.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_R.ab1", tmp_path / "SWAP_F.ab1")
    _, c = call(tmp_path)
    assert c.status == "FAIL"
    assert len(c.reasons) == 1 and c.reasons[0].startswith("forward and reverse reads are swapped"), c.reasons


def test_swapped_names_pass_without_orientation_check(tmp_path):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "SWAP_R.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_R.ab1", tmp_path / "SWAP_F.ab1")
    _, c = call(tmp_path, QCSettings(check_read_orientation=False))
    assert c.status == "PASS", c.reasons


# --- B4: IUPAC basecalls written by the instrument ------------------------------------------------------

AT = FLANK5 + 250  # amplicon index of reference 251


def _iupac(index, code):
    def mutate(calls, quals, peaks, traces):
        return calls[:index] + code + calls[index + 1:], quals, peaks, traces
    return mutate


def _het_target():
    return REF[:250] + "R" + REF[251:]


def test_iupac_call_in_one_read_gives_mixture(tmp_path):
    amplicon_pair(tmp_path, _het_target(), mutate_f=_iupac(AT, "R"))
    _, c = call(tmp_path)
    p = next(p for p in c.positions if p.ref_pos == 250)
    assert p.base == "R" and p.coverage == 2
    assert c.status == "PASS", c.reasons


def test_iupac_call_in_both_reads_gives_mixture(tmp_path):
    rev_index = 959 - AT  # reverse read = revcomp(amplicon[100:960])
    amplicon_pair(tmp_path, _het_target(), mutate_f=_iupac(AT, "R"), mutate_r=_iupac(rev_index, "Y"))
    _, c = call(tmp_path)
    p = next(p for p in c.positions if p.ref_pos == 250)
    assert p.base == "R" and p.coverage == 2
    assert not any(r.startswith("low quality") for r in c.reasons), c.reasons
    assert c.status == "PASS", c.reasons
    assert revcomp("R") == "Y"
