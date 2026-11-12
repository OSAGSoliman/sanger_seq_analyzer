"""The QC tool: Run, Results, Fetch data and Settings pages around one :class:`QcController`."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMessageBox, QWidget

from ...pipeline import AB1_SUFFIXES, RunResult
from ...presentation import plural
from ..persist import Store
from ..tools import ToolSpec, ToolUI
from .controller import QcController
from .examples import find_examples
from .export_dialog import ExportDialog
from .results_page import ResultsPage
from .run_page import RunPage
from .settings_page import SettingsPage


def _short(path: Path) -> str:
    try:
        return "~/" + path.resolve().relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


class QcTool(ToolUI):
    def __init__(self, window: QWidget, store: Store):
        super().__init__(window)
        self.window = window
        self.controller = c = QcController(store, self)
        self.run_page = RunPage(c)
        self.results = ResultsPage(c)
        self.settings = SettingsPage(c)
        from ..fetch import FetchController, FetchPage
        self.fetch_controller = FetchController(store, self)
        self.fetch = FetchPage(self.fetch_controller, c)
        self.results.restore_view_options()
        self.run_page.setup.runRequested.connect(self.start_run)
        self.run_page.setup.clearRequested.connect(self.clear_all)
        self.run_page.setup.editSettings.connect(lambda: self.pageRequested.emit("settings"))
        self.results.newRunRequested.connect(lambda: self.pageRequested.emit("run"))
        self.results.exportRequested.connect(self.export)
        c.runStarted.connect(self._run_started)
        c.runProgress.connect(self._progress)
        c.sampleDone.connect(self._sample_done)
        c.runFinished.connect(self._run_finished)
        c.runFailed.connect(self._run_failed)
        c.inputsChanged.connect(self._update_context)
        c.cleared.connect(self._cleared)
        self._actions = self._make_actions()
        c.validityChanged.connect(self._update_actions)
        self._update_actions()

    # --- ToolUI -----------------------------------------------------------------------------------------

    def pages(self):
        return [("run", "Run", self.run_page), ("results", "Results", self.results),
                ("fetch", "Fetch data", self.fetch), ("settings", "Settings", self.settings)]

    def start(self) -> None:
        """Called once the shell has created the tabs."""
        self.pageEnabledChanged.emit("results", False)
        self._update_context()

    def actions(self) -> dict[str, QAction]:
        return self._actions

    def _make_actions(self) -> dict[str, QAction]:
        def act(text: str, slot, shortcut=None) -> QAction:
            a = QAction(text, self.window)
            if shortcut is not None:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            return a

        chrom = self.results.detail.chrom
        return {
            "add_folder": act("Add folder…", self._add_folder, QKeySequence.StandardKey.Open),
            "add_files": act("Add files…", self._add_files),
            "load_examples": act("Load example data", self._load_examples),
            "export": act("Export…", self.export, "Ctrl+E"),
            "run": act("Run analysis", self.start_run, "Ctrl+R"),
            "cancel": act("Cancel run", self.controller.cancel_run, Qt.Key.Key_Escape),
            "find": act("Find sample", self._focus_search, QKeySequence.StandardKey.Find),
            "zoom_in": act("Zoom in", lambda: chrom.zoom_step(1), QKeySequence.StandardKey.ZoomIn),
            "zoom_out": act("Zoom out", lambda: chrom.zoom_step(-1), QKeySequence.StandardKey.ZoomOut),
            "zoom_reset": act("Actual size", chrom.reset_zoom, "Ctrl+0"),
        }

    def can_close(self) -> bool:
        if self.controller.running:
            answer = QMessageBox.question(self.window, "Analysis running",
                                          "An analysis is running. Cancel it and close the window?")
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self.controller.shutdown()
        return True

    def save_state(self) -> None:
        self.controller.save()
        self.results.save_state()

    def accept_drop(self, paths: list[Path]) -> None:
        usable = [p for p in paths if p.is_dir() or p.suffix.lower() in AB1_SUFFIXES]
        ignored = len(paths) - len(usable)
        if usable:
            self.controller.add_inputs(usable)
        self.pageRequested.emit("run")
        if ignored:
            self.run_page.setup.show_notice(f"{plural(ignored, 'file')} ignored: not .ab1", "neutral")

    # --- run --------------------------------------------------------------------------------------------

    def start_run(self) -> None:
        reason = self.controller.run_blocked_reason()
        if reason:
            self.pageRequested.emit("run")
            return
        self._keep = self.results.current_sample()
        self.controller.start_run()

    def _run_started(self, names: list[str]) -> None:
        self.run_page.show_running(names)
        self.pageRequested.emit("run")
        for key in ("results", "settings"):
            self.pageEnabledChanged.emit(key, False)
        self.contextTextChanged.emit(f"Running · 0 of {len(names)} samples finished")
        self._update_actions()

    def _progress(self, n: int, total: int, name: str) -> None:
        self.run_page.running.progress(n, total, name)

    def _sample_done(self, n: int, total: int, sr) -> None:
        self.run_page.running.sample_done(n, total, sr)
        self.contextTextChanged.emit(f"Running · {n} of {total} samples finished")

    def _run_finished(self, result: RunResult | None) -> None:
        self.run_page.run_ended(result)
        self.pageEnabledChanged.emit("settings", True)
        has_result = self.controller.result is not None
        self.pageEnabledChanged.emit("results", has_result)
        if result is not None and not result.cancelled:
            self.results.show_run(result, getattr(self, "_keep", None))
            if self.controller.last_export_error:
                self.results.show_notice(f"Output files were not written: {self.controller.last_export_error}")
            else:
                self.results.hide_notice()
            self.pageRequested.emit("results")
        self._update_context()
        self._update_actions()

    def _run_failed(self, message: str, details: str) -> None:
        self.run_page.setup.show_notice(f"The analysis stopped with an error: {message}", "error", details)

    def clear_all(self) -> None:
        c = self.controller
        if c.running:
            return
        if c.result is not None:
            reviewed = len(c.reviews)
            extra = f" and your review of {plural(reviewed, 'sample')}" if reviewed else ""
            answer = QMessageBox.question(self.window, "Clear", f"Clear the inputs, the results{extra}? Output "
                                          "files already written stay on disk. Rules, reference and filename tags "
                                          "are kept.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        c.clear_all()

    def _cleared(self) -> None:
        self.run_page.show_setup()
        self.run_page.setup.hide_notice()
        self.results.hide_notice()
        self.pageEnabledChanged.emit("results", False)
        self._update_context()
        self._update_actions()

    def export(self) -> None:
        if self.controller.result is None:
            return
        ExportDialog(self.controller, self.window).exec()

    # --- helpers ----------------------------------------------------------------------------------------

    def _update_context(self) -> None:
        c = self.controller
        if c.running:
            return
        if c.inputs:
            self.contextTextChanged.emit(", ".join(_short(p) for p in c.inputs))
        else:
            self.contextTextChanged.emit("No results yet" if c.result is None else "")

    def _update_actions(self) -> None:
        a, c = self._actions, self.controller
        a["run"].setEnabled(not c.running and not c.run_blocked_reason())
        a["cancel"].setEnabled(c.running)
        a["export"].setEnabled(c.result is not None and not c.running)
        a["load_examples"].setEnabled(find_examples() is not None and not c.running)

    def _add_folder(self) -> None:
        self.pageRequested.emit("run")
        self.run_page.setup.browse_folder()

    def _add_files(self) -> None:
        self.pageRequested.emit("run")
        self.run_page.setup.browse_files()

    def _load_examples(self) -> None:
        self.pageRequested.emit("run")
        self.run_page.setup.load_examples()

    def _focus_search(self) -> None:
        if self.controller.result is not None:
            self.pageRequested.emit("results")
            self.results.list.search.setFocus()
            self.results.list.search.selectAll()


SPEC = ToolSpec("qc", "Sanger QC", QcTool)
