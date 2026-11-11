"""Qt-free view models shared by the desktop window and the HTML report.

Everything a person reads about a result (list subtitles, verdict sentences,
reason explanations, metric tiles, alignment cells, tooltips, read-QC panels)
is built here from the pipeline objects, so the window and ``report.html``
always say the same thing and the wording can be tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .pipeline import RunResult, SampleResult, ScanResult
from .qc import IUPAC_TO_BASES, QCSettings
from .recall import DIRECTIONS, GAP, RULES, TWO_BASE_CODES, Position, RuleResult, SampleCall
from .settings_schema import FIELD_BY_NAME, changed_fields, format_display, rule_set_label

LABEL = dict(DIRECTIONS)  # "F" -> "forward"
FAMILY_LABEL = {"lab": "Lab read rule", "recall": "RECall rejection rule"}
STRONG_FLAGS = frozenset({"diff", "mixture", "n", "deletion", "insertion", "stop"})


def fmt_q(value: float) -> str:
    """Mean quality as the design shows it: Q13, Q55.5."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


# --- rules ---------------------------------------------------------------------------------------------

def rule_summary(s: QCSettings) -> str:
    """The rule sentence shown in the run summary and the report header (SU-3)."""
    both = ", forward and reverse both required" if s.require_both_directions else ""
    coverage = (", incomplete coverage of the reference" if s.require_full_reference else "")
    coding = []
    if s.check_stop_codons:
        coding.append("stop codon")
    if s.check_frameshifts:
        coding.append("frameshift insert/deletion")
    return (f"Read rule: ≥ {s.min_good_fraction:.0%} of bases ≥ Q{s.min_quality}, trimmed length ≥ "
            f"{s.min_trimmed_length} bp{both}. RECall rules (Woods et al. 2012): bases < Q{s.censor_quality} "
            f"censored; mixture area ≥ {s.mixture_area * 100:g}%; mark area ≥ {s.mark_area * 100:g}%; mark average "
            f"quality < Q{s.mark_avg_quality}. Fail on {', '.join(coding + [''])}mixtures > "
            f"{s.max_mixture_fraction * 100:g}%, N ≥ {s.max_n + 1}, marks ≥ {s.max_marks + 1}, > "
            f"{s.max_single_coverage_run} consecutive single-coverage bases < Q{s.single_coverage_quality}"
            f"{coverage}, or no usable coverage.")


@dataclass
class RuleRow:
    label: str
    value: str
    changed: bool


def rules_used(s: QCSettings) -> list[tuple[str, list[RuleRow]]]:
    """The "Rules for this run" card: (group title, rows) with changed values flagged."""
    changed = set(changed_fields(s))

    def row(name: str, label: str | None = None) -> RuleRow:
        meta = FIELD_BY_NAME[name]
        return RuleRow(label or meta.label, format_display(meta, getattr(s, name)), name in changed)

    coding = not (s.check_stop_codons or s.check_frameshifts)
    return [
        ("Lab read rules", [
            row("min_good_fraction", "Bases ≥ Q" + str(s.min_quality)),
            row("min_trimmed_length"),
            row("require_both_directions"),
        ]),
        ("RECall rules", [
            row("censor_quality"),
            row("mixture_area"),
            row("mark_area"),
            row("mark_avg_quality"),
            row("max_mixture_fraction"),
            row("max_n"),
            row("max_marks"),
            RuleRow("Weak single-coverage run", f"> {s.max_single_coverage_run} below Q{s.single_coverage_quality}",
                    bool({"max_single_coverage_run", "single_coverage_quality"} & changed)),
            RuleRow("Coding checks", "Off (non-coding)" if coding else f"On, codon 1 at {s.frame}",
                    bool({"check_stop_codons", "check_frameshifts", "frame"} & changed)),
            row("require_full_reference", "Whole reference covered"),
        ]),
    ]


def rule_status(r: RuleResult) -> str:
    return {True: "Pass", False: "Fail", None: "Not applied"}[r.passed]


# --- sample list and header ----------------------------------------------------------------------------

