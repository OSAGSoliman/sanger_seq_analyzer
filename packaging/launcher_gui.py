"""Entry point of the windowed app built by PyInstaller (SangerSeqAnalyzer).

Imports only the desktop window. ``--self-test OUTDIR`` runs the headless check
used by CI; the command line is a separate executable (``launcher_cli.py``).
"""

import sys

from sanger_seq_analyzer.gui import main

if __name__ == "__main__":
    sys.exit(main())
