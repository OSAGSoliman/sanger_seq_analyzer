"""Expected results for the bundled examples (FR 5.1). These are the values the window shows."""

import pytest

SAMPLES = {
    # name: (status, length, ref span, mixtures, N, marks, reasons)
    "GOOD01": ("PASS", 450, (1, 450), 1, 0, 1, []),
    "GOOD02": ("PASS", 447, (1, 450), 1, 0, 4, []),
    "BORDER01": ("PASS", 450, (1, 450), 0, 0, 0, []),
    "BAD01": ("FAIL", 0, (0, 0), 0, 0, 0, [
        "forward read failed QC: only 21.5% of bases are >= Q20 (cutoff 75%); trimmed length 80 < 100",
        "reverse read failed QC: only 20.7% of bases are >= Q20 (cutoff 75%); trimmed length 88 < 100"]),
    "BAD02": ("FAIL", 450, (1, 450), 0, 0, 450, ["reverse read failed QC: only 70.0% of bases are >= Q20 (cutoff 75%)"]),
    "BAD03": ("FAIL", 450, (1, 450), 0, 0, 450, ["reverse read does not match the reference (no alignment)"]),
    "BAD04": ("FAIL", 450, (1, 450), 0, 0, 0, ["stop codon at codon 60"]),
    "BAD05": ("FAIL", 449, (1, 450), 0, 0, 1, ["bad deletion: 1 bp deletion at reference position 301 (frameshift)"]),
    "BAD06": ("FAIL", 450, (1, 450), 28, 0, 28, ["too many mixtures: 28 (6.2% > 3.5%)"]),
    "BAD07": ("FAIL", 450, (1, 450), 0, 0, 450, ["reverse read missing"]),
    "BAD08": ("FAIL", 450, (1, 450), 2, 0, 113, ["113 marked positions (fail at >= 100)"]),
}

READS = {
    # (name, dir): (status, raw length, mean Q, % >= Q20, trim 1-based, trimmed, strand, span, identity)
    ("GOOD01", "F"): ("PASS", 800, 51.7, 97.1, (5, 795), 791, "+", (1, 450), 0.9978),
    ("GOOD01", "R"): ("PASS", 860, 51.8, 97.9, (4, 852), 849, "-", (1, 450), 0.9956),
    ("GOOD02", "F"): ("PASS", 800, 51.5, 97.8, (7, 793), 787, "+", (1, 450), 0.9911),
    ("GOOD02", "R"): ("PASS", 860, 51.9, 97.6, (5, 853), 849, "-", (1, 450), 0.9933),
    ("BORDER01", "F"): ("PASS", 800, 43.9, 79.0, (169, 800), 632, "+", (1, 450), 1.0),
    ("BORDER01", "R"): ("PASS", 860, 44.7, 81.0, (161, 860), 700, "-", (1, 450), 1.0),
    ("BAD01", "F"): ("FAIL", 800, 13.8, 21.5, (30, 109), 80, None, None, None),
    ("BAD01", "R"): ("FAIL", 860, 13.7, 20.7, (14, 101), 88, None, None, None),
    ("BAD02", "F"): ("PASS", 800, 51.7, 97.9, (3, 797), 795, "+", (1, 450), 1.0),
    ("BAD02", "R"): ("FAIL", 860, 41.0, 70.0, (73, 677), 605, None, None, None),
    ("BAD03", "F"): ("PASS", 800, 51.9, 97.6, (2, 792), 791, "+", (1, 450), 1.0),
    ("BAD03", "R"): ("PASS", 860, 52.1, 98.0, (4, 852), 849, None, None, None),
    ("BAD04", "F"): ("PASS", 800, 51.8, 97.5, (4, 790), 787, "+", (1, 450), 0.9978),
    ("BAD04", "R"): ("PASS", 860, 51.9, 97.7, (5, 853), 849, "-", (1, 450), 0.9978),
    ("BAD05", "F"): ("PASS", 800, 51.8, 97.9, (5, 798), 794, "+", (1, 450), 0.9978),
    ("BAD05", "R"): ("PASS", 860, 51.9, 97.8, (4, 852), 849, "-", (1, 450), 0.9978),
    ("BAD06", "F"): ("PASS", 800, 50.3, 94.2, (5, 795), 791, "+", (1, 450), 0.9622),
    ("BAD06", "R"): ("PASS", 860, 50.5, 94.5, (4, 851), 848, "-", (1, 450), 0.9756),
    ("BAD07", "F"): ("PASS", 800, 51.5, 97.6, (8, 797), 790, "+", (1, 450), 1.0),
    ("BAD08", "F"): ("PASS", 800, 44.7, 76.0, (15, 757), 743, "+", (1, 450), 0.9911),
    ("BAD08", "R"): ("PASS", 860, 44.8, 77.0, (27, 817), 791, "-", (1, 450), 0.9889),
}


def test_all_examples_present(examples_run):
    assert sorted(s.sample for s in examples_run.samples) == sorted(SAMPLES)
    assert examples_run.skipped == [] and examples_run.errors == {}


@pytest.mark.parametrize("name", SAMPLES)
def test_sample(example, name):
    status, length, span, mixtures, n, marks, reasons = SAMPLES[name]
    c = example(name).consensus
    assert (c.status, c.length, (c.ref_start, c.ref_end), c.mixtures, c.n_count, c.marks, c.reasons) == \
        (status, length, span, mixtures, n, marks, reasons)


@pytest.mark.parametrize("key", READS)
def test_read(example, key):
    name, direction = key
    status, raw, mean_q, good, trim, trimmed, strand, span, identity = READS[key]
    s = example(name)
    read = s.consensus.reads[direction]
    assert (read.status, read.length, round(read.mean_quality, 1), round(read.good_fraction * 100, 1),
            (read.trim_start + 1, read.trim_end), read.trimmed_length) == (status, raw, mean_q, good, trim, trimmed)
    a = next((a for a in s.consensus.aligned if a.direction == direction), None)
    if strand is None:
        assert a is None
    else:
        assert (a.strand, (a.ref_start + 1, a.ref_end), round(a.identity, 4)) == (strand, span, identity)


def test_bad07_has_no_reverse_read(example):
    assert example("BAD07").consensus.reverse is None and example("BAD07").reverse_chrom is None


def test_example_facts(example):
    good01 = example("GOOD01").consensus
    assert next(p for p in good01.positions if p.ref_number == 101).base == "A"
    p281 = next(p for p in good01.positions if p.ref_number == 281)
    assert p281.base == "Y" and round(p281.mean_quality) == 13
    good02 = example("GOOD02").consensus
    p401 = next(p for p in good02.positions if p.ref_number == 401)
    assert p401.base == "R" and p401.cons_index == 398
    assert example("BAD08").consensus.max_single_run == 1
    bad06 = example("BAD06").consensus
    mixed = [p.ref_number for p in bad06.positions if p.base not in "ACGTN-"]
    assert mixed[0] == 21 and len(mixed) == 28
