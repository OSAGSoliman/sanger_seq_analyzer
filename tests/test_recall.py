"""RECall rules (Woods et al. 2012, Tables 1 and 2)."""

from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.recall import Obs, vote

S = QCSettings()


def o(base, uncalled="", ratio=0.0, q=40):
    return Obs(base, q, uncalled, ratio)


def test_vote_clean_agreement():
    assert vote([o("A"), o("A")], S) == "A"


def test_vote_mixture_seen_in_both_reads():
    assert vote([o("A", "G", 0.5), o("G", "A", 0.6)], S) == "R"
    assert vote([o("A", "G", 0.25), o("A", "G", 0.30)], S) == "R"


def test_vote_mixture_in_half_the_reads_is_a_tie():
    # 2 reads: A,A called; G uncalled >= 20% in one read -> A=2, G=1; 1 is not > 2/2 -> A
    assert vote([o("A", "G", 0.30), o("A", "G", 0.05)], S) == "A"


def test_vote_below_mixture_area_is_ignored():
    assert vote([o("C", "T", 0.19), o("C", "T", 0.19)], S) == "C"


def test_vote_disagreement_tie_gives_mixture():
    assert vote([o("C"), o("T")], S) == "Y"


def test_vote_majority_of_three():
    # T seen once of three reads: count 1 < 3/2, dropped from the list
    assert vote([o("C"), o("C"), o("T")], S) == "C"


def test_vote_never_three_bases():
    assert vote([o("A", "G", 0.5), o("C", "G", 0.5), o("G", "A", 0.5)], S) in {"R", "S", "M"}


def test_vote_no_mixtures_option():
    assert vote([o("A", "G", 0.5), o("A", "G", 0.5)], QCSettings(call_mixtures=False)) == "A"


def test_vote_no_coverage():
    assert vote([], S) == "N"
