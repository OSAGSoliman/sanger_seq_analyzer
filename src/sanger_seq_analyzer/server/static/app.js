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
    if (!input) { return; }
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
