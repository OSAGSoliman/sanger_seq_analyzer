"""Write FASTA, CSV and HTML outputs for a run."""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path

from . import __version__
from .pipeline import RunResult, SampleResult
from .qc import ReadQC
from .recall import GAP, SampleCall


def _wrap(seq: str, width: int = 70) -> str:
    return "\n".join(seq[i:i + width] for i in range(0, len(seq), width))


def _fasta(header: str, seq: str) -> str:
    return f">{header}\n{_wrap(seq)}\n"


def write_outputs(result: RunResult, outdir: str | Path, html_report: bool = True) -> dict[str, Path]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "consensus": out / "consensus.fasta",
        "reads": out / "trimmed_reads.fasta",
        "samples_csv": out / "sample_summary.csv",
        "reads_csv": out / "read_qc.csv",
        "marks_csv": out / "marks.csv",
    }

    with open(paths["consensus"], "w", newline="\n") as cons_fh, \
         open(paths["reads"], "w", newline="\n") as reads_fh:
        for s in result.samples:
            c = s.consensus
            if c.status == "PASS":
                cons_fh.write(_fasta(f"{s.sample} ref={c.ref_start}-{c.ref_end} len={c.length} "
                                     f"mixtures={c.mixtures} marks={c.marks}", c.sequence))
            for direction, read in (("F", c.forward), ("R", c.reverse)):
                if read is not None and read.passed:
                    reads_fh.write(_fasta(f"{read.name} {direction} trimmed={read.trim_start + 1}-{read.trim_end}",
                                          read.sequence))

    with open(paths["samples_csv"], "w", newline="") as fh:
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

    with open(paths["reads_csv"], "w", newline="") as fh:
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

    with open(paths["marks_csv"], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample", "consensus_position", "reference_position", "base", "coverage",
                    "mean_quality", "marks"])
        for s in result.samples:
            i = 0
            for p in s.consensus.positions:
                if p.base != "-":
                    i += 1
                if p.marks:
                    w.writerow([s.sample, i if p.base != "-" else "", p.ref_pos + 1 if p.ref_pos is not None
                                else "insertion", p.base, p.coverage, f"{p.mean_quality:.1f}", "; ".join(p.marks)])

    if html_report:
        paths["html"] = out / "report.html"
        paths["html"].write_text(render_html(result), encoding="utf-8")
    return paths


def _read_data(read: ReadQC, chrom) -> dict:
    """Compact trace data for the in-page chromatogram viewer."""
    data = {"calls": chrom.sequence, "quals": [int(q) for q in chrom.qualities],
            "trim": [read.trim_start, read.trim_end], "traces": {}, "peaks": []}
    if chrom.has_traces and len(chrom.peak_locations) == len(chrom) and len(chrom) > 1:
        spacing = float(max(1, (chrom.peak_locations[-1] - chrom.peak_locations[0]) / (len(chrom) - 1)))
        step = max(1, int(round(spacing / 6)))  # keep ~6 points per base
        for b in "ACGT":
            data["traces"][b] = [int(v) for v in chrom.traces[b][::step]]
        data["peaks"] = [round(float(p) / step, 1) for p in chrom.peak_locations]
    return data


def _read_block(direction: str, read: ReadQC | None, chrom, min_q: int, viewers: dict) -> str:
    if read is None or chrom is None:
        return f"<div class='read'><b>{direction}</b>: <span class='FAIL'>missing</span></div>"
    vid = f"v{len(viewers)}"
    viewers[vid] = _read_data(read, chrom)
    reasons = f"<div class='why'>{html.escape('; '.join(read.reasons))}</div>" if read.reasons else ""
    return (
        f"<div class='read'><b>{direction}</b> {html.escape(chrom.path.name)} "
        f"<span class='{read.status}'>{read.status}</span> &middot; {read.good_fraction:.1%} bases &ge; Q{min_q} "
        f"&middot; mean QV {read.mean_quality:.1f} &middot; trimmed {read.trim_start + 1}-{read.trim_end} "
        f"({read.trimmed_length} bp){reasons}"
        f"<div class='chrom'><canvas data-viewer='{vid}'></canvas></div></div>"
    )


