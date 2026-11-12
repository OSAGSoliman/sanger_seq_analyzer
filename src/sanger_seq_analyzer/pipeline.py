"""Batch analysis: find .ab1 files, pair forward/reverse reads, QC, consensus.

The work is split in three steps so a window can show progress, cancel, and
re-run with new settings without reading the files again:

* :func:`scan` pairs files into samples and says why any file is left out;
* :func:`load_sample` reads a sample's ``.ab1`` files (the only disk access,
  cached by :class:`ChromCache`);
* :func:`analyse_sample` applies read QC, assembly and the rules. It never
  raises: an unexpected error becomes a failing sample with the reason.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .abi import Chromatogram, read_ab1
from .qc import QCSettings, ReadQC, qc_read
from .recall import DIRECTIONS, Reason, SampleCall, call_sample
from .references import ReferenceInfo, from_sequence, resolve_reference
from .references import get as get_builtin

# 214 / 216 are the WHO measles N-450 primers MeV214 (forward) and MeV216 (reverse), so lab files named
# "<accession>_214.ab1" and "<accession>_216.ab1" pair without changing any setting.
FORWARD_TAGS = ("F", "FWD", "FOR", "FORWARD", "214", "MeV214")
REVERSE_TAGS = ("R", "REV", "REVERSE", "216", "MeV216")
# Defaults of earlier versions: remembered tag lists equal to one of these are upgraded to the current defaults.
LEGACY_TAGS = ((("F", "FWD", "FOR", "FORWARD"), ("R", "REV", "REVERSE")),)
AB1_SUFFIXES = (".ab1", ".abi", ".ab!")
SYSTEM_FILES = {"thumbs.db", "desktop.ini"}
SEPARATORS = "_-. "

SKIP_REASONS = {
    "no_tag": "no forward/reverse tag in the file name",
    "duplicate": "duplicate direction",
    "not_ab1": "not an .ab1 file",
    "missing": "path does not exist",
    "hidden": "hidden or system file",
}


@dataclass
class SkippedFile:
    path: Path
    reason: str  # a key of SKIP_REASONS
    detail: str = ""
    sample: str | None = None

    @property
    def text(self) -> str:
        return self.detail or SKIP_REASONS[self.reason]


@dataclass
class ScanResult:
    pairs: dict[str, dict[str, Path]]  # sample -> {"F": path, "R": path}
    skipped: list[SkippedFile] = field(default_factory=list)
    n_files: int = 0  # trace files considered (hidden files excluded)
    bad_header: list[Path] = field(default_factory=list)  # files that do not start with ABIF

    @property
    def complete(self) -> int:
        return sum(1 for slot in self.pairs.values() if len(slot) == 2)


@dataclass
class LoadedSample:
    name: str
    paths: dict[str, Path]
    chroms: dict[str, Chromatogram | None]
    errors: dict[str, str]  # direction -> "Type: message" for unreadable files


@dataclass
class SampleResult:
    sample: str
    forward_chrom: Chromatogram | None
    reverse_chrom: Chromatogram | None
    consensus: SampleCall
    forward_path: Path | None = None
    reverse_path: Path | None = None
    error: str | None = None  # traceback of an unexpected analysis error
    read_errors: dict[str, str] = field(default_factory=dict)  # direction -> why the file could not be read
    review: str = ""  # reviewer's confirmed status ("PASS"/"FAIL"), set on exported copies (see review.py)
    auto_status: str = ""  # status the rules gave, when ``review`` is set

    def chrom(self, direction: str) -> Chromatogram | None:
        return self.forward_chrom if direction == "F" else self.reverse_chrom

    def path(self, direction: str) -> Path | None:
        return self.forward_path if direction == "F" else self.reverse_path


@dataclass
class RunResult:
    samples: list[SampleResult]
    settings: QCSettings
    reference: str = ""  # normalised reference sequence
    skipped: list[SkippedFile] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # file path (or sample) -> message
    reference_info: ReferenceInfo | None = None
    inputs: list[Path] = field(default_factory=list)
    forward_tags: tuple[str, ...] = FORWARD_TAGS
    reverse_tags: tuple[str, ...] = REVERSE_TAGS
    started: datetime | None = None
    finished: datetime | None = None
    version: str = __version__
    cancelled: bool = False
    planned: int = 0  # samples found by the scan

    @property
    def unpaired(self) -> list[Path]:
        """Files with a tag problem (no tag, duplicate direction, not .ab1)."""
        return [s.path for s in self.skipped if s.reason in ("no_tag", "duplicate", "not_ab1")]

    def count(self, status: str) -> int:
        return sum(1 for s in self.samples if s.consensus.status == status)


def _tag_pattern(forward_tags, reverse_tags) -> re.Pattern | None:
    tags = sorted({t for t in (*forward_tags, *reverse_tags) if t}, key=len, reverse=True)
    if not tags:
        return None
    alternatives = "|".join(re.escape(t) for t in tags)
    sep = re.escape(SEPARATORS)
    return re.compile(rf"^(?P<sample>.+)[{sep}](?P<dir>{alternatives})(?:[{sep}].*)?$", re.IGNORECASE)


def split_name(stem: str, forward_tags=FORWARD_TAGS, reverse_tags=REVERSE_TAGS) -> tuple[str, str | None]:
    """Split a file stem into (sample, 'F'|'R'|None).

    Matches the last separator-delimited direction tag, so ``HIV01_RT_F_A03``
    gives ``("HIV01_RT", "F")``. Tags are literal text, compared without case.
    """
    pattern = _tag_pattern(forward_tags, reverse_tags)
    m = pattern.match(stem) if pattern else None
    if not m:
        return stem, None
    direction = "F" if m["dir"].upper() in {t.upper() for t in forward_tags} else "R"
    return m["sample"], direction


def validate_tags(forward_tags, reverse_tags) -> list[str]:
    """Problems with the tag lists, worded for a person (empty list = fine)."""
    problems = []
    for label, tags in (("forward", forward_tags), ("reverse", reverse_tags)):
        if not [t for t in tags if t.strip()]:
            problems.append(f"give at least one {label} tag")
        bad = [t for t in tags if any(c in SEPARATORS or c.isspace() for c in t)]
        if bad:
            problems.append(f"{label} tag {bad[0]!r} contains a separator (_ - . or space)")
    both = {t.upper() for t in forward_tags} & {t.upper() for t in reverse_tags}
    if both:
        problems.append(f"tag {sorted(both)[0]!r} is in both lists")
    return problems


def is_hidden(path: Path) -> bool:
    return path.name.startswith(".") or path.name.lower() in SYSTEM_FILES


def _has_abif_header(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"ABIF"
    except OSError:
        return False


def _collect(inputs: list[str | Path]) -> tuple[list[Path], list[SkippedFile]]:
    """Trace files in ``inputs`` (folders searched recursively, each file once) and what was left out."""
    skipped: list[SkippedFile] = []
    seen: set[Path] = set()
    files: list[Path] = []

    def consider(p: Path, direct: bool) -> None:
        key = p.resolve()
        if key in seen:
            return
        seen.add(key)
        if is_hidden(p):
            skipped.append(SkippedFile(p, "hidden"))
        elif p.suffix.lower() not in AB1_SUFFIXES:
            if direct:
                skipped.append(SkippedFile(p, "not_ab1"))
        else:
            files.append(p)

    for item in inputs:
        p = Path(item)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                inside_hidden = any(part.startswith(".") for part in f.relative_to(p).parts[:-1])
                if f.is_file() and f.suffix.lower() in AB1_SUFFIXES and not inside_hidden:
                    consider(f, direct=False)
        elif p.is_file():
            consider(p, direct=True)
        else:
            skipped.append(SkippedFile(p, "missing"))
    return sorted(files), skipped


def scan(inputs: list[str | Path], forward_tags=FORWARD_TAGS, reverse_tags=REVERSE_TAGS,
         check_headers: bool = False) -> ScanResult:
    """Pair the trace files found in ``inputs`` (folders are searched recursively).

    Every file that is not used gets a reason. A file reached twice (a folder and a
    file inside it) counts once. When a sample has two files for one direction the
    first in path order is used.
    """
    files, skipped = _collect(inputs)
    pairs: dict[str, dict[str, Path]] = {}
    for f in files:
        sample, direction = split_name(f.stem, forward_tags, reverse_tags)
        if direction is None:
            skipped.append(SkippedFile(f, "no_tag"))
            continue
        slot = pairs.setdefault(sample, {})
        if direction in slot:
            label = dict(DIRECTIONS)[direction]
            skipped.append(SkippedFile(f, "duplicate", f"duplicate {label} for {sample} (used: {slot[direction].name})",
                                       sample))
        else:
            slot[direction] = f
    bad_header = [f for f in files if check_headers and not _has_abif_header(f)]
    return ScanResult(dict(sorted(pairs.items())), skipped, len(files), bad_header)


def find_ab1_files(inputs: list[str | Path]) -> list[Path]:
    """Trace files found in ``inputs`` (hidden files skipped, each file once)."""
    return _collect(inputs)[0]


def pair_files(files: list[Path], forward_tags=FORWARD_TAGS, reverse_tags=REVERSE_TAGS):
    """Group files into {sample: {"F": path, "R": path}} plus a list of unmatched files."""
    sr = scan(files, forward_tags, reverse_tags)
    return sr.pairs, [s.path for s in sr.skipped if s.reason in ("no_tag", "duplicate", "not_ab1")]


class ChromCache:
    """Chromatograms already read, keyed by (resolved path, mtime, size), so re-runs skip the disk."""

    def __init__(self) -> None:
        self._items: dict[tuple, Chromatogram] = {}

    def get(self, path: Path) -> Chromatogram:
        st = path.stat()
        key = (path.resolve(), st.st_mtime_ns, st.st_size)
        chrom = self._items.get(key)
        if chrom is None:
            chrom = read_ab1(path)
            self._items[key] = chrom
        return chrom

    def __len__(self) -> int:
        return len(self._items)


def load_sample(sample: str, slot: dict[str, Path], cache: ChromCache | None = None) -> LoadedSample:
    """Read a sample's files; an unreadable file is recorded, not raised."""
    cache = cache or ChromCache()
    chroms: dict[str, Chromatogram | None] = {"F": None, "R": None}
    errors: dict[str, str] = {}
    for direction, path in slot.items():
        try:
            chroms[direction] = cache.get(path)
        except Exception as exc:  # unreadable/corrupt file: report and continue
            errors[direction] = f"{type(exc).__name__}: {exc}"
    return LoadedSample(sample, dict(slot), chroms, errors)


