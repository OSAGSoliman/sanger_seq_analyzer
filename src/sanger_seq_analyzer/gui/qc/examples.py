"""Locate the bundled example .ab1 files (source checkout, installed package or frozen app)."""

from __future__ import annotations

import sys
from pathlib import Path


def candidates() -> list[Path]:
    here = Path(__file__).resolve()
    out = []
    if hasattr(sys, "_MEIPASS"):  # PyInstaller bundle
        out.append(Path(sys._MEIPASS) / "examples")
    exe = Path(sys.executable).resolve()
    out += [exe.parent / "examples", exe.parent.parent / "examples"]
    if sys.platform == "darwin":  # next to SangerSeqAnalyzer.app
        out += [p / "examples" for p in exe.parents[:4]]
    out.append(here.parents[4] / "examples")  # src/sanger_seq_analyzer/gui/qc -> repository root
    out.append(Path.cwd() / "examples")
    return out


def find_examples() -> Path | None:
    """The first examples folder that holds .ab1 files, or None."""
    for path in candidates():
        try:
            if path.is_dir() and any(path.rglob("*.ab1")):
                return path
        except OSError:
            continue
    return None
