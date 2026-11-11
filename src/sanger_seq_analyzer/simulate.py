"""Synthetic Sanger reads and a minimal ABIF (.ab1) writer.

Used to build the example data set and the test suite. The simulated
chromatograms are self-consistent: each base call's Phred quality matches
the size of the competing peaks in the trace.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from .abi import BASES

SPACING = 12  # trace points between peaks
SIGMA = 2.4  # peak width
CHANNEL_ORDER = "GATC"  # FWO_1 order used by ABI instruments

_MIX = {"R": "AG", "Y": "CT", "S": "GC", "W": "AT", "K": "GT", "M": "AC"}
_COMP = str.maketrans("ACGTRYSWKMN", "TGCAYRSWMKN")


def revcomp(seq: str) -> str:
    return seq.translate(_COMP)[::-1]


def quality_profile(n: int, rng: np.random.Generator, kind: str = "good", good_fraction: float | None = None):
    """Per-base Phred values shaped like a real run.

    ``kind`` is "good" (high plateau, ramp at both ends) or "poor" (weak,
    noisy signal). ``good_fraction`` forces roughly that share of bases >= Q20.
    """
    x = np.arange(n)
    if kind == "good":
        ramp_in = np.clip(x / 25, 0, 1)
        ramp_out = np.clip((n - x) / 90, 0, 1)
        q = 8 + 46 * ramp_in * ramp_out + rng.normal(0, 4, n)
    else:
        q = 13 + 6 * np.sin(x / 37) + rng.normal(0, 6, n)
    q = np.clip(np.round(q), 2, 60).astype(int)
    if good_fraction is not None:
        target = int(round(good_fraction * n))
        order = np.argsort(-q, kind="stable")
        q[order[:target]] = np.maximum(q[order[:target]], 20 + rng.integers(0, 15, target))
        q[order[target:]] = np.minimum(q[order[target:]], rng.integers(4, 20, n - target))
    return q


def simulate_read(template: str, quals: np.ndarray, rng: np.random.Generator, signal: float = 1000.0):
    """Turn a template (may contain IUPAC mixture codes) into calls, qualities, peaks and traces."""
    n = len(template)
    n_points = SPACING * (n + 2)
    t = np.arange(n_points)
    traces = {b: np.zeros(n_points) for b in BASES}
    calls, out_q, peaks = [], [], []
    decay = np.linspace(1.0, 0.45, n)

    for i, base in enumerate(template):
        loc = SPACING * (i + 1)
        amp = signal * decay[i] * rng.uniform(0.7, 1.3)
        q = int(quals[i])
        if base in _MIX:
            a, b = _MIX[base]
            heights = {a: amp * rng.uniform(0.5, 0.6), b: amp * rng.uniform(0.4, 0.5)}
            q = min(q, int(rng.integers(12, 20)))
        else:
            ratio = 10 ** (-q / 20)  # Q40 -> 1% secondary peak, Q10 -> 32%
            other = rng.choice([c for c in BASES if c != base])
            heights = {base: amp, other: amp * ratio}
            if q < 10 and rng.random() < 0.35:  # basecaller miscall in noisy region
                heights[base], heights[other] = heights[other], heights[base]
        for b, h in heights.items():
            traces[b] += h * np.exp(-0.5 * ((t - loc) / SIGMA) ** 2)
        calls.append(max(heights, key=heights.get))
        out_q.append(q)
        peaks.append(loc)

    noise = signal * 0.01
    for b in BASES:
        traces[b] = np.clip(traces[b] + rng.normal(0, noise, n_points), 0, 32000)
    return "".join(calls), np.asarray(out_q), np.asarray(peaks), traces


def write_ab1(path: str | Path, sample: str, calls: str, quals, peaks, traces: dict[str, np.ndarray]) -> None:
    """Write a minimal but valid ABIF file readable by Biopython and trace viewers."""
    entries = []  # (name, number, elemtype, elemsize, n_elements, data bytes)

    def short_array(arr):
        return struct.pack(f">{len(arr)}h", *np.clip(np.asarray(arr), -32768, 32767).astype(int))

    for i, base in enumerate(CHANNEL_ORDER):
        data = short_array(np.round(traces[base]))
        entries.append(("DATA", 9 + i, 4, 2, len(traces[base]), data))
    entries.append(("FWO_", 1, 2, 1, 4, CHANNEL_ORDER.encode()))
    entries.append(("PBAS", 2, 2, 1, len(calls), calls.encode()))
    entries.append(("PCON", 2, 2, 1, len(quals), bytes(int(q) for q in quals)))
    entries.append(("PLOC", 2, 4, 2, len(peaks), short_array(peaks)))
    name = sample.encode()[:255]
    entries.append(("SMPL", 1, 18, 1, len(name) + 1, bytes([len(name)]) + name))

    header_size = 128
    data_blob = bytearray()
    directory = bytearray()
    data_start = header_size
    offsets = []
    for _, _, _, _, _, data in entries:
        if len(data) > 4:
            offsets.append(data_start + len(data_blob))
            data_blob += data
        else:
            offsets.append(None)
    dir_offset = data_start + len(data_blob)
    for (tag, num, etype, esize, nelem, data), off in zip(entries, offsets):
        if off is None:
            off_field = struct.unpack(">I", data.ljust(4, b"\0"))[0]
        else:
            off_field = off
        directory += struct.pack(">4sIhhiiii", tag.encode(), num, etype, esize, nelem, len(data), off_field, 0)

    header = struct.pack(">4sh4sIhhiiii", b"ABIF", 101, b"tdir", 1, 1023, 28,
                         len(entries), len(directory), dir_offset, 0)
    header = header.ljust(header_size, b"\0")
    Path(path).write_bytes(header + bytes(data_blob) + bytes(directory))


def make_read(path, sample, template, rng, kind="good", good_fraction=None, signal=1000.0):
    quals = quality_profile(len(template), rng, kind, good_fraction)
    calls, q, peaks, traces = simulate_read(template, quals, rng, signal)
    write_ab1(path, sample, calls, q, peaks, traces)
    return calls, q
