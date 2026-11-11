"""Regenerate the example .ab1 files in examples/.

    python scripts/make_examples.py

A synthetic 1000 bp amplicon is "sequenced" with a forward and a reverse
primer. examples/reference.fasta holds only the protein-coding target region
inside it (187 codons, no stop codons), which is what reads are assembled
against, just as RECall assembles PR-RT reads against HXB2. Each sample is
built to show one outcome, so the expected result is known.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sanger_seq_analyzer.simulate import make_read, revcomp  # noqa: E402

EX = ROOT / "examples"
TARGET_START, N_CODONS = 171, 187  # target = amplicon[171:732]
F_START, F_END = 0, 800  # region read by the forward primer
R_START, R_END = 100, 960  # region read (other strand) by the reverse primer
STOPS = {"TAA", "TAG", "TGA"}


def coding_sequence(rng, n_codons):
    codons = [a + b + c for a in "ACGT" for b in "ACGT" for c in "ACGT" if a + b + c not in STOPS]
    return "".join(rng.choice(codons, n_codons))


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


def other_base(b, avoid=""):
    return next(x for x in "ACGT" if x != b and x not in avoid)


def main() -> None:
    rng = np.random.default_rng(2026)
    target = coding_sequence(rng, N_CODONS)
    flank5 = "".join(rng.choice(list("ACGT"), TARGET_START))
    flank3 = "".join(rng.choice(list("ACGT"), 1000 - TARGET_START - len(target)))
    amplicon = flank5 + target + flank3

    for folder in ("good", "bad"):
        d = EX / folder
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.ab1"):
            f.unlink()
    (EX / "reference.fasta").write_text(
        f">example_target synthetic {len(target)} bp coding region ({N_CODONS} codons, frame 1)\n"
        + "\n".join(target[i:i + 70] for i in range(0, len(target), 70)) + "\n"
    )

    def pair(folder, sample, template, f_kw=None, r_kw=None, r_template=None, skip=""):
        r_template = r_template or template
        if skip != "F":
            make_read(EX / folder / f"{sample}_F.ab1", f"{sample}_F", template[F_START:F_END], rng, **(f_kw or {}))
        if skip != "R":
            make_read(EX / folder / f"{sample}_R.ab1", f"{sample}_R",
                      revcomp(r_template[R_START:min(R_END, len(r_template))]), rng, **(r_kw or {}))

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
    pair("good", "GOOD01", edit(amplicon, {100: snp(100), 280: "R"}))
    # A C/T-type mixture and an in-frame 3 bp deletion (marked, still passes).
    pair("good", "GOOD02", edit(amplicon, {400: "Y"}, deletions=[(200, 3)]))
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
    # Too many mixtures: 30 heterozygous positions (5.3% > 3.5%).
    mix_codes = {"AG": "R", "CT": "Y", "AC": "M", "GT": "K", "CG": "S", "AT": "W"}
    subs = {}
    for pos in range(20, 560, 18):
        b = target[pos]
        partner = other_base(b)
        subs[pos] = mix_codes["".join(sorted(b + partner))]
    pair("bad", "BAD06", edit(amplicon, subs))
    # Forward read only: the reverse read is missing.
    pair("bad", "BAD07", amplicon, skip="R")
    # Both reads pass the 75% cutoff (79% / 81%), but their weak bases are spread through the
    # middle instead of at the ends, so RECall rejects the assembly (Ns, marks, no usable coverage).
    pair("bad", "BAD08", amplicon, dict(good_fraction=0.79, weak="scatter"), dict(good_fraction=0.81, weak="scatter"))

    print(f"Wrote examples to {EX}")


if __name__ == "__main__":
    main()
