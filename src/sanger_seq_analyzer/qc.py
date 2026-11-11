"""Per-read processing: settings, quality trimming, peak areas and the lab's read cutoff."""

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
    """All tunable parameters.

    RECall defaults are from Woods et al. 2012 (J Clin Microbiol 50:1936),
    Table 1 (mixture calling and marks) and Table 2 (rejection criteria).
    """

    # --- Lab read-level rules -------------------------------------------------
    min_quality: int = 20  # Phred value counted as a "good" base
    min_good_fraction: float = 0.75  # read passes if >= 75% of bases are >= min_quality
    min_trimmed_length: int = 100  # shortest usable read after trimming
    require_both_directions: bool = True  # a sample needs a forward and a reverse read
    trim_error_limit: float = 0.05  # Mott's algorithm error limit for end trimming

    # --- RECall Table 1: mixture calling and marks ----------------------------
    censor_quality: int = 10  # bases below this phred score are excluded from assembly
    mixture_area: float = 0.20  # uncalled/called peak area that counts towards a mixture
    call_mixtures: bool = True
    mark_area: float = 0.175  # uncalled/called peak area that marks a position (>= 50% of reads)
    mark_avg_quality: int = 20  # mark positions whose mean quality across reads is below this

    # --- RECall Table 2: rejection criteria -----------------------------------
    check_stop_codons: bool = True
    check_frameshifts: bool = True
    max_mixture_fraction: float = 0.035  # fail if > 3.5% of bases are mixtures
    max_n: int = 4  # fail if >= 5 Ns
    max_marks: int = 99  # fail if >= 100 marked positions
    single_coverage_quality: int = 40  # single-read bases below this count towards the run
    max_single_coverage_run: int = 3  # fail if > 3 such consecutive bases

    # --- Reference alignment --------------------------------------------------
    frame: int = 1  # 1-based reference position of the first base of codon 1
    min_read_identity: float = 0.80  # a read that matches the reference worse than this is rejected
    min_read_aligned: int = 50  # ... or aligns over fewer bases than this
    require_full_reference: bool = True  # every reference position needs both reads (one if directions optional)
    check_read_orientation: bool = True  # forward must align to +, reverse to -


@dataclass
class ReadQC:
    """One trimmed read, ready for assembly."""

    name: str
    length: int
    mean_quality: float
    good_fraction: float  # fraction of bases with Phred >= min_quality (whole read)
    trim_start: int  # 0-based, inclusive
    trim_end: int  # 0-based, exclusive
    sequence: str  # trimmed called bases
    qualities: np.ndarray  # trimmed qualities
    uncalled: str  # largest secondary peak's base at each trimmed position ("" if none)
    area_ratios: np.ndarray  # uncalled/called peak area at each trimmed position
    mixtures: int  # positions where area ratio >= mixture_area (informational)
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

    Each base scores ``error_limit - P(error)``; the kept region is the
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


def peak_areas(chrom: Chromatogram) -> np.ndarray | None:
    """Area of each channel under each base call, shape (n_bases, 4) in ACGT order.

    Each base owns the trace between the midpoints to its neighbouring peaks,
    as phred does when it measures called and uncalled peak areas.
    """
    if not chrom.has_traces or len(chrom.peak_locations) != len(chrom) or len(chrom) == 0:
        return None
    n_points = min(len(chrom.traces[b]) for b in BASES)
    locs = np.clip(chrom.peak_locations.astype(float), 0, n_points - 1)
    if len(locs) > 1:
        mids = (locs[:-1] + locs[1:]) / 2
        first = locs[0] - (mids[0] - locs[0])
        last = locs[-1] + (locs[-1] - mids[-1])
        bounds = np.concatenate([[first], mids, [last]])
    else:
        bounds = np.array([locs[0] - 6, locs[0] + 6])
    bounds = np.clip(np.round(bounds), 0, n_points).astype(int)
    areas = np.zeros((len(chrom), 4))
    for j, base in enumerate(BASES):
        signal = np.clip(chrom.traces[base][:n_points].astype(np.float64), 0, None)
        cs = np.concatenate([[0.0], np.cumsum(signal)])
        areas[:, j] = cs[bounds[1:]] - cs[bounds[:-1]]
    return areas


def uncalled_peaks(chrom: Chromatogram) -> tuple[str, np.ndarray]:
    """Largest secondary ("uncalled") peak base and its area relative to the called peak."""
    n = len(chrom)
    areas = peak_areas(chrom)
    if areas is None:
        return "?" * n, np.zeros(n)
    uncalled, ratios = [], np.zeros(n)
    for i, called in enumerate(chrom.sequence):
        if called not in BASES:
            uncalled.append("?")
            continue
        row = areas[i]
        primary = row[BASES.index(called)]
        others = [(row[j], b) for j, b in enumerate(BASES) if b != called]
        area, base = max(others)
        uncalled.append(base)
        ratios[i] = area / primary if primary > 0 else 0.0
    return "".join(uncalled), ratios


def qc_read(chrom: Chromatogram, settings: QCSettings | None = None) -> ReadQC:
    """Trim a read and apply the lab's read-level cutoff."""
    s = settings or QCSettings()
    q = chrom.qualities
    n = len(chrom)
    good_fraction = float((q >= s.min_quality).mean()) if n else 0.0
    mean_q = float(q.mean()) if n else 0.0
    start, end = mott_trim(q, s.trim_error_limit)
    uncalled, ratios = uncalled_peaks(chrom)

    reasons = []
    if not chrom.has_qualities:
        reasons.append("file has no quality values")
    elif good_fraction < s.min_good_fraction:
        reasons.append(
            f"only {good_fraction:.1%} of bases are >= Q{s.min_quality} "
            f"(cutoff {s.min_good_fraction:.0%})"
        )
    if chrom.has_qualities and end - start < s.min_trimmed_length:
        reasons.append(f"trimmed length {end - start} < {s.min_trimmed_length}")

    r = ratios[start:end]
    return ReadQC(
        name=chrom.name,
        length=n,
        mean_quality=mean_q,
        good_fraction=good_fraction,
        trim_start=start,
        trim_end=end,
        sequence=chrom.sequence[start:end],
        qualities=q[start:end].copy(),
        uncalled=uncalled[start:end],
        area_ratios=r.copy(),
        mixtures=int((r >= s.mixture_area).sum()),
        passed=not reasons,
        reasons=reasons,
    )