def _short_reason(c: SampleCall, rule: str, text: str, s: QCSettings) -> str:
    if rule.endswith("_cutoff"):
        read = c.reads.get("F" if rule.startswith("forward") else "R")
        label = "Forward" if rule.startswith("forward") else "Reverse"
        if read is not None and read.reasons == ["file has no quality values"]:
            return f"{label} read has no quality values"
        if read is not None and any(r.startswith("only") for r in read.reasons):
            return f"{label} read failed QC ({read.good_fraction:.1%} ≥ Q{s.min_quality})"
        return f"{label} read failed QC (trimmed {read.trimmed_length if read else 0} bp)"
    if rule == "mixtures":
        return f"Too many mixtures: {c.mixtures} ({c.mixture_fraction:.1%})"
    if rule == "full_coverage":
        return "Incomplete coverage of the reference"
    if rule.endswith("_match"):
        return capitalize(text.split(" (")[0])
    return capitalize(text)


def sample_subtitle(sr: SampleResult, s: QCSettings | None = None) -> str:
    """Second line of a sample-list row (design 3.9)."""
    c = sr.consensus
    if c.status == "PASS":
        return f"{c.length} bp · {plural(c.mixtures, 'mixture')} · {plural(c.marks, 'mark')}"
    if not c.reason_items:
        return ""
    first = c.reason_items[0]
    more = f" · +{len(c.reason_items) - 1} more" if len(c.reason_items) > 1 else ""
    return _short_reason(c, first.rule, first.text, s or QCSettings()) + more


def failure_categories(sr: SampleResult) -> list[str]:
    """Category labels of a sample's failure reasons (used by the list filter)."""
    return sorted({RULES[r.rule][2] for r in sr.consensus.reason_items})


def verdict_sentence(sr: SampleResult) -> str:
    """One sentence next to the verdict badge (design 3.10)."""
    c = sr.consensus
    if c.status == "PASS":
        reads = [d for d in ("F", "R") if c.reads.get(d) is not None]
        who = "Both reads" if len(reads) == 2 else f"The {LABEL[reads[0]]} read" if reads else "The reads"
        return (f"{who} passed the lab cutoff and no RECall rejection rule was triggered. "
                "Included in consensus.fasta.")
    n = len(c.reason_items)
    return f"{plural(n, 'rejection reason')} · not written to consensus.fasta"


def fasta_header(sr: SampleResult) -> str:
    c = sr.consensus
    return f"{sr.sample} ref={c.ref_start}-{c.ref_end} len={c.length} mixtures={c.mixtures} marks={c.marks}"


def fasta_text(sr: SampleResult, width: int = 70) -> str:
    seq = sr.consensus.sequence
    body = "\n".join(seq[i:i + width] for i in range(0, len(seq), width))
    return f">{fasta_header(sr)}\n{body}\n" if seq else ""


# --- reasons -------------------------------------------------------------------------------------------

@dataclass
class ReasonView:
    rule: str
    title: str
    family: str  # "Lab read rule" / "RECall rejection rule"
    explanation: str
    hint: str
    goto_column: int | None = None
    goto_label: str = ""
    read: str | None = None  # "F"/"R" when the reason is about one read
    action: str = ""  # "show_read", "open_folder" or "" (GUI buttons)


def _read_explanation(c: SampleCall, direction: str, s: QCSettings, lab_only: bool) -> str:
    read = c.reads.get(direction)
    parts = []
    if read is not None and "file has no quality values" in read.reasons:
        parts.append("The file stores no quality values, so the read cutoff cannot be met.")
    elif read is not None:
        if read.good_fraction < s.min_good_fraction:
            parts.append(f"Only {read.good_fraction:.1%} of bases are ≥ Q{s.min_quality} "
                         f"(cutoff {s.min_good_fraction:.0%}).")
        if read.trimmed_length < s.min_trimmed_length:
            parts.append(f"Trimmed length {read.trimmed_length} bp, minimum {s.min_trimmed_length} bp.")
    if lab_only:
        parts.append("The lab read rules are checked first, so RECall rules were not applied.")
    return " ".join(parts)


