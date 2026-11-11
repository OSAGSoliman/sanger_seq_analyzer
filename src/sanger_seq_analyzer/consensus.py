"""Merge a forward and reverse read into one consensus sequence."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from Bio.Align import PairwiseAligner, substitution_matrices

from .qc import IUPAC, IUPAC_TO_BASES, QCSettings, ReadQC

_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")
_ALPHABET = "ACGTRYSWKMBDHVN"


def reverse_complement(seq: str) -> str:
    return seq.upper().translate(_COMPLEMENT)[::-1]


def _aligner(mode: str = "global") -> PairwiseAligner:
    matrix = substitution_matrices.Array(alphabet=_ALPHABET, dims=2)
    for a in _ALPHABET:
        for b in _ALPHABET:
            sa, sb = IUPAC_TO_BASES[a], IUPAC_TO_BASES[b]
            if a == b and len(sa) == 1:
                matrix[a, b] = 2.0
            elif sa & sb:
                matrix[a, b] = 1.0
            else:
                matrix[a, b] = -3.0
    aligner = PairwiseAligner()
    aligner.mode = mode
    aligner.substitution_matrix = matrix
    aligner.open_gap_score = -6
    aligner.extend_gap_score = -2
    if mode == "global":
        # Overhanging ends are expected (reads start at different places).
        aligner.end_gap_score = 0
    return aligner


@dataclass
class ConsensusResult:
    name: str
    sequence: str
    status: str  # PASS / SINGLE / FAIL
    forward: ReadQC | None
    reverse: ReadQC | None
    overlap: int = 0
    overlap_identity: float = 0.0
    discordant: int = 0
    mixtures: int = 0
    ambiguous: int = 0  # N calls in the consensus
    reasons: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.sequence)


def _merge_column(fb: str, qf: int, rb: str, qr: int, min_q: int) -> tuple[str, bool]:
    """Combine one aligned column covered by both reads. Returns (base, discordant)."""
    if fb == rb:
        return fb, False
    sf, sr = IUPAC_TO_BASES.get(fb, set("ACGT")), IUPAC_TO_BASES.get(rb, set("ACGT"))
    shared = sf & sr
    if shared and (len(sf) > 1 or len(sr) > 1):
        # A mixture seen in only one direction: keep what both reads support.
        return IUPAC[frozenset(shared)], False
    # Genuine disagreement: trust the clearly better read, otherwise N.
    if qf >= min_q and qf - qr >= 10:
        return fb, True
    if qr >= min_q and qr - qf >= 10:
        return rb, True
    return "N", True


def build_consensus(
    name: str,
    forward: ReadQC | None,
    reverse: ReadQC | None,
    settings: QCSettings | None = None,
    min_overlap: int = 20,
    min_identity: float = 0.90,
) -> ConsensusResult:
    """Build a consensus from QC'd forward and reverse reads.

    The reverse read is reverse-complemented and aligned to the forward read.
    Reads that failed QC are not used. With only one passing read the
    consensus is that read alone (status SINGLE).
    """
    s = settings or QCSettings()
    use_f = forward if forward is not None and forward.passed else None
    use_r = reverse if reverse is not None and reverse.passed else None

    reasons = []
    for label, read in (("forward", forward), ("reverse", reverse)):
        if read is None:
            reasons.append(f"{label} read missing")
        elif not read.passed:
            reasons.append(f"{label} read failed QC: " + "; ".join(read.reasons))

    if use_f is None and use_r is None:
        return ConsensusResult(name, "", "FAIL", forward, reverse, reasons=reasons)

    if use_f is None or use_r is None:
        read = use_f or use_r
        seq = read.sequence if read is use_f else reverse_complement(read.sequence)
        quals = read.qualities if read is use_f else read.qualities[::-1]
        seq = "".join(b if q >= s.min_quality else "N" for b, q in zip(seq, quals))
        return _finish(ConsensusResult(name, seq, "SINGLE", forward, reverse, reasons=reasons))

    f_seq, f_q = use_f.sequence, use_f.qualities
    r_seq, r_q = reverse_complement(use_r.sequence), use_r.qualities[::-1]
    if not f_seq or not r_seq:
        return ConsensusResult(name, "", "FAIL", forward, reverse, reasons=reasons + ["empty read"])

    aln = _aligner("global").align(f_seq, r_seq)[0]
    fi, ri = aln.indices
    covered = [(a, b) for a, b in zip(fi, ri) if a >= 0 and b >= 0]
    overlap = len(covered)
    identical = sum(1 for a, b in covered if IUPAC_TO_BASES[f_seq[a]] & IUPAC_TO_BASES[r_seq[b]])
    identity = identical / overlap if overlap else 0.0

    first = next((k for k, (a, b) in enumerate(zip(fi, ri)) if a >= 0 and b >= 0), None)
    last = max((k for k, (a, b) in enumerate(zip(fi, ri)) if a >= 0 and b >= 0), default=None)

    out, discordant = [], 0
    for k, (a, b) in enumerate(zip(fi, ri)):
        if a >= 0 and b >= 0:
            base, bad = _merge_column(f_seq[a], int(f_q[a]), r_seq[b], int(r_q[b]), s.min_quality)
            discordant += bad
            out.append(base)
            continue
        base, q = (f_seq[a], int(f_q[a])) if a >= 0 else (r_seq[b], int(r_q[b]))
        internal = first is not None and first < k < last
        if internal:
            # Indel inside the overlap: keep the inserted base only if well supported.
            if q >= s.min_quality + 10:
                out.append(base)
            discordant += 1
        else:
            # Single coverage: no second read to confirm, so weak calls become N.
            out.append(base if q >= s.min_quality else "N")

    result = ConsensusResult(
        name, "".join(out), "PASS", forward, reverse,
        overlap=overlap, overlap_identity=identity, discordant=discordant, reasons=reasons,
    )
    if overlap < min_overlap:
        result.status = "FAIL"
        result.reasons.append(f"forward/reverse overlap {overlap} bp < {min_overlap} bp")
    elif identity < min_identity:
        result.status = "FAIL"
        result.reasons.append(
            f"forward/reverse agree at only {identity:.1%} of overlap (< {min_identity:.0%})"
        )
    return _finish(result)


def trim_ends(seq: str, window: int = 10) -> str:
    """Drop ragged ends: the sequence starts and ends with ``window`` clean bases."""
    clean = [c in "ACGT" for c in seq]
    starts = [i for i in range(len(seq) - window + 1) if all(clean[i:i + window])]
    if not starts:
        return ""
    return seq[starts[0]:starts[-1] + window]


def _finish(result: ConsensusResult) -> ConsensusResult:
    if result.status == "FAIL":
        result.sequence = ""  # never hand out a sequence from a failed sample
    result.sequence = trim_ends(result.sequence)
    seq = result.sequence
    result.mixtures = sum(1 for c in seq if c not in "ACGTN")
    result.ambiguous = seq.count("N")
    return result


@dataclass
class ReferenceHit:
    strand: str  # "+" or "-"
    identity: float
    ref_start: int
    ref_end: int
    sequence: str  # consensus oriented to the reference


def orient_to_reference(seq: str, reference: str) -> ReferenceHit | None:
    """Align ``seq`` (both strands) to a reference and return the better hit."""
    if not seq or not reference:
        return None
    aligner = _aligner("local")
    reference = "".join(c for c in reference.upper() if c in _ALPHABET)
    best = None
    for strand, query in (("+", seq), ("-", reverse_complement(seq))):
        aln = aligner.align(reference, query)[0]
        ri, qi = aln.indices
        pairs = [(a, b) for a, b in zip(ri, qi) if a >= 0 and b >= 0]
        if not pairs:
            continue
        same = sum(1 for a, b in pairs if IUPAC_TO_BASES[reference[a]] & IUPAC_TO_BASES[query[b]])
        cols = int(np.sum((np.asarray(ri) >= 0) | (np.asarray(qi) >= 0)))
        hit = ReferenceHit(strand, same / cols, int(min(a for a, _ in pairs)),
                           int(max(a for a, _ in pairs)) + 1, query)
        if best is None or aln.score > best[0]:
            best = (aln.score, hit)
    return best[1] if best else None
