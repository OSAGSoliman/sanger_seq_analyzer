"""Data contract the window and the report rely on (FR 4.2, 4.3)."""

import pytest

from sanger_seq_analyzer.recall import Indel, StopCodon

COMPLEMENT = str.maketrans("ACGT", "TGCA")


def test_every_aligned_base_maps_back_to_its_read(examples_run):
    checked = mismatches = 0
    for s in examples_run.samples:
        for a in s.consensus.aligned:
            chrom = s.chrom(a.direction)
            observations = list(a.columns.values()) + [o for ins in a.inserts.values() for o in ins]
            for o in observations:
                raw = a.raw_index(o)
                if raw is None:
                    assert o.base == "-"
                    continue
                base = chrom.sequence[raw] if a.strand == "+" else chrom.sequence[raw].translate(COMPLEMENT)
                checked += 1
                mismatches += base != o.base or int(chrom.qualities[raw]) != o.quality
    assert checked >= 7642 and mismatches == 0


@pytest.mark.parametrize("direction, ref, raw, x", [
    ("F", 1, 171, 2064), ("F", 200, 370, 4452), ("F", 201, None, None), ("F", 204, 371, 4464),
    ("F", 450, 617, 7416), ("R", 1, 788, 9468), ("R", 200, 589, 7080), ("R", 204, 588, 7068),
    ("R", 450, 342, 4116),
])
def test_good02_position_mapping(example, direction, ref, raw, x):
    s = example("GOOD02")
    a = next(a for a in s.consensus.aligned if a.direction == direction)
    got = a.raw_index(a.columns[ref - 1])
    assert got == raw
    if raw is not None:
        assert int(s.chrom(direction).peak_locations[raw]) == x
        assert a.ref_map(len(s.chrom(direction)))[raw] == ref - 1


def test_consensus_numbering(example):
    c = example("GOOD02").consensus
    p204 = next(p for p in c.positions if p.ref_number == 204)
    assert p204.cons_index == 201 and p204.ref_base == "T"
    deleted = [p for p in c.positions if p.base == "-"]
    assert [p.ref_number for p in deleted] == [201, 202, 203] and all(p.cons_index is None for p in deleted)
    assert [p.column for p in c.positions] == list(range(len(c.positions)))
    assert c.indel_records == [Indel("deletion", 201, 3, False, 200)]
    assert c.indels == ["3 bp deletion at reference 201"]


def test_stop_codon_record(example):
    c = example("BAD04").consensus
    assert c.stop_records == [StopCodon(60, "TAG", "AAG", 178, [177, 178, 179])]
    assert c.stop_codons == [60]


def test_rule_checklist(example):
    good = example("GOOD01").consensus
    assert all(r.passed for r in good.rules), [(r.rule, r.passed) for r in good.rules]
    assert [r.rule for r in good.rules][:2] == ["forward_present", "reverse_present"]
    bad04 = example("BAD04").consensus
    failed = [r for r in bad04.rules if r.passed is False]
    assert [(r.rule, r.observed) for r in failed] == [("stop_codon", "codon 60")]
    bad02 = example("BAD02").consensus
    assert all(r.passed is None for r in bad02.rules if r.family == "recall")
    assert bad02.rule("reverse_cutoff").passed is False


@pytest.mark.parametrize("name, provisional", [
    ("BAD01", False), ("BAD02", True), ("BAD03", True), ("BAD07", True), ("GOOD01", False), ("BAD04", False),
])
def test_provisional_assemblies(example, name, provisional):
    assert example(name).consensus.provisional is provisional


def test_position_observations(example):
    c = example("GOOD01").consensus
    p = next(p for p in c.positions if p.ref_number == 281)
    assert p.base == "Y" and set(p.obs) == {"F", "R"} and p.marks == ["mark area", "low average quality"]
    assert all(o.read_index is not None for o in p.obs.values())
