"""Reading Sanger chromatogram files (ABI / AB1).

Wraps Biopython's ABIF parser and exposes the pieces the rest of the
package needs: base calls, Phred qualities, peak locations and the four
raw trace channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from Bio import SeqIO

BASES = "ACGT"


@dataclass
class Chromatogram:
    """A single Sanger read loaded from an .ab1 file."""

    name: str
    path: Path
    sequence: str
    qualities: np.ndarray  # Phred quality per base call (int)
    peak_locations: np.ndarray  # trace index of each base call (int)
    traces: dict[str, np.ndarray] = field(default_factory=dict)  # base -> signal (int32, lossless)
    has_qualities: bool = True  # False when the file stores no Phred values (all reported as 0)

    def __len__(self) -> int:
        return len(self.sequence)

    @property
    def has_traces(self) -> bool:
        return all(b in self.traces and len(self.traces[b]) for b in BASES)


def read_ab1(path: str | Path) -> Chromatogram:
    """Load an .ab1 file into a :class:`Chromatogram`."""
    path = Path(path)
    record = SeqIO.read(str(path), "abi")
    raw = record.annotations["abif_raw"]

    sequence = str(record.seq).upper()
    quals = record.letter_annotations.get("phred_quality")
    has_qualities = quals is not None and len(quals) == len(sequence) and len(sequence) > 0
    if not has_qualities:
        quals = [0] * len(sequence)
    qualities = np.asarray(quals, dtype=int)

    ploc = raw.get("PLOC2") or raw.get("PLOC1") or ()
    peak_locations = np.asarray(ploc, dtype=int)

    traces: dict[str, np.ndarray] = {}
    order = raw.get("FWO_1", b"GATC")
    if isinstance(order, bytes):
        order = order.decode("ascii", "replace")
    # Processed (analysed) data lives in DATA9..DATA12, in FWO_1 order.
    for i, base in enumerate(order.upper()[:4]):
        data = raw.get(f"DATA{9 + i}")
        if data is not None:
            traces[base] = np.asarray(data, dtype=np.int32)

    if len(peak_locations) != len(sequence):
        peak_locations = np.asarray([], dtype=int)

    return Chromatogram(
        name=_sample_name(record, path),
        path=path,
        sequence=sequence,
        qualities=qualities,
        peak_locations=peak_locations,
        traces=traces,
        has_qualities=has_qualities,
    )


def _sample_name(record, path: Path) -> str:
    return path.stem or record.id or record.name