def _alignment_view(c: SampleCall, reference: str, width: int = 60) -> str:
    """Reference vs consensus in blocks, with differences, mixtures, Ns and marks highlighted."""
    if not c.positions:
        return ""
    cols = []
    for p in c.positions:
        ref = reference[p.ref_pos] if p.ref_pos is not None and p.ref_pos < len(reference) else GAP
        cols.append((p, ref))
    blocks = []
    for i in range(0, len(cols), width):
        chunk = cols[i:i + width]
        first_ref = next((p.ref_pos + 1 for p, _ in chunk if p.ref_pos is not None), "")
        ref_row, cons_row = [], []
        for p, ref in chunk:
            cls = []
            if p.base == "N":
                cls.append("n")
            elif p.base not in "ACGT-":
                cls.append("mix")
            elif p.base != ref:
                cls.append("diff")
            if p.marks:
                cls.append("mk")
            where = f"ref {p.ref_pos + 1}" if p.ref_pos is not None else "insertion"
            tip = f"{where}: {p.base} (ref {ref}), {p.coverage} read(s), mean Q{p.mean_quality:.0f}"
            if p.marks:
                tip += " - " + ", ".join(p.marks)
            ref_row.append(html.escape(ref))
            cons_row.append(f"<span class='{' '.join(cls)}' title='{html.escape(tip)}'>{html.escape(p.base)}</span>"
                            if cls else f"<span title='{html.escape(tip)}'>{html.escape(p.base)}</span>")
        blocks.append(f"<div class='blk'><span class='lbl'>Ref  {first_ref:>5}</span> {''.join(ref_row)}\n"
                      f"<span class='lbl'>Cons      </span> {''.join(cons_row)}</div>")
    legend = ("<div class='legend'><span class='diff'>A</span> differs from reference "
              "<span class='mix'>R</span> mixture <span class='n'>N</span> no call "
              "<span class='mk'>A</span> marked (hover for details)</div>")
    return f"{legend}<pre class='aln'>{''.join(blocks)}</pre>"


def _sample_block(s: SampleResult, min_q: int, reference: str, viewers: dict) -> str:
    c = s.consensus
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in c.reasons)
    seq = html.escape(_wrap(c.sequence, 80)) if c.sequence else "(no assembly)"
    stats = (f"<p>{c.length} bp &middot; reference {c.ref_start}-{c.ref_end} &middot; mixtures {c.mixtures} "
             f"({c.mixture_fraction:.1%}) &middot; N {c.n_count} &middot; marks {c.marks} &middot; "
             f"longest weak single-coverage run {c.max_single_run}"
             f"{' &middot; indels: ' + html.escape('; '.join(c.indels)) if c.indels else ''}</p>"
             if c.positions else "")
    fasta_label = "Consensus (FASTA)" if c.status == "PASS" else "Assembly (not exported: sample failed)"
    return (
        f"<section><h2>{html.escape(s.sample)} <span class='{c.status}'>{c.status}</span></h2>{stats}"
        f"{'<ul>' + reasons + '</ul>' if reasons else ''}"
        f"{_alignment_view(c, reference)}"
        f"{_read_block('Forward', c.forward, s.forward_chrom, min_q, viewers)}"
        f"{_read_block('Reverse', c.reverse, s.reverse_chrom, min_q, viewers)}"
        f"<details><summary>{fasta_label}</summary><pre>&gt;{html.escape(s.sample)}\n{seq}</pre></details></section>"
    )


_VIEWER_JS = r"""
const COLORS = {A: '#2e7d32', C: '#1565c0', G: '#212121', T: '#c62828'};
function drawViewer(canvas, d, minQ) {
  const n = d.calls.length, hasTr = d.peaks.length === n && n > 1;
  const nPts = hasTr ? d.traces.A.length : n * 6;
  const px = 2, W = Math.max(nPts * px, 200), H = 200;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const g = canvas.getContext('2d'); g.scale(dpr, dpr);
  const peak = i => (hasTr ? d.peaks[i] : i * 6 + 3) * px;
  const top = 34, trH = 118, qTop = 160, qH = 36;
  // shade the trimmed-off ends
  g.fillStyle = 'rgba(128,128,128,0.15)';
  const t0 = d.trim[0] > 0 ? (peak(d.trim[0] - 1) + peak(d.trim[0])) / 2 : 0;
  const t1 = d.trim[1] < n ? (peak(d.trim[1] - 1) + peak(d.trim[1])) / 2 : W;
  g.fillRect(0, 0, t0, H); g.fillRect(t1, 0, W - t1, H);
  if (hasTr) {
    let max = 1;
    const lo = Math.floor(t0 / px), hi = Math.ceil(t1 / px);
    for (const b of 'ACGT') for (let i = lo; i < hi; i++) max = Math.max(max, d.traces[b][i] || 0);
    for (const b of 'ACGT') {
      g.strokeStyle = COLORS[b]; g.lineWidth = 1; g.beginPath();
      d.traces[b].forEach((v, i) => {
        const y = top + trH - Math.min(v / max, 1.05) * trH;
        i ? g.lineTo(i * px, y) : g.moveTo(0, y);
      });
      g.stroke();
    }
  }
  g.textAlign = 'center';
  for (let i = 0; i < n; i++) {
    const x = peak(i), b = d.calls[i];
    g.font = 'bold 11px monospace'; g.fillStyle = COLORS[b] || '#ff6f00';
    g.fillText(b, x, 13);
    if ((i + 1) % 10 === 0) { g.font = '9px sans-serif'; g.fillStyle = '#777'; g.fillText(i + 1, x, 26); }
    const q = d.quals[i] || 0;
    g.fillStyle = q >= minQ ? '#66bb6a' : '#ef5350';
    const h = Math.min(q, 60) / 60 * qH;
    g.fillRect(x - px * 2, qTop + qH - h, px * 4 - 1, h);
  }
  g.strokeStyle = '#999'; g.setLineDash([3, 3]);
  const yq = qTop + qH - minQ / 60 * qH;
  g.beginPath(); g.moveTo(0, yq); g.lineTo(W, yq); g.stroke();
}
document.querySelectorAll('canvas[data-viewer]').forEach(c => drawViewer(c, VIEWERS[c.dataset.viewer], MIN_Q));
"""


