"""Command line (`sanger-seq`)."""

import json
import shutil

from builders import EXAMPLES

from sanger_seq_analyzer.cli import build_parser, main, settings_from_args


def test_new_flags_map_to_settings():
    args = build_parser().parse_args(["qc", "x", "-r", "measles-n450", "--allow-partial-coverage",
                                      "--no-orientation-check", "--trim-error-limit", "0.01", "--min-identity", "90",
                                      "--min-aligned", "120"])
    s = settings_from_args(args)
    assert not s.require_full_reference and not s.check_read_orientation
    assert (s.trim_error_limit, s.min_read_identity, s.min_read_aligned) == (0.01, 0.9, 120)


def test_defaults_match_qcsettings():
    from sanger_seq_analyzer.qc import QCSettings
    assert settings_from_args(build_parser().parse_args(["qc", "x", "-r", "measles-n450"])) == QCSettings()


def test_reference_errors_exit_2(tmp_path, capsys):
    assert main(["qc", str(EXAMPLES / "good"), "-r", str(tmp_path / "nope.fa"), "-o", str(tmp_path)]) == 2
    assert "reference not found" in capsys.readouterr().err
    two = tmp_path / "two.fa"
    two.write_text(">a\nACGTACGTAC\n>b\nACGTACGTAA\n", encoding="utf-8")
    assert main(["qc", str(EXAMPLES / "good"), "-r", str(two), "-o", str(tmp_path)]) == 2
    assert "a, b" in capsys.readouterr().err


def test_reference_record_and_bad_tags(tmp_path, capsys):
    two = tmp_path / "two.fa"
    from sanger_seq_analyzer.references import get
    two.write_text(">other\nACGTACGTAC\n>n450\n" + get("measles-n450").sequence + "\n", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["qc", str(EXAMPLES / "good"), "-r", str(two), "--reference-record", "n450", "-o", str(out),
                 "--no-html"]) == 0
    info = json.loads((out / "run_info.json").read_text(encoding="utf-8"))
    assert info["reference"]["record_id"] == "n450"
    assert main(["qc", str(EXAMPLES / "good"), "-r", "measles-n450", "--forward-tags", "F", "--reverse-tags", "f",
                 "-o", str(out)]) == 2
    assert "both lists" in capsys.readouterr().err


def test_skipped_files_are_reported(tmp_path, capsys):
    shutil.copytree(EXAMPLES / "good", tmp_path / "in")
    (tmp_path / "in" / "notag.ab1").write_bytes(b"x")
    assert main(["qc", str(tmp_path / "in"), "-r", "measles-n450", "-o", str(tmp_path / "out"), "--no-html"]) == 0
    assert "notag.ab1 (no forward/reverse tag in the file name)" in capsys.readouterr().err


BLOCK_QT = """
import sys

class BlockQt:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("PySide6", "shiboken6"):
            raise ImportError("Qt is not installed")

sys.meta_path.insert(0, BlockQt())
from sanger_seq_analyzer.cli import main
assert "PySide6" not in sys.modules
rc_qc = main(["qc", sys.argv[1], "-r", "measles-n450", "-o", sys.argv[2], "--no-html"])
rc_gui = main(["gui"])
print(rc_qc, rc_gui)
"""


def test_cli_works_without_qt(tmp_path):
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, "-c", BLOCK_QT, str(EXAMPLES / "good"), str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "0 1"
    assert "pip install PySide6-Essentials" in proc.stderr
    assert (tmp_path / "out" / "consensus.fasta").read_text(encoding="utf-8").count(">") == 3
