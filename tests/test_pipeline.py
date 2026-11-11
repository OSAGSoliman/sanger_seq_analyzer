from sanger_seq_analyzer.pipeline import load_reference, run, split_name
from sanger_seq_analyzer.report import write_outputs


def test_split_name():
    assert split_name("HIV01_RT_F_A03") == ("HIV01_RT", "F")
    assert split_name("sample-12-rev") == ("sample-12", "R")
    assert split_name("plain") == ("plain", None)


def test_good_pair_matches_reference(make_pair):
    folder, ref = make_pair()
    c = run([folder], ref).samples[0].consensus
    assert c.status == "PASS", c.reasons
    assert c.sequence == ref and (c.ref_start, c.ref_end) == (1, 300)
    assert {a.strand for a in c.aligned} == {"+", "-"}


def test_heterozygous_position_becomes_iupac(make_pair):
    folder, ref = make_pair(edit=lambda t: t[:100] + ("Y" if t[100] in "CT" else "R") + t[101:])
    c = run([folder], ref).samples[0].consensus
    assert c.status == "PASS" and c.mixtures == 1 and c.sequence[100] in "RY"


def test_stop_codon_fails(make_pair):
    folder, ref = make_pair(edit=lambda t: t[:30] + "TGA" + t[33:])
    c = run([folder], ref).samples[0].consensus
    assert c.status == "FAIL" and c.stop_codons == [11]


def test_frameshift_deletion_fails(make_pair):
    folder, ref = make_pair(edit=lambda t: t[:150] + t[152:])
    c = run([folder], ref).samples[0].consensus
    assert c.status == "FAIL" and any("bad deletion" in r for r in c.reasons)


def test_in_frame_deletion_passes_and_is_marked(make_pair):
    folder, ref = make_pair(edit=lambda t: t[:150] + t[153:])
    c = run([folder], ref).samples[0].consensus
    assert c.status == "PASS" and c.length == 297 and c.marks >= 3


def test_bad_pair_fails(make_pair):
    poor = {"kind": "poor", "signal": 150.0}
    folder, ref = make_pair(f_kw=poor, r_kw=poor)
    c = run([folder], ref).samples[0].consensus
    assert c.status == "FAIL" and c.sequence == ""


def test_read_below_cutoff_fails_sample(make_pair):
    folder, ref = make_pair(r_kw={"good_fraction": 0.6})
    c = run([folder], ref).samples[0].consensus
    assert c.status == "FAIL" and "reverse read failed QC" in c.reasons[0]


def test_missing_reverse_fails(make_pair):
    folder, ref = make_pair()
    (folder / "S1_R.ab1").unlink()
    c = run([folder], ref).samples[0].consensus
    assert c.forward.passed
    assert c.status == "FAIL" and c.reasons == ["reverse read missing"]


def test_missing_forward_fails(make_pair, tmp_path):
    folder, ref = make_pair()
    (folder / "S1_F.ab1").unlink()
    res = run([folder], ref)
    c = res.samples[0].consensus
    assert c.status == "FAIL" and c.reasons == ["forward read missing"]
    paths = write_outputs(res, tmp_path / "out")
    assert paths["consensus"].read_text() == ""


EXPECTED = {
    "GOOD01": "PASS", "GOOD02": "PASS", "BORDER01": "PASS",
    "BAD01": "read failed QC", "BAD02": "reverse read failed QC", "BAD03": "does not match the reference",
    "BAD04": "stop codon", "BAD05": "bad deletion", "BAD06": "too many mixtures",
    "BAD07": "reverse read missing", "BAD08": "marked positions",
}


def test_examples_give_expected_result(examples_dir, tmp_path):
    res = run([examples_dir], load_reference(examples_dir / "reference.fasta"))
    got = {s.sample: s.consensus for s in res.samples}
    assert set(got) == set(EXPECTED)
    for name, expect in EXPECTED.items():
        c = got[name]
        if expect == "PASS":
            assert c.status == "PASS", (name, c.reasons)
        else:
            assert c.status == "FAIL" and any(expect in r for r in c.reasons), (name, c.reasons)
    assert got["GOOD01"].mixtures == 1 and got["GOOD02"].mixtures == 1
    assert any("3 bp deletion" in i for i in got["GOOD02"].indels)

    paths = write_outputs(res, tmp_path)
    fasta = paths["consensus"].read_text()
    assert fasta.count(">") == 3 and ">BAD" not in fasta
    page = paths["html"].read_text()
    assert "canvas data-viewer" in page and "class='aln'" in page


def test_cli(examples_dir, tmp_path):
    from sanger_seq_analyzer.cli import main
    rc = main(["qc", str(examples_dir / "good"), "-r", str(examples_dir / "reference.fasta"),
               "-o", str(tmp_path), "--no-html"])
    assert rc == 0
    assert (tmp_path / "consensus.fasta").read_text().count(">") == 3
