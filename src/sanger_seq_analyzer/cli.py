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
    p.add_argument("-r", "--reference", required=True,
                   help="FASTA of the region to assemble against (e.g. HXB2 PR-RT); its first base starts codon 1")
    p.add_argument("-o", "--outdir", default="sanger_results", help="output folder (default: %(default)s)")

    g = p.add_argument_group("lab read rules")
    g.add_argument("--cutoff", type=float, default=d.min_good_fraction * 100,
                   help="percent of bases that must be good for a read to pass (default: %(default)s)")
    g.add_argument("--min-quality", type=int, default=d.min_quality,
                   help="Phred value that counts as a good base (default: %(default)s)")
    g.add_argument("--min-length", type=int, default=d.min_trimmed_length,
                   help="minimum trimmed read length (default: %(default)s)")
    g.add_argument("--allow-one-direction", action="store_true",
                   help="do not fail samples that only have a forward or only a reverse read")
    g.add_argument("--forward-tags", default=",".join(FORWARD_TAGS),
                   help="filename tags that mark forward reads (default: %(default)s)")
    g.add_argument("--reverse-tags", default=",".join(REVERSE_TAGS),
                   help="filename tags that mark reverse reads (default: %(default)s)")

    g = p.add_argument_group("RECall rules (defaults from Woods et al. 2012, Tables 1 and 2)")
    g.add_argument("--censor-quality", type=int, default=d.censor_quality,
                   help="bases below this phred score are excluded from assembly (default: %(default)s)")
    g.add_argument("--mixture-area", type=float, default=d.mixture_area * 100,
                   help="uncalled/called peak area %% that counts towards a mixture (default: %(default)s)")
    g.add_argument("--no-mixtures", action="store_true", help="do not call mixtures")
    g.add_argument("--mark-area", type=float, default=d.mark_area * 100,
                   help="uncalled/called peak area %% that marks a position (default: %(default)s)")
    g.add_argument("--mark-quality", type=int, default=d.mark_avg_quality,
                   help="mark positions whose average quality is below this (default: %(default)s)")
    g.add_argument("--max-mixtures", type=float, default=d.max_mixture_fraction * 100,
                   help="fail if more than this %% of bases are mixtures (default: %(default)s)")
    g.add_argument("--max-n", type=int, default=d.max_n, help="fail if N count exceeds this (default: %(default)s)")
    g.add_argument("--max-marks", type=int, default=d.max_marks,
                   help="fail if marked positions exceed this (default: %(default)s)")
    g.add_argument("--single-coverage-quality", type=int, default=d.single_coverage_quality,
                   help="single-read bases below this quality count as weak (default: %(default)s)")
    g.add_argument("--max-single-run", type=int, default=d.max_single_coverage_run,
                   help="fail if more consecutive weak single-coverage bases than this (default: %(default)s)")
    g.add_argument("--frame", type=int, default=d.frame,
                   help="reference position where codon 1 starts (default: %(default)s)")
    g.add_argument("--non-coding", action="store_true",
                   help="target is not protein coding: skip stop-codon and frameshift checks")
    p.add_argument("--no-html", action="store_true", help="skip the HTML report")


def settings_from_args(args: argparse.Namespace) -> QCSettings:
    return QCSettings(
        min_quality=args.min_quality,
        min_good_fraction=args.cutoff / 100,
        min_trimmed_length=args.min_length,
        require_both_directions=not args.allow_one_direction,
        censor_quality=args.censor_quality,
        mixture_area=args.mixture_area / 100,
        call_mixtures=not args.no_mixtures,
        mark_area=args.mark_area / 100,
        mark_avg_quality=args.mark_quality,
        max_mixture_fraction=args.max_mixtures / 100,
        max_n=args.max_n,
        max_marks=args.max_marks,
        single_coverage_quality=args.single_coverage_quality,
        max_single_coverage_run=args.max_single_run,
        frame=args.frame,
        check_stop_codons=not args.non_coding,
        check_frameshifts=not args.non_coding,
    )


def cmd_qc(args: argparse.Namespace) -> int:
    result = run(
        args.inputs,
        load_reference(args.reference),
        settings_from_args(args),
        forward_tags=tuple(t.strip() for t in args.forward_tags.split(",") if t.strip()),
        reverse_tags=tuple(t.strip() for t in args.reverse_tags.split(",") if t.strip()),
        progress=lambda n, total, name: print(f"[{n}/{total}] {name}", file=sys.stderr),
    )
    if not result.samples:
        print("No forward/reverse .ab1 files found.", file=sys.stderr)
        return 1
    paths = write_outputs(result, args.outdir, html_report=not args.no_html)

    width = max(len(s.sample) for s in result.samples)
    print(f"\n{'sample':<{width}}  status  length  mix  N   marks  reasons")
    for s in result.samples:
        c = s.consensus
        print(f"{s.sample:<{width}}  {c.status:<6}  {c.length:>6}  {c.mixtures:>3}  {c.n_count:>2}  {c.marks:>5}  "
              f"{'; '.join(c.reasons)}")
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

    p = sub.add_parser("qc", help="QC and assemble forward/reverse .ab1 reads with RECall rules")
    _add_qc_options(p)
    p.set_defaults(func=cmd_qc)

    p = sub.add_parser("gui", help="open the desktop window")
    p.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
