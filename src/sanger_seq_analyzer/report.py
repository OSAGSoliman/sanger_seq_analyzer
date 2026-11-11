"""Write FASTA, CSV and HTML outputs for a run."""

from __future__ import annotations

import base64
import csv
import html
import io
from datetime import datetime
from pathlib import Path

from . import __version__
from .pipeline import RunResult, SampleResult
from .qc import ReadQC


def _wrap(seq: str, width: int = 70) -> str:
    return "\n".join(seq[i:i + width] for i in range(0, len(seq), width))


def _fasta(header: str, seq: str) -> str:
    return f">{header}\n{_wrap(seq)}\n"


def write_outputs(result: RunResult, outdir: str | Path, html_report: bool = True) -> dict[str, Path]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "consensus": out / "consensus.fasta",
        "single": out / "single_read.fasta",
        "reads": out / "trimmed_reads.fasta",
        "samples_csv": out / "sample_summary.csv",
        "reads_csv": out / "read_qc.csv",
    }

    with open(paths["consensus"], "w", newline="\n") as cons_fh, \
         open(paths["single"], "w", newline="\n") as single_fh, \
         open(paths["reads"], "w", newline="\n") as reads_fh:
        for s in result.samples:
            c = s.consensus
            if c.status == "PASS":
                cons_fh.write(_fasta(f"{s.sample} len={c.length} mixtures={c.mixtures} N={c.ambiguous}", c.sequence))
            elif c.status == "SINGLE":
                which = "forward" if c.forward and c.forward.passed else "reverse"
                single_fh.write(_fasta(f"{s.sample} single={which} len={c.length}", c.sequence))
            for direction, read in (("F", c.forward), ("R", c.reverse)):
                if read is not None and read.passed:
                    reads_fh.write(_fasta(f"{read.name} {direction} trimmed={read.trim_start + 1}-{read.trim_end}",
                                          read.sequence))

    with open(paths["samples_csv"], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample", "status", "consensus_length", "overlap_bp", "overlap_identity",
                    "discordant", "mixtures", "N", "forward_status", "reverse_status",
                    "reference_strand", "reference_identity", "reasons"])
        for s in result.samples:
            c, h = s.consensus, s.reference_hit
            w.writerow([s.sample, c.status, c.length, c.overlap, f"{c.overlap_identity:.4f}",
                        c.discordant, c.mixtures, c.ambiguous,
                        c.forward.status if c.forward else "MISSING",
                        c.reverse.status if c.reverse else "MISSING",
                        h.strand if h else "", f"{h.identity:.4f}" if h else "", "; ".join(c.reasons)])

    with open(paths["reads_csv"], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample", "direction", "file", "status", "raw_length", "mean_quality",
                    f"pct_bases_q{result.settings.min_quality}", "trim_start", "trim_end",
                    "trimmed_length", "mixtures", "reasons"])
        for s in result.samples:
            for direction, read, chrom in (("F", s.consensus.forward, s.forward_chrom),
                                           ("R", s.consensus.reverse, s.reverse_chrom)):
                if read is None:
                    continue
                w.writerow([s.sample, direction, chrom.path.name if chrom else "", read.status, read.length,
                            f"{read.mean_quality:.1f}", f"{read.good_fraction * 100:.1f}",
                            read.trim_start + 1, read.trim_end, read.trimmed_length, read.mixtures,
                            "; ".join(read.reasons)])

    if html_report:
        paths["html"] = out / "report.html"
        paths["html"].write_text(render_html(result), encoding="utf-8")
    return paths


