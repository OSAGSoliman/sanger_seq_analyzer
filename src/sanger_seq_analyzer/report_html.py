"""The self-contained HTML report (design spec section 4).

All wording comes from :mod:`presentation`, so the report says exactly what the
window says. The page needs no network: styles and the trace viewer are inline
(:mod:`report_assets`), and trace data is embedded as JSON, downsampled to about
six points per base. Canvases are drawn when they scroll into view.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from html import escape

import numpy as np

from . import presentation as pr
from .pipeline import RunResult, SampleResult
from .qc import QCSettings
from .report_assets import CSS, JS

ICONS = {
    "PASS": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>',
    "FAIL": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>',
    "MISSING": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 8h7"/></svg>',
}
POINTS_PER_BASE = 6
TRACE_SCALE = 250  # traces are stored as bytes, 250 = the kept region's maximum (base64 in the page)
FLAGGED_LINKS = 12
BLOCK = 60


def e(text) -> str:
    return escape(str(text), quote=True)


def badge(status: str, big: bool = False) -> str:
    return f'<span class="badge b-{status}{" big" if big else ""}">{ICONS[status]}{e(status)}</span>'


class _Ids:
    """Unique, URL-safe element ids for sample names."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def __call__(self, name: str) -> str:
        base = "s-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "sample")
        out, k = base, 1
        while out in self.used:
            k += 1
            out = f"{base}-{k}"
        self.used.add(out)
        return out


# --- header, rules, summary ----------------------------------------------------------------------------

def _header(result: RunResult) -> str:
    ref = result.reference_info
    when = result.finished or result.started or datetime.now()
    s = result.settings
    parts = [f'Reference <span class="mono">{e(ref.name if ref else "custom")}</span>']
    desc = ", ".join(x for x in [ref.description if ref else "", f"{len(result.reference)} bp",
                                 f"{len(result.reference) // 3} codons"
                                 if s.check_stop_codons or s.check_frameshifts else ""] if x)
    parts.append(e(desc))
    if result.inputs:
        names = ", ".join(f'<span class="mono" title="{e(p)}">{e(p.name or p)}</span>' for p in result.inputs)
        parts.append(f"input {'folder' if len(result.inputs) == 1 else 'folders'} {names}")
    cancelled = (f' <span class="chip">cancelled: {len(result.samples)} of {result.planned} analysed</span>'
                 if result.cancelled else "")
    return (f'<header class="top"><div><div class="caps">sanger_seq_analyzer {e(result.version)} · '
            f'{when:%Y-%m-%d %H:%M}</div><h1>Sanger QC report{cancelled}</h1><p class="sub">{" · ".join(parts)}</p>'
            f'</div><div class="counts">{_count_badge("PASS", result.count("PASS"))}'
            f'{_count_badge("FAIL", result.count("FAIL"))}<span class="sub">of '
            f'{pr.plural(len(result.samples), "sample")}</span></div></header>')


def _count_badge(status: str, n: int) -> str:
    return f'<span class="badge big b-{status}">{ICONS[status]}{n} {status}</span>'


def _rules(s: QCSettings) -> str:
    cols = []
    for title, rows in pr.rules_used(s):
        items = "".join(f'<div class="kv"><span class="sub">{e(r.label)}</span><b>{e(r.value)}'
                        f'{"<span class=chip>changed</span>" if r.changed else ""}</b></div>' for r in rows)
        label = title + (" · Woods et al. 2012" if title.startswith("RECall") else "")
        cols.append(f'<div><div class="caps">{e(label)}</div>'
                    f'{"<div class=grid>" + items + "</div>" if len(rows) > 4 else items}</div>')
    return f'<section class="card rules" aria-label="Rules used">{"".join(cols)}</section>'


