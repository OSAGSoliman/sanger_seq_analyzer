"""Settings page, built from :data:`settings_schema.FIELDS` (design spec 1.6, 3.16; FR 2.6)."""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea,
    QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from ...pipeline import FORWARD_TAGS, REVERSE_TAGS, split_name, validate_tags
from ...presentation import plural, skipped_rows
from ...qc import QCSettings
from ...settings_schema import FIELD_BY_NAME, FieldMeta, changed_fields, format_display, from_display, to_display, \
    validate
from ..theme import current
from ..theme.icons import pixmap
from ..widgets.badge import Chip
from ..widgets.common import button, card, divider, hbox, label, set_prop, themed_icon, vbox
from ..widgets.notice import Notice
from ..widgets.switch import Switch
from .controller import QcController

DEFAULTS = QCSettings()
GROUPS = {
    "lab": ["min_good_fraction", "min_quality", "min_trimmed_length", "require_both_directions"],
    "assembly": ["censor_quality", "call_mixtures", "mixture_area", "mark_area", "mark_avg_quality"],
    "rejection": ["max_mixture_fraction", "max_n", "max_marks", "single_coverage_quality", "max_single_coverage_run"],
    "coding": ["frame"],
    "advanced": ["trim_error_limit", "min_read_identity", "min_read_aligned", "require_full_reference",
                 "check_read_orientation"],
}
NON_CODING_HELP = ("Table 2 rejects stop codons and frameshift indels in a protein-coding target. Turn this on for "
                   "a non-coding target to skip both checks.")


def _tags(text: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in text.split(",") if t.strip())


class _Row:
    """One settings row: name and hint, control, default text and reset link."""

    def __init__(self, page: SettingsPage, grid: QGridLayout, row: int, title: str, hint: str, control: QWidget,
                 unit: str = "", help_text: str = ""):
        self.page = page
        name = label(title)
        name.setStyleSheet("font-weight: 500;")
        hint_label = label(hint, "hint", wrap=True)
        left = vbox(name, hint_label, spacing=1)
        if help_text:
            name.setToolTip(help_text)
            hint_label.setToolTip(help_text)
            control.setToolTip(help_text)
        self.control = control
        middle = QHBoxLayout()
        middle.setSpacing(6)
        if unit == "Q":
            middle.addWidget(label("Q", "secondary"))
        middle.addWidget(control)
        if unit and unit != "Q":
            middle.addWidget(label(unit, "secondary"))
        middle.addStretch(1)
        self.default = label("", "defaultText")
        self.default.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.reset = button("Reset", "link")
        self.reset.hide()
        right = vbox(self.default, self.reset, spacing=0)
        right.setAlignment(self.reset, Qt.AlignmentFlag.AlignRight)
        self.error = label("", "error", wrap=True)
        self.error.hide()
        grid.addLayout(left, row, 0)
        grid.addLayout(vbox(middle, self.error, spacing=2), row, 1)
        grid.addLayout(right, row, 2)
        grid.addWidget(divider(), row + 1, 0, 1, 3)

    def set_state(self, default_text: str, changed: bool) -> None:
        self.default.setText(f"Default {default_text}")
        set_prop(self.default, "changed", changed)
        self.reset.setVisible(changed)

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
        set_prop(self.control, "invalid", bool(message))