def _stop_explanation(c: SampleCall) -> tuple[str, str, int | None]:
    stop = c.stop_records[0]
    columns = [c.positions[i] for i in stop.columns]
    end = stop.ref_start + 2
    text = (f"Reference positions {stop.ref_start}–{end} read {stop.bases} in the consensus "
            f"(reference {stop.ref_bases}).")
    changed = next((p for p in columns if p.base != p.ref_base), None)
    where = changed.ref_number if changed and changed.ref_number else stop.ref_start
    if changed is not None and changed.ref_number:
        calls = {d: o for d, o in changed.obs.items() if o.base != GAP}
        if calls and all(o.base == changed.base for o in calls.values()):
            who = "Both reads call" if len(calls) == 2 else f"The {LABEL[next(iter(calls))]} read calls"
            quals = ", ".join(f"{LABEL[d]} Q{o.quality}" for d, o in sorted(calls.items()))
            text += f" {who} {changed.base} at position {where}: {quals}."
        elif calls:
            text += " The reads disagree at position " + str(where) + ": " + ", ".join(
                f"{LABEL[d]} {o.base} Q{o.quality}" for d, o in sorted(calls.items())) + "."
    elif changed is None:
        text += " The reference itself reads a stop codon here."
    hint = (f"Check the two traces at position {where}. If the target is not protein-coding, turn on "
            "“Non-coding target” in Settings to skip stop-codon and frameshift checks, then re-run.")
    return text, hint, stop.columns[0]


