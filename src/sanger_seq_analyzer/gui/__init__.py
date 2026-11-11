"""Desktop window (PySide6). Importing this package does not import Qt; ``main`` does.

The window is organised as tools (see :mod:`.tools`); QC is the first one.
"""

from __future__ import annotations

import importlib
import sys

MISSING_QT = ("The desktop window is not included in this installation ({error}).\n"
              "Install it with: pip install PySide6-Essentials")


def main(argv: list[str] | None = None) -> int:
    """Start the window (``sanger-seq-gui``); ``--self-test OUTDIR`` runs the headless check."""
    try:
        importlib.import_module("PySide6.QtWidgets")  # fails when Qt is not installed

        from .app import run_app
    except ImportError as exc:
        if sys.stderr is not None:
            print(MISSING_QT.format(error=exc), file=sys.stderr)
        return 1
    return run_app(argv)