def analyse_sample(loaded: LoadedSample, reference: str, settings: QCSettings) -> SampleResult:
    """QC, assembly and rules for one loaded sample. Never raises."""
    chroms = {d: c for d, c in loaded.chroms.items() if d not in loaded.errors}
    result = SampleResult(loaded.name, chroms.get("F"), chroms.get("R"), SampleCall(loaded.name, "FAIL", "", {}),
                          loaded.paths.get("F"), loaded.paths.get("R"), read_errors=dict(loaded.errors))
    try:
        qcs: dict[str, ReadQC | None] = {d: (qc_read(c, settings) if c is not None else None)
                                         for d, c in (("F", chroms.get("F")), ("R", chroms.get("R")))}
        unreadable = {d: loaded.paths[d].name for d in loaded.errors}
        result.consensus = call_sample(loaded.name, qcs, reference, settings, unreadable=unreadable)
    except Exception as exc:
        message = f"analysis error: {type(exc).__name__}: {exc}"
        result.consensus.reason_items = [Reason("internal_error", message)]
        result.consensus.error = message
        result.error = traceback.format_exc()
    return result


def load_reference(spec: str | Path) -> str:
    """A built-in reference by name (e.g. ``measles-n450``) or the only record of a FASTA file."""
    return resolve_reference(spec).sequence