def reason_views(sr: SampleResult, s: QCSettings | None = None) -> list[ReasonView]:
    """Title, explanation, hint and jump target for every failure reason (design 3.11)."""
    s = s or QCSettings()
    c = sr.consensus
    flagged_hint = "Step through flagged positions with the arrows in the alignment bar."
    lab_only = any(r.family == "lab" for r in c.reason_items) and c.provisional
    out: list[ReasonView] = []
    for reason in c.reason_items:
        rule, text = reason.rule, reason.text
        view = ReasonView(rule, capitalize(text), FAMILY_LABEL[reason.family], "", "")
        rr = c.rule(rule)
        if rule.endswith("_cutoff"):
            d = "F" if rule.startswith("forward") else "R"
            view.title = f"{LABEL[d].capitalize()} read failed QC"
            view.explanation = _read_explanation(c, d, s, lab_only)
            view.hint = "Re-sequence this direction, or review the cutoff in Settings."
            view.read, view.action = d, "show_read"
        elif rule.endswith("_present"):
            d = "F" if rule.startswith("forward") else "R"
            if "unreadable" in text:
                name = sr.path(d).name if sr.path(d) else "the file"
                error = sr.read_errors.get(d, "")
                view.explanation = f"The {LABEL[d]} file {name} could not be read" + (f": {error}." if error else ".")
                view.hint = "Check that the file is a complete .ab1 trace; export it again from the sequencer."
                view.read, view.action = d, "show_read"
            elif text == "no usable reads":
                view.explanation = "No read of this sample could be used."
                view.hint = "Add the forward and reverse files for this sample."
            else:
                view.explanation = f"No {LABEL[d]} file was found for this sample."
                view.hint = "Add the file, or turn off Require both directions in Settings."
                view.action = "open_folder"
        elif rule.endswith("_match"):
            d = "F" if rule.startswith("forward") else "R"
            detail = rr.observed if rr else ""
            if detail.startswith("aligns over only"):
                view.explanation = f"{LABEL[d].capitalize()} read {detail}."
            elif detail == "no alignment":
                view.explanation = f"The {LABEL[d]} read has no usable alignment to the reference."
            else:
                view.explanation = (f"The {LABEL[d]} read matches the reference at {detail.split()[0]} "
                                    f"(minimum {s.min_read_identity:.0%}).")
            view.hint = "Possible sample mix-up or wrong reference."
            view.read, view.action = d, "show_read"
        elif rule == "strands":
            if "same strand" in text:
                view.explanation = ("Both files align to the same strand, so they are probably the same read "
                                    "twice or two reads from the same primer.")
            else:
                view.explanation = f"The file named as {text.split()[0]} aligns in the opposite orientation."
            view.hint = ("Check the file names and the F/R tags in Settings. Turn off Check read orientation only "
                         "if the reference is stored reverse-complemented.")
        elif rule == "full_coverage":
            missing = text.split("reference ", 1)[1].split(" not covered")[0]
            by = "both reads" if s.require_both_directions else "a read"
            view.title = "Incomplete coverage of the reference"
            view.explanation = (f"Reference {missing.replace('-', '–')} not covered by {by}. RECall requires "
                                "double coverage over the whole reference.")
            view.hint = ("Check that both primers span the whole target, or turn off Require the whole reference "
                         "in Settings.")
            view.goto_column, view.goto_label = rr.column if rr else None, "Go to first gap in coverage"
        elif rule == "stop_codon":
            view.explanation, view.hint, view.goto_column = _stop_explanation(c)
            view.goto_label = f"Go to codon {c.stop_records[0].codon}"
        elif rule in ("bad_insert", "bad_deletion"):
            kind = "insertion" if rule == "bad_insert" else "deletion"
            indel = next(i for i in c.indel_records if i.kind == kind and i.frameshift)
            view.explanation = f"{indel.length} bp {kind} at reference position {indel.ref_pos}."
            view.hint = f"Check both traces at {indel.ref_pos}."
            view.goto_column, view.goto_label = indel.column, f"Go to position {indel.ref_pos}"
        elif rule == "mixtures":
            view.explanation = (f"{c.mixtures} mixtures, {c.mixture_fraction:.1%} of bases "
                                f"(limit {s.max_mixture_fraction * 100:g}%).")
            view.hint = "Evenly spread mixtures suggest a mixed template."
            view.goto_column, view.goto_label = rr.column if rr else None, "Go to first mixture"
        elif rule == "n_count":
            view.explanation = f"{plural(c.n_count, 'N')} in the consensus (fails at {s.max_n + 1})."
            view.hint = flagged_hint
            view.goto_column, view.goto_label = rr.column if rr else None, "Go to first N"
        elif rule == "marks":
            view.explanation = f"{c.marks} marked positions (fails at {s.max_marks + 1})."
            view.hint = flagged_hint
            view.goto_column, view.goto_label = rr.column if rr else None, "Go to first flagged"
        elif rule == "single_coverage":
            view.explanation = (f"{c.max_single_run} consecutive bases from one read below "
                                f"Q{s.single_coverage_quality} (fails above {s.max_single_coverage_run}).")
            view.hint = flagged_hint
            view.goto_column, view.goto_label = rr.column if rr else None, "Go to first flagged"
        elif rule == "low_quality":
            view.explanation = capitalize(text.split(": ", 1)[1]) + "."
            view.hint = flagged_hint
            view.goto_column, view.goto_label = rr.column if rr else None, "Go to first flagged"
        elif rule == "internal_error":
            view.title = "Analysis error"
            view.explanation = f"An unexpected error stopped the analysis of this sample ({c.error})."
            view.hint = "The details are in the log file (Help > Open log folder)."
        out.append(view)
    return out


# --- metric tiles --------------------------------------------------------------------------------------

@dataclass
class Tile:
    label: str
    value: str
    suffix: str = ""
    hint: str = ""
    failed: bool = False
    tooltip: str = ""
    provisional: bool = False


def _indel_tile(c: SampleCall, s: QCSettings) -> tuple[str, str]:
    if not c.indel_records:
        return "None", "frameshift fails" if s.check_frameshifts else "not checked"
    if len(c.indel_records) > 1:
        return plural(len(c.indel_records), "indel"), "frameshift fails" if s.check_frameshifts else "not checked"
    i = c.indel_records[0]
    value = f"{i.length} bp {'del' if i.kind == 'deletion' else 'ins'} @ {i.ref_pos}"
    if i.frameshift:
        return value, "frameshift fails" if s.check_frameshifts else "not checked"
    return value, "in-frame, marked"


