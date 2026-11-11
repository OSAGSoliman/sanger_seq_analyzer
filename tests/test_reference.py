"""Reference validation and normalisation (audit B9, B16)."""

import pytest
from builders import EXAMPLES, REF

from sanger_seq_analyzer.pipeline import run
from sanger_seq_analyzer.references import (
    ReferenceError, coding_warnings, normalize_sequence, resolve_reference,
)


def test_builtin_reference():
    info = resolve_reference("measles-n450")
    assert info.sequence == REF and info.builtin and len(info.sha256) == 64
    assert info.warnings == []


def test_rna_reference_is_converted_with_a_warning():
    seq, warnings, errors = normalize_sequence(REF.replace("T", "U").lower())
    assert seq == REF and not errors and any("U" in w for w in warnings)


def test_gaps_digits_and_whitespace_are_stripped():
    seq, warnings, errors = normalize_sequence("1 ACGT-ACGT\n61 ..AC GT\n")
    assert seq == "ACGTACGTACGT" and not errors and any("gap" in w for w in warnings)


@pytest.mark.parametrize("text, fragment", [
    ("", "no sequence"),
    ("NNNNNNNNNN", "no A, C, G or T"),
    ("MKVLAAGIVGLLLAQEFP", "protein"),
    ("ACGT#ACGT", "#"),
])
def test_invalid_sequences(text, fragment):
    _, _, errors = normalize_sequence(text)
    assert errors and any(fragment in e for e in errors), errors


def test_multi_record_fasta_needs_a_choice(tmp_path):
    f = tmp_path / "two.fasta"
    f.write_text(">one first\nACGTACGTAC\n>two second\nGGGGCCCCAA\n", encoding="utf-8")
    with pytest.raises(ReferenceError, match="one, two"):
        resolve_reference(f)
    info = resolve_reference(f, record_id="two")
    assert info.sequence == "GGGGCCCCAA" and info.record_id == "two" and info.path == f


def test_missing_and_empty_files(tmp_path):
    with pytest.raises(ReferenceError, match="not found"):
        resolve_reference(tmp_path / "nope.fasta")
    (tmp_path / "empty.fasta").write_text("", encoding="utf-8")
    with pytest.raises(ReferenceError):
        resolve_reference(tmp_path / "empty.fasta")


def test_rna_reference_gives_the_same_result(tmp_path):
    f = tmp_path / "rna.fasta"
    f.write_text(">n450 rna\n" + REF.replace("T", "U") + "\n", encoding="utf-8")
    info = resolve_reference(f)
    assert info.sequence == REF and info.warnings
    clean = run([EXAMPLES / "good"], REF)
    rna = run([EXAMPLES / "good"], info)
    assert [s.consensus.status for s in rna.samples] == [s.consensus.status for s in clean.samples] == ["PASS"] * 3
    good01 = next(s for s in rna.samples if s.sample == "GOOD01").consensus
    diffs = [p for p in good01.positions if p.base in "ACGT" and p.base != p.ref_base]
    assert [p.ref_number for p in diffs] == [101]


def test_coding_warnings():
    assert coding_warnings(REF, 1) == []
    assert any("stop codon" in w for w in coding_warnings(REF + "TAA", 1))
    assert any("multiple of 3" in w for w in coding_warnings(REF + "A", 1))
