"""Export dialog and GUI/CLI output equality (AT-G20, AT-B22)."""

import re

from guihelpers import EXAMPLES, run_examples
from PySide6.QtWidgets import QMessageBox

from sanger_seq_analyzer.cli import main as cli_main
from sanger_seq_analyzer.gui.qc.export_dialog import ExportDialog

STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")


def test_export_dialog_writes_the_same_files_as_the_cli(window, tmp_path, monkeypatch):
    run_examples(window, [EXAMPLES])
    c = window.tools[0].controller
    dialog = ExportDialog(c, window)
    assert dialog.export_button.text() == "Export 7 files"
    assert "8 failing samples are left out of consensus.fasta: BAD01–BAD08" in dialog.warning.text()
    gui = tmp_path / "gui"
    dialog.folder.setFullText(str(gui))
    assert dialog.export(confirm=False)
    assert dialog.success.isVisibleTo(dialog) and dialog.export_button.text() == "Exported"
    assert (gui / "consensus.fasta").read_text(encoding="utf-8").count(">") == 3
    assert (gui / "trimmed_reads.fasta").read_text(encoding="utf-8").count(">") == 18
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    assert not dialog.export()  # overwrite needs confirmation
    assert "consensus.fasta" in asked[0][2]
    dialog.close()

    cli = tmp_path / "cli"
    assert cli_main(["qc", str(EXAMPLES), "-r", "measles-n450", "-o", str(cli)]) == 0
    for name in ("consensus.fasta", "trimmed_reads.fasta", "sample_summary.csv", "read_qc.csv", "marks.csv"):
        assert (gui / name).read_bytes() == (cli / name).read_bytes(), name
    for name in ("report.html", "run_info.json"):
        a = STAMP.sub("", (gui / name).read_text(encoding="utf-8"))
        b = STAMP.sub("", (cli / name).read_text(encoding="utf-8"))
        assert a == b, name


def test_export_error_is_shown_in_the_dialog(window, tmp_path):
    run_examples(window, [EXAMPLES / "good"])
    dialog = ExportDialog(window.tools[0].controller, window)
    out = tmp_path / "out"
    (out / "marks.csv").mkdir(parents=True)
    dialog.folder.setFullText(str(out))
    assert not dialog.export(confirm=False)
    assert dialog.error.isVisibleTo(dialog) and "marks.csv" in dialog.error.text()
    dialog.close()
