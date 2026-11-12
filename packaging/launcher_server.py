"""Entry point of the server executable (SangerSeqServer.exe). Never imports Qt.

With no arguments it runs ``serve`` with the data folder from SANGER_SEQ_DATA; any
arguments are passed to the sanger-seq command line (e.g. ``server create-admin``).
"""

import sys

from sanger_seq_analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["serve"]))
