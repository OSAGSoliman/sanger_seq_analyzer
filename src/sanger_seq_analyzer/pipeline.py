"""Batch analysis: find .ab1 files, pair forward/reverse reads, QC, consensus."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from Bio import SeqIO

from .abi import Chromatogram, read_ab1
from .consensus import ConsensusResult, ReferenceHit, build_consensus, orient_to_reference
from .qc import QCSettings, ReadQC, qc_read

FORWARD_TAGS = ("F", "FWD", "FOR", "FORWARD")
REVERSE_TAGS = ("R", "REV", "REVERSE")
AB1_SUFFIXES = (".ab1", ".abi", ".ab!")


@dataclass
class SampleResult:
    sample: str
    forward_chrom: Chromatogram | None
    reverse_chrom: Chromatogram | None
    consensus: ConsensusResult
    reference_hit: ReferenceHit | None = None


@dataclass
class RunResult:
    samples: list[SampleResult]
    settings: QCSettings
    unpaired: list[Path] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def split_name(stem: str, forward_tags=FORWARD_TAGS, reverse_tags=REVERSE_TAGS) -> tuple[str, str | None]:
    """Split a file stem into (sample, 'F'|'R'|None).

    Matches the last separator-delimited direction tag, so ``HIV01_RT_F_A03``
    gives ``("HIV01_RT", "F")``.
    """
    tags = "|".join(sorted({*forward_tags, *reverse_tags}, key=len, reverse=True))
    m = re.match(rf"^(?P<sample>.+)[_\-. ](?P<dir>{tags})(?:[_\-. ].*)?$", stem, re.IGNORECASE)
    if not m:
        return stem, None
    direction = "F" if m["dir"].upper() in {t.upper() for t in forward_tags} else "R"
    return m["sample"], direction


def find_ab1_files(inputs: list[str | Path]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in AB1_SUFFIXES))
        elif p.is_file():
            files.append(p)
    return files


def pair_files(files: list[Path], forward_tags=FORWARD_TAGS, reverse_tags=REVERSE_TAGS):
    """Group files into {sample: {"F": path, "R": path}} plus a list of unmatched files."""
    pairs: dict[str, dict[str, Path]] = {}
    unpaired: list[Path] = []
    for f in files:
        sample, direction = split_name(f.stem, forward_tags, reverse_tags)
        if direction is None:
            unpaired.append(f)
            continue
        slot = pairs.setdefault(sample, {})
        if direction in slot:
            unpaired.append(f)  # duplicate direction for the same sample
        else:
            slot[direction] = f
    return dict(sorted(pairs.items())), unpaired


def load_reference(path: str | Path | None) -> str | None:
    if not path:
        return None
    return str(next(SeqIO.parse(str(path), "fasta")).seq)


def run(
    inputs: list[str | Path],
    settings: QCSettings | None = None,
    reference: str | None = None,
    forward_tags=FORWARD_TAGS,
    reverse_tags=REVERSE_TAGS,
    progress=None,
) -> RunResult:
    """Analyse every forward/reverse pair found in ``inputs``."""
    settings = settings or QCSettings()
    pairs, unpaired = pair_files(find_ab1_files(inputs), forward_tags, reverse_tags)
    result = RunResult(samples=[], settings=settings, unpaired=unpaired)

    for n, (sample, slot) in enumerate(pairs.items(), 1):
        if progress:
            progress(n, len(pairs), sample)
        chroms: dict[str, Chromatogram | None] = {"F": None, "R": None}
        qcs: dict[str, ReadQC | None] = {"F": None, "R": None}
        for direction, path in slot.items():
            try:
                chroms[direction] = read_ab1(path)
                qcs[direction] = qc_read(chroms[direction], settings)
            except Exception as exc:  # unreadable/corrupt file: report and continue
                result.errors[str(path)] = f"{type(exc).__name__}: {exc}"
        cons = build_consensus(sample, qcs["F"], qcs["R"], settings)
        hit = orient_to_reference(cons.sequence, reference) if reference and cons.sequence else None
        if hit is not None:
            cons.sequence = hit.sequence
        result.samples.append(SampleResult(sample, chroms["F"], chroms["R"], cons, hit))
    return result
