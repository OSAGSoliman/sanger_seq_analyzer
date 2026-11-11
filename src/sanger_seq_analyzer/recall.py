"""RECall-style assembly, mixture calling, marking and pass/fail.

Implements the rules described in Woods et al. 2012, "Automating HIV drug
resistance genotyping with RECall" (J Clin Microbiol 50:1936-1942):

* reads are trimmed, bases with phred < 10 are censored, and every read is
  aligned to a user-supplied reference;
* each reference position is called by voting over the reads (Table 1);
* potentially problematic positions are "marked";
* the consensus is rejected by the criteria in Table 2.

Numbering conventions: ``AlignedRead.ref_start/ref_end`` and ``Position.ref_pos``
are 0-based (``ref_end`` exclusive); everything on :class:`SampleCall` that is
shown to people (``ref_start``, ``ref_end``, :class:`Indel`, :class:`StopCodon`)
is 1-based and inclusive.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from Bio.Align import PairwiseAligner, substitution_matrices

from .qc import IUPAC, IUPAC_TO_BASES, QCSettings, ReadQC

_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN?", "TGCAYRSWMKVHDBN?")
_ALPHABET = "ACGTRYSWKMBDHVN"
GAP = "-"
STOPS = {"TAA", "TAG", "TGA"}
TWO_BASE_CODES = frozenset(code for code, bases in IUPAC_TO_BASES.items() if len(bases) == 2)

# End trimming of the semi-global alignment. An aligned end is kept from the first paired column
# that opens a window of _TRIM_WINDOW columns with at least _TRIM_MIN_IDENTICAL identical ones.
# Where the read reaches the end of the reference that column may be a mismatch (a terminal
# variant is real sequence). Where the read ends inside the reference, flank or noise can be
# gap-aligned onto the reference, so the column must be identical and the window gap-free.
_TRIM_WINDOW = 10
_TRIM_MIN_IDENTICAL = 8

DIRECTIONS = (("F", "forward"), ("R", "reverse"))

# rule id -> (checklist label, family, failure category shown in filters)
RULES: dict[str, tuple[str, str, str]] = {
    "forward_present": ("Forward read present", "lab", "Missing or unreadable read"),
    "reverse_present": ("Reverse read present", "lab", "Missing or unreadable read"),
    "forward_cutoff": ("Forward read passes the read cutoff", "lab", "Read cutoff"),
    "reverse_cutoff": ("Reverse read passes the read cutoff", "lab", "Read cutoff"),
    "forward_match": ("Forward read matches the reference", "lab", "No match to reference"),
    "reverse_match": ("Reverse read matches the reference", "lab", "No match to reference"),
    "strands": ("Reads on the expected strands", "lab", "Strand"),
    "full_coverage": ("Whole reference covered", "recall", "Incomplete coverage"),
    "stop_codon": ("No stop codon", "recall", "Stop codon"),
    "bad_insert": ("No frameshift insertion", "recall", "Frameshift"),
    "bad_deletion": ("No frameshift deletion", "recall", "Frameshift"),
    "mixtures": ("Mixtures within limit", "recall", "Too many mixtures"),
    "n_count": ("N count within limit", "recall", "Too many Ns"),
    "marks": ("Marked positions within limit", "recall", "Too many marks"),
    "single_coverage": ("Weak single-coverage run within limit", "recall", "Single coverage"),
    "low_quality": ("Every position has a usable read", "recall", "Low quality"),
    "internal_error": ("Analysis completed", "lab", "Analysis error"),
}


def reverse_complement(seq: str) -> str:
    return seq.upper().translate(_COMPLEMENT)[::-1]


@lru_cache(maxsize=1)
def _aligner() -> PairwiseAligner:
    """Semi-global aligner: the read may overhang the reference (and vice versa) for free."""
    matrix = substitution_matrices.Array(alphabet=_ALPHABET, dims=2)
    for a in _ALPHABET:
        for b in _ALPHABET:
            sa, sb = IUPAC_TO_BASES[a], IUPAC_TO_BASES[b]
            matrix[a, b] = 2.0 if (a == b and len(sa) == 1) else (0.0 if sa & sb else -3.0)
    aligner = PairwiseAligner()
    aligner.mode = "global"
    aligner.substitution_matrix = matrix
    aligner.open_gap_score = -8
    aligner.extend_gap_score = -2
    try:
        aligner.end_insertion_score = 0.0
        aligner.end_deletion_score = 0.0
    except AttributeError:  # Biopython < 1.84
        aligner.target_end_gap_score = 0.0
        aligner.query_end_gap_score = 0.0
    return aligner


@dataclass
class Obs:
    """What one read shows at one alignment column."""

    base: str  # called base, or "-" for a deletion relative to the reference
    quality: int
    uncalled: str = ""
    ratio: float = 0.0  # uncalled/called peak area
    read_index: int | None = None  # index into the trimmed read, original orientation; None for a gap


@dataclass
class AlignedRead:
    read: ReadQC
    direction: str  # "F" or "R" (from the file name)
    strand: str  # "+" or "-" relative to the reference
    ref_start: int  # 0-based, inclusive
    ref_end: int  # 0-based, exclusive
    identity: float
    columns: dict[int, Obs] = field(default_factory=dict)  # ref position -> observation
    inserts: dict[int, list[Obs]] = field(default_factory=dict)  # bases inserted after ref position

    def spans(self, pos: int) -> bool:
        return self.ref_start <= pos < self.ref_end

    def raw_index(self, obs: Obs) -> int | None:
        """Index of ``obs`` in the untrimmed read (``Chromatogram.sequence``), or None for a gap."""
        return None if obs.read_index is None else self.read.trim_start + obs.read_index

    def ref_map(self, n_raw: int) -> np.ndarray:
        """0-based reference position of every untrimmed base; -1 trimmed/unaligned, -2 inserted."""
        out = np.full(n_raw, -1, dtype=int)
        for pos, o in self.columns.items():
            raw = self.raw_index(o)
            if raw is not None and raw < n_raw:
                out[raw] = pos
        for obs_list in self.inserts.values():
            for o in obs_list:
                raw = self.raw_index(o)
                if raw is not None and raw < n_raw:
                    out[raw] = -2
        return out


def _trim_bounds(ri: np.ndarray, paired: np.ndarray, ok: np.ndarray, ref_len: int) -> tuple[int, int] | None:
    """Indices of the first and last alignment column to keep (see _TRIM_WINDOW)."""
    n = len(ok)
    if n < _TRIM_WINDOW:
        return None
    kernel = np.ones(_TRIM_WINDOW, dtype=int)
    good = np.convolve(ok.astype(int), kernel, "valid") >= _TRIM_MIN_IDENTICAL
    gap_free = np.convolve(paired.astype(int), kernel, "valid") == _TRIM_WINDOW
    first, last = np.flatnonzero(paired)[[0, -1]]
    lenient_start, lenient_end = ri[first] == 0, ri[last] == ref_len - 1

    def window_ok(at_end: bool, lenient: bool) -> np.ndarray:
        w = good if lenient else good & gap_free
        out = np.zeros(n, bool)
        if at_end:
            out[_TRIM_WINDOW - 1:] = w
        else:
            out[:n - _TRIM_WINDOW + 1] = w
        return out & (paired if lenient else ok)

    starts = np.flatnonzero(window_ok(False, lenient_start))
    ends = np.flatnonzero(window_ok(True, lenient_end))
    if not starts.size or not ends.size or ends[-1] < starts[0]:
        return None
    return int(starts[0]), int(ends[-1])


def align_read(read: ReadQC, direction: str, reference: str) -> AlignedRead | None:
    """Align a trimmed read (either strand) end-to-end against the reference.

    The read may overhang either end of the reference. Junk at the read ends (primer,
    flank or noise that the aligner pushed into the reference) is trimmed back to a
    well-matching window, so a variant at the first or last reference base is kept.
    """
    if not read.sequence:
        return None
    aligner = _aligner()
    seq = "".join(c if c in _ALPHABET else "N" for c in read.sequence.upper())
    candidates = []
    for strand in ("+", "-"):
        query = seq if strand == "+" else reverse_complement(seq)
        aln = aligner.align(reference, query)[0]
        candidates.append((aln.score, strand, query, aln))
    _, strand, query, aln = max(candidates, key=lambda c: c[0])

    ri, qi = (np.asarray(x, dtype=int) for x in aln.indices)
    both = np.flatnonzero((ri >= 0) & (qi >= 0))
    if not both.size:
        return None
    ri, qi = ri[both[0]:both[-1] + 1], qi[both[0]:both[-1] + 1]
    ref_codes = np.frombuffer(reference.encode("ascii"), dtype=np.uint8)
    query_codes = np.frombuffer(query.encode("ascii"), dtype=np.uint8)
    paired = (ri >= 0) & (qi >= 0)
    ok = np.zeros(len(ri), bool)
    ok[paired] = ref_codes[ri[paired]] == query_codes[qi[paired]]
    bounds = _trim_bounds(ri, paired, ok, len(reference))
    if bounds is None:
        return None
    s, e = bounds
    ri, qi, ok = ri[s:e + 1], qi[s:e + 1], ok[s:e + 1]

    n = len(seq)
    quals = read.qualities if strand == "+" else read.qualities[::-1]
    ratios = read.area_ratios if strand == "+" else read.area_ratios[::-1]
    uncalled = read.uncalled if strand == "+" else reverse_complement(read.uncalled)

    def obs(q: int) -> Obs:
        u = uncalled[q] if q < len(uncalled) and uncalled[q] in "ACGT" else ""
        return Obs(query[q], int(quals[q]), u, float(ratios[q]), q if strand == "+" else n - 1 - q)

    columns: dict[int, Obs] = {}
    inserts: dict[int, list[Obs]] = {}
    last_ref = None
    for a, b in zip(ri.tolist(), qi.tolist()):
        if a >= 0:
            columns[a] = obs(b) if b >= 0 else Obs(GAP, 0)
            last_ref = a
        elif last_ref is not None:
            inserts.setdefault(last_ref, []).append(obs(b))
    return AlignedRead(read, direction, strand, min(columns), max(columns) + 1,
                       float(ok.sum()) / len(ok), columns, inserts)


def is_usable(o: Obs, s: QCSettings) -> bool:
    """A base that may vote: a clean or two-base IUPAC call at or above the censor quality."""
    return (o.base in "ACGT" or o.base in TWO_BASE_CODES) and o.quality >= s.censor_quality


def vote(observations: list[Obs], s: QCSettings) -> str:
    """RECall's per-position call (Woods et al. 2012, Methods).

    Count each nucleotide seen as a called base, or as an uncalled base whose
    area is >= the mixture-area cutoff; keep nucleotides seen in at least half
    of the reads; a mixture is called when the second most common one has more
    than half the count of the most common (a tie therefore also gives a
    mixture). Never more than two bases. A two-base IUPAC call written by the
    instrument (R, Y, ...) counts for both of its bases.
    """
    n = len(observations)
    if n == 0:
        return "N"
    counts: Counter[str] = Counter()
    for o in observations:
        if o.base in TWO_BASE_CODES:
            counts.update(IUPAC_TO_BASES[o.base])
            continue
        counts[o.base] += 1
        if s.call_mixtures and o.uncalled and o.ratio >= s.mixture_area:
            counts[o.uncalled] += 1
    kept = sorted(((c, b) for b, c in counts.items() if c >= n / 2), key=lambda x: (-x[0], x[1]))
    if not kept:
        return "N"
    c1, b1 = kept[0]
    if len(kept) > 1 and kept[1][0] > c1 / 2:
        return IUPAC[frozenset((b1, kept[1][1]))]
    return b1


@dataclass
class Position:
    ref_pos: int | None  # 0-based reference position; None for an insertion
    base: str  # consensus call ("-" for a deletion)
    coverage: int  # reads contributing an uncensored observation
    mean_quality: float
    marks: list[str] = field(default_factory=list)
    column: int = 0  # index in SampleCall.positions
    cons_index: int | None = None  # 1-based index in the consensus; None for a deletion
    ref_base: str = ""  # reference base; "-" for an insertion
    obs: dict[str, Obs] = field(default_factory=dict)  # direction -> what that read shows here
    anchor: int | None = None  # insertions: 0-based reference position of the preceding column
    ins_offset: int = 0  # 0 for reference columns, 1..k within an insertion

    @property
    def ref_number(self) -> int | None:
        return None if self.ref_pos is None else self.ref_pos + 1


@dataclass(frozen=True)
class Reason:
    rule: str
    text: str

    @property
    def family(self) -> str:
        return RULES[self.rule][1]


@dataclass
class RuleResult:
    """One line of the rule checklist. ``passed`` is None when the rule was not applied."""

    rule: str
    label: str
    family: str
    observed: str
    limit: str
    passed: bool | None
    column: int | None = None


@dataclass
class Indel:
    kind: str  # "insertion" or "deletion"
    ref_pos: int  # 1-based: first deleted position, or the position an insertion follows
    length: int
    frameshift: bool
    column: int  # first alignment column of the indel

    def __str__(self) -> str:
        return f"{self.length} bp {self.kind} at reference {self.ref_pos}"


@dataclass
class StopCodon:
    codon: int  # codon number in the reference frame
    bases: str  # consensus triplet
    ref_bases: str  # reference triplet ("-" for inserted columns)
    ref_start: int  # 1-based reference position of the first base
    columns: list[int] = field(default_factory=list)


@dataclass
class SampleCall:
    """Assembly and RECall verdict for one sample."""

    name: str
    status: str  # PASS / FAIL
    sequence: str
    reads: dict[str, ReadQC | None]  # direction -> read QC
    aligned: list[AlignedRead] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    ref_start: int = 0  # 1-based, inclusive
    ref_end: int = 0  # 1-based, inclusive
    mixtures: int = 0
    n_count: int = 0
    marks: int = 0
    max_single_run: int = 0
    indel_records: list[Indel] = field(default_factory=list)
    stop_records: list[StopCodon] = field(default_factory=list)
    reason_items: list[Reason] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)  # full checklist, in display order
    provisional: bool = False  # assembled for display only: a lab read rule failed
    error: str | None = None

    @property
    def reasons(self) -> list[str]:
        return [r.text for r in self.reason_items]

    @property
    def indels(self) -> list[str]:
        return [str(i) for i in self.indel_records]

    @property
    def stop_codons(self) -> list[int]:
        return [s.codon for s in self.stop_records]

    @property
    def length(self) -> int:
        return len(self.sequence)

    @property
    def mixture_fraction(self) -> float:
        return self.mixtures / self.length if self.length else 0.0

    @property
    def forward(self) -> ReadQC | None:
        return self.reads.get("F")

    @property
    def reverse(self) -> ReadQC | None:
        return self.reads.get("R")

    def fail(self, rule: str, text: str) -> None:
        self.reason_items.append(Reason(rule, text))

    def rule(self, rule_id: str) -> RuleResult | None:
        return next((r for r in self.rules if r.rule == rule_id), None)


def assemble(aligned: list[AlignedRead], s: QCSettings) -> list[Position]:
    """Call every reference position spanned by the reads, plus insertions.

    A reference column is a deletion only when more reads show a gap than show a base
    (censored bases count as bases here, so a weak base is never outvoted by one gap).
    An inserted column is kept only when more reads carry it than continue without it
    and at least one inserted base is usable; otherwise the anchor is marked.
    """
    if not aligned:
        return []
    start = min(r.ref_start for r in aligned)
    end = max(r.ref_end for r in aligned)
    positions: list[Position] = []
    for pos in range(start, end):
        spanning = [r for r in aligned if r.spans(pos)]
        obs = {r.direction: r.columns[pos] for r in spanning if pos in r.columns}
        gaps = sum(o.base == GAP for o in obs.values())
        bases = [o for o in obs.values() if o.base != GAP]
        if gaps > len(bases):
            p = Position(pos, GAP, gaps, 0.0, ["deletion"])
        else:
            p = _call(pos, [o for o in bases if is_usable(o, s)], s, gaps)
        p.obs = obs
        positions.append(p)
        anchor = p

        through = [r for r in spanning if r.spans(pos + 1)]
        longest = max((len(r.inserts.get(pos, [])) for r in through), default=0)
        kept = 0
        for k in range(longest):
            ins = {r.direction: r.inserts[pos][k] for r in through if len(r.inserts.get(pos, [])) > k}
            usable = [o for o in ins.values() if is_usable(o, s)]
            if len(ins) > len(through) - len(ins) and usable:
                kept += 1
                q = _call(None, usable, s)
                q.marks.append("insertion")
                q.obs = {r.direction: ins.get(r.direction, Obs(GAP, 0)) for r in through}
                q.anchor, q.ins_offset = pos, kept
                positions.append(q)
            elif "insertion in some reads" not in anchor.marks:
                anchor.marks.append("insertion in some reads")
    return positions


def _call(pos: int | None, usable: list[Obs], s: QCSettings, gaps: int = 0) -> Position:
    base = vote(usable, s)
    mean_q = float(np.mean([o.quality for o in usable])) if usable else 0.0
    p = Position(pos, base, len(usable), mean_q)
    if not usable:
        p.marks.append("no usable coverage")
        return p
    if gaps:
        p.marks.append("deletion in some reads")
    if len(usable) == 1:
        p.marks.append("single coverage")
    if sum(o.ratio >= s.mark_area or o.base in TWO_BASE_CODES for o in usable) >= len(usable) / 2:
        p.marks.append("mark area")
    if mean_q < s.mark_avg_quality:
        p.marks.append("low average quality")
    return p


def _number(positions: list[Position], reference: str) -> None:
    """Fill column, consensus index and reference base of every position."""
    cons = 0
    for i, p in enumerate(positions):
        p.column = i
        if p.base != GAP:
            cons += 1
            p.cons_index = cons
        p.ref_base = reference[p.ref_pos] if p.ref_pos is not None else GAP


def format_ranges(positions: list[int]) -> str:
    """0-based positions -> '1-100, 352-450' (1-based, inclusive)."""
    out, run_start, prev = [], None, None
    for p in sorted(positions) + [None]:
        if p is not None and prev is not None and p == prev + 1:
            prev = p
            continue
        if run_start is not None:
            out.append(f"{run_start + 1}" if run_start == prev else f"{run_start + 1}-{prev + 1}")
        run_start = prev = p
    return ", ".join(out)


def _rule(rule: str, observed: str, limit: str, passed: bool | None, column: int | None = None) -> RuleResult:
    label, family, _ = RULES[rule]
    return RuleResult(rule, label, family, observed, limit, passed, column)


def call_sample(
    name: str,
    reads: dict[str, ReadQC | None],
    reference: str,
    settings: QCSettings | None = None,
    *,
    unreadable: dict[str, str] | None = None,
) -> SampleCall:
    """Assemble one sample's reads against the reference and apply RECall's rules.

    ``reference`` must already be normalised (see :func:`references.normalize_sequence`).
    ``unreadable`` maps a direction to the file name that could not be read; that
    direction always fails, whether or not both directions are required.
    """
    s = settings or QCSettings()
    reference = reference.upper()
    if not reference or any(c not in _ALPHABET for c in reference):
        raise ValueError("reference is empty or not normalised (IUPAC DNA letters only)")
    unreadable = unreadable or {}
    result = SampleCall(name, "FAIL", "", reads)
    rules: dict[str, RuleResult] = {}

    # Lab rules: both directions present and readable, every read passes the read cutoff.
    for direction, label in DIRECTIONS:
        read = reads.get(direction)
        present, cutoff = f"{label}_present", f"{label}_cutoff"
        required = "required" if s.require_both_directions else "optional"
        if direction in unreadable:
            result.fail(present, f"{label} read unreadable ({unreadable[direction]})")
            rules[present] = _rule(present, "unreadable", required, False)
        elif read is None:
            if s.require_both_directions:
                result.fail(present, f"{label} read missing")
            rules[present] = _rule(present, "missing", required, not s.require_both_directions)
        else:
            rules[present] = _rule(present, "present", required, True)
        limit = f"≥ {s.min_good_fraction:.0%} ≥ Q{s.min_quality}, ≥ {s.min_trimmed_length} bp"
        if read is None:
            rules[cutoff] = _rule(cutoff, "–", limit, None)
            continue
        observed = f"{read.good_fraction:.1%} ≥ Q{s.min_quality}, {read.trimmed_length} bp"
        rules[cutoff] = _rule(cutoff, observed, limit, read.passed)
        if not read.passed:
            result.fail(cutoff, f"{label} read failed QC: " + "; ".join(read.reasons))

    match_limit = f"≥ {s.min_read_identity:.0%} identity over ≥ {s.min_read_aligned} bp"
    for direction, label in DIRECTIONS:
        read, match = reads.get(direction), f"{label}_match"
        if read is None or not read.passed:
            rules[match] = _rule(match, "–", match_limit, None)
            continue
        a = align_read(read, direction, reference)
        if a is None:
            problem = "no alignment"
        elif a.ref_end - a.ref_start < s.min_read_aligned:
            problem = f"aligns over only {a.ref_end - a.ref_start} bp"
        elif a.identity < s.min_read_identity:
            problem = f"{a.identity:.0%} identity"
        else:
            problem = ""
        if problem:
            result.fail(match, f"{label} read does not match the reference ({problem})")
            rules[match] = _rule(match, problem, match_limit, False)
        else:
            result.aligned.append(a)
            rules[match] = _rule(match, f"{a.identity:.2%} over {a.ref_start + 1}–{a.ref_end}", match_limit, True)

    rules["strands"] = _check_strands(result, s)

    lab_failed = bool(result.reason_items)
    if not result.aligned and not lab_failed:
        result.fail("forward_present", "no usable reads")
    if result.aligned:
        result.positions = assemble(result.aligned, s)
        _number(result.positions, reference)
    recall_rules = _apply_rules(result, s, len(reference))
    if lab_failed or not result.aligned:
        # A lab read rule failed: the assembly is for display only and only that cause is reported
        # (one missing read would otherwise also trip the single-coverage and mark limits).
        result.reason_items = [r for r in result.reason_items if r.family == "lab"]
        result.provisional = bool(result.positions)
        for r in recall_rules.values():
            r.passed = None
    rules.update(recall_rules)
    result.rules = [rules[k] for k in RULES if k in rules]
    result.status = "FAIL" if result.reason_items else "PASS"
    return result


def _check_strands(result: SampleCall, s: QCSettings) -> RuleResult:
    """Forward must align to +, reverse to -, and never both to the same strand (B3)."""
    by_dir = {a.direction: a.strand for a in result.aligned}
    limit = "F on +, R on −" if s.check_read_orientation else "opposite strands"
    observed = ", ".join(f"{d} on {by_dir[d]}" for d, _ in DIRECTIONS if d in by_dir) or "–"
    if "F" in by_dir and "R" in by_dir and by_dir["F"] == by_dir["R"]:
        result.fail("strands", f"forward and reverse reads align to the same strand ({by_dir['F']}): "
                               "duplicate or mislabelled file")
    if s.check_read_orientation:
        if by_dir.get("F") == "-":
            result.fail("strands", "forward read aligns to the minus strand (expected plus)")
        if by_dir.get("R") == "+":
            result.fail("strands", "reverse read aligns to the plus strand (expected minus)")
    failed = any(r.rule == "strands" for r in result.reason_items)
    return _rule("strands", observed, limit, None if not by_dir else not failed)


def _apply_rules(r: SampleCall, s: QCSettings, ref_len: int) -> dict[str, RuleResult]:
    """Metrics and RECall rejection rules (Table 2) on an assembly; returns the checklist entries."""
    pos = r.positions
    out: dict[str, RuleResult] = {}
    if not pos:
        for rule in ("full_coverage", "stop_codon", "bad_insert", "bad_deletion", "mixtures", "n_count",
                     "marks", "single_coverage", "low_quality"):
            out[rule] = _rule(rule, "–", "", None)
        return out
    ref_positions = [p.ref_pos for p in pos if p.ref_pos is not None]
    r.ref_start, r.ref_end = ref_positions[0] + 1, ref_positions[-1] + 1
    r.sequence = "".join(p.base for p in pos if p.base != GAP)
    r.mixtures = sum(1 for c in r.sequence if c not in "ACGTN")
    r.n_count = r.sequence.count("N")
    r.marks = sum(1 for p in pos if p.marks)

    # Whole-reference coverage (paper: double primer coverage over the entire sequence length).
    need = 2 if s.require_both_directions else 1
    depth = np.zeros(ref_len, dtype=int)
    for a in r.aligned:
        depth[a.ref_start:a.ref_end] += 1
    uncovered = np.flatnonzero(depth < need).tolist()
    by = "both reads" if need == 2 else "a read"
    if not s.require_full_reference:
        out["full_coverage"] = _rule("full_coverage", f"{r.ref_start}–{r.ref_end}", "not required", True)
    elif uncovered:
        text = f"reference {format_ranges(uncovered)} not covered by {by}"
        r.fail("full_coverage", f"incomplete coverage: {text} (whole reference required)")
        col = next((p.column for p in pos if p.ref_pos is not None and p.ref_pos >= uncovered[0]), 0)
        out["full_coverage"] = _rule("full_coverage", f"missing {format_ranges(uncovered)}",
                                     f"1–{ref_len} by {by}", False, col)
    else:
        out["full_coverage"] = _rule("full_coverage", f"1–{ref_len}", f"1–{ref_len} by {by}", True)

    # Indels relative to the reference must keep the reading frame.
    run: list[Position] = []
    for p in pos + [Position(None, "", 0, 0.0)]:  # sentinel flushes the last run
        kind = "deletion" if p.base == GAP else ("insertion" if p.ref_pos is None and p.base else None)
        if run and kind == _indel_kind(run[0]):
            run.append(p)
            continue
        if run:
            first = run[0]
            at = first.ref_pos + 1 if first.ref_pos is not None else first.anchor + 1
            r.indel_records.append(Indel(_indel_kind(first), at, len(run), len(run) % 3 != 0, first.column))
        run = [p] if kind else []
    for kind, rule in (("insertion", "bad_insert"), ("deletion", "bad_deletion")):
        found = [i for i in r.indel_records if i.kind == kind]
        bad = [i for i in found if i.frameshift]
        if not s.check_frameshifts:
            out[rule] = _rule(rule, f"{len(found)} {kind}(s)" if found else "none", "not checked", True)
            continue
        for i in bad:
            r.fail(rule, f"bad {'insert' if kind == 'insertion' else 'deletion'}: "
                         f"{i.length} bp {kind} at reference position {i.ref_pos} (frameshift)")
        observed = "; ".join(f"{i.length} bp at {i.ref_pos}{' (frameshift)' if i.frameshift else ''}"
                             for i in found) or "none"
        shown = bad or found
        out[rule] = _rule(rule, observed, "in frame only", not bad, shown[0].column if shown else None)

    # Stop codons, read in the reference frame.
    if s.check_stop_codons:
        frame0 = s.frame - 1
        codon: list[Position] = []
        codon_no = None
        for p in pos:
            if p.base == GAP:
                continue
            if p.ref_pos is not None and p.ref_pos >= frame0 and (p.ref_pos - frame0) % 3 == 0:
                codon, codon_no = [], (p.ref_pos - frame0) // 3 + 1
            if codon_no is None:
                continue
            codon.append(p)
            if len(codon) == 3:
                bases = "".join(q.base for q in codon)
                if bases in STOPS:
                    on_ref = [q.ref_number for q in codon if q.ref_pos is not None]
                    start = on_ref[0] if on_ref else codon[0].anchor + 1
                    r.stop_records.append(StopCodon(codon_no, bases, "".join(q.ref_base for q in codon),
                                                    start, [q.column for q in codon]))
                codon = []
        if r.stop_records:
            r.fail("stop_codon", "stop codon at codon " + ", ".join(map(str, r.stop_codons)))
            out["stop_codon"] = _rule("stop_codon", "codon " + ", ".join(map(str, r.stop_codons)), "none",
                                      False, r.stop_records[0].columns[0])
        else:
            out["stop_codon"] = _rule("stop_codon", "none", "none", True)
    else:
        out["stop_codon"] = _rule("stop_codon", "not checked", "not checked", True)

    mix_failed = bool(r.length) and r.mixture_fraction > s.max_mixture_fraction
    if mix_failed:
        r.fail("mixtures", f"too many mixtures: {r.mixtures} ({r.mixture_fraction:.1%} > "
                           f"{s.max_mixture_fraction:.1%})")
    first_mix = next((p.column for p in pos if p.base not in "ACGTN" + GAP), None)
    out["mixtures"] = _rule("mixtures", f"{r.mixtures} ({r.mixture_fraction:.2%})",
                            f"≤ {s.max_mixture_fraction:.1%}", not mix_failed, first_mix)
    if r.n_count > s.max_n:
        r.fail("n_count", f"N count {r.n_count} (fail at >= {s.max_n + 1})")
    first_n = next((p.column for p in pos if p.base == "N"), None)
    out["n_count"] = _rule("n_count", str(r.n_count), f"< {s.max_n + 1}", r.n_count <= s.max_n, first_n)
    if r.marks > s.max_marks:
        r.fail("marks", f"{r.marks} marked positions (fail at >= {s.max_marks + 1})")
    first_mark = next((p.column for p in pos if p.marks), None)
    out["marks"] = _rule("marks", str(r.marks), f"< {s.max_marks + 1}", r.marks <= s.max_marks, first_mark)

    # Single coverage: > 3 consecutive single-read bases below Q40.
    run_len = best = 0
    best_start = start = None
    for p in pos:
        if p.base != GAP and p.coverage == 1 and p.mean_quality < s.single_coverage_quality:
            run_len += 1
            if run_len == 1:
                start = p.column
            if run_len > best:
                best, best_start = run_len, start
        elif p.base != GAP:
            run_len = 0
    r.max_single_run = best
    single_failed = best > s.max_single_coverage_run
    if single_failed:
        r.fail("single_coverage", f"single coverage: {best} consecutive bases from one read below "
                                  f"Q{s.single_coverage_quality} (fail at > {s.max_single_coverage_run})")
    out["single_coverage"] = _rule("single_coverage", f"{best} base{'s' if best != 1 else ''}",
                                   f"≤ {s.max_single_coverage_run}", not single_failed, best_start)

    low = [p for p in pos if not p.coverage and p.ref_pos is not None]
    if low:
        r.fail("low_quality", f"low quality: no usable read at {len(low)} position(s) from reference "
                              f"{low[0].ref_number}")
    out["low_quality"] = _rule("low_quality", f"{len(low)} position{'s' if len(low) != 1 else ''} without a read",
                               "0", not low, low[0].column if low else None)
    return out


def _indel_kind(p: Position) -> str | None:
    if p.base == GAP:
        return "deletion"
    if p.ref_pos is None and p.base:
        return "insertion"
    return None
