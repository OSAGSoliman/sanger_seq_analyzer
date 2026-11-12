/* Sanger Seq Analyzer (server): small helpers next to htmx. No inline scripts (CSP). */
(function () {
  "use strict";

  // close the user menu when clicking elsewhere
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.usermenu[open]").forEach(function (d) {
      if (!d.contains(e.target)) { d.removeAttribute("open"); }
    });
  });

  // copy text: the Clipboard API needs HTTPS; fall back to a hidden textarea on plain HTTP
  function copyText(text, button) {
    function done(ok) {
      if (!button) { return; }
      var old = button.textContent;
      button.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(function () { button.textContent = old; }, 1500);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    document.body.removeChild(area);
    done(ok);
  }
  window.ssaCopyText = copyText;

  function cellText(cell) {
    var input = cell.querySelector("input:not([type=checkbox]),select");
    var text = input ? input.value : cell.textContent;
    return text.replace(/\s+/g, " ").trim();
  }

  // <button data-copy-table="#table-id">: the shown rows as tab-separated text (pastes into Excel)
  function tableText(table) {
    var skip = (table.getAttribute("data-copy-skip") || "").split(",").filter(Boolean).map(Number);
    var lines = [];
    table.querySelectorAll("tr").forEach(function (tr) {
      if (tr.hidden || tr.style.display === "none") { return; }
      var cells = [];
      Array.prototype.forEach.call(tr.children, function (c, i) {
        if (skip.indexOf(i) < 0) { cells.push(cellText(c)); }
      });
      lines.push(cells.join("\t"));
    });
    return lines.join("\n") + "\n";
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy-table]");
    if (b) {
      var t = document.querySelector(b.getAttribute("data-copy-table"));
      if (t) { copyText(tableText(t), b); }
      return;
    }
    var c = e.target.closest("[data-copy-text]");
    if (c) {
      var src = document.querySelector(c.getAttribute("data-copy-text"));
      if (src) { copyText(src.value !== undefined ? src.value : src.textContent, c); }
    }
  });

  // <input data-filter-table="#table-id">: hide rows that do not contain the text
  document.addEventListener("input", function (e) {
    var input = e.target.closest("[data-filter-table]");
    if (!input || input.getAttribute("data-filter-table").indexOf(",") >= 0) { return; }  // several tables: below
    var table = document.querySelector(input.getAttribute("data-filter-table"));
    if (!table) { return; }
    var q = input.value.trim().toLowerCase();
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var hay = Array.prototype.map.call(tr.children, cellText).join(" ").toLowerCase();
      tr.dataset.textHidden = q && hay.indexOf(q) < 0 ? "1" : "";
      tr.hidden = tr.dataset.textHidden === "1" || tr.dataset.kindHidden === "1";
    });
  });

  // <dialog> openers: <button data-open-dialog="#id">, closers: <button data-close-dialog>
  document.addEventListener("click", function (e) {
    var o = e.target.closest("[data-open-dialog]");
    if (o) { var d = document.querySelector(o.getAttribute("data-open-dialog")); if (d) { d.showModal(); } }
    var x = e.target.closest("[data-close-dialog]");
    if (x) { var dd = x.closest("dialog"); if (dd) { dd.close(); } }
  });
})();

/* Fetch data page: view switch, Show filters on several tables, auto-submitting selects, dialogs. */
(function () {
  "use strict";
  var current = { tag: "", text: "" };

  function rowsOf(selectors) {
    var out = [];
    selectors.split(",").forEach(function (sel) {
      var t = document.querySelector(sel.trim());
      if (t) { out = out.concat(Array.prototype.slice.call(t.querySelectorAll("tbody tr"))); }
    });
    return out;
  }
  function apply(selectors) {
    rowsOf(selectors).forEach(function (tr) {
      var tags = (tr.dataset.tags || "").split(" ");
      var tagHidden = current.tag && tags.indexOf(current.tag) < 0;
      tr.dataset.kindHidden = tagHidden ? "1" : "";
      tr.hidden = tagHidden || tr.dataset.textHidden === "1";
    });
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-tag-filter] button");
    if (b) {
      var nav = b.closest("[data-tag-filter]");
      nav.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
      current.tag = b.dataset.tag || "";
      apply(nav.getAttribute("data-tag-filter"));
      return;
    }
    var v = e.target.closest("[data-view-switch] button");
    if (v) {
      var sw = v.closest("[data-view-switch]");
      sw.querySelectorAll("button").forEach(function (x) {
        x.classList.toggle("on", x === v);
        var pane = document.querySelector(x.dataset.view);
        if (pane) { pane.hidden = x !== v; }
      });
    }
    var s = e.target.closest("[data-show-dialog]");
    if (s) { s.dataset.pendingDialog = "1"; }
  });

  // the text filter can cover several tables: data-filter-table="#a,#b"
  document.addEventListener("input", function (e) {
    var input = e.target.closest("[data-filter-table]");
    if (!input || input.getAttribute("data-filter-table").indexOf(",") < 0) { return; }
    current.text = input.value.trim().toLowerCase();
    var sels = input.getAttribute("data-filter-table");
    // the same row index in every table: hide by the text of the MeaNS row and the all-columns row together
    var tables = sels.split(",").map(function (x) { return document.querySelector(x.trim()); }).filter(Boolean);
    var n = tables.length ? tables[0].tBodies[0].rows.length : 0;
    for (var i = 0; i < n; i++) {
      var hay = tables.map(function (t) {
        var r = t.tBodies[0].rows[i];
        return r ? Array.prototype.map.call(r.cells, function (c) {
          var inp = c.querySelector("input:not([type=checkbox])");
          return inp ? inp.value : c.textContent;
        }).join(" ") : "";
      }).join(" ").toLowerCase();
      var hidden = current.text && hay.indexOf(current.text) < 0;
      tables.forEach(function (t) {
        var r = t.tBodies[0].rows[i];
        if (r) { r.dataset.textHidden = hidden ? "1" : ""; r.hidden = hidden || r.dataset.kindHidden === "1"; }
      });
    }
  });

  // a redrawn results table keeps the chosen filter
  document.addEventListener("htmx:afterSettle", function (e) {
    var nav = document.querySelector("[data-tag-filter]");
    if (nav) {
      nav.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", (x.dataset.tag || "") === current.tag); });
      apply(nav.getAttribute("data-tag-filter"));
    }
    var find = document.querySelector("[data-filter-table*=',']");
    if (find && current.text) { find.value = current.text; find.dispatchEvent(new Event("input", { bubbles: true })); }
    var opener = document.querySelector("[data-show-dialog][data-pending-dialog]");
    if (opener && e.detail && e.detail.target && e.detail.target.id === "save-body") {
      delete opener.dataset.pendingDialog;
      var d = document.querySelector(opener.getAttribute("data-show-dialog"));
      if (d && !d.open) { d.showModal(); }
    }
  });

  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest("select[data-autosubmit-select]");
    if (sel) { sel.form.requestSubmit ? sel.form.requestSubmit() : sel.form.submit(); }
  });
})();
