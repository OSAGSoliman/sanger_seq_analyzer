import numpy as np

from sanger_seq_analyzer.abi import read_ab1
from sanger_seq_analyzer.qc import QCSettings, call_mixtures, mott_trim, qc_read
from sanger_seq_analyzer.simulate import make_read


def test_mott_trim_keeps_high_quality_core():
    q = np.array([5] * 10 + [40] * 50 + [5] * 10)
    assert mott_trim(q) == (10, 60)


def test_mott_trim_all_bad():
    assert mott_trim(np.array([2, 3, 4])) == (0, 0)


def test_ab1_round_trip(tmp_path):
    rng = np.random.default_rng(0)
    calls, quals = make_read(tmp_path / "x_F.ab1", "x", "ACGT" * 50, rng)
    chrom = read_ab1(tmp_path / "x_F.ab1")
    assert chrom.sequence == calls
    assert list(chrom.qualities) == list(quals)
    assert chrom.has_traces and len(chrom.peak_locations) == len(calls)


def test_75_percent_cutoff(tmp_path):
    rng = np.random.default_rng(3)
    template = "".join(rng.choice(list("ACGT"), 600))
    make_read(tmp_path / "above_F.ab1", "above", template, rng, good_fraction=0.78)
    make_read(tmp_path / "below_F.ab1", "below", template, rng, good_fraction=0.72)
    above = qc_read(read_ab1(tmp_path / "above_F.ab1"))
    below = qc_read(read_ab1(tmp_path / "below_F.ab1"))
    assert above.passed and above.good_fraction >= 0.75
    assert not below.passed and "75%" in below.reasons[0]
    # The cutoff is configurable.
    assert qc_read(read_ab1(tmp_path / "below_F.ab1"), QCSettings(min_good_fraction=0.70)).passed


def test_mixture_called_with_iupac(tmp_path):
    rng = np.random.default_rng(5)
    template = "".join(rng.choice(list("ACGT"), 300))
    template = template[:150] + "R" + template[151:]
    make_read(tmp_path / "m_F.ab1", "m", template, rng)
    seq, _ = call_mixtures(read_ab1(tmp_path / "m_F.ab1"), 0.20)
    assert seq[150] == "R"
