"""Runs the analysis off the GUI thread (RN-2).

The worker only emits signals; it never touches widgets, and results are not
changed after they were emitted. The controller connects these signals to its
own slots (never to lambdas, which would run in the worker thread).
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ... import pipeline
from ...pipeline import ChromCache, ScanResult
from ...qc import QCSettings
from ...references import ReferenceInfo
from ...report import ExportError, write_outputs


@dataclass(frozen=True)
class RunJob:
    inputs: tuple[Path, ...]
    reference: ReferenceInfo
    settings: QCSettings
    forward_tags: tuple[str, ...]
    reverse_tags: tuple[str, ...]
    scan: ScanResult
    export_dir: Path | None  # None: do not write files when the run finishes
    write_html: bool = True


class AnalysisWorker(QObject):
    progress = Signal(int, int, str)
    sampleDone = Signal(int, int, object)
    exported = Signal(object)  # dict[str, Path]
    exportFailed = Signal(str)
    failed = Signal(str, str)  # message, traceback
    finished = Signal(object)  # RunResult, or None after a failure

    def __init__(self, job: RunJob, cache: ChromCache, cancel: threading.Event):
        super().__init__()
        self.job, self.cache, self.cancel = job, cache, cancel

    @Slot()
    def run(self) -> None:
        job = self.job
        try:
            result = pipeline.run(list(job.inputs), job.reference, job.settings, job.forward_tags, job.reverse_tags,
                                  progress=self.progress.emit, should_cancel=self.cancel.is_set,
                                  on_sample=self._sample_done, cache=self.cache, scan_result=job.scan)
        except Exception as exc:  # anything unexpected outside the per-sample guard
            self.failed.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
            self.finished.emit(None)
            return
        if job.export_dir is not None and not result.cancelled:  # a cancelled run is never exported (RN-4)
            try:
                self.exported.emit(write_outputs(result, job.export_dir, html_report=job.write_html))
            except ExportError as exc:
                self.exportFailed.emit(str(exc))
        self.finished.emit(result)

    def _sample_done(self, n: int, total: int, sample) -> None:
        self.sampleDone.emit(n, total, sample)