def _summary(result: RunResult, ids: dict[str, str]) -> str:
    head = [("Sample", "text", ""), ("Status", "text", ""), ("Length", "num", "r"), ("Mixtures", "num", "r"),
            ("N", "num", "r"), ("Marks", "num", "r"), ("Reason", "text", "")]
    th = "".join(f'<th data-type="{t}" class="caps {cls}"><button type="button">{e(h)}</button></th>'
                 for h, t, cls in head)
    rows = []
    for sr in result.samples:
        c = sr.consensus
        reason = "; ".join(c.reasons)
        prov = ' title="display only: a lab read rule failed"' if c.provisional else ""
        rows.append(
            f'<tr data-status="{c.status}"><td data-v="{e(sr.sample)}">'
            f'<a href="#{ids[sr.sample]}">{e(sr.sample)}</a></td>'
            f'<td data-v="{c.status}">{badge(c.status)}</td>'
            f'<td class="r num" data-v="{c.length}"{prov}>{c.length}</td>'
            f'<td class="r num" data-v="{c.mixtures}"{prov}>{c.mixtures}</td>'
            f'<td class="r num" data-v="{c.n_count}"{prov}>{c.n_count}</td>'
            f'<td class="r num" data-v="{c.marks}"{prov}>{c.marks}</td>'
            f'<td class="sub" data-v="{e(reason)}">{e(reason) or "–"}</td></tr>')
    skipped = pr.skipped_rows(result)
    groups = []
    for kind, label in (("skipped", "Files without a forward/reverse tag, or duplicates"),
                        ("unreadable", "unreadable files")):
        names = [f'<span class="mono" title="{e(r.cause)}">{e(r.name)}</span> ({e(r.cause)})'
                 for r in skipped if r.kind == kind]
        groups.append(f"{label}: {', '.join(names) if names else 'none'}")
    tools = ('<div class="tools"><input id="filter-text" type="search" placeholder="Filter samples" '
             'aria-label="Filter samples"><select id="filter-status" aria-label="Status"><option value="">All</option>'
             '<option value="PASS">Pass</option><option value="FAIL">Fail</option></select>'
             '<span class="hint">Click a column title to sort.</span></div>')
    return (f'<section class="card summary" aria-label="Sample summary">{tools}<table class="st" id="summary">'
            f'<thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="foot hint">{" · ".join(groups)}</div></section>')


# --- one sample ----------------------------------------------------------------------------------------

def _reason_box(sr: SampleResult, s: QCSettings) -> str:
    views = pr.reason_views(sr, s)
    if not views:
        return ""
    items = "".join(f'<div class="item"><h3>{e(v.title)}</h3><div class="caps fam">{e(v.family)}</div>'
                    f'<div class="ex">{e(v.explanation)}</div><div class="hn">{e(v.hint)}</div></div>'
                    for v in views)
    return f'<div class="reason"><span class="disc">{ICONS["FAIL"]}</span><div class="items">{items}</div></div>'


def _tiles(sr: SampleResult, s: QCSettings, ref_len: int) -> str:
    out = []
    for t in pr.metric_tiles(sr, s, ref_len):
        cls = "tile" + (" bad" if t.failed else "") + (" prov" if t.provisional else "")
        tip = f' title="{e(t.tooltip)}"' if t.tooltip else ""
        suffix = f" <small>{e(t.suffix)}</small>" if t.suffix else ""
        out.append(f'<div class="{cls}"{tip}><span class="caps">{e(t.label)}</span>'
                   f'<span class="v num">{e(t.value)}{suffix}</span></div>')
    return f'<div class="tiles">{"".join(out)}</div>'


def _cell_class(col: pr.Column) -> str:
    f = col.flags
    cls = ("s" if "stop" in f else "n" if "n" in f else "m" if "mixture" in f else "d" if "diff" in f
           else "g" if "deletion" in f else "")
    if col.marks:
        cls += " k"
    return cls.strip()