def _is_file(text: str) -> bool:
    try:
        return Path(text).is_file()
    except (OSError, ValueError):  # e.g. a long sequence is not a valid file name
        return False


def _reference_info(reference: str | ReferenceInfo) -> ReferenceInfo:
    """A sequence, built-in name or FASTA path as a validated :class:`ReferenceInfo`."""
    if isinstance(reference, ReferenceInfo):
        return reference
    text = str(reference).strip()
    if get_builtin(text) is not None or _is_file(text):
        return resolve_reference(text)
    return from_sequence(text)


ProgressFn = Callable[[int, int, str], None]
SampleFn = Callable[[int, int, SampleResult], None]


def run(
    inputs: list[str | Path],
    reference: str | ReferenceInfo,
    settings: QCSettings | None = None,
    forward_tags=FORWARD_TAGS,
    reverse_tags=REVERSE_TAGS,
    progress: ProgressFn | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_sample: SampleFn | None = None,
    cache: ChromCache | None = None,
    scan_result: ScanResult | None = None,
) -> RunResult:
    """Analyse every forward/reverse pair found in ``inputs``.

    ``reference`` is a sequence, a built-in name, a FASTA path or a :class:`ReferenceInfo`.
    ``progress(n, total, name)`` is called before each sample and ``on_sample(n, total,
    result)`` after it; ``should_cancel()`` is checked between samples.
    """
    settings = settings or QCSettings()
    info = _reference_info(reference)
    sr = scan_result or scan(inputs, forward_tags, reverse_tags)
    cache = cache or ChromCache()
    result = RunResult(samples=[], settings=settings, reference=info.sequence, skipped=list(sr.skipped),
                       reference_info=info, inputs=[Path(p) for p in inputs], forward_tags=tuple(forward_tags),
                       reverse_tags=tuple(reverse_tags), started=datetime.now(), planned=len(sr.pairs))
    total = len(sr.pairs)
    for n, (sample, slot) in enumerate(sr.pairs.items(), 1):
        if should_cancel and should_cancel():
            result.cancelled = True
            break
        if progress:
            progress(n, total, sample)
        loaded = load_sample(sample, slot, cache)
        for direction, message in loaded.errors.items():
            result.errors[str(slot[direction])] = message
        sample_result = analyse_sample(loaded, info.sequence, settings)
        if sample_result.consensus.error:
            result.errors[f"sample {sample}"] = sample_result.consensus.error
        result.samples.append(sample_result)
        if on_sample:
            on_sample(n, total, sample_result)
    result.finished = datetime.now()
    return result
