"""Command-line interface.

Subcommands are registered in ``build_parser`` so new tools can be added
alongside ``qc`` without changing how the program is launched.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .pipeline import FORWARD_TAGS, REVERSE_TAGS, load_reference, run
from .qc import QCSettings
from .report import write_outputs


def _add_qc_options(p: argparse.ArgumentParser) -> None:
    d = QCSettings()
    p.add_argument("inputs", nargs="+", help=".ab1 files and/or folders containing them")
    p.add_argument("-o", "--outdir", default="sanger_results", help="output folder (default: %(default)s)")
    p.add_argument("--min-quality", type=int, default=d.min_quality,
                   help="Phred value that counts as a good base (default: %(default)s)")
    p.add_argument("--cutoff", type=float, default=d.min_good_fraction * 100,
                   help="percent of bases that must be good for a read to pass (default: %(default)s)")
    p.add_argument("--min-length", type=int, default=d.min_trimmed_length,
                   help="minimum trimmed read length (default: %(default)s)")
    p.add_argument("--mixture-threshold", type=float, default=d.mixture_threshold * 100,
                   help="secondary peak height, as percent of the main peak, that calls a mixture "
                        "(default: %(default)s)")
    p.add_argument("--no-mixtures", action="store_true", help="do not call mixed bases")
    p.add_argument("--reference", help="FASTA reference used to orient consensus sequences")
    p.add_argument("--forward-tags", default=",".join(FORWARD_TAGS),
                   help="filename tags that mark forward reads (default: %(default)s)")
    p.add_argument("--reverse-tags", default=",".join(REVERSE_TAGS),
                   help="filename tags that mark reverse reads (default: %(default)s)")
    p.add_argument("--no-html", action="store_true", help="skip the HTML report")


def cmd_qc(args: argparse.Namespace) -> int:
    settings = QCSettings(
        min_quality=args.min_quality,
        min_good_fraction=args.cutoff / 100,
        min_trimmed_length=args.min_length,
        mixture_threshold=args.mixture_threshold / 100,
        call_mixtures=not args.no_mixtures,
    )
    result = run(
        args.inputs,
        settings,
        reference=load_reference(args.reference),
        forward_tags=tuple(t.strip() for t in args.forward_tags.split(",") if t.strip()),
        reverse_tags=tuple(t.strip() for t in args.reverse_tags.split(",") if t.strip()),
        progress=lambda n, total, name: print(f"[{n}/{total}] {name}", file=sys.stderr),
    )
    if not result.samples:
        print("No forward/reverse .ab1 files found.", file=sys.stderr)
        return 1
    paths = write_outputs(result, args.outdir, html_report=not args.no_html)

    width = max(len(s.sample) for s in result.samples)
    print(f"\n{'sample':<{width}}  status  length  F     R     notes")
    for s in result.samples:
        c = s.consensus
        f = c.forward.status if c.forward else "-"
        r = c.reverse.status if c.reverse else "-"
        print(f"{s.sample:<{width}}  {c.status:<6}  {c.length:>6}  {f:<4}  {r:<4}  {'; '.join(c.reasons)}")
    for p in result.unpaired:
        print(f"skipped (no F/R tag or duplicate): {p.name}", file=sys.stderr)
    for p, err in result.errors.items():
        print(f"error reading {p}: {err}", file=sys.stderr)
    print(f"\nOutputs written to {paths['consensus'].parent}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import main as gui_main

    gui_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sanger-seq", description="Sanger sequencing analysis toolkit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("qc", help="QC forward/reverse .ab1 reads and write consensus FASTA")
    _add_qc_options(p)
    p.set_defaults(func=cmd_qc)

    p = sub.add_parser("gui", help="open the desktop window")
    p.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
