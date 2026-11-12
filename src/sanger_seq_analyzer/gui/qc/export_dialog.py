"""Export dialog: choose a folder and which files to write (design spec 3.19; OU-3..OU-5, EX-3..EX-6)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from ... import review
from ...presentation import export_notice, plural
from ...report import EXPORT_FILES, ExportError, export_counts
from ..theme import current
from ..theme.icons import pixmap
from ..widgets.common import button, caps, divider, hbox, label, themed_icon
from ..widgets.notice import Notice
from ..widgets.path_field import PathField
from .controller import QcController


class _FileList(QFrame):
    """Bordered list of checkable files (painted border: radius does not clip children)."""

    def paintEvent(self, _e) -> None:
        t = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.c("border.default"), 1))
        p.setBrush(t.c("bg.surface"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


class ExportDialog(QDialog):
    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.c = controller
        run = controller.result
        self.setWindowTitle("Export results")
        self.setMinimumSize(760, 620)
        self.resize(780, 860)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 16)
        bl.setSpacing(14)
        ref = run.reference_info.name if run.reference_info else "the reference"
        bl.addWidget(label("Export results", "dialogTitle"))
        reviews = controller.reviews
        n_reviewed = review.reviewed_count(run, reviews)
        bl.addWidget(label(f"{plural(len(run.samples), 'sample')} analysed against {ref} · "
                           f"{review.count(run, reviews, 'PASS')} pass · {review.count(run, reviews, 'FAIL')} fail · "
                           f"{n_reviewed} of {len(run.samples)} reviewed", "secondary"))
        bl.addSpacing(2)
        bl.addWidget(caps("Output folder"))
        self.folder = PathField("Folder for the result files")
        self.folder.setFullText(str(controller.output_path() or ""))
        browse = button("Browse…", icon_name="folder")
        browse.clicked.connect(self._browse)
        bl.addLayout(hbox(self.folder, browse))
        bl.addWidget(caps("Samples to export"))
        self.scope_group = QButtonGroup(self)
        self.scope_buttons: dict[str, QRadioButton] = {}
        scope_row = QHBoxLayout()
        scope_row.setSpacing(18)
        for key, text in review.SCOPES.items():
            n = len(review.select(run, reviews, key)) if key != "selected" else None
            rb = QRadioButton(text if n is None else f"{text} ({n})")
            rb.setAccessibleName(text)
            self.scope_group.addButton(rb)
            self.scope_buttons[key] = rb
            rb.toggled.connect(self._scope_changed)
            scope_row.addWidget(rb)
        scope_row.addStretch(1)
        bl.addLayout(scope_row)
        self.chosen_box = QWidget()
        cl = QVBoxLayout(self.chosen_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.chosen_filter = QLineEdit()
        self.chosen_filter.setPlaceholderText("Filter samples")
        self.chosen_filter.setClearButtonEnabled(True)
        self.chosen_filter.textChanged.connect(self._filter_chosen)
        tick_all = button("Tick shown", size="small")
        tick_all.clicked.connect(lambda: self._tick_shown(True))
        untick = button("Untick shown", size="small")
        untick.clicked.connect(lambda: self._tick_shown(False))
        cl.addLayout(hbox(self.chosen_filter, tick_all, untick))
        self.chosen = QListWidget()
        self.chosen.setMaximumHeight(180)
        for sr in run.samples:
            status = review.effective_status(sr, reviews)
            mark = {"confirmed": " ✓", "changed": " ✓*"}.get(review.review_label(sr, reviews), "")
            item = QListWidgetItem(f"{sr.sample}  ·  {status}{mark}")
            item.setData(Qt.ItemDataRole.UserRole, sr.sample)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.chosen.addItem(item)
        self.chosen.itemChanged.connect(lambda _i: self._update())
        cl.addWidget(self.chosen)
        bl.addWidget(self.chosen_box)
        self.review_hint = Notice("", "warn")
        bl.addWidget(self.review_hint)
        if n_reviewed < len(run.samples):
            self.review_hint.set(f"{plural(len(run.samples) - n_reviewed, 'sample')} not reviewed yet: they are "
                                 "exported with the status the rules gave. Confirm or change them on the Results "
                                 "page.", "warn")
        else:
            self.review_hint.hide()
        bl.addWidget(caps("Files to write"))
        files = _FileList()
        fl = QVBoxLayout(files)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.setSpacing(0)
        counts = export_counts(run)
        self.checks: dict[str, QCheckBox] = {}
        self.count_labels: dict[str, QLabel] = {}
        for i, f in enumerate(EXPORT_FILES):
            if i:
                fl.addWidget(divider())
            row = QWidget()
            row.setFixedHeight(52)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 0, 16, 0)
            rl.setSpacing(10)
            check = QCheckBox()
            check.setChecked(True)
            check.setAccessibleName(f.filename)
            check.toggled.connect(self._update)
            self.checks[f.key] = check
            icon = QLabel()
            icon.setPixmap(pixmap("file", current()["text.tertiary"], 16))
            name = label(f.filename, "mono")
            name.setStyleSheet("font-weight: 500;")
            desc = label(f.description, "hint")
            count = label(counts[f.key], "secondary")
            self.count_labels[f.key] = count
            count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(check)
            rl.addWidget(icon)
            text = QVBoxLayout()
            text.setSpacing(1)
            text.addStretch(1)
            text.addWidget(name)
            text.addWidget(desc)
            text.addStretch(1)
            rl.addLayout(text, 1)
            rl.addWidget(count)
            fl.addWidget(row)
        bl.addWidget(files)
        notice = export_notice(run)
        self.warning = Notice(notice, "warn")
        self.warning.setVisible(bool(notice))
        bl.addWidget(self.warning)
        self.error = Notice("", "error")
        self.error.hide()
        bl.addWidget(self.error)
        self.success = Notice("", "success")
        self.success.hide()
        bl.addWidget(self.success)
        bl.addStretch(1)
        lay.addWidget(scroll, 1)
        footer = QFrame()
        footer.setProperty("band", True)
        footer.setStyleSheet("QFrame[band=\"true\"] { border-radius: 0; }")
        fo = QHBoxLayout(footer)
        fo.setContentsMargins(24, 12, 24, 12)
        fo.setSpacing(8)
        self.open_folder = button("Open output folder", icon_name="folder")
        self.open_folder.clicked.connect(self._open_folder)
        self.open_report = button("Open report", icon_name="external")
        self.open_report.clicked.connect(self._open_report)
        close = button("Close")
        close.clicked.connect(self.reject)
        self.export_button = button("Export", "primary", icon_name="export")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self.export)
        fo.addWidget(self.open_folder)
        fo.addWidget(self.open_report)
        fo.addStretch(1)
        fo.addWidget(close)
        fo.addWidget(self.export_button)
        lay.addWidget(footer)
        self.folder.pathEdited.connect(lambda _t: self._update())
        scope = controller.store.get_str("dialogs/export_scope", "all")
        self.scope_buttons.get(scope, self.scope_buttons["all"]).setChecked(True)
        self._scope_changed()
        self.export_button.setFocus()

    def keys(self) -> list[str]:
        return [k for k, c in self.checks.items() if c.isChecked()]

    def scope(self) -> str:
        return next((k for k, b in self.scope_buttons.items() if b.isChecked()), "all")

    def set_scope(self, scope: str) -> None:
        self.scope_buttons[scope].setChecked(True)

    def chosen_names(self) -> list[str]:
        return [self.chosen.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.chosen.count())
                if self.chosen.item(i).checkState() == Qt.CheckState.Checked]

    def set_chosen(self, names) -> None:
        wanted = set(names)
        for i in range(self.chosen.count()):
            item = self.chosen.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in wanted
                               else Qt.CheckState.Unchecked)

    def sample_names(self) -> list[str]:
        return review.select(self.c.result, self.c.reviews, self.scope(), self.chosen_names())

    def _scope_changed(self, *_args) -> None:
        self.chosen_box.setVisible(self.scope() == "selected")
        self._update()

    def _filter_chosen(self, text: str) -> None:
        q = text.strip().lower()
        for i in range(self.chosen.count()):
            item = self.chosen.item(i)
            item.setHidden(bool(q) and q not in item.text().lower())

    def _tick_shown(self, on: bool) -> None:
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for i in range(self.chosen.count()):
            if not self.chosen.item(i).isHidden():
                self.chosen.item(i).setCheckState(state)

    def outdir(self) -> Path | None:
        text = self.folder.fullText().strip()
        return Path(text) if text else None

    def _update(self) -> None:
        if not hasattr(self, "export_button"):
            return  # still building the dialog
        n = len(self.keys())
        names = self.sample_names()
        n_samples = len(names)
        everything = n_samples == len(self.c.result.samples) and not self.c.reviews
        self.export_button.setText(f"Export {plural(n, 'file')}" if everything
                                   else f"Export {plural(n, 'file')} · {plural(n_samples, 'sample')}")
        counts = export_counts(review.apply(self.c.result, self.c.reviews, names))
        for key, count_label in self.count_labels.items():
            count_label.setText(counts[key])
        self.export_button.setEnabled(bool(n) and bool(n_samples) and self.outdir() is not None)
        out = self.outdir()
        self.open_folder.setEnabled(out is not None and out.is_dir())
        self.open_report.setEnabled(out is not None and (out / "report.html").is_file())

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the output folder", self.folder.fullText())
        if folder:
            self.folder.setFullText(folder)
            self._update()

    def existing(self) -> list[str]:
        out = self.outdir()
        names = {f.key: f.filename for f in EXPORT_FILES}
        return [names[k] for k in self.keys() if out is not None and (out / names[k]).exists()]

    def export(self, confirm: bool = True) -> bool:
        out = self.outdir()
        if out is None:
            return False
        existing = self.existing()
        if confirm and existing:
            answer = QMessageBox.question(self, "Replace files", "These files already exist in the folder and will "
                                          "be replaced:\n\n" + "\n".join(existing))
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.error.hide()
        self.success.hide()
        try:
            paths = self.c.export(out, self.keys(), self.scope(), self.chosen_names())
        except ExportError as exc:
            self.error.set(f"Nothing more was written. {str(exc)[:1].upper() + str(exc)[1:]}. Close the file if "
                           "another program has it open, or choose another folder.", "error")
            self.error.show()
            self._update()
            return False
        self.c.store.set("dialogs/export", str(out))
        self.c.store.set("dialogs/export_scope", self.scope())
        self.c.last_export = paths
        self.c.exported.emit(paths)
        self.success.set(f"{plural(len(paths), 'file')} for {plural(len(self.sample_names()), 'sample')} written "
                         f"to {out}", "success")
        self.success.show()
        self.export_button.setText("Exported")
        themed_icon(self.export_button, "check", "text.onAccent")
        QTimer.singleShot(2000, self._restore_button)
        self._update_open()
        return True

    def _update_open(self) -> None:
        out = self.outdir()
        self.open_folder.setEnabled(out is not None and out.is_dir())
        self.open_report.setEnabled(out is not None and (out / "report.html").is_file())

    def _restore_button(self) -> None:
        themed_icon(self.export_button, "export", "text.onAccent")
        self._update()

    def _open_folder(self) -> None:
        out = self.outdir()
        if out is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

    def _open_report(self) -> None:
        out = self.outdir()
        if out is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out / "report.html")))
