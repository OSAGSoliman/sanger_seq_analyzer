"""Setup form, threaded run, cancel, re-run, errors, drag and drop (AT-G05..G07, G22..G24, G26, G28, G30)."""

import shutil
import time

from guihelpers import EXAMPLES, run_examples, wait_until
from PySide6.QtCore import QMimeData, QPointF, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from sanger_seq_analyzer import pipeline
from sanger_seq_analyzer.gui.shell import log_folder
from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.references import get


def _setup(window):
    tool = window.tools[0]
    return tool, tool.controller, tool.run_page.setup


def test_run_blocked_with_reasons(window, tmp_path):
    tool, c, setup = _setup(window)
    assert not setup.run_button.isEnabled() and setup.status.text() == "Choose an input folder to start"
    c.set_inputs([EXAMPLES])
    c.rescan_now()
    assert setup.run_button.isEnabled()
    c.set_tags((), ("R",))
    assert not setup.run_button.isEnabled() and "forward tag" in setup.status.text()
    c.set_tags(("F",), ("R",))
    c.set_settings(QCSettings(frame=0))
    assert not setup.run_button.isEnabled() and "Codon 1 starts at" in setup.status.text()
    c.set_settings(QCSettings())
    c.set_reference(f"file:{tmp_path / 'missing.fasta'}")
    assert not setup.run_button.isEnabled() and "reference not found" in setup.status.text()


def test_pairing_preview(window):
    tool, c, setup = _setup(window)
    c.set_inputs([EXAMPLES])
    c.rescan_now()
    sr = c.scan_result
    assert len(sr.pairs) == 11 and not [s for s in sr.skipped if s.reason != "hidden"]
    rows = {setup.pairing.item(r, 0).text(): setup.pairing.item(r, 3).text() for r in range(setup.pairing.rowCount())}
    assert len(rows) == 11 and rows["BAD07"] == "reverse missing"
    assert setup.summary_head.text() == "21 .ab1 files found · grouped into 11 samples"


def test_threaded_run(window):
    tool, c, setup = _setup(window)
    progress, threads, ticks = [], [], []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(1)
    c.runProgress.connect(lambda n, total, name: progress.append((n, total)))
    c.sampleDone.connect(lambda *a: threads.append(QThread.currentThread()))
    run_examples(window)
    timer.stop()
    assert progress[-1] == (11, 11) and ticks
    assert all(t is QApplication.instance().thread() for t in threads)
    assert window.current_page() == "results" and window.tabs["results"].isEnabled()
    assert tool.results.summary.passed.text() == "3 PASS" and len(tool.results.list.visible_names()) == 11


def test_cancel(window, monkeypatch):
    tool, c, setup = _setup(window)
    real = pipeline.analyse_sample

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "analyse_sample", slow)
    c.set_inputs([EXAMPLES])
    c.rescan_now()
    finished = []
    c.runFinished.connect(finished.append)
    assert c.start_run()
    wait_until(lambda: len(tool.run_page.running.model.results) >= 2)
    started = time.perf_counter()
    c.cancel_run()
    wait_until(lambda: bool(finished))
    # Cancelling waits for the sample in progress only (generous bound for slow CI machines);
    # the sample count below is what proves the remaining samples were skipped.
    assert time.perf_counter() - started < 10.0
    assert finished[0].cancelled and 2 <= len(finished[0].samples) < 11
    assert "cancelled" in setup.notice.text() and setup.notice.isVisible()
    assert setup.run_button.isEnabled() and c.result is None and not window.tabs["results"].isEnabled()


def test_rerun_with_new_cutoff_keeps_selection(window, monkeypatch):
    tool, c, _ = _setup(window)
    run_examples(window)
    tool.results.list.select_name("BAD02")
    assert tool.results.detail.sr.consensus.reasons[0].startswith("reverse read failed QC")

    def no_disk(path):
        raise AssertionError(f"read again: {path}")

    monkeypatch.setattr(pipeline, "read_ab1", no_disk)
    tool.settings.controls["min_good_fraction"].setValue(70)
    assert c.settings.min_good_fraction == 0.7 and c.results_stale()
    assert tool.results.summary.stale.isVisibleTo(tool.results.summary)
    done = []
    c.runFinished.connect(done.append)
    tool.start_run()
    wait_until(lambda: bool(done))
    sr = tool.results.detail.sr
    assert sr.sample == "BAD02" and not any("reverse read failed QC" in r for r in sr.consensus.reasons)
    assert not c.results_stale()


