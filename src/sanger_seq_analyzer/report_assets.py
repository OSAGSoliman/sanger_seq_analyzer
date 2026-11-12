"""Stylesheet and script embedded in ``report.html`` (design spec 4.2).

The colour variables are generated from :mod:`design_tokens`, so the report uses
exactly the app's palette, light and dark. Nothing is loaded from the network.
"""

from __future__ import annotations

from .design_tokens import palette


def _variables(theme: str) -> str:
    return "".join(f"--{key.replace('.', '-')}:{value};" for key, value in palette(theme).items())


CSS_BODY = r"""
*{box-sizing:border-box}
:root{--font-ui:"Segoe UI Variable","Segoe UI",system-ui,-apple-system,"Helvetica Neue",sans-serif;
--font-mono:"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace}
body{margin:0;background:var(--bg-window);color:var(--text-primary);font:13px/1.45 var(--font-ui)}
a{color:var(--accent)}a:hover{color:var(--accent-hover)}
.page{max-width:1052px;margin:0 auto;padding:24px;display:flex;flex-direction:column;gap:16px}
.card{background:var(--bg-surface);border:1px solid var(--border-default);border-radius:8px}
.mono{font-family:var(--font-mono)}.num{font-variant-numeric:tabular-nums}
.caps{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--text-tertiary)}
.sub{color:var(--text-secondary)}.hint{color:var(--text-tertiary);font-size:12px}
.badge{display:inline-flex;align-items:center;gap:4px;height:20px;padding:0 8px 0 6px;border-radius:10px;
font-size:11px;font-weight:700;letter-spacing:.03em;white-space:nowrap;border:1px solid}
.badge svg{width:12px;height:12px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.badge.big{height:26px;border-radius:13px;font-size:13px;padding:0 12px 0 8px}.badge.big svg{width:14px;height:14px}
.b-PASS{background:var(--pass-bg);color:var(--pass-fg);border-color:var(--pass-border)}
.b-FAIL{background:var(--fail-bg);color:var(--fail-fg);border-color:var(--fail-border)}
.countf{font-family:inherit;cursor:pointer}.countf[aria-pressed=true]{outline:2px solid currentColor;outline-offset:2px}
.b-MISSING{background:var(--bg-window);color:var(--text-secondary);border-color:var(--border-default)}
.chip{display:inline-flex;align-items:center;height:18px;padding:0 6px;border-radius:4px;font-size:11px;
background:var(--warn-bg);color:var(--warn-fg);border:1px solid var(--warn-border);margin-left:6px}
header.top{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;
padding-bottom:14px;border-bottom:2px solid var(--text-primary)}
header.top h1{margin:2px 0 0;font-size:26px;font-weight:600;letter-spacing:-.015em}
header.top p{margin:4px 0 0}.counts{display:flex;align-items:center;gap:8px}
.rules{padding:12px 14px;display:flex;flex-wrap:wrap;gap:8px 32px}.rules>div{flex:1 1 300px;min-width:0}
.rules>div+div{flex:2 1 520px}.rules .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 24px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:3px 0;border-bottom:1px solid var(--border-subtle);font-size:12px}
.kv b{font-weight:600;white-space:nowrap}
.summary{overflow-x:auto}.tools{display:flex;gap:8px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--border-default)}
.tools input,.tools select{height:28px;border:1px solid var(--border-strong);border-radius:6px;padding:0 8px;
background:var(--bg-surface);color:var(--text-primary);font:inherit}
table.st{width:100%;border-collapse:collapse;min-width:760px}
table.st th{background:var(--bg-sunken);border-bottom:1px solid var(--border-default);height:32px;padding:0 14px;text-align:left}
table.st th button{all:unset;cursor:pointer}table.st th button:focus-visible{outline:2px solid var(--accent)}
table.st th[aria-sort=ascending] button::after{content:" \25B2";font-size:9px}
table.st th[aria-sort=descending] button::after{content:" \25BC";font-size:9px}
table.st td{border-bottom:1px solid var(--border-subtle);padding:6px 14px;height:34px;vertical-align:middle}
table.st .r{text-align:right}table.st td a{font-weight:600}.foot{padding:8px 14px}
section.sample{padding:14px 12px;display:flex;flex-direction:column;gap:12px}
.shead{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.shead h2{margin:0;font-size:20px;font-weight:600}
.grow{flex-grow:1}
.reason{background:var(--fail-bgSoft);border:1px solid var(--fail-border);border-radius:8px;padding:12px 14px;
display:flex;gap:12px}
.disc{flex:none;width:28px;height:28px;border-radius:14px;background:var(--fail-solid);display:flex;align-items:center;justify-content:center}
.disc svg{width:14px;height:14px;fill:none;stroke:#fff;stroke-width:2.4;stroke-linecap:round}
.reason .items{flex:1;min-width:0}.reason .item+.item{border-top:1px solid var(--fail-border);margin-top:10px;padding-top:10px}
.reason h3{margin:0;font-size:15px;font-weight:600;color:var(--fail-title)}
.reason .fam{color:var(--fail-fg);margin:1px 0 4px}.reason .ex{color:var(--fail-text)}.reason .hn{color:var(--fail-hint);font-size:12px;margin-top:2px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:1px;background:var(--border-default);
border:1px solid var(--border-default);border-radius:8px;overflow:hidden}
.tile{background:var(--bg-surface);padding:9px 12px;display:flex;flex-direction:column;gap:1px;min-width:0}
.tile .v{font-size:18px;font-weight:600;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .v small{font-size:12px;font-weight:500;color:var(--text-secondary)}
.tile.bad{background:var(--fail-bgSoft)}.tile.bad .caps,.tile.bad .v,.tile.bad .v small{color:var(--fail-fg)}
.tile.prov .v,.tile.prov .v small{color:var(--text-disabled)}
.alhead{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}.alhead b{color:var(--text-primary);font-size:13px}
.sw{display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;font-size:12px;border-radius:2px;
margin-right:4px;font-family:var(--font-mono)}
.aln{overflow-x:auto;font-family:var(--font-mono)}.blk{margin-bottom:8px;width:max-content}
.al{display:flex;height:18px;align-items:stretch}.al.ru{height:14px}
.lab{width:60px;flex:none;font-size:11px;color:var(--text-tertiary);line-height:18px;font-family:var(--font-ui)}
.al span:not(.lab){width:16px;flex:none;text-align:center;font-size:13px;line-height:18px}
.rr span:not(.lab){color:var(--text-tertiary)}.cr span:not(.lab){cursor:pointer}
.cr span:not(.lab):hover{background:var(--bg-hover)}
.ru span{position:relative;border-bottom:3px solid transparent}.ru .ca{border-bottom-color:var(--hl-codon-a)}
.ru .cb{border-bottom-color:var(--hl-codon-b)}
.ru i{position:absolute;right:1px;top:-3px;font:10px var(--font-ui);color:var(--text-tertiary);font-style:normal;white-space:nowrap}
.d,.sw.d{background:var(--hl-diff-bg);color:var(--hl-diff-fg)!important;font-weight:700}
.m,.sw.m{background:var(--hl-mix-bg);color:var(--hl-mix-fg)!important;font-weight:700}
.n,.sw.n{background:var(--hl-n-bg);color:var(--hl-n-fg)!important;font-weight:700}
.g,.sw.g{background:var(--hl-gap-bg);color:var(--hl-gap-fg)!important}
.k,.sw.k{box-shadow:inset 0 -2px 0 var(--hl-mark)}
.s,.sw.s{background:var(--hl-stop-bg);color:var(--hl-stop-fg)!important;font-weight:700;
box-shadow:inset 0 2px 0 var(--hl-stop-rule),inset 0 -2px 0 var(--hl-stop-rule)}
.s.k{box-shadow:inset 0 2px 0 var(--hl-stop-rule),inset 0 -2px 0 var(--hl-mark)}
.rsel{background:var(--accent-subtle);color:var(--text-primary)!important}
.sel{outline:2px solid var(--accent);outline-offset:-1px;border-radius:2px}
.read{border:1px solid var(--border-default);border-radius:8px;overflow:hidden}
.read.bad{border-color:var(--fail-border)}.read.bad .rd{background:var(--fail-bgSoft)}
.rd{display:flex;align-items:center;gap:8px 20px;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid var(--border-default)}
.rd .kvv{display:flex;flex-direction:column}.rd .kvv b{font-weight:600}
.meter{position:relative;width:120px;height:6px;border-radius:3px;background:var(--track);margin:4px 0 2px}
.meter>span{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--pass-solid)}
.meter.low>span{background:var(--fail-bar)}
.meter>i{position:absolute;top:-3px;width:2px;height:12px;background:var(--text-primary)}
.bad-t{color:var(--fail-fg)}.prob{padding:6px 12px;color:var(--fail-fg);font-size:12px}
.notice{padding:6px 12px;background:var(--warn-bg);color:var(--warn-fg);font-size:12px}
.trace-scroll{overflow-x:auto;padding:6px 0 0;background:var(--bg-surface)}.trace-scroll canvas{display:block}
.placeholder{padding:16px 12px;color:var(--text-secondary);text-align:center;background:var(--bg-sunken)}
details{border:1px solid var(--border-default);border-radius:6px}
details>summary{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-sunken);
color:var(--text-secondary);cursor:pointer;border-radius:6px}
details[open]>summary{border-bottom:1px solid var(--border-default);border-radius:6px 6px 0 0}
details>.in{padding:8px 12px}details pre{margin:0;font:12px/1.5 var(--font-mono);white-space:pre-wrap;word-break:break-all}
details .read{border:0;border-radius:0}
footer.legend{color:var(--text-tertiary);font-size:12px;padding:4px 0 24px}
@media print{
 body{background:#fff}.page{max-width:none;padding:0}.card{border-color:#999;break-inside:avoid}
 section.sample{break-before:page;break-inside:auto}.tools{display:none}
 details{display:block}details>summary{display:none}.trace-scroll,.aln{overflow:visible}
 .al span:not(.lab){font-size:9pt}body{font-size:10pt}
}
"""