def metric_tiles(sr: SampleResult, s: QCSettings, ref_len: int) -> list[Tile]:
    """The eight tiles of the metric strip (design 3.12)."""
    c = sr.consensus
    failed = {r.rule for r in c.rules if r.passed is False}
    if not c.positions:
        dash = "–"
        return [Tile("Length", dash, hint="no assembly"), Tile("Reference span", dash, hint=f"of {ref_len} bp"),
                Tile("Mixtures", dash, hint=f"fails above {s.max_mixture_fraction * 100:g}%"),
                Tile("N count", dash, hint=f"fails at {s.max_n + 1}"),
                Tile("Marks", dash, hint=f"fails at {s.max_marks + 1}"),
                Tile("Weak 1× run", dash, hint=f"fails above {s.max_single_coverage_run}"),
                Tile("Indels", dash, hint="frameshift fails"), Tile("Stop codons", dash, hint="any fails")]
    prov = c.provisional
    indel_value, indel_hint = _indel_tile(c, s)
    stops = c.stop_codons
    tiles = [
        Tile("Length", f"{c.length}", "bp", "consensus" if c.status == "PASS" else "assembly"),
        Tile("Reference span", f"{c.ref_start}–{c.ref_end}", "", f"of {ref_len} bp", "full_coverage" in failed),
        Tile("Mixtures", f"{c.mixtures}", f"{c.mixture_fraction:.2%}", f"fails above {s.max_mixture_fraction * 100:g}%",
             "mixtures" in failed),
        Tile("N count", f"{c.n_count}", "", f"fails at {s.max_n + 1}", "n_count" in failed),
        Tile("Marks", f"{c.marks}", "", f"fails at {s.max_marks + 1}", "marks" in failed),
        Tile("Weak 1× run", f"{c.max_single_run}", "", f"fails above {s.max_single_coverage_run}",
             "single_coverage" in failed,
             f"Longest run of single-coverage bases below Q{s.single_coverage_quality}"),
        Tile("Indels", indel_value, "", indel_hint, bool({"bad_insert", "bad_deletion"} & failed)),
        Tile("Stop codons", f"{len(stops)}" if stops else "None",
             ("codon " + ", ".join(map(str, stops))) if stops else "",
             "any fails" if s.check_stop_codons else "not checked", "stop_codon" in failed),
    ]
    for t in tiles:
        t.provisional = prov
        if prov:
            t.tooltip = "Display only: a lab read rule failed, so RECall rules were not applied."
    return tiles


# --- alignment -----------------------------------------------------------------------------------------

@dataclass
class ReadCell:
    base: str  # in reference orientation; "-" for a gap
    quality: int
    censored: bool
    raw: int | None  # untrimmed read index, None for a gap
    between: tuple[int | None, int | None] = (None, None)  # flanking raw indices for a gap
    uncalled: str = ""
    ratio: float = 0.0


@dataclass
class Column:
    index: int
    ref_pos: int | None
    ref_number: int | None
    ref_base: str
    cons_base: str
    cons_index: int | None
    flags: frozenset[str]
    marks: list[str]
    coverage: int
    mean_quality: float
    codon: int | None = None
    codon_pos: int | None = None
    anchor_number: int | None = None  # insertions: 1-based reference position they follow
    reads: dict[str, ReadCell] = field(default_factory=dict)

    @property
    def flagged_strong(self) -> bool:
        return bool(self.flags & STRONG_FLAGS) or bool(set(self.marks) - {"single coverage"})


@dataclass
class AlignmentData:
    columns: list[Column]
    flagged: list[int]  # column indices the stepper visits
    col_to_raw: dict[str, list[int | None]]  # direction -> raw index per column
    raw_to_col: dict[str, dict[int, int]]  # direction -> raw index -> column
    coding: bool
    stops: list[tuple[int, int, int]] = field(default_factory=list)  # column triplets of stop codons

    def first_flagged(self, kind: str | None = None) -> int | None:
        for i in self.flagged:
            if kind is None or kind in self.columns[i].flags:
                return i
        return None


