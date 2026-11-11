"""View models shared by the window and the report (design spec 3.8-3.18)."""

import pytest
from builders import EXAMPLES

from sanger_seq_analyzer import design_tokens as dt
from sanger_seq_analyzer import presentation as pr
from sanger_seq_analyzer.pipeline import scan
from sanger_seq_analyzer.qc import QCSettings

S = QCSettings()


def col_at(data, ref_number):
    return next(c for c in data.columns if c.ref_number == ref_number)


def test_alignment_classes_good01(example):
    data = pr.alignment(example("GOOD01"), S)
    c101, c281 = col_at(data, 101), col_at(data, 281)
    assert "diff" in c101.flags and (c101.ref_base, c101.cons_base) == ("C", "A")
    assert {"mixture", "marked"} <= c281.flags and c281.cons_base == "Y"
    assert data.flagged == [100, 280] and data.coding
    assert (c281.codon, c281.codon_pos) == (94, 2)


def test_alignment_read_cells_and_gaps(example):
    data = pr.alignment(example("GOOD02"), S)
    c204 = col_at(data, 204)
    assert c204.reads["F"].raw == 371 and c204.reads["R"].raw == 588
    assert data.raw_to_col["F"][371] == c204.index and data.col_to_raw["R"][c204.index] == 588
    c201 = col_at(data, 201)
    assert "deletion" in c201.flags and c201.reads["F"].base == "-"
    assert c201.reads["F"].between == (370, 371) and c201.reads["R"].between == (589, 588)


def test_flagged_skips_single_coverage_only(example):
    data = pr.alignment(example("BAD02"), S)
    assert len(data.columns) == 450 and data.flagged == []
    assert pr.alignment(example("BAD04"), S).flagged == [177, 178, 179]


def test_tooltip_and_readout(example):
    sr = example("GOOD01")
    data = pr.alignment(sr, S)
    lines = pr.tooltip_lines(col_at(data, 281), data, S, sr.consensus)
    assert lines[0] == "Ref 281 · codon 94, base 2"
    assert lines[1] == "Consensus Y (C/T) · reference C"
    assert lines[2] == "Coverage 2 reads · mean Q13"
    assert lines[3].startswith("F: ") and "secondary" in lines[3]
    assert lines[-1] == "Marked: mark area, low average quality"
    assert pr.position_readout(col_at(data, 281)) == "Ref 281 · codon 94 · cons Y · ref C · 2 reads · mean Q13 · marked"
    bad04 = example("BAD04")
    d4 = pr.alignment(bad04, S)
    assert pr.tooltip_lines(col_at(d4, 178), d4, S, bad04.consensus)[-1] == "Stop codon TAG (reference AAG)"


@pytest.mark.parametrize("name, text", [
    ("GOOD01", "450 bp · 1 mixture · 1 mark"),
    ("GOOD02", "447 bp · 1 mixture · 4 marks"),
    ("BAD01", "Forward read failed QC (21.5% ≥ Q20) · +1 more"),
    ("BAD02", "Reverse read failed QC (70.0% ≥ Q20)"),
    ("BAD03", "Reverse read does not match the reference"),
    ("BAD04", "Stop codon at codon 60"),
    ("BAD06", "Too many mixtures: 28 (6.2%)"),
    ("BAD07", "Reverse read missing"),
    ("BAD08", "113 marked positions (fail at >= 100)"),
])
def test_sample_subtitle(example, name, text):
    assert pr.sample_subtitle(example(name), S) == text


def test_verdict_sentences(example):
    assert pr.verdict_sentence(example("GOOD01")) == (
        "Both reads passed the lab cutoff and no RECall rejection rule was triggered. Included in consensus.fasta.")
    assert pr.verdict_sentence(example("BAD04")) == "1 rejection reason · not written to consensus.fasta"
    assert pr.verdict_sentence(example("BAD01")) == "2 rejection reasons · not written to consensus.fasta"


def test_stop_codon_reason(example):
    (view,) = pr.reason_views(example("BAD04"), S)
    assert view.title == "Stop codon at codon 60" and view.family == "RECall rejection rule"
    assert view.explanation == ("Reference positions 178–180 read TAG in the consensus (reference AAG). "
                                "Both reads call T at position 178: forward Q55, reverse Q56.")
    assert view.goto_column == 177 and view.goto_label == "Go to codon 60"