def _alignment_blocks(sr: SampleResult, data: pr.AlignmentData, s: QCSettings, anchors: dict[int, str]) -> str:
    blocks = []
    cols = data.columns
    for start in range(0, len(cols), BLOCK):
        chunk = cols[start:start + BLOCK]
        first = next((c.ref_number for c in chunk if c.ref_number), "")
        ruler, ref_row, cons_row = [], [], []
        for col in chunk:
            band = ""
            if data.coding and col.codon:
                band = "ca" if col.codon % 2 else "cb"
            num = f"<i>{col.ref_number}</i>" if col.ref_number and col.ref_number % 10 == 0 else ""
            ruler.append(f'<span class="{band}">{num}</span>' if band or num else "<span></span>")
            ref_row.append('<span class="g">-</span>' if col.ref_number is None else f"<span>{e(col.ref_base)}</span>")
            cls = _cell_class(col)
            strong = col.flagged_strong or col.marks
            title = ("\n".join(pr.tooltip_lines(col, data, s, sr.consensus)) if strong else
                     f"{pr.tooltip_lines(col, data, s)[0]} · {col.cons_base} · {pr.plural(col.coverage, 'read')} · "
                     f"mean Q{pr.fmt_q(col.mean_quality)}")
            ident = f' id="{anchors[col.index]}"' if col.index in anchors else ""
            cls_attr = f' class="{cls}"' if cls else ""
            cons_row.append(f'<span{cls_attr}{ident} data-col="{col.index}" title="{e(title)}">'
                            f'{e(col.cons_base)}</span>')
        blocks.append(f'<div class="blk"><div class="al ru"><span class="lab"></span>{"".join(ruler)}</div>'
                      f'<div class="al rr"><span class="lab">Ref <span class="num">{first}</span></span>'
                      f'{"".join(ref_row)}</div><div class="al cr"><span class="lab">Cons</span>'
                      f'{"".join(cons_row)}</div></div>')
    return "".join(blocks)


def _legend(data: pr.AlignmentData) -> str:
    items = [('<span class="sw d">A</span>differs from reference'), ('<span class="sw m">Y</span>mixture'),
             ('<span class="sw n">N</span>no call'), ('<span class="sw g">-</span>deletion'),
             ('<span class="sw k">A</span>marked')]
    if data.stops:
        items.append('<span class="sw s">T</span>stop codon')
    if data.coding:
        items.append("codon bands under the ruler")
    items.append("Hover a base for details; click it to scroll both traces there.")
    return "".join(f"<span>{i}</span>" for i in items)


def _alignment(sr: SampleResult, data: pr.AlignmentData, s: QCSettings, anchors: dict[int, str]) -> str:
    c = sr.consensus
    if not data.columns:
        return ""
    body = (f'<div class="alhead hint"><b>Alignment</b>{_legend(data)}</div>'
            f'<div class="aln">{_alignment_blocks(sr, data, s, anchors)}</div>')
    if not c.provisional:
        return f"<div>{body}</div>"
    reads = [pr.LABEL[a.direction] for a in c.aligned]
    who = f"the {reads[0]} read only" if len(reads) == 1 else "both reads"
    single = sum(1 for p in c.positions if "single coverage" in p.marks)
    extra = (f", every position single coverage ({pr.plural(c.marks, 'mark')})" if single == len(c.positions)
             else f", {pr.plural(c.marks, 'marked position')}")
    return (f"<details><summary>Alignment of {who} · {c.length} bp{extra} (display only: a lab read rule "
            f"failed)</summary><div class=\"in\">{body}</div></details>")


def _pack(values: np.ndarray) -> str:
    """Trace values as base64 bytes (0-255), about half the size of a JSON number list."""
    return base64.b64encode(np.clip(np.round(values), 0, 255).astype(np.uint8).tobytes()).decode("ascii")