def _flags(p: Position, stop_columns: set[int]) -> frozenset[str]:
    f = set()
    if p.ref_pos is None:
        f.add("insertion")
    if p.base == GAP:
        f.add("deletion")
    elif p.base == "N":
        f.add("n")
    elif p.base in TWO_BASE_CODES or p.base not in "ACGT":
        f.add("mixture")
    elif p.ref_pos is not None and p.ref_base in "ACGT" and p.base != p.ref_base:
        f.add("diff")
    if p.marks:
        f.add("marked")
    if "single coverage" in p.marks:
        f.add("single")
    if p.column in stop_columns:
        f.add("stop")
    return frozenset(f)


def alignment(sr: SampleResult, s: QCSettings) -> AlignmentData:
    """Column view of an assembly: reference, consensus and both reads (in reference orientation)."""
    c = sr.consensus
    stop_columns = {i for st in c.stop_records for i in st.columns}
    trim = {a.direction: a.read.trim_start for a in c.aligned}
    coding = s.check_stop_codons or s.check_frameshifts
    frame0 = s.frame - 1
    columns: list[Column] = []
    col_to_raw: dict[str, list[int | None]] = {d: [] for d in trim}
    raw_to_col: dict[str, dict[int, int]] = {d: {} for d in trim}
    for p in c.positions:
        col = Column(p.column, p.ref_pos, p.ref_number, p.ref_base, p.base, p.cons_index, _flags(p, stop_columns),
                     list(p.marks), p.coverage, p.mean_quality,
                     anchor_number=p.anchor + 1 if p.anchor is not None else None)
        if coding and p.ref_pos is not None and p.ref_pos >= frame0:
            col.codon, col.codon_pos = (p.ref_pos - frame0) // 3 + 1, (p.ref_pos - frame0) % 3 + 1
        for d in trim:
            o = p.obs.get(d)
            raw = trim[d] + o.read_index if o is not None and o.read_index is not None else None
            col_to_raw[d].append(raw)
            if o is None:
                continue
            if raw is not None:
                raw_to_col[d][raw] = p.column
            col.reads[d] = ReadCell(o.base, o.quality, o.base != GAP and o.quality < s.censor_quality, raw,
                                    uncalled=o.uncalled, ratio=o.ratio)
        columns.append(col)
    # Gaps point at the read bases on either side (a deletion is drawn between two peaks).
    for d, raws in col_to_raw.items():
        prev = None
        pending: list[Column] = []
        for col, raw in zip(columns, raws):
            cell = col.reads.get(d)
            if cell is None:
                continue
            if raw is None:
                pending.append(col)
                continue
            for g in pending:
                g.reads[d].between = (prev, raw)
            pending, prev = [], raw
        for g in pending:
            g.reads[d].between = (prev, None)
    stops = [tuple(st.columns) for st in c.stop_records]
    return AlignmentData(columns, flagged_columns(columns), col_to_raw, raw_to_col, coding, stops)


def flagged_columns(columns: list[Column], single_limit: int = 50) -> list[int]:
    """Columns the flagged-position stepper visits (design 3.17)."""
    flagged = [c for c in columns if c.flags & STRONG_FLAGS or c.marks]
    single_only = [c for c in flagged if not c.flagged_strong]
    if len(single_only) > single_limit:
        flagged = [c for c in flagged if c.flagged_strong]
    return [c.index for c in flagged]


def _bases_of(code: str) -> str:
    bases = IUPAC_TO_BASES.get(code)
    return f" ({'/'.join(sorted(bases))})" if bases and len(bases) > 1 and code != "N" else ""


def _where(col: Column) -> str:
    if col.ref_number is None:
        return f"Insertion after ref {col.anchor_number}"
    codon = f" · codon {col.codon}, base {col.codon_pos}" if col.codon else ""
    return f"Ref {col.ref_number}{codon}"


def read_cell_text(direction: str, cell: ReadCell, s: QCSettings) -> str:
    if cell.base == GAP:
        return f"{direction}: deletion"
    text = f"{direction}: {cell.base} Q{cell.quality}"
    if cell.uncalled and cell.ratio >= 0.005:
        text += f", secondary {cell.uncalled} {cell.ratio:.0%} of called peak"
    if cell.censored:
        text += f" (censored, below Q{s.censor_quality})"
    return text


