"""Window, persistence, theme and the self-test entry (AT-G01, AT-G25, AT-G29, AT-G31)."""

import json

from guihelpers import EXAMPLES, close_window, image_varies, make_window, select
from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from sanger_seq_analyzer import design_tokens as dt
from sanger_seq_analyzer.gui.app import run_app
from sanger_seq_analyzer.gui.theme.manager import manager
from sanger_seq_analyzer.qc import QCSettings

KNOWN_OFFSCREEN = ("propagateSizeHints",)


def test_window_opens_and_closes_without_warnings(qapp, tmp_path):
    messages = []

    def handler(kind, _context, text):
        if kind != QtMsgType.QtDebugMsg and not any(k in text for k in KNOWN_OFFSCREEN):
            messages.append(text)

    previous = qInstallMessageHandler(handler)
    try:
        w = make_window(tmp_path / "s.ini")
        assert w.windowTitle().startswith("Sanger Seq Analyzer 0.4")
        assert [t.text() for t in w.tabs.values()] == ["Run", "Results", "Fetch data", "Settings"]
        assert not w.tabs["results"].isEnabled()
        close_window(w, qapp)
    finally:
        qInstallMessageHandler(previous)
    assert messages == []


def test_preferences_are_restored(qapp, tmp_path):
    store = tmp_path / "s.ini"
    gone = tmp_path / "gone"
    gone.mkdir()
    fasta = tmp_path / "ref.fasta"
    from sanger_seq_analyzer.references import get
    fasta.write_text(">n450\n" + get("measles-n450").sequence + "\n", encoding="utf-8")
    w = make_window(store)
    c = w.tools[0].controller
    c.set_inputs([EXAMPLES / "good", gone])
    c.set_output_dir(str(tmp_path / "out"))
    c.set_reference(f"file:{fasta}")
    c.set_settings(QCSettings(min_good_fraction=0.7, max_n=9, call_mixtures=False))
    c.set_tags(("FW",), ("RV",))
    close_window(w, qapp)
    gone.rmdir()

    w = make_window(store)
    c = w.tools[0].controller
    assert c.inputs == [EXAMPLES / "good"]
    assert c.output_dir == str(tmp_path / "out") and c.reference_spec == f"file:{fasta}"
    assert (c.settings.min_good_fraction, c.settings.max_n, c.settings.call_mixtures) == (0.7, 9, False)
    assert (c.forward_tags, c.reverse_tags) == (("FW",), ("RV",))
    assert w.tools[0].settings.controls["min_good_fraction"].value() == 70
    close_window(w, qapp)


def test_dark_theme_paints(tool):
    manager().set_mode("dark")
    try:
        detail = select(tool, "GOOD01")
        assert image_varies(detail.detail.alignment)
        assert image_varies(detail.chrom.tracks["F"])
    finally:
        manager().set_mode("light")
    p = dt.palette("dark")
    assert dt.contrast(p["pass.fg"], p["pass.bg"]) >= 4.5 and dt.contrast(p["fail.fg"], p["fail.bg"]) >= 4.5


def test_self_test_entry(qapp, tmp_path):
    assert run_app(["--self-test", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "self_test.json").read_text(encoding="utf-8"))
    assert report["ok"] and (report["pass"], report["fail"]) == (3, 8) and len(report["files"]) == 7