def _trace_data(sr: SampleResult, direction: str, data: pr.AlignmentData) -> dict:
    """Compact per-read data for the canvas viewer."""
    chrom = sr.chrom(direction)
    read = sr.consensus.reads.get(direction)
    n = len(chrom)
    aligned = next((a for a in sr.consensus.aligned if a.direction == direction), None)
    trim = [read.trim_start, read.trim_end] if read else [0, n]
    out = {"calls": chrom.sequence, "q": [int(q) for q in chrom.qualities], "trim": trim,
           "rc": bool(aligned and aligned.strand == "-"), "pk": [], "tr": {}, "trimPts": [0, 0]}
    if chrom.has_traces and len(chrom.peak_locations) == n and n > 1:
        peaks = chrom.peak_locations.astype(float)
        spacing = max(1.0, float(peaks[-1] - peaks[0]) / (n - 1))
        step = max(1, int(round(spacing / POINTS_PER_BASE)))
        lo_raw, hi_raw = (trim if trim[1] > trim[0] else [0, n])
        lo = max(0.0, (peaks[lo_raw] - spacing / 2) / step)
        hi = (peaks[hi_raw - 1] + spacing / 2) / step
        ds = {b: chrom.traces[b][::step].astype(float) for b in "ACGT"}
        kept = max(1.0, max(float(v[int(lo):int(hi) + 1].max(initial=0)) for v in ds.values()))
        scale = TRACE_SCALE / kept
        out["tr"] = {b: _pack(v * scale) for b, v in ds.items()}
        out["pk"] = [round(p / step, 1) for p in peaks]
        out["trimPts"] = [round(lo, 1), round(hi, 1)]
    # alignment column -> base index (a gap points half-way between its neighbours)
    cols = []
    for col in data.columns:
        cell = col.reads.get(direction)
        if cell is None:
            cols.append(None)
        elif cell.raw is not None:
            cols.append(cell.raw)
        elif None not in cell.between:
            cols.append((cell.between[0] + cell.between[1]) / 2)
        else:
            cols.append(cell.between[0] if cell.between[0] is not None else cell.between[1])
    out["cols"] = cols
    if aligned is not None:
        ref_of = {raw: data.columns[c].ref_number for raw, c in data.raw_to_col.get(direction, {}).items()}
        out["rul"] = [[raw, num] for raw, num in sorted(ref_of.items()) if num and num % 10 == 0]
    else:
        out["rul"] = [[i, i + 1] for i in range(9, n, 10)]
    return out


def _read_box(sr: SampleResult, direction: str, s: QCSettings, data: pr.AlignmentData, viewers: dict
              ) -> tuple[str, str]:
    """One read's box and its state: "ok", "bad", "missing" or "unreadable"."""
    v = pr.read_view(sr, direction, s)
    name = f'<span class="mono hint">{e(v.file_name)}{" · " + e(v.orientation) if v.orientation else ""}</span>'
    head = f'<b>{e(v.label)}</b>{badge(v.status)}{name}'
    problems = "".join(f'<div class="prob">{e(p)}</div>' for p in v.problems)
    if v.state in ("missing", "unreadable"):
        return f'<div class="read bad"><div class="rd">{head}</div>{problems}</div>', v.state
    pct = min(100.0, v.good_fraction * 100)
    meter = (f'<div class="kvv"><span class="hint">Bases ≥ Q{v.min_quality} (cutoff {v.cutoff:.0%})</span>'
             f'<b class="num{" bad-t" if v.meter_failed else ""}">{e(v.meter_text)}</b>'
             f'<div class="meter{" low" if v.meter_failed else ""}"><span style="width:{pct:.1f}%"></span>'
             f'<i style="left:calc({v.cutoff * 100:.1f}% - 1px)"></i></div></div>')
    rows = "".join(f'<div class="kvv"><span class="hint">{e(k)}</span><b class="num">{e(val)}</b></div>'
                   for k, val in v.rows)
    vid = f"v{len(viewers)}"
    viewers[vid] = _trace_data(sr, direction, data)
    notice = f'<div class="notice">{e(v.notice)}</div>' if v.notice else ""
    bad = v.status == "FAIL" or v.state == "not_assembled"
    box = (f'<div class="read{" bad" if bad else ""}"><div class="rd">{head}<span class="grow"></span>{meter}{rows}'
           f'</div>{problems}{notice}<div class="trace-scroll"><canvas data-v="{vid}" width="1" height="1" '
           f'aria-label="{e(v.label)} chromatogram"></canvas></div></div>')
    return box, ("bad" if bad else "ok")


