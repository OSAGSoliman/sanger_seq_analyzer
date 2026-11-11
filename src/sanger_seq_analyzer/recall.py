"""RECall-style assembly, mixture calling, marking and pass/fail.

Implements the rules described in Woods et al. 2012, "Automating HIV drug
resistance genotyping with RECall" (J Clin Microbiol 50:1936-1942):

* reads are trimmed, bases with phred < 10 are censored, and every read is
  aligned to a user-supplied reference;
* each reference position is called by voting over the reads (Table 1);
* potentially problematic positions are "marked";
* the consensus is rejected by the criteria in Table 2.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from Bio.Align import PairwiseAligner, substitution_matrices

from .qc import IUPAC, IUPAC_TO_BASES, QCSettings, ReadQC

_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN?", "TGCAYRSWMKVHDBN?")
_ALPHABET = "ACGTRYSWKMBDHVN"
GAP = "-"
STOPS = {"TAA", "TAG", "TGA"}


def reverse_complement(seq: str) -> str:
    return seq.upper().translate(_COMPLEMENT)[::-1]


def _aligner() -> PairwiseAligner:
    matrix = substitution_matrices.Array(alphabet=_ALPHABET, dims=2)
    for a in _ALPHABET:
        for b in _ALPHABET:
            sa, sb = IUPAC_TO_BASES[a], IUPAC_TO_BASES[b]
            matrix[a, b] = 2.0 if (a == b and len(sa) == 1) else (0.0 if sa & sb else -3.0)
    aligner = PairwiseAligner()
    aligner.mode = "local"  # modified Smith-Waterman, as in RECall
    aligner.substitution_matrix = matrix
    aligner.open_gap_score = -8
    aligner.extend_gap_score = -2
    return aligner


@dataclass
class Obs:
    """What one read shows at one alignment column."""

    base: str  # called base, or "-" for a deletion relative to the reference
    quality: int
    uncalled: str = ""
    ratio: float = 0.0  # uncalled/called peak area


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


def align_read(read: ReadQC, direction: str, reference: str) -> AlignedRead | None:
    """Align a trimmed read (either strand) to the reference."""
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

    quals = read.qualities if strand == "+" else read.qualities[::-1]
    ratios = read.area_ratios if strand == "+" else read.area_ratios[::-1]
    uncalled = read.uncalled if strand == "+" else reverse_complement(read.uncalled)

    def obs(i: int) -> Obs:
        u = uncalled[i] if i < len(uncalled) and uncalled[i] in "ACGT" else ""
        return Obs(query[i], int(quals[i]), u, float(ratios[i]))

    ri, qi = aln.indices
    columns: dict[int, Obs] = {}
    inserts: dict[int, list[Obs]] = {}
    last_ref, same, cols = None, 0, 0
    for a, b in zip(ri, qi):
        a, b = int(a), int(b)
        cols += 1
        if a >= 0 and b >= 0:
            columns[a] = obs(b)
            same += query[b] == reference[a]
            last_ref = a
        elif a >= 0:
            columns[a] = Obs(GAP, 0)
            last_ref = a
        elif last_ref is not None:
            inserts.setdefault(last_ref, []).append(obs(b))
    if not columns:
        return None
    return AlignedRead(read, direction, strand, min(columns), max(columns) + 1,
                       same / cols if cols else 0.0, columns, inserts)


def vote(observations: list[Obs], s: QCSettings) -> str:
    """RECall's per-position call (Woods et al. 2012, Methods).

    Count each nucleotide seen as a called base, or as an uncalled base whose
    area is >= the mixture-area cutoff; keep nucleotides seen in at least half
    of the reads; a mixture is called when the second most common one has more
    than half the count of the most common (a tie therefore also gives a
    mixture). Never more than two bases.
    """
    n = len(observations)
    if n == 0:
        return "N"
    counts: Counter[str] = Counter()
    for o in observations:
        counts[o.base] += 1
        if s.call_mixtures and o.base != GAP and o.uncalled and o.ratio >= s.mixture_area:
            counts[o.uncalled] += 1
    kept = sorted(((c, b) for b, c in counts.items() if c >= n / 2), key=lambda x: (-x[0], x[1]))
    if not kept:
        return "N"
    c1, b1 = kept[0]
    if len(kept) > 1:
        c2, b2 = kept[1]
        if c2 > c1 / 2:
            if GAP in (b1, b2):
                return b1 if b1 != GAP else b2  # base vs deletion: keep the base (position is marked)
            return IUPAC[frozenset((b1, b2))]
    return b1


@dataclass
class Position:
    ref_pos: int | None  # 0-based reference position; None for an insertion
    base: str  # consensus call ("-" for a deletion)
    coverage: int  # reads contributing an uncensored observation
    mean_quality: float
    marks: list[str] = field(default_factory=list)


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
    stop_codons: list[int] = field(default_factory=list)  # codon numbers (reference numbering)
    indels: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

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


def assemble(aligned: list[AlignedRead], s: QCSettings) -> list[Position]:
    """Call every reference position spanned by the reads, plus insertions."""
    if not aligned:
        return []
    start = min(r.ref_start for r in aligned)
    end = max(r.ref_end for r in aligned)
    positions: list[Position] = []
    for pos in range(start, end):
        spanning = [r for r in aligned if r.spans(pos)]
        obs = [r.columns[pos] for r in spanning if pos in r.columns]
        usable = [o for o in obs if o.base == GAP or (o.base in "ACGT" and o.quality >= s.censor_quality)]
        positions.append(_call(pos, usable, s))

        # Insertions after this position, among reads that continue past it.
        through = [r for r in spanning if r.spans(pos + 1)]
        longest = max((len(r.inserts.get(pos, [])) for r in through), default=0)
        for k in range(longest):
            col = []
            for r in through:
                ins = r.inserts.get(pos, [])
                col.append(ins[k] if k < len(ins) else Obs(GAP, 0))
            col = [o for o in col if o.base == GAP or (o.base in "ACGT" and o.quality >= s.censor_quality)]
            p = _call(None, col, s)
            if p.base != GAP:
                p.marks.append("insertion")
                positions.append(p)
    return positions


def _call(pos: int | None, usable: list[Obs], s: QCSettings) -> Position:
    base = vote(usable, s)
    bases = [o for o in usable if o.base != GAP]
    mean_q = float(np.mean([o.quality for o in bases])) if bases else 0.0
    p = Position(pos, base, len(usable), mean_q)
    if not usable:
        p.marks.append("no usable coverage")
        return p
    if base == GAP:
        p.marks.append("deletion")
        return p
    if any(o.base == GAP for o in usable):
        p.marks.append("deletion in some reads")
    if len(usable) == 1:
        p.marks.append("single coverage")
    if bases and sum(o.ratio >= s.mark_area for o in bases) >= len(usable) / 2:
        p.marks.append("mark area")
    if bases and mean_q < s.mark_avg_quality:
        p.marks.append("low average quality")
    return p


def call_sample(
    name: str,
    reads: dict[str, ReadQC | None],
    reference: str,
    settings: QCSettings | None = None,
) -> SampleCall:
    """Assemble one sample's reads against the reference and apply RECall's rules."""
    s = settings or QCSettings()
    reference = "".join(c for c in reference.upper() if c in _ALPHABET)
    result = SampleCall(name, "FAIL", "", reads)

    # Lab rules: both directions present, every read passes the read cutoff.
    for direction, label in (("F", "forward"), ("R", "reverse")):
        read = reads.get(direction)
        if read is None:
            if s.require_both_directions:
                result.reasons.append(f"{label} read missing")
        elif not read.passed:
            result.reasons.append(f"{label} read failed QC: " + "; ".join(read.reasons))

    usable = {d: r for d, r in reads.items() if r is not None and r.passed}
    for direction, read in usable.items():
        a = align_read(read, direction, reference)
        label = "forward" if direction == "F" else "reverse"
        if a is None or a.ref_end - a.ref_start < s.min_read_aligned or a.identity < s.min_read_identity:
            if a is None:
                ident = "no alignment"
            elif a.ref_end - a.ref_start < s.min_read_aligned:
                ident = f"aligns over only {a.ref_end - a.ref_start} bp"
            else:
                ident = f"{a.identity:.0%} identity"
            result.reasons.append(f"{label} read does not match the reference ({ident})")
        else:
            result.aligned.append(a)
    lab_reasons = list(result.reasons)

    if not result.aligned:
        if not result.reasons:
            result.reasons.append("no usable reads")
        return result

    result.positions = assemble(result.aligned, s)
    if result.reasons:
        # Already failed on a read-level rule; assemble for display, but report only that cause
        # (one missing read would otherwise also trip the single-coverage and mark limits).
        _apply_rules(result, s)
        result.reasons = [r for r in result.reasons if r in lab_reasons]
    else:
        _apply_rules(result, s)
    if result.reasons:
        result.status = "FAIL"
    else:
        result.status = "PASS"
    return result


def _apply_rules(r: SampleCall, s: QCSettings) -> None:
    pos = r.positions
    ref_positions = [p.ref_pos for p in pos if p.ref_pos is not None]
    r.ref_start, r.ref_end = ref_positions[0] + 1, ref_positions[-1] + 1
    r.sequence = "".join(p.base for p in pos if p.base != GAP)
    r.mixtures = sum(1 for c in r.sequence if c not in "ACGTN")
    r.n_count = r.sequence.count("N")
    r.marks = sum(1 for p in pos if p.marks)

    # Indels relative to the reference must keep the reading frame.
    run_kind, run_len, run_at, last_ref = None, 0, 0, 0
    for p in pos + [Position(None, "", 0, 0.0)]:  # sentinel flushes the last run
        kind = "deletion" if p.base == GAP else ("insertion" if p.ref_pos is None and p.base else None)
        if kind == run_kind and kind is not None:
            run_len += 1
            continue
        if run_kind is not None:
            r.indels.append(f"{run_len} bp {run_kind} at reference {run_at}")
            if s.check_frameshifts and run_len % 3:
                r.reasons.append(f"bad {'insert' if run_kind == 'insertion' else 'deletion'}: "
                                 f"{run_len} bp {run_kind} at reference position {run_at} (frameshift)")
        run_kind, run_len = kind, 1
        # Deletions are reported at their first reference position, insertions after the previous one.
        run_at = p.ref_pos + 1 if p.ref_pos is not None else last_ref
        if p.ref_pos is not None:
            last_ref = p.ref_pos + 1

    # Stop codons, read in the reference frame.
    if s.check_stop_codons:
        frame0 = s.frame - 1
        codon, codon_no = "", None
        for p in pos:
            if p.base == GAP:
                continue
            if p.ref_pos is not None and (p.ref_pos - frame0) % 3 == 0:
                codon, codon_no = "", (p.ref_pos - frame0) // 3 + 1
            if codon_no is None:
                continue
            codon += p.base
            if len(codon) == 3:
                if codon in STOPS:
                    r.stop_codons.append(codon_no)
                codon = ""
        if r.stop_codons:
            r.reasons.append("stop codon at codon " + ", ".join(map(str, r.stop_codons)))

    if r.length and r.mixture_fraction > s.max_mixture_fraction:
        r.reasons.append(f"too many mixtures: {r.mixtures} ({r.mixture_fraction:.1%} > "
                         f"{s.max_mixture_fraction:.1%})")
    if r.n_count > s.max_n:
        r.reasons.append(f"N count {r.n_count} (fail at >= {s.max_n + 1})")
    if r.marks > s.max_marks:
        r.reasons.append(f"{r.marks} marked positions (fail at >= {s.max_marks + 1})")

    # Single coverage: > 3 consecutive single-read bases below Q40.
    run = best = 0
    for p in pos:
        if p.base != GAP and p.coverage == 1 and p.mean_quality < s.single_coverage_quality:
            run += 1
            best = max(best, run)
        elif p.base != GAP:
            run = 0
    r.max_single_run = best
    if best > s.max_single_coverage_run:
        r.reasons.append(f"single coverage: {best} consecutive bases from one read below "
                         f"Q{s.single_coverage_quality} (fail at > {s.max_single_coverage_run})")

    low = [p.ref_pos + 1 for p in pos if not p.coverage and p.ref_pos is not None]
    if low:
        r.reasons.append(f"low quality: no usable read at {len(low)} position(s) from reference {low[0]}")

