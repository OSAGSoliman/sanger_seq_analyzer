"""Output files: encodings, atomic writes, errors and run_info.json (audit B8; FR EX-2, EX-9)."""

import builtins
import json
import shutil

import pytest
from builders import EXAMPLES, REF

from sanger_seq_analyzer import report
from sanger_seq_analyzer.pipeline import run
from sanger_seq_analyzer.report import EXPORT_FILES, ExportError, write_outputs


def test_non_ascii_names_with_a_cp1252_default(tmp_path, monkeypatch):
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "麻疹_S1_F.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_R.ab1", tmp_path / "麻疹_S1_R.ab1")
    res = run([tmp_path], REF)
    real_open = builtins.open

    def windows_open(file, mode="r", *args, **kwargs):
        if "b" not in mode and "encoding" not in kwargs and len(args) < 2:
            kwargs["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", windows_open)
    paths = write_outputs(res, tmp_path / "out")
    monkeypatch.undo()
    assert {p.name for p in (tmp_path / "out").iterdir()} == {f.filename for f in EXPORT_FILES}
    raw = paths["samples_csv"].read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and "麻疹_S1" in raw.decode("utf-8-sig")
    assert paths["consensus"].read_text(encoding="utf-8").startswith(">麻疹_S1 ")
    assert not paths["consensus"].read_bytes().startswith(b"\xef\xbb\xbf")


def test_target_that_is_a_directory_raises_export_error(tmp_path):
    res = run([EXAMPLES / "good"], REF)
    (tmp_path / "out" / "marks.csv").mkdir(parents=True)
    with pytest.raises(ExportError) as err:
        write_outputs(res, tmp_path / "out")
    assert err.value.path.name == "marks.csv" and "marks.csv" in str(err.value)
    assert not list((tmp_path / "out").glob("*.part"))


def test_subset_of_files(tmp_path):
    res = run([EXAMPLES / "good"], REF)
    paths = write_outputs(res, tmp_path, files=["consensus", "run_info"])
    assert set(paths) == {"consensus", "run_info"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["consensus.fasta", "run_info.json"]


def test_run_info(tmp_path):
    res = run([EXAMPLES], "measles-n450")
    info = json.loads(write_outputs(res, tmp_path, html_report=False)["run_info"].read_text(encoding="utf-8"))
    assert info["version"] and info["started"] and info["finished"] and info["cancelled"] is False
    assert info["reference"]["name"] == "measles-n450" and info["reference"]["length"] == 450
    assert len(info["reference"]["sha256"]) == 64
    assert info["counts"] == {"samples": 11, "pass": 3, "fail": 8}
    assert info["settings"]["min_good_fraction"] == 0.75 and info["settings"]["require_full_reference"] is True
    assert info["forward_tags"] == ["F", "FWD", "FOR", "FORWARD"]
    assert info["inputs"] == [str(EXAMPLES)]


def test_html_flag_skips_report(tmp_path):
    res = run([EXAMPLES / "good"], REF)
    paths = write_outputs(res, tmp_path, html_report=False)
    assert "html" not in paths and not (tmp_path / "report.html").exists()


def test_export_files_table():
    assert [f.filename for f in EXPORT_FILES] == [
        "consensus.fasta", "report.html", "sample_summary.csv", "read_qc.csv", "marks.csv",
        "trimmed_reads.fasta", "run_info.json"]
    assert report.EXPORT_FILES is EXPORT_FILES
