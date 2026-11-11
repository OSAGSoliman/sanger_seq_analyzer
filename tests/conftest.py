from pathlib import Path

import numpy as np
import pytest

from sanger_seq_analyzer.simulate import make_read, revcomp

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
STOPS = {"TAA", "TAG", "TGA"}


@pytest.fixture(scope="session")
def examples_dir():
    return EXAMPLES


def coding(rng, n_codons):
    codons = [a + b + c for a in "ACGT" for b in "ACGT" for c in "ACGT" if a + b + c not in STOPS]
    return "".join(rng.choice(codons, n_codons))


@pytest.fixture
def make_pair(tmp_path):
    """Write a simulated F/R pair around a coding target; return (folder, reference, amplicon builder).

    ``edit(target)`` lets a test change the target (e.g. add a stop codon) before reads are made.
    """
    def _make(sample="S1", f_kw=None, r_kw=None, seed=1, edit=None):
        rng = np.random.default_rng(seed)
        reference = coding(rng, 100)  # 300 bp target
        flank5 = "".join(rng.choice(list("ACGT"), 150))
        flank3 = "".join(rng.choice(list("ACGT"), 150))
        target = edit(reference) if edit else reference
        amplicon = flank5 + target + flank3
        make_read(tmp_path / f"{sample}_F.ab1", sample, amplicon[:500], rng, **(f_kw or {}))
        make_read(tmp_path / f"{sample}_R.ab1", sample, revcomp(amplicon[100:]), rng, **(r_kw or {}))
        return tmp_path, reference
    return _make


@pytest.fixture(scope="session")
def examples_run():
    """The bundled examples analysed once against the built-in measles N-450 reference."""
    from sanger_seq_analyzer.pipeline import run
    return run([EXAMPLES], "measles-n450")


@pytest.fixture(scope="session")
def example(examples_run):
    """Look up one sample of ``examples_run`` by name."""
    by_name = {s.sample: s for s in examples_run.samples}
    return by_name.__getitem__
