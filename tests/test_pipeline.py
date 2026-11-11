from sanger_seq_analyzer.consensus import reverse_complement
from sanger_seq_analyzer.pipeline import load_reference, run, split_name
from sanger_seq_analyzer.report import write_outputs


def test_split_name():
    assert split_name("HIV01_RT_F_A03") == ("HIV01_RT", "F")
    assert split_name("sample-12-rev") == ("sample-12", "R")
    assert split_name("plain") == ("plain", None)


def test_good_pair_consensus_matches_template(make_pair):
    folder, template = make_pair()
    res = run([folder])
    c = res.samples[0].consensus
    assert c.status == "PASS" and c.overlap > 250
    assert c.sequence in template or reverse_complement(c.sequence) in template
    assert len(c.sequence) > 600


def test_bad_pair_fails(make_pair):
    poor = {"kind": "poor", "signal": 150.0}
    folder, _ = make_pair(f_kw=poor, r_kw=poor)
    c = run([folder]).samples[0].consensus
    assert c.status == "FAIL" and c.sequence == ""


def test_one_bad_read_gives_single(make_pair):
    folder, _ = make_pair(r_kw={"good_fraction": 0.6})
    c = run([folder]).samples[0].consensus
    assert c.status == "SINGLE"


def test_examples_give_expected_status(examples_dir, tmp_path):
    res = run([examples_dir], reference=load_reference(examples_dir / "reference.fasta"))
    status = {s.sample: s.consensus.status for s in res.samples}
    assert status == {"GOOD01": "PASS", "GOOD02": "PASS", "BORDER01": "PASS",
                      "BAD01": "FAIL", "BAD02": "SINGLE", "BAD03": "FAIL"}
    mixtures = {s.sample: s.consensus.sequence.count("R") + s.consensus.sequence.count("Y")
                for s in res.samples}
    assert mixtures["GOOD01"] == 1 and mixtures["GOOD02"] == 1
    paths = write_outputs(res, tmp_path)
    fasta = paths["consensus"].read_text()
    assert ">GOOD01" in fasta and ">BAD01" not in fasta and ">BAD03" not in fasta
    assert ">BAD02" in paths["single"].read_text()
    assert "PASS" in paths["html"].read_text()


def test_cli(examples_dir, tmp_path):
    from sanger_seq_analyzer.cli import main
    assert main(["qc", str(examples_dir / "good"), "-o", str(tmp_path), "--no-html"]) == 0
    assert (tmp_path / "consensus.fasta").read_text().count(">") == 3


def test_missing_reverse_fails(make_pair):
    folder, _ = make_pair()
    (folder / "S1_R.ab1").unlink()
    c = run([folder]).samples[0].consensus
    assert c.forward.passed  # the forward read itself is fine...
    assert c.status == "FAIL" and c.sequence == ""  # ...but one direction is not enough
    assert "reverse read missing" in c.reasons


def test_missing_forward_fails(make_pair, tmp_path):
    folder, _ = make_pair()
    (folder / "S1_F.ab1").unlink()
    res = run([folder])
    c = res.samples[0].consensus
    assert c.status == "FAIL" and "forward read missing" in c.reasons
    paths = write_outputs(res, tmp_path / "out")
    assert paths["consensus"].read_text() == "" and paths["single"].read_text() == ""