def test_read_reasons(example):
    (view,) = pr.reason_views(example("BAD02"), S)
    assert view.title == "Reverse read failed QC" and view.family == "Lab read rule" and view.read == "R"
    assert view.explanation.startswith("Only 70.0% of bases are ≥ Q20 (cutoff 75%).")
    (missing,) = pr.reason_views(example("BAD07"), S)
    assert missing.explanation == "No reverse file was found for this sample." and missing.action == "open_folder"
    (nomatch,) = pr.reason_views(example("BAD03"), S)
    assert nomatch.explanation == "The reverse read has no usable alignment to the reference."
    (deletion,) = pr.reason_views(example("BAD05"), S)
    assert deletion.goto_label == "Go to position 301"
    assert deletion.explanation == "1 bp deletion at reference position 301."
    (mix,) = pr.reason_views(example("BAD06"), S)
    assert mix.explanation == "28 mixtures, 6.2% of bases (limit 3.5%)."


def test_metric_tiles(example):
    tiles = {t.label: t for t in pr.metric_tiles(example("GOOD02"), S, 450)}
    assert (tiles["Indels"].value, tiles["Indels"].hint) == ("3 bp del @ 201", "in-frame, marked")
    assert (tiles["Mixtures"].value, tiles["Mixtures"].suffix) == ("1", "0.22%")
    assert tiles["Length"].hint == "consensus" and not any(t.failed for t in tiles.values())
    bad04 = {t.label: t for t in pr.metric_tiles(example("BAD04"), S, 450)}
    assert bad04["Stop codons"].failed and bad04["Stop codons"].suffix == "codon 60"
    assert [t.value for t in pr.metric_tiles(example("BAD01"), S, 450)][:2] == ["–", "–"]
    assert all(t.provisional for t in pr.metric_tiles(example("BAD02"), S, 450))


def test_read_views(example):
    v = pr.read_view(example("GOOD01"), "F", S)
    assert (v.state, v.status, v.meter_text, v.file_name) == ("ok", "PASS", "97.1%", "GOOD01_F.ab1")
    assert v.rows == [("Mean QV", "51.7"), ("Kept after trim", "5–795 · 791 of 800"),
                      ("Strand · ref span", "+ · 1–450"), ("Identity", "99.78%")]
    r = pr.read_view(example("GOOD01"), "R", S)
    assert r.orientation == "shown reverse-complemented"
    bad02 = pr.read_view(example("BAD02"), "R", S)
    assert bad02.meter_text == "70.0% · 5.0 points short" and bad02.meter_failed and bad02.state == "not_assembled"
    assert pr.read_view(example("BAD07"), "R", S).state == "missing"
    assert pr.read_view(example("BAD03"), "R", S).state == "not_assembled"


def test_run_level_texts(examples_run):
    assert pr.run_context(examples_run) == "Reference measles-n450 · 450 bp · rules: defaults · 0 files not analysed"
    assert pr.export_notice(examples_run) == (
        "8 failing samples are left out of consensus.fasta: BAD01–BAD08. "
        "They stay in the report and the CSV files with their reasons.")
    assert "≥ 75% of bases ≥ Q20" in pr.rule_summary(S)
    groups = dict(pr.rules_used(QCSettings(min_good_fraction=0.7)))
    assert groups["Lab read rules"][0].value == "70%" and groups["Lab read rules"][0].changed


def test_scan_summary():
    summary = pr.scan_summary(scan([EXAMPLES], check_headers=True))
    assert summary.headline == "21 .ab1 files found · grouped into 11 samples"
    assert ("10 with forward + reverse", "neutral") in summary.chips
    assert ("1 forward only: BAD07", "warn") in summary.chips
    assert summary.ready == 11 and not summary.warning


@pytest.mark.parametrize("theme", dt.THEMES)
def test_contrast(theme):
    p = dt.palette(theme)
    for fg, bg in [("pass.fg", "pass.bg"), ("fail.fg", "fail.bg"), ("text.primary", "bg.surface"),
                   ("text.secondary", "bg.surface"), ("text.tertiary", "bg.surface"), ("fail.title", "fail.bgSoft"),
                   ("accent.onSubtle", "accent.subtle"), ("warn.fg", "warn.bg"), ("text.onAccent", "accent")]:
        assert dt.contrast(p[fg], p[bg]) >= 4.5, (theme, fg, bg)
