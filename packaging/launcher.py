"""Entry point for the frozen (PyInstaller) app.

Double-clicking opens the window; passing arguments runs the command line,
e.g. ``SangerSeqAnalyzer qc folder -o results``.
"""

import sys

from sanger_seq_analyzer.cli import main as cli_main
from sanger_seq_analyzer.gui import main as gui_main

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli_main())
    gui_main()
