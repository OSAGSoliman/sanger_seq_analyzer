"""Scenario builders shared by the regression tests.

Reads are simulated around the built-in ``measles-n450`` reference: a 171 bp
5' flank, the 450 bp target and a 379 bp 3' flank (1,000 bp amplicon). The
forward read covers amplicon 0-800 and the reverse read amplicon 100-960 by
default, so both span the whole reference, like the bundled examples.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from sanger_seq_analyzer.qc import ReadQC
from sanger_seq_analyzer.references import get as get_reference
from sanger_seq_analyzer.simulate import make_read, quality_profile, revcomp, simulate_read, write_ab1

REF = get_reference("measles-n450").sequence
FLANK5 = 171
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

Mutation = Callable[[str, np.ndarray, np.ndarray, dict], tuple]


def random_seq(rng: np.random.Generator, n: int) -> str:
    return "".join(rng.choice(list("ACGT"), n))


def flanks(seed: int = 11) -> tuple[str, str]:
    rng = np.random.default_rng(seed)
    return random_seq(rng, FLANK5), random_seq(rng, 379)


def amplicon(target: str = REF, seed: int = 11) -> str:
    f5, f3 = flanks(seed)
    return f5 + target + f3


def custom_read(path: Path, template: str, rng: np.random.Generator, mutate: Mutation | None = None) -> None:
    """Simulate a read from ``template`` and let ``mutate(calls, quals, peaks, traces)`` edit it before writing."""
    quals = quality_profile(len(template), rng)
    data = simulate_read(template, quals, rng)
    if mutate is not None:
        data = mutate(*data)
    write_ab1(path, path.stem, *data)


def amplicon_pair(folder: Path, target: str = REF, *, name: str = "X", f_slice=(0, 800), r_slice=(100, 960),
                  mutate_f: Mutation | None = None, mutate_r: Mutation | None = None, amp: str | None = None,
                  seed: int = 3) -> Path:
    """Write ``{name}_F.ab1`` and ``{name}_R.ab1`` for ``target`` inside its flanks; return the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    amp = amp if amp is not None else amplicon(target)
    rng = np.random.default_rng(seed)
    custom_read(folder / f"{name}_F.ab1", amp[f_slice[0]:f_slice[1]], rng, mutate_f)
    custom_read(folder / f"{name}_R.ab1", revcomp(amp[r_slice[0]:r_slice[1]]), rng, mutate_r)
    return folder


def simple_read(path: Path, template: str, seed: int = 1, **kw) -> None:
    make_read(path, path.stem, template, np.random.default_rng(seed), **kw)


def readqc(seq: str, quals=None, name: str = "r") -> ReadQC:
    """A passing, untrimmed :class:`ReadQC` built directly from a sequence."""
    n = len(seq)
    q = np.full(n, 40) if quals is None else np.asarray(quals)
    return ReadQC(name, n, float(q.mean()) if n else 0.0, 1.0, 0, n, seq, q, "?" * n, np.zeros(n), 0, True)


def alt_base(b: str) -> str:
    return {"A": "C", "C": "A", "G": "C", "T": "C"}[b]
