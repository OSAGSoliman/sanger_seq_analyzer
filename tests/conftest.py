from pathlib import Path

import numpy as np
import pytest

from sanger_seq_analyzer.simulate import make_read, revcomp

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="session")
def examples_dir():
    return EXAMPLES


@pytest.fixture
def make_pair(tmp_path):
    """Write a simulated F/R pair and return (folder, template)."""
    def _make(sample="S1", f_kw=None, r_kw=None, seed=1, length=700):
        rng = np.random.default_rng(seed)
        template = "".join(rng.choice(list("ACGT"), length))
        make_read(tmp_path / f"{sample}_F.ab1", sample, template[:500], rng, **(f_kw or {}))
        make_read(tmp_path / f"{sample}_R.ab1", sample, revcomp(template[200:]), rng, **(r_kw or {}))
        return tmp_path, template
    return _make
