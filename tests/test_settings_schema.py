"""Settings metadata, validation and storage round trip (FR 2.6, AT-B18)."""

import dataclasses
import json

from sanger_seq_analyzer.qc import QCSettings
from sanger_seq_analyzer.settings_schema import (
    FIELD_BY_NAME, FIELDS, changed_fields, format_display, from_display, rule_set_label, settings_from_dict,
    settings_to_dict, to_display, validate,
)


def test_every_setting_has_metadata():
    assert {f.name for f in dataclasses.fields(QCSettings)} == {f.name for f in FIELDS}
    assert len(FIELDS) == len(FIELD_BY_NAME)


def test_display_conversion():
    cutoff, max_n, max_marks = FIELD_BY_NAME["min_good_fraction"], FIELD_BY_NAME["max_n"], FIELD_BY_NAME["max_marks"]
    assert to_display(cutoff, 0.75) == 75 and from_display(cutoff, 75) == 0.75
    assert to_display(FIELD_BY_NAME["mark_area"], 0.175) == 17.5
    assert to_display(max_n, 4) == 5 and from_display(max_n, 5) == 4
    assert to_display(max_marks, 99) == 100 and from_display(max_marks, 100) == 99
    assert format_display(cutoff, 0.75) == "75%" and format_display(FIELD_BY_NAME["min_quality"], 20) == "Q20"
    assert format_display(FIELD_BY_NAME["mark_area"], 0.175) == "17.5%"
    assert format_display(FIELD_BY_NAME["min_trimmed_length"], 100) == "100 bp"


def test_round_trip_through_json():
    s = QCSettings(min_good_fraction=0.7, max_n=9, call_mixtures=False, trim_error_limit=0.01)
    back, problems = settings_from_dict(json.loads(json.dumps(settings_to_dict(s))))
    assert back == s and problems == []


def test_unknown_keys_are_ignored_and_bad_values_reset():
    back, problems = settings_from_dict({"min_quality": 25, "colour": "blue", "frame": 0, "call_mixtures": "yes"})
    assert back.min_quality == 25 and back.frame == 1 and back.call_mixtures is True
    assert sorted(p.field for p in problems) == ["call_mixtures", "frame"]


def test_validate_ranges_and_cross_field_rules():
    assert validate(QCSettings()) == []
    problems = validate(QCSettings(frame=0, min_good_fraction=1.5, censor_quality=30), reference_length=450)
    assert {p.field for p in problems if p.severity == "error"} == {"frame", "min_good_fraction", "censor_quality"}
    assert any(p.field == "frame" for p in validate(QCSettings(frame=451), reference_length=450))
    warn = validate(QCSettings(mark_area=0.3))
    assert [(p.field, p.severity) for p in warn] == [("mark_area", "warning")]


def test_changed_fields():
    assert changed_fields(QCSettings()) == [] and rule_set_label(QCSettings()) == "defaults"
    s = QCSettings(min_good_fraction=0.7, max_marks=50)
    assert changed_fields(s) == ["min_good_fraction", "max_marks"] and rule_set_label(s) == "2 changed"
