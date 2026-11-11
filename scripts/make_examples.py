"""Regenerate the example .ab1 files in examples/.

    python scripts/make_examples.py

Every example is derived from examples/reference.fasta so the expected
result of each one is known.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sanger_seq_analyzer.simulate import make_read, revcomp  # noqa: E402

EX = ROOT / "examples"
F_START, F_END = 40, 760  # region read by the forward primer
R_START, R_END = 140, 860  # region read (on the other strand) by the reverse primer


def mutate(seq: str, changes: dict[int, str]) -> str:
    s = list(seq)
    for pos, base in changes.items():
        s[pos] = base
    return "".join(s)


def main() -> None:
    rng = np.random.default_rng(2026)
    reference = "".join(rng.choice(list("ACGT"), 900))
    (EX / "good").mkdir(parents=True, exist_ok=True)
    (EX / "bad").mkdir(parents=True, exist_ok=True)
    (EX / "reference.fasta").write_text(
        ">example_amplicon synthetic 900 bp reference\n"
        + "\n".join(reference[i:i + 70] for i in range(0, 900, 70)) + "\n"
    )

    def pair(folder, sample, template, f_kw, r_kw, r_template=None):
        r_template = r_template or template
        make_read(EX / folder / f"{sample}_F.ab1", f"{sample}_F", template[F_START:F_END], rng, **f_kw)
        make_read(EX / folder / f"{sample}_R.ab1", f"{sample}_R", revcomp(r_template[R_START:R_END]), rng, **r_kw)

    good = dict(kind="good")
    # Good: clean reads, one SNP and one heterozygous A/G mixture (R) in the overlap.
    pair("good", "GOOD01", mutate(reference, {200: "G" if reference[200] != "G" else "A", 450: "R"}), good, good)
    # Good: a C/T mixture (Y), plus a variant only the forward read covers.
    pair("good", "GOOD02", mutate(reference, {600: "Y", 90: "T" if reference[90] != "T" else "C"}), good, good)
    # Good, close to the line: 79% and 81% of bases >= Q20 (cutoff is 75%).
    pair("good", "BORDER01", reference, dict(kind="good", good_fraction=0.79),
         dict(kind="good", good_fraction=0.81))

    # Bad: weak, noisy signal in both directions (failed reaction).
    poor = dict(kind="poor", signal=150.0)
    pair("bad", "BAD01", reference, poor, poor)
    # Bad: reverse read just under the cutoff (70% >= Q20) -> forward only, SINGLE.
    pair("bad", "BAD02", reference, good, dict(kind="good", good_fraction=0.70))
    # Bad: both reads are clean but come from different templates (sample mix-up).
    other = "".join(rng.choice(list("ACGT"), 900))
    pair("bad", "BAD03", reference, good, good, r_template=other)

    print(f"Wrote examples to {EX}")


if __name__ == "__main__":
    main()
