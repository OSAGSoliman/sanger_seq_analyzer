"""Settings page: defaults, form to QCSettings, reset (AT-G02, AT-G03, AT-G04)."""

import pytest

from sanger_seq_analyzer.gui.widgets.switch import Switch
from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.settings_schema import FIELDS, from_display, to_display

DISPLAY_DEFAULTS = {"min_good_fraction": 75, "mixture_area": 20, "mark_area": 17.5, "max_mixture_fraction": 3.5,
                    "max_n": 5, "max_marks": 100, "min_quality": 20}


def _value(control):
    return control.isChecked() if isinstance(control, Switch) else control.value()


def test_defaults_on_first_start(window):
    tool = window.tools[0]
    page = tool.settings
    assert tool.controller.reference_spec == "builtin:measles-n450"
    assert tool.run_page.setup.reference.currentText() == "measles-n450"
    for name, shown in DISPLAY_DEFAULTS.items():
        assert _value(page.controls[name]) == shown, name
    assert page.form_settings() == QCSettings()
    assert page.chip.text() == "All values at defaults" and not page.reset_all.isEnabled()


def _new_value(meta, current):
    if meta.kind == "bool":
        return not current
    step = meta.step if meta.kind != "int" else 1
    candidate = current + step
    return candidate if candidate <= meta.maximum else current - step


@pytest.mark.parametrize("meta", [f for f in FIELDS if f.name not in ("check_stop_codons", "check_frameshifts")],
                         ids=lambda m: m.name)
def test_each_control_sets_its_field(window, meta):
    tool = window.tools[0]
    page = tool.settings
    page.advanced_toggle.setChecked(True)
    control = page.controls[meta.name]
    if meta.name == "mixture_area":
        assert control.isEnabled()
    shown = _new_value(meta, to_display(meta, getattr(QCSettings(), meta.name)))
    if isinstance(control, Switch):
        control.setChecked(shown)
    else:
        control.setValue(shown)
    expected = from_display(meta, shown)
    assert getattr(tool.controller.settings, meta.name) == pytest.approx(expected), meta.name
    assert page.rows[meta.name].reset.isVisibleTo(page)


def test_non_coding_switch_turns_off_both_checks(window):
    page = window.tools[0].settings
    page.controls["non_coding"].setChecked(True)
    s = window.tools[0].controller.settings
    assert not s.check_stop_codons and not s.check_frameshifts and not page.controls["frame"].isEnabled()


def test_reset_all(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tool = window.tools[0]
    page = tool.settings
    page.controls["min_good_fraction"].setValue(70)
    page.controls["call_mixtures"].setChecked(False)
    page.controls["forward_tags"].setText("FW")
    page._tags_edited()
    assert page.chip.text() == "3 changed" and not page.controls["mixture_area"].isEnabled()
    page.reset_to_defaults()
    assert tool.controller.settings == QCSettings() and tool.controller.forward_tags[0] == "F"
    assert all(not r.reset.isVisibleTo(page) for r in page.rows.values())
    assert page.chip.text() == "All values at defaults"