def test_unreadable_and_untagged_files(window, tmp_path):
    tool, c, _ = _setup(window)
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / "X_F.ab1")
    (tmp_path / "X_R.ab1").write_bytes(b"ABIF but broken")
    shutil.copy(EXAMPLES / "good/GOOD02_F.ab1", tmp_path / "notag.ab1")
    run_examples(window, [tmp_path])
    sr = c.result.samples[0]
    assert sr.consensus.status == "FAIL" and "reverse read unreadable (X_R.ab1)" in sr.consensus.reasons
    files = [tool.results.list.files.item(i).text() for i in range(tool.results.list.files.count())]
    assert any(t.startswith("notag.ab1 — no forward/reverse tag") for t in files)
    assert any(t.startswith("X_R.ab1 — ") for t in files)
    assert "2 files not analysed" in tool.results.summary.files.text()


def test_drag_and_drop(window, tmp_path):
    tool, c, setup = _setup(window)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(EXAMPLES / "good")), QUrl.fromLocalFile(str(EXAMPLES / "bad/BAD07_F.ab1")),
                  QUrl.fromLocalFile(str(tmp_path / "notes.txt"))])
    target = window.centralWidget()
    args = (Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    enter = QDragEnterEvent(QPointF(100, 100), *args)  # the platform sends DragEnter before Drop
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted()
    QApplication.sendEvent(target, QDropEvent(QPointF(100, 100), *args))
    assert c.inputs == [EXAMPLES / "good", EXAMPLES / "bad/BAD07_F.ab1"]
    assert setup.notice.text() == "1 file ignored: not .ab1"


def test_custom_references(window, tmp_path):
    tool, c, setup = _setup(window)
    c.set_inputs([EXAMPLES / "good"])
    c.rescan_now()
    ref = get("measles-n450").sequence
    two = tmp_path / "two.fasta"
    two.write_text(f">alpha\n{ref}\n>beta\n{ref}\n", encoding="utf-8")
    c.set_reference(f"file:{two}")
    assert not setup.run_button.isEnabled() and setup.record_row.isVisibleTo(setup)
    assert c.reference_records == ["alpha", "beta"]
    setup.record.setCurrentIndex(2)
    setup._record_chosen(2)
    assert c.reference is not None and c.reference.record_id == "beta" and setup.run_button.isEnabled()
    rna = tmp_path / "rna.fasta"
    rna.write_text(">rna\n" + ref.replace("T", "U") + "\n", encoding="utf-8")
    c.set_reference(f"file:{rna}")
    assert setup.run_button.isEnabled() and setup.ref_notice.kind == "warn" and "U read as T" in setup.ref_notice.text()
    protein = tmp_path / "protein.fasta"
    protein.write_text(">p\nMKVLAAGIVGLLLAQEFPWQ\n", encoding="utf-8")
    c.set_reference(f"file:{protein}")
    assert not setup.run_button.isEnabled() and "protein" in setup.status.text()


def test_analysis_error_is_logged_and_batch_continues(window, monkeypatch):
    tool, c, _ = _setup(window)
    real = pipeline.call_sample

    def boom(name, *args, **kwargs):
        if name == "BAD04":
            raise RuntimeError("synthetic GUI failure")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(pipeline, "call_sample", boom)
    run_examples(window)
    by_name = {s.sample: s for s in c.result.samples}
    assert by_name["BAD04"].consensus.reasons == ["analysis error: RuntimeError: synthetic GUI failure"]
    assert by_name["GOOD01"].consensus.status == "PASS" and len(by_name) == 11
    log = (log_folder() / "sanger-seq-analyzer.log").read_text(encoding="utf-8")
    assert "synthetic GUI failure" in log and "Traceback" in log
    tool.results.list.select_name("BAD04")
    assert tool.results.detail.reasons._views[0].title == "Analysis error"