def _quality_plot(read: ReadQC, chrom, min_q: int) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 1.4), dpi=90)
    q = chrom.qualities
    colors = ["#2e7d32" if v >= min_q else "#c62828" for v in q]
    ax.bar(range(len(q)), q, width=1.0, color=colors, linewidth=0)
    ax.axvspan(read.trim_start, read.trim_end, color="#1565c0", alpha=0.08)
    ax.axhline(min_q, color="#555", lw=0.8, ls="--")
    ax.set_xlim(0, max(len(q), 1))
    ax.set_ylim(0, 62)
    ax.set_ylabel("QV", fontsize=8)
    ax.tick_params(labelsize=7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout(pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _read_block(direction: str, read: ReadQC | None, chrom, min_q: int) -> str:
    if read is None or chrom is None:
        return f"<div class='read'><b>{direction}</b>: <span class='FAIL'>missing</span></div>"
    img = _quality_plot(read, chrom, min_q)
    reasons = f"<div class='why'>{html.escape('; '.join(read.reasons))}</div>" if read.reasons else ""
    return (
        f"<div class='read'><b>{direction}</b> {html.escape(chrom.path.name)} "
        f"<span class='{read.status}'>{read.status}</span> &middot; {read.good_fraction:.1%} bases &ge; Q{min_q} "
        f"&middot; mean QV {read.mean_quality:.1f} &middot; trimmed {read.trim_start + 1}-{read.trim_end} "
        f"({read.trimmed_length} bp) &middot; mixtures {read.mixtures}{reasons}"
        f"<br><img alt='quality' src='data:image/png;base64,{img}'></div>"
    )


def _sample_block(s: SampleResult, min_q: int) -> str:
    c = s.consensus
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in c.reasons)
    seq = html.escape(_wrap(c.sequence, 80)) if c.sequence else "(no consensus)"
    ref = (f" &middot; reference {s.reference_hit.strand} strand, {s.reference_hit.identity:.1%} identity"
           if s.reference_hit else "")
    return (
        f"<section><h2>{html.escape(s.sample)} <span class='{c.status}'>{c.status}</span></h2>"
        f"<p>{c.length} bp &middot; overlap {c.overlap} bp ({c.overlap_identity:.1%} agree) &middot; "
        f"discordant {c.discordant} &middot; mixtures {c.mixtures} &middot; N {c.ambiguous}{ref}</p>"
        f"{'<ul>' + reasons + '</ul>' if reasons else ''}"
        f"{_read_block('Forward', c.forward, s.forward_chrom, min_q)}"
        f"{_read_block('Reverse', c.reverse, s.reverse_chrom, min_q)}"
        f"<pre>{seq}</pre></section>"
    )


def render_html(result: RunResult) -> str:
    st = result.settings
    counts = {k: sum(1 for s in result.samples if s.consensus.status == k) for k in ("PASS", "SINGLE", "FAIL")}
    rows = "".join(
        f"<tr><td><a href='#s{i}'>{html.escape(s.sample)}</a></td><td class='{s.consensus.status}'>"
        f"{s.consensus.status}</td><td>{s.consensus.length}</td><td>{s.consensus.mixtures}</td>"
        f"<td>{s.consensus.ambiguous}</td></tr>"
        for i, s in enumerate(result.samples)
    )
    blocks = "".join(f"<a id='s{i}'></a>{_sample_block(s, st.min_quality)}" for i, s in enumerate(result.samples))
    extra = ""
    if result.unpaired:
        extra += "<p><b>Files not matched to a forward/reverse pair:</b> " + ", ".join(
            html.escape(p.name) for p in result.unpaired) + "</p>"
    if result.errors:
        extra += "<p><b>Unreadable files:</b></p><ul>" + "".join(
            f"<li>{html.escape(k)}: {html.escape(v)}</li>" for k, v in result.errors.items()) + "</ul>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Sanger QC report</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#222;max-width:1000px}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ddd;padding:3px 10px;text-align:left}}
.PASS{{color:#1b5e20;font-weight:600}}.SINGLE{{color:#e65100;font-weight:600}}.FAIL{{color:#b71c1c;font-weight:600}}
section{{border-top:2px solid #eee;margin-top:18px}}.read{{margin:8px 0}}.why{{color:#b71c1c;font-size:90%}}
pre{{background:#f6f6f6;padding:8px;font-size:12px;overflow-x:auto}}
</style></head><body>
<h1>Sanger QC report</h1>
<p>sanger_seq_analyzer {__version__} &middot; {datetime.now():%Y-%m-%d %H:%M}<br>
Read passes when &ge; {st.min_good_fraction:.0%} of bases are &ge; Q{st.min_quality} and trimmed length &ge;
{st.min_trimmed_length} bp. Mixture threshold {st.mixture_threshold:.0%} of the main peak.</p>
<p><span class='PASS'>PASS {counts['PASS']}</span> &middot; <span class='SINGLE'>SINGLE {counts['SINGLE']}</span>
&middot; <span class='FAIL'>FAIL {counts['FAIL']}</span></p>
<table><tr><th>Sample</th><th>Status</th><th>Length</th><th>Mixtures</th><th>N</th></tr>{rows}</table>
{extra}{blocks}</body></html>"""