CSS = (":root{" + _variables("light") + "}\n"
       "@media (prefers-color-scheme:dark){:root{" + _variables("dark") + "}}\n" + CSS_BODY)

JS = r"""
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("trace-data").textContent);
  var PX = 14, LET = 18, RUL = 12, TH = 110, QH = 30, GAP = 4;
  var H = LET + RUL + TH + GAP + QH;
  var COMP = {A: "T", C: "G", G: "C", T: "A", R: "Y", Y: "R", K: "M", M: "K", S: "S", W: "W", N: "N"};
  var drawn = {}, state = {};
  function unpack(d) {  // traces travel as base64 bytes
    if (d.unpacked) { return; }
    ["A", "C", "G", "T"].forEach(function (b) {
      if (typeof d.tr[b] !== "string") { return; }
      var s = atob(d.tr[b]), a = new Uint8Array(s.length);
      for (var i = 0; i < s.length; i++) { a[i] = s.charCodeAt(i); }
      d.tr[b] = a;
    });
    d.unpacked = true;
  }
  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim();
  }
  function geometry(d) {
    unpack(d);
    var n = d.calls.length, hasTr = d.pk.length === n && n > 1;
    var ppb = hasTr ? Math.max(1e-6, (d.pk[n - 1] - d.pk[0]) / (n - 1)) : 1;
    var sx = hasTr ? PX / ppb : PX;
    var W = Math.ceil(hasTr ? d.tr.A.length * sx : n * PX) + PX;
    function raw(i) { return hasTr ? d.pk[i] * sx : i * PX + PX / 2; }
    function px(i) {  // fractional base index (gap = midpoint) -> canvas x in display orientation
      var lo = Math.max(0, Math.min(n - 1, Math.floor(i))), hi = Math.min(n - 1, lo + 1);
      var x = raw(lo) + (raw(hi) - raw(lo)) * (i - lo);
      return d.rc ? W - x : x;
    }
    return {n: n, hasTr: hasTr, sx: sx, W: W, px: px};
  }
  function draw(canvas) {
    var id = canvas.getAttribute("data-v"), d = DATA.reads[id], g = geometry(d);
    var dpr = Math.min(window.devicePixelRatio || 1, 2, 32000 / g.W);
    canvas.width = Math.floor(g.W * dpr); canvas.height = Math.floor(H * dpr);
    canvas.style.width = g.W + "px"; canvas.style.height = H + "px";
    var c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, g.W, H);
    var top = LET + RUL, qTop = top + TH + GAP, minQ = DATA.minQ, n = g.n;
    var col = {A: css("base-A"), C: css("base-C"), G: css("base-G"), T: css("base-T"), N: css("base-N")};
    // quality row ground
    c.fillStyle = css("bg-sunken"); c.fillRect(0, qTop, g.W, QH);
    // selected position and stop codon bands
    var sel = state[id];
    if (sel !== undefined && sel !== null) {
      var sxp = g.px(sel);
      c.fillStyle = css("chrom-sel-fill"); c.fillRect(sxp - PX / 2, 0, PX, H);
      c.strokeStyle = css("chrom-sel-edge"); c.lineWidth = 1;
      c.strokeRect(Math.round(sxp - PX / 2) + 0.5, 0.5, PX - 1, H - 1);
    }
    // traces, darker G first so it sits behind
    if (g.hasTr) {
      var lo = Math.max(0, Math.floor(d.trimPts[0])), hi = Math.min(d.tr.A.length, Math.ceil(d.trimPts[1]));
      var max = 1;
      ["A", "C", "G", "T"].forEach(function (b) {
        for (var i = lo; i < hi; i++) { if (d.tr[b][i] > max) { max = d.tr[b][i]; } }
      });
      c.lineWidth = 1.25; c.lineJoin = "round";
      ["G", "A", "T", "C"].forEach(function (b) {
        var shown = d.rc ? COMP[b] : b, ys = d.tr[b];
        c.strokeStyle = col[shown]; c.beginPath();
        for (var i = 0; i < ys.length; i++) {
          var x = i * g.sx; x = d.rc ? g.W - x : x;
          var y = top + TH - Math.min(ys[i] / max, 1.02) * (TH - 6);
          if (i) { c.lineTo(x, y); } else { c.moveTo(x, y); }
        }
        c.stroke();
      });
    }
    // letters, ruler and quality bars
    c.textAlign = "center"; c.textBaseline = "alphabetic";
    var mono = css("font-mono") || "monospace", ui = css("font-ui") || "sans-serif";
    var good = css("chrom-qual-good"), bad = css("chrom-qual-bad"), bw = Math.max(2, 0.57 * PX);
    for (var i = 0; i < n; i++) {
      var x = g.px(i), b = d.rc ? (COMP[d.calls[i]] || d.calls[i]) : d.calls[i];
      c.font = "700 12px " + mono; c.fillStyle = col[b] || col.N; c.fillText(b, x, 13);
      var q = d.q[i] || 0, h = Math.min(q, 60) / 60 * (QH - 2);
      c.fillStyle = q >= minQ ? good : bad; c.fillRect(x - bw / 2, qTop + QH - h, bw, h);
    }
    c.font = "9px " + ui; c.fillStyle = css("text-tertiary");
    d.rul.forEach(function (r) { c.fillText(String(r[1]), g.px(r[0]), LET + RUL - 2); });
    c.strokeStyle = css("chrom-qual-line"); c.setLineDash([3, 3]); c.lineWidth = 1;
    var yq = Math.round(qTop + QH - minQ / 60 * (QH - 2)) + 0.5;
    c.beginPath(); c.moveTo(0, yq); c.lineTo(g.W, yq); c.stroke();
    // trimmed ends
    var t0 = d.trim[0] > 0 ? g.px(d.trim[0] - 0.5) : null, t1 = d.trim[1] < n ? g.px(d.trim[1] - 0.5) : null;
    c.fillStyle = css("chrom-trim-fill"); c.strokeStyle = css("chrom-trim-edge");
    [[t0, d.rc ? g.W : 0], [t1, d.rc ? 0 : g.W]].forEach(function (p) {
      if (p[0] === null) { return; }
      var a = Math.min(p[0], p[1]), w = Math.abs(p[1] - p[0]);
      c.fillRect(a, 0, w, H);
      c.beginPath(); c.moveTo(Math.round(p[0]) + 0.5, 0); c.lineTo(Math.round(p[0]) + 0.5, H); c.stroke();
      if (w > 60) { c.fillStyle = css("text-secondary"); c.font = "10px " + ui; c.fillText("trimmed", a + w / 2, qTop - 4);
        c.fillStyle = css("chrom-trim-fill"); }
    });
    c.setLineDash([]);
    drawn[id] = true;
  }
  var canvases = Array.prototype.slice.call(document.querySelectorAll("canvas[data-v]"));
  if (typeof IntersectionObserver === "function") {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); draw(e.target); } });
    }, {rootMargin: "300px"});
    canvases.forEach(function (cv) { io.observe(cv); });
  } else {
    canvases.forEach(draw);
  }
  // click a base in an alignment: select it and scroll that sample's traces there
  function select(section, column) {
    Array.prototype.forEach.call(section.querySelectorAll(".sel,.rsel"), function (el) {
      el.classList.remove("sel"); el.classList.remove("rsel");
    });
    var cell = section.querySelector('.cr [data-col="' + column + '"]');
    if (cell) {
      cell.classList.add("sel");
      var ref = cell.parentNode.previousElementSibling;
      var idx = Array.prototype.indexOf.call(cell.parentNode.children, cell);
      if (ref && ref.children[idx]) { ref.children[idx].classList.add("rsel"); }
    }
    Array.prototype.forEach.call(section.querySelectorAll("canvas[data-v]"), function (cv) {
      var id = cv.getAttribute("data-v"), d = DATA.reads[id], raw = d.cols[column];
      state[id] = raw === null || raw === undefined ? null : raw;
      if (state[id] === null) { if (drawn[id]) { draw(cv); } return; }
      draw(cv);
      var box = cv.parentNode, x = geometry(d).px(state[id]);
      box.scrollLeft = Math.max(0, x - box.clientWidth / 2);
    });
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target, section = t.closest ? t.closest("section.sample") : null;
    if (!section) { return; }
    var colAttr = t.getAttribute && t.getAttribute("data-col");
    if (colAttr !== null && colAttr !== undefined && colAttr !== "") { select(section, Number(colAttr)); }
  });
  // summary table: sort and filter
  var table = document.getElementById("summary");
  if (table) {
    var body = table.tBodies[0], text = document.getElementById("filter-text"), status = document.getElementById("filter-status");
    var counter = document.getElementById("filter-count");
    var filter = function () {
      var q = (text.value || "").toLowerCase(), st = status.value, shown = 0;
      Array.prototype.forEach.call(body.rows, function (r) {
        var ok = (!q || r.textContent.toLowerCase().indexOf(q) >= 0) && (!st || r.getAttribute("data-status") === st);
        r.style.display = ok ? "" : "none";
        if (ok) { shown += 1; }
        // the sample's own section (chromatograms, alignment) follows its summary row
        var link = r.querySelector("a[href^='#']"), sec = link ? document.getElementById(link.getAttribute("href").slice(1)) : null;
        if (sec) { sec.style.display = ok ? "" : "none"; }
      });
      if (counter) { counter.textContent = (q || st) ? "Showing " + shown + " of " + body.rows.length + " samples" : ""; }
      Array.prototype.forEach.call(document.querySelectorAll(".countf"), function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-filter") === st ? "true" : "false");
      });
    };
    text.addEventListener("input", filter); status.addEventListener("change", filter);
    Array.prototype.forEach.call(document.querySelectorAll(".countf"), function (b) {
      b.addEventListener("click", function () {
        var v = b.getAttribute("data-filter");
        status.value = status.value === v ? "" : v;
        filter();
        table.scrollIntoView({behavior: "smooth", block: "start"});
      });
    });
    Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, k) {
      var btn = th.querySelector("button");
      if (!btn) { return; }
      btn.addEventListener("click", function () {
        var asc = th.getAttribute("aria-sort") !== "ascending", numeric = th.getAttribute("data-type") === "num";
        Array.prototype.forEach.call(table.tHead.rows[0].cells, function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = a.cells[k].getAttribute("data-v"), y = b.cells[k].getAttribute("data-v");
          var cmp = numeric ? Number(x) - Number(y) : x.localeCompare(y);
          return asc ? cmp : -cmp;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  }
  // printing: open every collapsed section and draw every trace first
  var reopened = [];
  window.addEventListener("beforeprint", function () {
    Array.prototype.forEach.call(document.querySelectorAll("details:not([open])"), function (el) {
      el.open = true; reopened.push(el);
    });
    canvases.forEach(function (cv) { if (!drawn[cv.getAttribute("data-v")]) { draw(cv); } });
  });
  window.addEventListener("afterprint", function () {
    reopened.forEach(function (el) { el.open = false; }); reopened = [];
  });
})();
"""
