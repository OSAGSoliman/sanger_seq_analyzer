"""State of the QC tool: inputs, reference, settings, the running analysis and the latest result.

Lives in the GUI thread. Pages read its state and call its methods; it emits
signals when something changes. All preferences are restored from and saved to
the :class:`Store`.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ... import review
from ...pipeline import FORWARD_TAGS, LEGACY_TAGS, REVERSE_TAGS, ChromCache, RunResult, SampleResult, ScanResult, scan, \
    validate_tags
from ...presentation import AlignmentData, alignment
from ...qc import QCSettings
from ...references import REFERENCES, ReferenceError, ReferenceInfo, coding_warnings, fasta_records, resolve_reference
from ...report import write_outputs
from ...settings_schema import errors, settings_from_dict, settings_to_dict, validate
from ..persist import Store
from .worker import AnalysisWorker, RunJob

log = logging.getLogger("sanger_seq_analyzer.gui")
DEFAULT_REFERENCE = "builtin:measles-n450"
RESULTS_FOLDER = "sanger_results"


class QcController(QObject):
    inputsChanged = Signal()
    scanChanged = Signal()
    referenceChanged = Signal()
    settingsChanged = Signal()
    outputChanged = Signal()
    validityChanged = Signal()
    runStarted = Signal(list)  # sample names in run order
    runProgress = Signal(int, int, str)
    sampleDone = Signal(int, int, object)
    runFinished = Signal(object)  # RunResult (cancelled or not), or None after a failure
    runFailed = Signal(str, str)
    exported = Signal(object)  # dict[str, Path]
    exportFailed = Signal(str)
    reviewsChanged = Signal(list)  # names of the samples whose reviewer decision changed
    cleared = Signal()  # inputs and results were cleared (Clear button)

    def __init__(self, store: Store, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.inputs: list[Path] = []
        self.reference_spec = DEFAULT_REFERENCE
        self.reference_record: str | None = None
        self.reference: ReferenceInfo | None = None
        self.reference_error = ""
        self.reference_records: list[str] = []  # ids to choose from when a FASTA has several records
        self.custom_references: list[str] = []
        self.settings = QCSettings()
        self.forward_tags: tuple[str, ...] = FORWARD_TAGS
        self.reverse_tags: tuple[str, ...] = REVERSE_TAGS
        self.output_dir = ""  # "" = <first input>/sanger_results
        self.auto_export = True
        self.write_html = True
        self.scan_result: ScanResult | None = None
        self.result: RunResult | None = None
        self.reviews: dict[str, str] = {}  # sample -> reviewer's confirmed "PASS"/"FAIL" for the shown result
        self.last_export: dict[str, Path] | None = None
        self.last_export_error = ""
        self.cache = ChromCache()
        self._alignments: dict[str, AlignmentData] = {}
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._cancel = threading.Event()
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(300)
        self._scan_timer.timeout.connect(self.rescan_now)
        self.load()

    # --- persistence ------------------------------------------------------------------------------------

    def load(self) -> None:
        s = self.store
        self.settings, problems = settings_from_dict(s.get_json("qc/settings", {}) or {})
        for p in problems:
            log.warning("stored setting ignored: %s", p.message)
        tags_f, tags_r = tuple(s.get_list("qc/forward_tags")), tuple(s.get_list("qc/reverse_tags"))
        if (tags_f, tags_r) in LEGACY_TAGS:
            tags_f, tags_r = FORWARD_TAGS, REVERSE_TAGS  # old defaults: pick up the 216 (F) / 214 (R) tags
        if tags_f and tags_r and not validate_tags(tags_f, tags_r):
            self.forward_tags, self.reverse_tags = tags_f, tags_r
        self.inputs = s.get_paths("run/inputs")
        out = s.get_str("run/output_dir")
        self.output_dir = out if out and Path(out).parent.exists() else ""
        self.auto_export = s.get_bool("run/auto_export", True)
        self.write_html = s.get_bool("run/write_html", True)
        self.custom_references = [p for p in s.get_list("run/custom_references") if Path(p).is_file()]
        spec = s.get_str("run/reference", DEFAULT_REFERENCE)
        if spec.startswith("file:") and not Path(spec[5:]).is_file():
            spec = DEFAULT_REFERENCE
        self._resolve_reference(spec, s.get_str("run/reference_record") or None)
        if self.inputs:
            self.rescan_now()

    def save(self) -> None:
        s = self.store
        s.set("qc/settings", settings_to_dict(self.settings))
        s.set("qc/forward_tags", list(self.forward_tags))
        s.set("qc/reverse_tags", list(self.reverse_tags))
        s.set("run/inputs", [str(p) for p in self.inputs])
        s.set("run/output_dir", self.output_dir)
        s.set("run/auto_export", self.auto_export)
        s.set("run/write_html", self.write_html)
        s.set("run/reference", self.reference_spec)
        s.set("run/reference_record", self.reference_record or "")
        s.set("run/custom_references", self.custom_references)
        s.sync()

    # --- inputs -----------------------------------------------------------------------------------------

    def add_inputs(self, paths: list[str | Path]) -> int:
        """Add folders or files; returns how many new sources were added."""
        known = {p.resolve() for p in self.inputs}
        added = 0
        for item in paths:
            p = Path(item)
            if p.exists() and p.resolve() not in known:
                self.inputs.append(p)
                known.add(p.resolve())
                added += 1
        if added:
            self._inputs_changed()
        return added

    def set_inputs(self, paths: list[str | Path]) -> None:
        self.inputs = []
        self.add_inputs(paths)
        if not paths:
            self._inputs_changed()

    def remove_input(self, path: Path) -> None:
        self.inputs = [p for p in self.inputs if p != path]
        self._inputs_changed()

    def clear_inputs(self) -> None:
        self.inputs = []
        self._inputs_changed()

    def _inputs_changed(self) -> None:
        self.save()
        self.inputsChanged.emit()
        self.outputChanged.emit()
        self._scan_timer.start()
        self.validityChanged.emit()

    @Slot()
    def rescan_now(self) -> None:
        self._scan_timer.stop()
        self.scan_result = scan(self.inputs, self.forward_tags, self.reverse_tags, check_headers=True) \
            if self.inputs else None
        self.scanChanged.emit()
        self.validityChanged.emit()

    # --- reference --------------------------------------------------------------------------------------

    def set_reference(self, spec: str, record: str | None = None) -> None:
        if spec.startswith("file:") and spec[5:] not in self.custom_references:
            self.custom_references.insert(0, spec[5:])
            del self.custom_references[8:]
        self._resolve_reference(spec, record)
        self.save()
        self.referenceChanged.emit()
        self.validityChanged.emit()

    def _resolve_reference(self, spec: str, record: str | None) -> None:
        self.reference_spec, self.reference_record = spec, record
        self.reference, self.reference_error, self.reference_records = None, "", []
        try:
            if spec.startswith("builtin:"):
                self.reference = resolve_reference(spec[8:])
            else:
                self.reference = resolve_reference(spec[5:], record)
        except ReferenceError as exc:
            self.reference_error = str(exc)
            if spec.startswith("file:") and "choose one" in self.reference_error:
                try:
                    self.reference_records = [r[0] for r in fasta_records(spec[5:])]
                except OSError:
                    pass

    def reference_warnings(self) -> list[str]:
        if self.reference is None:
            return []
        out = list(self.reference.warnings)
        if self.settings.check_stop_codons or self.settings.check_frameshifts:
            out += coding_warnings(self.reference.sequence, self.settings.frame)
        return out

    def builtin_references(self) -> list[str]:
        return list(REFERENCES)

    # --- settings, tags, output -------------------------------------------------------------------------

    def set_settings(self, settings: QCSettings) -> None:
        if settings != self.settings:
            self.settings = settings
            self.save()
            self.settingsChanged.emit()
            self.validityChanged.emit()

    def set_tags(self, forward: tuple[str, ...], reverse: tuple[str, ...]) -> None:
        if (forward, reverse) != (self.forward_tags, self.reverse_tags):
            self.forward_tags, self.reverse_tags = forward, reverse
            if not validate_tags(forward, reverse):
                self.save()
                self._scan_timer.start()
            self.settingsChanged.emit()
            self.validityChanged.emit()

    def default_output(self) -> Path | None:
        if not self.inputs:
            return None
        first = self.inputs[0]
        return (first if first.is_dir() else first.parent) / RESULTS_FOLDER

    def output_path(self) -> Path | None:
        return Path(self.output_dir) if self.output_dir else self.default_output()

    def set_output_dir(self, path: str) -> None:
        self.output_dir = path.strip()
        self.save()
        self.outputChanged.emit()
        self.validityChanged.emit()

    def set_export_options(self, auto_export: bool | None = None, write_html: bool | None = None) -> None:
        if auto_export is not None:
            self.auto_export = auto_export
        if write_html is not None:
            self.write_html = write_html
        self.save()
        self.validityChanged.emit()

    # --- validation (RN-1) ------------------------------------------------------------------------------

    def problems(self) -> list[tuple[str, str]]:
        """(field, message) pairs that block a run, most important first."""
        out: list[tuple[str, str]] = []
        if not self.inputs:
            out.append(("inputs", "Choose an input folder to start"))
        elif self.scan_result is not None and not self.scan_result.pairs:
            out.append(("inputs", "No forward/reverse .ab1 pairs found in the inputs"))
        if self.reference is None:
            out.append(("reference", f"Reference: {self.reference_error or 'choose a reference'}"))
        ref_len = self.reference.length if self.reference else None
        for p in errors(validate(self.settings, ref_len)):
            out.append((p.field, f"Settings: {p.message}"))
        for message in validate_tags(self.forward_tags, self.reverse_tags):
            out.append(("tags", f"Settings: {message}"))
        if self.auto_export and self.output_path() is None and self.inputs:
            out.append(("output", "Choose an output folder"))
        return out

    def run_blocked_reason(self) -> str:
        problems = self.problems()
        return problems[0][1] if problems else ""

    # --- running ----------------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start_run(self) -> bool:
        if self.running or self.run_blocked_reason():
            return False
        if self.scan_result is None or self._scan_timer.isActive():
            self.rescan_now()
        job = RunJob(tuple(self.inputs), self.reference, dataclasses.replace(self.settings), self.forward_tags,
                     self.reverse_tags, self.scan_result, self.output_path() if self.auto_export else None,
                     self.write_html)
        self._cancel = threading.Event()
        self.last_export, self.last_export_error = None, ""
        thread = QThread(self)
        worker = AnalysisWorker(job, self.cache, self._cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.sampleDone.connect(self._on_sample)
        worker.exported.connect(self._on_exported)
        worker.exportFailed.connect(self._on_export_failed)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        log.info("run started: %d samples, reference %s", len(job.scan.pairs), job.reference.name)
        self.runStarted.emit(list(job.scan.pairs))
        self.validityChanged.emit()
        thread.start()
        return True

    def cancel_run(self) -> None:
        self._cancel.set()

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Cancel a run and wait for the worker thread (closing the window, RN-5)."""
        if self._thread is not None:
            self._cancel.set()
            self._thread.quit()
            self._thread.wait(timeout_ms)

    @Slot(int, int, str)
    def _on_progress(self, n: int, total: int, name: str) -> None:
        self.runProgress.emit(n, total, name)

    @Slot(int, int, object)
    def _on_sample(self, n: int, total: int, sr: SampleResult) -> None:
        if sr.error:
            log.error("analysis error in sample %s:\n%s", sr.sample, sr.error)
        self.sampleDone.emit(n, total, sr)

    @Slot(object)
    def _on_exported(self, paths: dict) -> None:
        self.last_export = paths
        self.exported.emit(paths)

    @Slot(str)
    def _on_export_failed(self, message: str) -> None:
        self.last_export_error = message
        log.error("export failed: %s", message)
        self.exportFailed.emit(message)

    @Slot(str, str)
    def _on_failed(self, message: str, details: str) -> None:
        log.error("run failed: %s\n%s", message, details)
        self.runFailed.emit(message, details)

    @Slot(object)
    def _on_finished(self, result: RunResult | None) -> None:
        self._thread = self._worker = None
        if result is not None and not result.cancelled:
            self.result = result
            self._alignments = {}
            self.reviews.clear()  # decisions belong to the result they were made on (dict shared with views)
        log.info("run finished: %s", "failed" if result is None else
                 f"{len(result.samples)} samples{' (cancelled)' if result.cancelled else ''}")
        self.validityChanged.emit()
        self.runFinished.emit(result)

    # --- results ----------------------------------------------------------------------------------------

    def alignment_for(self, sr: SampleResult) -> AlignmentData:
        data = self._alignments.get(sr.sample)
        if data is None:
            data = alignment(sr, self.result.settings if self.result else self.settings)
            self._alignments[sr.sample] = data
        return data

    def results_stale(self) -> bool:
        """True when the settings or tags changed since the shown result was computed (ST-4)."""
        r = self.result
        if r is None:
            return False
        return (settings_to_dict(r.settings) != settings_to_dict(self.settings)
                or (r.forward_tags, r.reverse_tags) != (self.forward_tags, self.reverse_tags))

    # --- review (confirm PASS / FAIL) ---------------------------------------------------------------------

    def set_review(self, names: list[str] | str, status: str | None) -> None:
        """Confirm samples as "PASS"/"FAIL", or clear the decision with None."""
        names = [names] if isinstance(names, str) else list(names)
        changed = []
        for name in names:
            old = self.reviews.get(name)
            if status in ("PASS", "FAIL"):
                self.reviews[name] = status
            else:
                self.reviews.pop(name, None)
            if self.reviews.get(name) != old:
                changed.append(name)
        if changed:
            self.reviewsChanged.emit(changed)

    def confirm_unreviewed(self) -> int:
        """Confirm every sample not yet reviewed with the status the rules gave; returns how many."""
        if self.result is None:
            return 0
        todo = [s for s in self.result.samples if s.sample not in self.reviews]
        changed = [s.sample for s in todo]
        for s in todo:
            self.reviews[s.sample] = s.consensus.status
        if changed:
            self.reviewsChanged.emit(changed)
        return len(changed)

    def status_of(self, sr: SampleResult) -> str:
        return review.effective_status(sr, self.reviews)

    # --- clear ------------------------------------------------------------------------------------------

    def clear_all(self) -> None:
        """Forget the inputs, the output folder choice, the result and the reviews (Clear button).

        The reference, rule settings and filename tags are kept: they are settings, not run data.
        """
        if self.running:
            return
        self.inputs = []
        self.output_dir = ""
        self.scan_result = None
        self.result = None
        self.reviews.clear()
        self.last_export, self.last_export_error = None, ""
        self._alignments = {}
        self.save()
        self.inputsChanged.emit()
        self.scanChanged.emit()
        self.outputChanged.emit()
        self.validityChanged.emit()
        self.cleared.emit()

    def export(self, outdir: Path, keys: list[str], scope: str = "all", chosen=()) -> dict[str, Path]:
        """Write the chosen files for the current result (raises ExportError).

        ``scope`` limits the samples ("all", "pass", "fail" or "selected" with ``chosen``);
        reviewer decisions are applied to what is written.
        """
        names = review.select(self.result, self.reviews, scope, chosen)
        return write_outputs(review.apply(self.result, self.reviews, names), outdir, files=keys)