class SettingsPage(QWidget):
    def __init__(self, controller: QcController, parent=None):
        super().__init__(parent)
        self.setProperty("page", True)
        self.c = controller
        self.controls: dict[str, QWidget] = {}
        self.rows: dict[str, _Row] = {}
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        page.setProperty("page", True)
        scroll.setWidget(page)
        center = QHBoxLayout(page)
        center.setContentsMargins(24, 32, 24, 32)
        column = QWidget()
        column.setMaximumWidth(1200)
        center.addStretch(1)
        center.addWidget(column, 100)
        center.addStretch(1)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        self.subtitle = label("", "secondary")
        self.chip = Chip("All values at defaults")
        self.reset_all = button("Reset to defaults", icon_name="reset")
        self.reset_all.clicked.connect(self.reset_to_defaults)
        lay.addLayout(hbox(vbox(label("Settings", "pageTitle"), self.subtitle, spacing=4), None, self.chip,
                           self.reset_all, spacing=12))
        columns = QHBoxLayout()
        columns.setSpacing(20)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(20)
        right.setSpacing(20)
        left.addWidget(self._lab_card())
        left.addWidget(self._files_card())
        left.addStretch(1)
        right.addWidget(self._recall_card())
        right.addWidget(self._advanced_card())
        right.addStretch(1)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        lay.addLayout(columns)
        lay.addStretch(1)
        controller.settingsChanged.connect(self.load)
        controller.referenceChanged.connect(self.load)
        controller.runFinished.connect(lambda _r: self.refresh_files())
        self.load()

    # --- construction -----------------------------------------------------------------------------------

    def _card(self, title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
        frame, lay = card(margins=(0, 0, 0, 6), spacing=0)
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(16, 14, 16, 10)
        hl.setSpacing(2)
        hl.addWidget(label(title, "cardTitle"))
        sub = label(subtitle, "hint")
        hl.addWidget(sub)
        lay.addWidget(head)
        lay.addWidget(divider())
        return frame, lay, sub

    def _grid(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(16, 0, 16, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(0, 1)
        grid.setColumnMinimumWidth(2, 86)
        return grid

    def _group(self, lay: QVBoxLayout, title: str | None, fields: list[str], extra=None) -> QGridLayout:
        if title:
            head = label(title, "groupTitle")
            head.setContentsMargins(16, 12, 16, 2)
            lay.addWidget(head)
        grid = self._grid()
        row = 0
        for name in fields:
            self._field_row(grid, row, FIELD_BY_NAME[name])
            row += 2
        if extra:
            row = extra(grid, row)
        lay.addLayout(grid)
        return grid

    def _field_row(self, grid: QGridLayout, row: int, meta: FieldMeta) -> None:
        if meta.kind == "bool":
            control = Switch()
            control.toggled.connect(lambda _on, n=meta.name: self._changed(n))
        else:
            if meta.kind == "int":
                control = QSpinBox()
                control.setRange(int(meta.minimum), int(meta.maximum))
            else:
                control = QDoubleSpinBox()
                control.setDecimals(meta.decimals if meta.kind == "percent" or meta.decimals else 1)
                control.setRange(meta.minimum, meta.maximum)
                control.setSingleStep(meta.step)
            control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            control.setAlignment(Qt.AlignmentFlag.AlignRight)
            control.setFixedWidth(76)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(lambda _v, n=meta.name: self._changed(n))
        self.controls[meta.name] = control
        r = _Row(self, grid, row, meta.label, meta.hint, control, meta.unit, meta.help)
        r.reset.clicked.connect(lambda _=False, n=meta.name: self._reset_field(n))
        self.rows[meta.name] = r

    def _lab_card(self) -> QFrame:
        frame, lay, _ = self._card("Lab read rules", "Checked first, per read")
        self._group(lay, None, GROUPS["lab"], self._tag_rows)
        self.both_warning = Notice("Single-read samples still fail RECall's mark and single-coverage limits unless "
                                   "those are relaxed (every position of a single read is single coverage).", "warn")
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(16, 6, 16, 6)
        wl.addWidget(self.both_warning)
        lay.addWidget(wrap)
        return frame

    def _tag_rows(self, grid: QGridLayout, row: int) -> int:
        for key, title, hint in (("forward_tags", "Forward filename tags",
                                  "Comma-separated; matched after _ - . or a space"),
                                 ("reverse_tags", "Reverse filename tags",
                                  "The sample name is everything before the last tag")):
            edit = QLineEdit()
            edit.setProperty("mono", True)
            edit.setMinimumWidth(200)
            edit.editingFinished.connect(self._tags_edited)
            self.controls[key] = edit
            r = _Row(self, grid, row, title, hint, edit)
            r.reset.clicked.connect(self._reset_tags)
            self.rows[key] = r
            row += 2
        self.tag_example = label("", "hint", wrap=True)
        grid.addWidget(self.tag_example, row, 0, 1, 3)
        return row + 1

    def _recall_card(self) -> QFrame:
        frame, lay, _ = self._card("RECall rules", "Defaults from Woods et al. 2012, Tables 1 and 2")
        self._group(lay, "Assembly, mixtures and marks", GROUPS["assembly"])
        lay.addWidget(divider())
        self._group(lay, "Rejection limits", GROUPS["rejection"])
        lay.addWidget(divider())
        self._group(lay, "Coding frame", GROUPS["coding"], self._non_coding_row)
        return frame

    def _non_coding_row(self, grid: QGridLayout, row: int) -> int:
        control = Switch()
        control.toggled.connect(lambda _on: self._changed("non_coding"))
        self.controls["non_coding"] = control
        r = _Row(self, grid, row, "Non-coding target", "Skip the stop-codon and frameshift checks", control,
                 help_text=NON_CODING_HELP)
        r.reset.clicked.connect(lambda: self._reset_field("non_coding"))
        self.rows["non_coding"] = r
        return row + 2

    def _advanced_card(self) -> QFrame:
        frame, lay = card(margins=(0, 0, 0, 6), spacing=0)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced: trimming, alignment and coverage")
        self.advanced_toggle.setProperty("variant", "flat")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setStyleSheet("font-weight: 600; padding: 12px 10px;")
        themed_icon(self.advanced_toggle, "chevron-right", "text.secondary", 14)
        lay.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        al = QVBoxLayout(self.advanced)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(divider())
        self._group(al, None, GROUPS["advanced"])
        self.advanced.hide()
        lay.addWidget(self.advanced)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        return frame

    def _toggle_advanced(self, on: bool) -> None:
        self.advanced.setVisible(on)
        themed_icon(self.advanced_toggle, "chevron-down" if on else "chevron-right", "text.secondary", 14)

    def _files_card(self) -> QFrame:
        frame, lay, self.files_subtitle = self._card("Files not analysed", "Last run")
        self.files_body = QVBoxLayout()
        self.files_body.setContentsMargins(16, 8, 16, 10)
        self.files_body.setSpacing(6)
        lay.addLayout(self.files_body)
        return frame

    # --- state ------------------------------------------------------------------------------------------

    def load(self) -> None:
        """Show the controller's settings in the form."""
        self._loading = True
        s = self.c.settings
        ref_len = self.c.reference.length if self.c.reference else None
        for name, control in self.controls.items():
            if name in FIELD_BY_NAME:
                meta = FIELD_BY_NAME[name]
                value = to_display(meta, getattr(s, name))
                if isinstance(control, Switch):
                    control.setChecked(bool(value))
                else:
                    if name in ("frame", "min_read_aligned") and ref_len:
                        control.setMaximum(ref_len)
                    control.setValue(value)
        self.controls["non_coding"].setChecked(not (s.check_stop_codons or s.check_frameshifts))
        self.controls["forward_tags"].setText(", ".join(self.c.forward_tags))
        self.controls["reverse_tags"].setText(", ".join(self.c.reverse_tags))
        self._loading = False
        self._refresh_state()

    def form_settings(self) -> QCSettings:
        """The settings the form currently shows."""
        values = {}
        for name, control in self.controls.items():
            if name in FIELD_BY_NAME:
                shown = control.isChecked() if isinstance(control, Switch) else control.value()
                values[name] = from_display(FIELD_BY_NAME[name], shown)
        coding = not self.controls["non_coding"].isChecked()
        values["check_stop_codons"] = values["check_frameshifts"] = coding
        return dataclasses.replace(self.c.settings, **values)

    def _changed(self, name: str) -> None:
        if self._loading:
            return
        self.c.set_settings(self.form_settings())
        self._refresh_state()

    def _tags_edited(self) -> None:
        self.c.set_tags(_tags(self.controls["forward_tags"].text()), _tags(self.controls["reverse_tags"].text()))
        self._refresh_state()

    def _reset_field(self, name: str) -> None:
        if name == "non_coding":
            s = dataclasses.replace(self.c.settings, check_stop_codons=True, check_frameshifts=True)
        else:
            s = dataclasses.replace(self.c.settings, **{name: getattr(DEFAULTS, name)})
        self.c.set_settings(s)

    def _reset_tags(self) -> None:
        self.c.set_tags(FORWARD_TAGS, REVERSE_TAGS)
        self.load()

    def reset_to_defaults(self) -> None:
        if self._n_changed():
            answer = QMessageBox.question(self, "Reset to defaults", "Restore every rule and the filename tags to "
                                          "the published defaults?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.c.set_tags(FORWARD_TAGS, REVERSE_TAGS)
        self.c.set_settings(QCSettings())
        self.load()

    def _n_changed(self) -> int:
        tags_changed = (self.c.forward_tags, self.c.reverse_tags) != (FORWARD_TAGS, REVERSE_TAGS)
        return len(changed_fields(self.c.settings)) + tags_changed

    def _refresh_state(self) -> None:
        s = self.c.settings
        changed = set(changed_fields(s))
        for name, row in self.rows.items():
            if name in FIELD_BY_NAME:
                row.set_state(format_display(FIELD_BY_NAME[name], getattr(DEFAULTS, name)), name in changed)
        coding_changed = bool({"check_stop_codons", "check_frameshifts"} & changed)
        self.rows["non_coding"].set_state("Off", coding_changed)
        for name, default, current_tags in (("forward_tags", FORWARD_TAGS, self.c.forward_tags),
                                            ("reverse_tags", REVERSE_TAGS, self.c.reverse_tags)):
            self.rows[name].set_state("", current_tags != default)
            self.rows[name].default.setText("Changed" if current_tags != default else "Default")
            self.rows[name].default.setToolTip("Default: " + ", ".join(default))
        ref_len = self.c.reference.length if self.c.reference else None
        problems = {}
        for p in validate(s, ref_len):
            problems.setdefault(p.field, p.message)
        for name, row in self.rows.items():
            if name in FIELD_BY_NAME:
                row.set_error(problems.get(name, ""))
        tag_problems = validate_tags(self.c.forward_tags, self.c.reverse_tags)
        self.rows["forward_tags"].set_error("; ".join(tag_problems))
        self.rows["reverse_tags"].set_error("")
        self.controls["mixture_area"].setEnabled(s.call_mixtures)
        self.controls["frame"].setEnabled(s.check_stop_codons or s.check_frameshifts)
        self.both_warning.setVisible(not s.require_both_directions)
        n = self._n_changed()
        self.chip.set("All values at defaults" if not n else f"{n} changed", "neutral" if not n else "warn")
        self.reset_all.setEnabled(bool(n))
        stem = "MV01_" + (self.c.forward_tags[0] if self.c.forward_tags else "F") + "_A03"
        sample, direction = split_name(stem, self.c.forward_tags, self.c.reverse_tags)
        example = f"{stem}.ab1 → sample {sample}, {'forward' if direction == 'F' else 'reverse'}" if direction else \
            f"{stem}.ab1 → no direction tag"
        self.tag_example.setText(f"Example: {example}")
        result = self.c.result
        if result is None:
            self.subtitle.setText("Used for the next run.")
        elif self.c.results_stale():
            self.subtitle.setText("Used for the next run. The current results were produced with different settings.")
        else:
            label_text = "the defaults" if not changed_fields(result.settings) else "these settings"
            self.subtitle.setText(f"Used for the next run. The current results were produced with {label_text}.")
        self.refresh_files()

    def refresh_files(self) -> None:
        body = self.files_body
        while body.count():
            item = body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear(item.layout())
        result = self.c.result
        rows = skipped_rows(result) if result else []
        if result:
            self.files_subtitle.setText("Last run · " + ", ".join(str(p) for p in result.inputs))
        skipped = [r for r in rows if r.kind == "skipped"]
        unreadable = [r for r in rows if r.kind == "unreadable"]
        for title, hint, items in (("Without a forward/reverse tag, or duplicates",
                                    "Rename so the name ends in one of the tags above, then re-run", skipped),
                                   ("Unreadable .ab1 files", "Shown with the error reported when opening the file",
                                    unreadable)):
            count = label(plural(len(items), "file"), "secondary")
            body.addLayout(hbox(vbox(label(title), label(hint, "hint"), spacing=1), None, count))
            for r in items:
                icon = QLabel()
                icon.setPixmap(pixmap("warning", current()["warn.fg"], 16))
                name = label(r.name, "mono")
                cause = label(r.cause, "hint", wrap=True)
                show = button("Show in folder", size="small")
                show.clicked.connect(lambda _=False, p=r.path: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(p.parent))))
                body.addLayout(hbox(icon, vbox(name, cause, spacing=0), None, show))
        if not result:
            body.addWidget(label("Run an analysis to see which files were left out.", "hint"))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
