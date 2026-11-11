"""Write FASTA, CSV, JSON and HTML outputs for a run.

Every file is written to ``<name>.part`` and then renamed, so an interrupted or
failed export never leaves a file that looks complete. CSV files are UTF-8 with
a byte-order mark (so Excel on Windows shows non-ASCII names correctly); FASTA,
JSON and HTML are plain UTF-8.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from .pipeline import RunResult
from .report_html import render_html


@dataclass(frozen=True)
class ExportFile:
    key: str
    filename: str
    description: str


EXPORT_FILES = (
    ExportFile("consensus", "consensus.fasta", "Consensus of every PASS sample, oriented to the reference"),
    ExportFile("html", "report.html", "Summary, alignments and chromatograms; opens in a browser and prints to PDF"),
    ExportFile("samples_csv", "sample_summary.csv",
               "One row per sample: status, length, span, mixtures, N, marks, indels, reasons"),
    ExportFile("reads_csv", "read_qc.csv", "One row per read: % bases ≥ Q20, mean QV, trim range, strand, identity"),
    ExportFile("marks_csv", "marks.csv", "Every marked position and why it was marked"),
    ExportFile("reads", "trimmed_reads.fasta", "Every read that passed the lab cutoff, after trimming"),
    ExportFile("run_info", "run_info.json", "Version, date, reference checksum, input files and every setting used"),
)
EXPORT_BY_KEY = {f.key: f for f in EXPORT_FILES}


class ExportError(Exception):
    """A file could not be written; names the file and the cause."""

    def __init__(self, path: Path, cause: Exception):
        self.path = Path(path)
        self.cause = cause
        detail = cause.strerror if isinstance(cause, OSError) and cause.strerror else str(cause)
        super().__init__(f"could not write {self.path}: {detail}")


def _wrap(seq: str, width: int = 70) -> str:
    return "\n".join(seq[i:i + width] for i in range(0, len(seq), width))


def _fasta(header: str, seq: str) -> str:
    return f">{header}\n{_wrap(seq)}\n"


def _passed_reads(result: RunResult):
    for s in result.samples:
        c = s.consensus
        for direction, read in (("F", c.forward), ("R", c.reverse)):
            if read is not None and read.passed:
                yield direction, read


def write_consensus(result: RunResult, fh: TextIO) -> None:
    for s in result.samples:
        c = s.consensus
        if c.status == "PASS":
            fh.write(_fasta(f"{s.sample} ref={c.ref_start}-{c.ref_end} len={c.length} "
                            f"mixtures={c.mixtures} marks={c.marks}", c.sequence))


def write_trimmed_reads(result: RunResult, fh: TextIO) -> None:
    for direction, read in _passed_reads(result):
        fh.write(_fasta(f"{read.name} {direction} trimmed={read.trim_start + 1}-{read.trim_end}", read.sequence))


def write_sample_summary(result: RunResult, fh: TextIO) -> None:
    w = csv.writer(fh)
    w.writerow(["sample", "status", "length", "ref_start", "ref_end", "mixtures", "mixture_pct", "N",
                "marks", "max_single_coverage_run", "stop_codons", "indels",
                "forward_status", "reverse_status", "reasons"])
    for s in result.samples:
        c = s.consensus
        w.writerow([s.sample, c.status, c.length, c.ref_start or "", c.ref_end or "", c.mixtures,
                    f"{c.mixture_fraction * 100:.2f}", c.n_count, c.marks, c.max_single_run,
                    " ".join(map(str, c.stop_codons)), "; ".join(c.indels),
                    c.forward.status if c.forward else "MISSING",
                    c.reverse.status if c.reverse else "MISSING", "; ".join(c.reasons)])


def write_read_qc(result: RunResult, fh: TextIO) -> None:
    w = csv.writer(fh)
    w.writerow(["sample", "direction", "file", "status", "raw_length", "mean_quality",
                f"pct_bases_q{result.settings.min_quality}", "trim_start", "trim_end",
                "trimmed_length", "strand", "ref_start", "ref_end", "ref_identity", "reasons"])
    for s in result.samples:
        aligned = {a.direction: a for a in s.consensus.aligned}
        for direction, read, chrom in (("F", s.consensus.forward, s.forward_chrom),
                                       ("R", s.consensus.reverse, s.reverse_chrom)):
            if read is None:
                continue
            a = aligned.get(direction)
            w.writerow([s.sample, direction, chrom.path.name if chrom else "", read.status, read.length,
                        f"{read.mean_quality:.1f}", f"{read.good_fraction * 100:.1f}",
                        read.trim_start + 1, read.trim_end, read.trimmed_length,
                        a.strand if a else "", a.ref_start + 1 if a else "", a.ref_end if a else "",
                        f"{a.identity:.4f}" if a else "", "; ".join(read.reasons)])


def write_marks(result: RunResult, fh: TextIO) -> None:
    w = csv.writer(fh)
    w.writerow(["sample", "consensus_position", "reference_position", "base", "coverage",
                "mean_quality", "marks"])
    for s in result.samples:
        for p in s.consensus.positions:
            if p.marks:
                w.writerow([s.sample, p.cons_index or "", p.ref_number if p.ref_pos is not None else "insertion",
                            p.base, p.coverage, f"{p.mean_quality:.1f}", "; ".join(p.marks)])


def run_info(result: RunResult) -> dict:
    """Everything needed to trace a result back to its inputs, reference and settings."""
    ref = result.reference_info
    return {
        "app": "sanger_seq_analyzer",
        "version": result.version,
        "rule_set": "Lab read rules + RECall (Woods et al. 2012, J Clin Microbiol 50:1936, Tables 1 and 2)",
        "started": result.started.isoformat(timespec="seconds") if result.started else None,
        "finished": result.finished.isoformat(timespec="seconds") if result.finished else None,
        "cancelled": result.cancelled,
        "reference": {
            "name": ref.name if ref else "",
            "description": ref.description if ref else "",
            "source": ref.source if ref else "",
            "path": str(ref.path) if ref and ref.path else None,
            "record_id": ref.record_id if ref else None,
            "length": len(result.reference),
            "sha256": ref.sha256 if ref else "",
            "warnings": list(ref.warnings) if ref else [],
        },
        "inputs": [str(p) for p in result.inputs],
        "forward_tags": list(result.forward_tags),
        "reverse_tags": list(result.reverse_tags),
        "settings": dataclasses.asdict(result.settings),
        "counts": {"samples": len(result.samples), "pass": result.count("PASS"), "fail": result.count("FAIL")},
        "planned_samples": result.planned,
        "files_not_analysed": [{"path": str(s.path), "reason": s.reason, "detail": s.text} for s in result.skipped],
        "errors": dict(result.errors),
    }


def write_run_info(result: RunResult, fh: TextIO) -> None:
    json.dump(run_info(result), fh, indent=2, ensure_ascii=False)
    fh.write("\n")


def write_html(result: RunResult, fh: TextIO) -> None:
    fh.write(render_html(result))


# key -> (writer, encoding, newline)
_WRITERS: dict[str, tuple[Callable[[RunResult, TextIO], None], str, str | None]] = {
    "consensus": (write_consensus, "utf-8", "\n"),
    "reads": (write_trimmed_reads, "utf-8", "\n"),
    "samples_csv": (write_sample_summary, "utf-8-sig", ""),
    "reads_csv": (write_read_qc, "utf-8-sig", ""),
    "marks_csv": (write_marks, "utf-8-sig", ""),
    "run_info": (write_run_info, "utf-8", "\n"),
    "html": (write_html, "utf-8", None),
}


def write_file(result: RunResult, key: str, outdir: str | Path) -> Path:
    """Write one export file atomically; raises :class:`ExportError`."""
    path = Path(outdir) / EXPORT_BY_KEY[key].filename
    part = path.with_name(path.name + ".part")
    writer, encoding, newline = _WRITERS[key]
    try:
        with open(part, "w", encoding=encoding, newline=newline) as fh:
            writer(result, fh)
        os.replace(part, path)
    except OSError as exc:
        try:
            part.unlink()
        except OSError:
            pass
        raise ExportError(path, exc) from exc
    return path


def write_outputs(result: RunResult, outdir: str | Path, html_report: bool = True,
                  files: list[str] | None = None) -> dict[str, Path]:
    """Write the export files (all of them by default) into ``outdir``; returns key -> path."""
    out = Path(outdir)
    keys = files if files is not None else [f.key for f in EXPORT_FILES if html_report or f.key != "html"]
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportError(out, exc) from exc
    return {key: write_file(result, key, out) for key in keys}


def export_counts(result: RunResult) -> dict[str, str]:
    """What each export file will hold, for the export dialog ("3 sequences", "21 rows")."""
    def n(count: int, word: str) -> str:
        return f"{count:,} {word}{'' if count == 1 else 's'}"
    marks = sum(1 for s in result.samples for p in s.consensus.positions if p.marks)
    reads = sum(1 for s in result.samples for r in (s.consensus.forward, s.consensus.reverse) if r is not None)
    return {
        "consensus": n(result.count("PASS"), "sequence"),
        "html": n(len(result.samples), "sample"),
        "samples_csv": n(len(result.samples), "row"),
        "reads_csv": n(reads, "row"),
        "marks_csv": n(marks, "row"),
        "reads": n(sum(1 for _ in _passed_reads(result)), "read"),
        "run_info": "1 file",
    }