def render_html(result: RunResult) -> str:
    st = result.settings
    counts = {k: sum(1 for s in result.samples if s.consensus.status == k) for k in ("PASS", "FAIL")}
    rows = "".join(
        f"<tr><td><a href='#s{i}'>{html.escape(s.sample)}</a></td><td class='{s.consensus.status}'>"
        f"{s.consensus.status}</td><td>{s.consensus.length}</td><td>{s.consensus.mixtures}</td>"
        f"<td>{s.consensus.n_count}</td><td>{s.consensus.marks}</td>"
        f"<td>{html.escape('; '.join(s.consensus.reasons))}</td></tr>"
        for i, s in enumerate(result.samples)
    )
    viewers: dict = {}
    blocks = "".join(f"<a id='s{i}'></a>{_sample_block(s, st.min_quality, result.reference, viewers)}"
                     for i, s in enumerate(result.samples))
    data = json.dumps(viewers, separators=(",", ":")).replace("</", "<\\/")
    extra = ""
    if result.unpaired:
        extra += "<p><b>Files without a forward/reverse tag (or duplicates), not analysed:</b> " + ", ".join(
            html.escape(p.name) for p in result.unpaired) + "</p>"
    if result.errors:
        extra += "<p><b>Unreadable files:</b></p><ul>" + "".join(
            f"<li>{html.escape(k)}: {html.escape(v)}</li>" for k, v in result.errors.items()) + "</ul>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Sanger QC report</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#222;max-width:1100px}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ddd;padding:3px 10px;text-align:left;vertical-align:top}}
.PASS{{color:#1b5e20;font-weight:600}}.FAIL{{color:#b71c1c;font-weight:600}}
section{{border-top:2px solid #eee;margin-top:18px}}.read{{margin:8px 0}}.why{{color:#b71c1c;font-size:90%}}
pre{{background:#f6f6f6;padding:8px;font-size:12px;overflow-x:auto}}small{{color:#666}}
.chrom{{overflow-x:auto;border:1px solid #ddd;background:#fff;margin-top:4px}}.chrom canvas{{display:block}}
.aln{{background:#fbfbfb;line-height:1.5}}.aln .blk{{margin-bottom:6px}}.lbl{{color:#888}}
.diff{{background:#bbdefb}}.mix{{background:#ffe082;font-weight:700}}.n{{background:#ef9a9a}}
.mk{{border-bottom:2px solid #fb8c00}}.legend{{font-size:85%;margin:6px 0}}.legend span{{font-family:monospace;padding:0 3px}}
</style></head><body>
<h1>Sanger QC report</h1>
<p>sanger_seq_analyzer {__version__} &middot; {datetime.now():%Y-%m-%d %H:%M}</p>
<p><small>Read rule: &ge; {st.min_good_fraction:.0%} of bases &ge; Q{st.min_quality}, trimmed length &ge;
{st.min_trimmed_length} bp{', forward and reverse both required' if st.require_both_directions else ''}.
RECall rules (Woods et al. 2012): bases &lt; Q{st.censor_quality} censored; mixture area &ge; {st.mixture_area:.1%};
mark area &ge; {st.mark_area:.1%}; mark average quality &lt; Q{st.mark_avg_quality}. Fail on stop codon, frameshift
insert/deletion, mixtures &gt; {st.max_mixture_fraction:.1%}, N &ge; {st.max_n + 1}, marks &ge; {st.max_marks + 1},
&gt; {st.max_single_coverage_run} consecutive single-coverage bases &lt; Q{st.single_coverage_quality},
or no usable coverage.</small></p>
<p><span class='PASS'>PASS {counts['PASS']}</span> &middot; <span class='FAIL'>FAIL {counts['FAIL']}</span></p>
<table><tr><th>Sample</th><th>Status</th><th>Length</th><th>Mixtures</th><th>N</th><th>Marks</th><th>Reasons</th></tr>
{rows}</table>
{extra}{blocks}
<p><small>Chromatograms: A green, C blue, G black, T red; bars show each base's quality (dashed line = Q{st.min_quality});
grey = trimmed ends. Scroll sideways to see the whole read.</small></p>
<script>const VIEWERS = {data}; const MIN_Q = {st.min_quality};{_VIEWER_JS}</script></body></html>"""
