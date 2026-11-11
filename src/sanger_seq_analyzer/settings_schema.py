"""Metadata, validation and serialisation for :class:`qc.QCSettings`.

The settings form in the window and the stored preferences are built from
``FIELDS``, so every field has one label, unit, range, group and help text.
Values are shown in display units: fractions as percentages and the "fail at"
limits (``max_n``, ``max_marks``) as the count that fails (stored value + 1).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .qc import QCSettings

DEFAULTS = QCSettings()

GROUPS = {
    "lab": "Lab read rules",
    "assembly": "Assembly, mixtures and marks",
    "rejection": "Rejection limits",
    "coding": "Coding frame",
    "advanced": "Advanced",
}


@dataclass(frozen=True)
class FieldMeta:
    name: str
    label: str
    group: str
    kind: str  # "int", "float", "percent" (stored as a fraction) or "bool"
    unit: str = ""  # "%", "bp" or "Q" (shown before the number)
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    decimals: int = 0
    display_offset: int = 0  # shown = stored + offset ("fail at" limits)
    advanced: bool = False
    help: str = ""
    hint: str = ""


@dataclass(frozen=True)
class Problem:
    field: str
    message: str
    severity: str = "error"  # "error" blocks a run, "warning" does not


FIELDS: tuple[FieldMeta, ...] = (
    FieldMeta("min_good_fraction", "Read cutoff", "lab", "percent", "%", 0, 100, 0.5, 1,
              hint="Share of bases at or above the minimum base quality",
              help="A read passes only when at least this share of its bases (whole read, before trimming) "
                   "reach the minimum base quality. Lab rule: 75%."),
    FieldMeta("min_quality", "Minimum base quality", "lab", "int", "Q", 1, 60,
              hint="Phred value that counts as a good base",
              help="Phred quality that counts a base as good for the read cutoff (Q20 = 1 error in 100)."),
    FieldMeta("min_trimmed_length", "Minimum trimmed length", "lab", "int", "bp", 1, 2000,
              hint="Shortest usable read after end trimming",
              help="Reads shorter than this after quality trimming fail the lab read rules."),
    FieldMeta("require_both_directions", "Require both directions", "lab", "bool",
              hint="A sample needs a forward and a reverse read",
              help="Fail a sample that has only a forward or only a reverse read. Turning this off still leaves "
                   "single-read samples failing RECall's mark and single-coverage limits unless those are relaxed."),
    FieldMeta("censor_quality", "Censor quality", "assembly", "int", "Q", 0, 60,
              hint="Bases below this are left out of the assembly",
              help="Table 1: bases with a phred score below 10 are censored (excluded from the assembly)."),
    FieldMeta("call_mixtures", "Call mixtures", "assembly", "bool",
              hint="Call IUPAC codes from secondary peaks",
              help="Methods: count each base seen as the called base or as an uncalled base whose peak area "
                   "reaches the mixture area; keep bases seen in at least half of the reads."),
    FieldMeta("mixture_area", "Mixture area", "assembly", "percent", "%", 1, 100, 0.5, 1,
              hint="Secondary peak area, % of the called peak",
              help="Table 1: an uncalled peak counts towards a mixture when its area is at least 20% of the "
                   "called peak."),
    FieldMeta("mark_area", "Mark area", "assembly", "percent", "%", 1, 100, 0.5, 1,
              hint="Mark when half the reads have a secondary peak this large",
              help="Table 1: mark a position when an uncalled peak of at least 17.5% of the called peak is seen "
                   "in at least 50% of the reads."),
    FieldMeta("mark_avg_quality", "Mark average quality", "assembly", "int", "Q", 0, 60,
              hint="Mark positions whose mean quality is below this",
              help="Table 1: mark a position whose average quality across the reads is below 20."),
    FieldMeta("max_mixture_fraction", "Maximum mixtures", "rejection", "percent", "%", 0, 100, 0.5, 1,
              hint="Fail when more bases than this are mixtures",
              help="Table 2: reject when more than 3.5% of the bases are mixtures."),
    FieldMeta("max_n", "Fail at N count", "rejection", "int", "", 1, 9999, display_offset=1,
              hint="Fail when the consensus has this many Ns",
              help="Table 2: reject when the consensus has 5 or more Ns."),
    FieldMeta("max_marks", "Fail at marked positions", "rejection", "int", "", 1, 99999, display_offset=1,
              hint="Fail when this many positions are marked",
              help="Table 2: reject when 100 or more positions are marked."),
    FieldMeta("single_coverage_quality", "Single-coverage quality", "rejection", "int", "Q", 0, 60,
              hint="Single-read bases below this count as weak",
              help="Table 2: single-coverage bases below phred 40 count towards the weak run."),
    FieldMeta("max_single_coverage_run", "Maximum weak single-coverage run", "rejection", "int", "", 0, 9999,
              hint="Fail when more weak single-read bases follow in a row",
              help="Table 2: reject when more than 3 consecutive single-coverage bases are below phred 40."),
    FieldMeta("frame", "Codon 1 starts at", "coding", "int", "", 1, 100000,
              hint="Reference position of the first base of codon 1",
              help="Stop codons are read in this frame (Table 2: reject on any unambiguous stop codon)."),
    FieldMeta("check_stop_codons", "Fail on stop codons", "coding", "bool",
              help="Table 2: reject when the consensus contains an unambiguous stop codon (TAA, TAG, TGA)."),
    FieldMeta("check_frameshifts", "Fail on frameshift indels", "coding", "bool",
              help="Table 2: reject an insertion or deletion whose length is not a multiple of 3."),
    FieldMeta("trim_error_limit", "End-trimming error limit", "advanced", "float", "", 0.001, 0.5, 0.005, 3,
              advanced=True, hint="Mott's algorithm; smaller trims harder",
              help="Each base scores (limit - error probability); the kept region is the best-scoring stretch."),
    FieldMeta("min_read_identity", "Minimum identity to reference", "advanced", "percent", "%", 50, 100, 1, 0,
              advanced=True, hint="A read matching worse than this is rejected",
              help="Reads that match the reference worse than this are reported as not matching."),
    FieldMeta("min_read_aligned", "Minimum aligned length", "advanced", "int", "bp", 1, 100000,
              advanced=True, hint="A read aligning over fewer bases is rejected",
              help="Reads that align over fewer reference bases than this are reported as not matching."),
    FieldMeta("require_full_reference", "Require the whole reference", "advanced", "bool", advanced=True,
              hint="Every position covered by both reads (one when directions are optional)",
              help="RECall by default requires double primer coverage over the entire sequence length "
                   "(Woods et al. 2012, p. 1938)."),
    FieldMeta("check_read_orientation", "Check read orientation", "advanced", "bool", advanced=True,
              hint="Forward read on the plus strand, reverse on the minus strand",
              help="Turn off only when the reference is stored reverse-complemented. Two reads on the same "
                   "strand always fail."),
)
FIELD_BY_NAME = {f.name: f for f in FIELDS}


def to_display(meta: FieldMeta, value):
    if meta.kind == "bool":
        return bool(value)
    if meta.kind == "percent":
        return round(value * 100, 6)
    return value + meta.display_offset


def from_display(meta: FieldMeta, shown):
    if meta.kind == "bool":
        return bool(shown)
    if meta.kind == "percent":
        return round(float(shown) / 100, 6)
    if meta.kind == "int":
        return int(round(shown)) - meta.display_offset
    return float(shown)


def format_display(meta: FieldMeta, value) -> str:
    """A value as the settings page shows it, e.g. "75%", "Q20", "100 bp", "On"."""
    if meta.kind == "bool":
        return "On" if value else "Off"
    shown = to_display(meta, value)
    number = f"{shown:.{meta.decimals}f}" if meta.kind in ("percent", "float") else f"{shown:g}"
    if meta.kind == "percent" and meta.decimals:
        number = number.rstrip("0").rstrip(".")
    if meta.unit == "Q":
        return f"Q{number}"
    if meta.unit == "%":
        return f"{number}%"
    return f"{number} {meta.unit}".strip()


def validate(settings: QCSettings, reference_length: int | None = None) -> list[Problem]:
    """Range and cross-field checks; ``reference_length`` bounds the frame and the aligned length."""
    problems: list[Problem] = []
    for meta in FIELDS:
        value = getattr(settings, meta.name)
        if meta.kind == "bool":
            if not isinstance(value, bool):
                problems.append(Problem(meta.name, f"{meta.label} must be on or off"))
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(Problem(meta.name, f"{meta.label} must be a number"))
            continue
        shown = to_display(meta, value)
        maximum = meta.maximum
        if reference_length and meta.name in ("frame", "min_read_aligned"):
            maximum = reference_length
        if not meta.minimum <= shown <= maximum:
            problems.append(Problem(meta.name, f"{meta.label} must be between "
                                               f"{_fmt(meta, meta.minimum)} and {_fmt(meta, maximum)}"))
    if settings.censor_quality > settings.min_quality:
        problems.append(Problem("censor_quality", "Censor quality must not be above the minimum base quality"))
    if settings.mark_area > settings.mixture_area:
        problems.append(Problem("mark_area", "Mark area is above the mixture area, so marks will be rarer than "
                                             "mixtures (the paper uses 17.5% and 20%)", "warning"))
    if settings.check_stop_codons != settings.check_frameshifts:
        problems.append(Problem("check_stop_codons", "Only one of the two coding checks is on", "warning"))
    return problems


def _fmt(meta: FieldMeta, shown) -> str:
    return f"{shown:g}{'%' if meta.unit == '%' else ''}"


def errors(problems: list[Problem]) -> list[Problem]:
    return [p for p in problems if p.severity == "error"]


def settings_to_dict(settings: QCSettings) -> dict:
    return dataclasses.asdict(settings)


def settings_from_dict(data: dict) -> tuple[QCSettings, list[Problem]]:
    """Settings from stored values: unknown keys are ignored, bad values reset to their default."""
    values: dict = {}
    problems: list[Problem] = []
    for meta in FIELDS:
        if meta.name not in data:
            continue
        value = data[meta.name]
        default = getattr(DEFAULTS, meta.name)
        if meta.kind == "bool":
            ok = isinstance(value, bool)
        elif meta.kind == "int":
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            value = float(value) if ok else value
        if ok:
            candidate = dataclasses.replace(DEFAULTS, **{meta.name: value})
            ok = not [p for p in errors(validate(candidate)) if p.field == meta.name]
        if ok:
            values[meta.name] = value
        else:
            problems.append(Problem(meta.name, f"stored {meta.label} {value!r} is not valid; using the default "
                                               f"{format_display(meta, default)}", "warning"))
    return QCSettings(**values), problems


def changed_fields(settings: QCSettings) -> list[str]:
    """Names of the fields that differ from the published defaults."""
    return [f.name for f in FIELDS if getattr(settings, f.name) != getattr(DEFAULTS, f.name)]


def rule_set_label(settings: QCSettings) -> str:
    """"defaults" or "N changed", as shown next to the rules."""
    n = len(changed_fields(settings))
    return "defaults" if not n else f"{n} changed"