def _sample(sr: SampleResult, result: RunResult, sid: str, viewers: dict) -> str:
    s = result.settings
    c = sr.consensus
    data = pr.alignment(sr, s)
    anchors = {i: f"{sid}-c{i}" for i in data.flagged}
    included = "Included in consensus.fasta" if c.status == "PASS" else "Not written to consensus.fasta"
    flagged = ""
    if data.flagged:
        links = []
        for i in data.flagged[:FLAGGED_LINKS]:
            col = data.columns[i]
            label = col.ref_number if col.ref_number else f"ins {col.anchor_number}"
            links.append(f'<a href="#{anchors[i]}" data-col="{i}">{e(label)}</a>')
        more = f" +{len(data.flagged) - FLAGGED_LINKS} more" if len(data.flagged) > FLAGGED_LINKS else ""
        flagged = f'<span class="hint">Flagged positions: {" · ".join(links)}{more}</span>'
    reads = []
    for direction in ("F", "R"):
        box, state = _read_box(sr, direction, s, data, viewers)
        if c.provisional and state == "ok":
            v = pr.read_view(sr, direction, s)
            box = (f"<details><summary>{e(v.label)} read · {e(v.status)} · {e(v.file_name)} (chromatogram)"
                   f"</summary>{box}</details>")
        reads.append(box)
    fasta = pr.fasta_text(sr)
    if fasta:
        summary = (f"Consensus (FASTA) · {c.length} bp" if c.status == "PASS"
                   else f"Assembly (not exported: sample failed) · {c.length} bp")
        fasta = f'<details><summary>{e(summary)}</summary><div class="in"><pre>{e(fasta)}</pre></div></details>'
    return (f'<section class="card sample" id="{sid}" aria-label="{e(sr.sample)}"><div class="shead">'
            f'<h2>{e(sr.sample)}</h2>{badge(c.status, big=True)}<span class="sub">{included}</span>'
            f'<span class="grow"></span>{flagged}</div>{_reason_box(sr, s)}'
            f'{_tiles(sr, s, len(result.reference))}{_alignment(sr, data, s, anchors)}{"".join(reads)}{fasta}'
            f'</section>')


def render_html(result: RunResult) -> str:
    """The whole report as one self-contained HTML page."""
    s = result.settings
    make_id = _Ids()
    ids = {sr.sample: make_id(sr.sample) for sr in result.samples}
    viewers: dict = {}
    sections = "".join(_sample(sr, result, ids[sr.sample], viewers) for sr in result.samples)
    payload = json.dumps({"minQ": s.min_quality, "reads": viewers}, separators=(",", ":")).replace("</", "<\\/")
    legend = (f"Chromatograms: A green, C blue, G black (light grey in dark mode), T red; bars show each base's "
              f"quality, the dashed line is Q{s.min_quality}; grey shading = trimmed ends. Reads on the minus "
              f"strand are shown reverse-complemented so both read left to right. Scroll sideways to see the "
              f"whole read. When printed, each sample starts on a new page. {pr.rule_summary(s)}")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Sanger QC report</title><style>{CSS}</style></head><body><main class="page">'
            f'{_header(result)}{_rules(s)}{_summary(result, ids)}{sections}'
            f'<footer class="legend">{e(legend)}</footer></main>'
            f'<script type="application/json" id="trace-data">{payload}</script><script>{JS}</script>'
            f'</body></html>')
