"""Entry point of the command-line executable built by PyInstaller (sanger-seq). Never imports Qt."""

import sys

from sanger_seq_analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())