def tooltip_lines(col: Column, data: AlignmentData, s: QCSettings, c: SampleCall | None = None) -> list[str]:
    """Hover text for one alignment column (design 3.17)."""
    cons = "- (deletion)" if col.cons_base == GAP else col.cons_base + _bases_of(col.cons_base)
    ref = "-" if col.ref_base == GAP else col.ref_base
    lines = [_where(col), f"Consensus {cons} · reference {ref}",
             f"Coverage {plural(col.coverage, 'read')} · mean Q{fmt_q(col.mean_quality)}"]
    for d in ("F", "R"):
        if d in col.reads:
            lines.append(read_cell_text(d, col.reads[d], s))
    if "stop" in col.flags and c is not None:
        stop = next(st for st in c.stop_records if col.index in st.columns)
        lines.append(f"Stop codon {stop.bases} (reference {stop.ref_bases})")
    elif col.marks:
        lines.append("Marked: " + ", ".join(col.marks))
    return lines


def position_readout(col: Column, c: SampleCall | None = None) -> str:
    """The chip above the chromatograms (design 3.18)."""
    parts = [_where(col).replace(", base", " · base").split(" · base")[0]]
    parts += [f"cons {col.cons_base}", f"ref {col.ref_base}", plural(col.coverage, "read"),
              f"mean Q{fmt_q(col.mean_quality)}"]
    if "stop" in col.flags and c is not None:
        stop = next(st for st in c.stop_records if col.index in st.columns)
        parts.append(f"stop codon {stop.bases}")
    elif col.marks:
        parts.append("marked")
    return " · ".join(parts)


# --- read QC column ------------------------------------------------------------------------------------

@dataclass
class ReadView:
    direction: str
    label: str
    state: str  # "ok", "missing", "unreadable", "not_assembled"
    status: str  # "PASS", "FAIL" or "MISSING"
    file_name: str = ""
    orientation: str = ""  # "shown reverse-complemented" for a minus-strand read
    good_fraction: float = 0.0
    cutoff: float = 0.75
    min_quality: int = 20
    meter_text: str = ""
    meter_failed: bool = False
    rows: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # shown in fail colour under the rows
    notice: str = ""  # e.g. no trace data
    has_traces: bool = True


def read_view(sr: SampleResult, direction: str, s: QCSettings) -> ReadView:
    """Everything the read-QC column of one chromatogram track shows (design 3.18, RQ-1)."""
    c = sr.consensus
    label = LABEL[direction].capitalize()
    path = sr.path(direction)
    view = ReadView(direction, label, "ok", "PASS", path.name if path else "", cutoff=s.min_good_fraction,
                    min_quality=s.min_quality)
    if direction in sr.read_errors:
        view.state, view.status = "unreadable", "FAIL"
        view.problems = [f"Unreadable file: {sr.read_errors[direction]}"]
        return view
    read, chrom = c.reads.get(direction), sr.chrom(direction)
    if read is None or chrom is None:
        view.state, view.status = "missing", "MISSING"
        view.problems = [f"No {LABEL[direction]} file for this sample"]
        return view
    a = next((x for x in c.aligned if x.direction == direction), None)
    view.status = read.status
    view.good_fraction = read.good_fraction
    short = (s.min_good_fraction - read.good_fraction) * 100
    view.meter_failed = read.good_fraction < s.min_good_fraction
    view.meter_text = f"{read.good_fraction:.1%}" + (f" · {short:.1f} points short" if view.meter_failed else "")
    view.has_traces = chrom.has_traces and len(chrom.peak_locations) == len(chrom)
    if not view.has_traces:
        view.notice = "No trace data in this file: calls and qualities only; peak-area rules could not be applied."
    view.rows = [("Mean QV", f"{read.mean_quality:.1f}"),
                 ("Kept after trim", f"{read.trim_start + 1}–{read.trim_end} · {read.trimmed_length} of {read.length}")]
    if a is not None:
        strand = "+" if a.strand == "+" else "−"
        view.rows += [("Strand · ref span", f"{strand} · {a.ref_start + 1}–{a.ref_end}"),
                      ("Identity", f"{a.identity:.2%}")]
        if a.strand == "-":
            view.orientation = "shown reverse-complemented"
    else:
        view.state = "not_assembled"
        view.rows.append(("Strand · ref span", "not assembled"))
        view.problems = list(read.reasons)
        rr = c.rule(f"{LABEL[direction]}_match")
        if rr is not None and rr.passed is False:
            view.problems.append(f"does not match the reference ({rr.observed})")
    return view


