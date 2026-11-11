"""Regenerate the example .ab1 files in examples/.

    python scripts/make_examples.py

The target is the real measles virus N-450 region (built-in reference
``measles-n450``, NC_001498.1:1233-1682). It sits inside a simulated 1000 bp
amplicon (random flanking sequence) that is "sequenced" with a forward and a
reverse primer. Run the examples with ``-r measles-n450`` (the default in the
desktop window). Each sample is built to show one outcome, so the expected
result is known.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sanger_seq_analyzer.references import get as get_reference  # noqa: E402
from sanger_seq_analyzer.simulate import make_read, revcomp  # noqa: E402

EX = ROOT / "examples"
TARGET_START = 171  # target = amplicon[171:621]
F_START, F_END = 0, 800  # region read by the forward primer
R_START, R_END = 100, 960  # region read (other strand) by the reverse primer
STOPS = {"TAA", "TAG", "TGA"}


def edit(seq, subs=None, deletions=()):
    """Apply substitutions {target_pos: base} then deletions [(target_pos, length)].

    Positions are 0-based within the target region.
    """
    s = list(seq)
    for pos, base in (subs or {}).items():
        s[TARGET_START + pos] = base
    for pos, length in sorted(deletions, reverse=True):
        del s[TARGET_START + pos:TARGET_START + pos + length]
    return "".join(s)


def main() -> None:
    rng = np.random.default_rng(2026)
    target = get_reference("measles-n450").sequence
    flank5 = "".join(rng.choice(list("ACGT"), TARGET_START))
    flank3 = "".join(rng.choice(list("ACGT"), 1000 - TARGET_START - len(target)))
    amplicon = flank5 + target + flank3

    for folder in ("good", "bad"):
        d = EX / folder
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.ab1"):
            f.unlink()
    stale = EX / "reference.fasta"  # older examples used a synthetic reference
    if stale.exists():
        stale.unlink()

    def pair(folder, sample, template, f_kw=None, r_kw=None, r_template=None, skip=""):
        r_template = r_template or template
        if skip != "F":
            make_read(EX / folder / f"{sample}_F.ab1", f"{sample}_F", template[F_START:F_END], rng, **(f_kw or {}))
        if skip != "R":
            make_read(EX / folder / f"{sample}_R.ab1", f"{sample}_R",
                      revcomp(r_template[R_START:min(R_END, len(r_template))]), rng, **(r_kw or {}))

    def mixture(pos):
        """IUPAC code for the reference base at target pos mixed with a transition partner."""
        partner = {"A": "G", "G": "A", "C": "T", "T": "C"}[target[pos]]
        return {"AG": "R", "CT": "Y"}["".join(sorted(target[pos] + partner))]

    def snp(pos):
        """A substitution at target pos that keeps its codon a non-stop codon."""
        c0 = pos - pos % 3
        for b in "ACGT":
            codon = list(target[c0:c0 + 3])
            codon[pos % 3] = b
            if b != target[pos] and "".join(codon) not in STOPS:
                return b
        raise ValueError(pos)

    # ---- good ---------------------------------------------------------------
    # One SNP and one heterozygous A/G-type mixture.
    pair("good", "GOOD01", edit(amplicon, {100: snp(100), 280: mixture(280)}))
    # A C/T-type mixture and an in-frame 3 bp deletion (marked, still passes).
    pair("good", "GOOD02", edit(amplicon, {400: mixture(400)}, deletions=[(200, 3)]))
    # Reads close to the line: 79% and 81% of bases >= Q20 (cutoff 75%). The weak bases are at
    # the start of each read, mostly before the target region, so the assembly is still clean.
    pair("good", "BORDER01", amplicon, dict(good_fraction=0.79, weak="start"),
         dict(good_fraction=0.81, weak="start"))

    # ---- bad ----------------------------------------------------------------
    # Failed reaction: weak, noisy signal in both directions.
    poor = dict(kind="poor", signal=150.0)
    pair("bad", "BAD01", amplicon, poor, poor)
    # Reverse read below the 75% cutoff (70% >= Q20).
    pair("bad", "BAD02", amplicon, r_kw=dict(good_fraction=0.70))
    # Clean reads from different templates (sample mix-up): reverse does not match the reference.
    unrelated = "".join(rng.choice(list("ACGT"), 1000))
    pair("bad", "BAD03", amplicon, r_template=unrelated)
    # Stop codon: codon 60 becomes TAG.
    pair("bad", "BAD04", edit(amplicon, {177: "T", 178: "A", 179: "G"}))
    # Frameshift: 1 bp deletion.
    pair("bad", "BAD05", edit(amplicon, deletions=[(300, 1)]))
    # Too many mixtures: 28 mixed positions (6.2% > 3.5%).
    pair("bad", "BAD06", edit(amplicon, {pos: mixture(pos) for pos in range(20, 440, 15)}))
    # Forward read only: the reverse read is missing.
    pair("bad", "BAD07", amplicon, skip="R")
    # Both reads pass the 75% cutoff (76% / 77%), but their weak bases are spread through the
    # middle instead of at the ends, so many positions get a low average quality and RECall
    # rejects the assembly on the mark count (113 marked positions, fail at >= 100).
    pair("bad", "BAD08", amplicon, dict(good_fraction=0.76, weak="scatter"), dict(good_fraction=0.77, weak="scatter"))

    print(f"Wrote examples to {EX}")


if __name__ == "__main__":
    main()
