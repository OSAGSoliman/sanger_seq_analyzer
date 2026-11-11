"""Per-read quality control: quality trimming, mixture calling and pass/fail."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .abi import BASES, Chromatogram

# Bases -> IUPAC ambiguity code.
IUPAC = {
    frozenset("A"): "A", frozenset("C"): "C", frozenset("G"): "G", frozenset("T"): "T",
    frozenset("AG"): "R", frozenset("CT"): "Y", frozenset("GC"): "S", frozenset("AT"): "W",
    frozenset("GT"): "K", frozenset("AC"): "M", frozenset("CGT"): "B", frozenset("AGT"): "D",
    frozenset("ACT"): "H", frozenset("ACG"): "V", frozenset("ACGT"): "N",
}
IUPAC_TO_BASES = {code: bases for bases, code in IUPAC.items()}


@dataclass
class QCSettings:
    """Tunable thresholds. Defaults follow the lab's 75% rule."""

    min_quality: int = 20  # Phred value counted as a "good" base (QV20 = 99% accurate)
    min_good_fraction: float = 0.75  # a read passes if >= 75% of its bases are >= min_quality
    min_trimmed_length: int = 100  # shortest usable read after trimming
    trim_error_limit: float = 0.05  # Mott's algorithm error limit (as in phred/Geneious)
    mixture_threshold: float = 0.20  # secondary/primary peak ratio that calls a mixture
    call_mixtures: bool = True


@dataclass
class ReadQC:
    """QC result for one chromatogram."""

    name: str
    length: int
    mean_quality: float
    good_fraction: float  # fraction of bases with Phred >= min_quality
    trim_start: int  # 0-based, inclusive
    trim_end: int  # 0-based, exclusive
    sequence: str  # trimmed sequence, with IUPAC mixture codes if enabled
    qualities: np.ndarray  # qualities of the trimmed sequence
    mixtures: int
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def trimmed_length(self) -> int:
        return self.trim_end - self.trim_start

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


def mott_trim(qualities: np.ndarray, error_limit: float = 0.05) -> tuple[int, int]:
    """Return (start, end) of the best-quality region using Mott's algorithm.

    Each base scores ``error_limit - P(error)``; the trimmed region is the
    maximum-sum contiguous segment. Returns (0, 0) if no base qualifies.
    """
    if len(qualities) == 0:
        return 0, 0
    perr = np.power(10.0, -np.asarray(qualities, dtype=float) / 10.0)
    scores = error_limit - perr
    best_sum, best = 0.0, (0, 0)
    run_sum, run_start = 0.0, 0
    for i, s in enumerate(scores):
        if run_sum + s <= 0:
            run_sum, run_start = 0.0, i + 1
        else:
            run_sum += s
            if run_sum > best_sum:
                best_sum, best = run_sum, (run_start, i + 1)
    return best


def peak_heights(chrom: Chromatogram, window: int = 2) -> np.ndarray | None:
    """Height of each channel at each base call, shape (n_bases, 4) in ACGT order."""
    if not chrom.has_traces or len(chrom.peak_locations) != len(chrom):
        return None
    n_points = min(len(chrom.traces[b]) for b in BASES)
    heights = np.zeros((len(chrom), 4))
    for j, base in enumerate(BASES):
        trace = chrom.traces[base]
        for i, loc in enumerate(chrom.peak_locations):
            lo, hi = max(0, loc - window), min(n_points, loc + window + 1)
            if lo < hi:
                heights[i, j] = trace[lo:hi].max()
    return heights


def call_mixtures(chrom: Chromatogram, threshold: float) -> tuple[str, int]:
    """Re-call bases from the trace, using IUPAC codes for mixed positions.

    A position is mixed when another channel's peak is at least ``threshold``
    times the height of the called base's peak (as RECall does).
    """
    heights = peak_heights(chrom)
    if heights is None:
        return chrom.sequence, 0
    out, n_mixed = [], 0
    for i, called in enumerate(chrom.sequence):
        row = heights[i]
        if called not in BASES or row.max() <= 0:
            out.append(called)
            continue
        primary = row[BASES.index(called)]
        if primary <= 0:
            out.append(called)
            continue
        present = {called} | {b for j, b in enumerate(BASES) if b != called and row[j] >= threshold * primary}
        code = IUPAC[frozenset(present)]
        if code != called:
            n_mixed += 1
        out.append(code)
    return "".join(out), n_mixed


def qc_read(chrom: Chromatogram, settings: QCSettings | None = None) -> ReadQC:
    """Trim, call mixtures and decide PASS/FAIL for one read."""
    s = settings or QCSettings()
    q = chrom.qualities
    n = len(chrom)
    good_fraction = float((q >= s.min_quality).mean()) if n else 0.0
    mean_q = float(q.mean()) if n else 0.0

    start, end = mott_trim(q, s.trim_error_limit)
    seq = chrom.sequence
    n_mixed = 0
    if s.call_mixtures:
        seq, _ = call_mixtures(chrom, s.mixture_threshold)
        n_mixed = sum(1 for c in seq[start:end] if c not in BASES)
        # Mixtures are only trusted inside the trimmed (high quality) region.
        seq = chrom.sequence[:start] + seq[start:end] + chrom.sequence[end:]

    reasons = []
    if good_fraction < s.min_good_fraction:
        reasons.append(
            f"only {good_fraction:.1%} of bases are >= Q{s.min_quality} "
            f"(cutoff {s.min_good_fraction:.0%})"
        )
    if end - start < s.min_trimmed_length:
        reasons.append(f"trimmed length {end - start} < {s.min_trimmed_length}")

    return ReadQC(
        name=chrom.name,
        length=n,
        mean_quality=mean_q,
        good_fraction=good_fraction,
        trim_start=start,
        trim_end=end,
        sequence=seq[start:end],
        qualities=q[start:end].copy(),
        mixtures=n_mixed,
        passed=not reasons,
        reasons=reasons,
    )