# --- run level -----------------------------------------------------------------------------------------

@dataclass
class SkippedRow:
    name: str
    path: Path
    cause: str
    kind: str  # "skipped" or "unreadable"


def skipped_rows(run: RunResult) -> list[SkippedRow]:
    """Files that were not analysed, and unreadable files, with the cause (UF-1)."""
    rows = [SkippedRow(s.path.name, s.path, s.text, "skipped") for s in run.skipped if s.reason != "hidden"]
    for key, message in run.errors.items():
        if not key.startswith("sample "):
            rows.append(SkippedRow(Path(key).name, Path(key), message, "unreadable"))
    return rows


def not_analysed_count(run: RunResult) -> int:
    return len(skipped_rows(run))


def run_context(run: RunResult) -> str:
    """'Reference measles-n450 · 450 bp · rules: defaults · 0 files not analysed' (design 3.8)."""
    name = run.reference_info.name if run.reference_info else "custom"
    n = not_analysed_count(run)
    return (f"Reference {name} · {len(run.reference)} bp · rules: {rule_set_label(run.settings)} · "
            f"{plural(n, 'file')} not analysed")


def name_range(names: list[str]) -> str:
    names = sorted(names)
    if len(names) <= 3:
        return ", ".join(names)
    return f"{names[0]}–{names[-1]}"


def export_notice(run: RunResult) -> str:
    """Warning above the export file list (design 3.19), or '' when every sample passed."""
    failed = [s.sample for s in run.samples if s.consensus.status == "FAIL"]
    if not run.samples or not failed:
        return ""
    if len(failed) == len(run.samples):
        return "No sample passed, so consensus.fasta will be empty."
    return (f"{plural(len(failed), 'failing sample')} {'is' if len(failed) == 1 else 'are'} left out of "
            f"consensus.fasta: {name_range(failed)}. They stay in the report and the CSV files with their reasons.")


@dataclass
class ScanSummary:
    headline: str
    chips: list[tuple[str, str]]  # (text, kind) kind = "neutral" or "warn"
    ready: int  # samples that will be analysed
    warning: str = ""


def scan_summary(sr: ScanResult) -> ScanSummary:
    """The sunken summary box under the input field (design 3.15)."""
    samples = len(sr.pairs)
    one_way = {d: sorted(n for n, slot in sr.pairs.items() if set(slot) == {d}) for d in ("F", "R")}
    no_tag = sum(1 for s in sr.skipped if s.reason == "no_tag")
    dup = sum(1 for s in sr.skipped if s.reason == "duplicate")
    chips = [(f"{sr.complete} with forward + reverse", "neutral")]
    for d, names in one_way.items():
        if names:
            chips.append((f"{len(names)} {LABEL[d]} only: {name_range(names)}", "warn"))
    chips.append((f"{no_tag} without an F/R tag", "warn" if no_tag else "neutral"))
    if dup:
        chips.append((f"{plural(dup, 'duplicate')}", "warn"))
    chips.append((f"{len(sr.bad_header)} unreadable", "warn" if sr.bad_header else "neutral"))
    headline = f"{plural(sr.n_files, '.ab1 file')} found · grouped into {plural(samples, 'sample')}"
    warning = ""
    if sr.n_files and not samples:
        warning = ("No forward/reverse .ab1 pairs found. File names need an F or R tag, for example "
                   "SAMPLE_F.ab1.")
    elif not sr.n_files:
        warning = "No .ab1 files found in the chosen inputs."
    return ScanSummary(headline, chips, samples, warning)
