/* Run pages: uploads, sample list filters, selection and "next after confirming". */
(function () {
  "use strict";

  // forms with data-confirm ask first
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  // choosing files or a folder uploads them at once (htmx form)
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (input.matches && input.matches("input[data-autosubmit]") && input.files && input.files.length) {
      var form = input.closest("form");
      form.querySelectorAll("input[type=file][data-autosubmit]").forEach(function (other) {
        if (other !== input) { other.value = ""; }
      });
      htmx.trigger(form, "submit");
    }
    if (input.matches && input.matches("select[data-toggle-upload]")) {
      var label = input.closest("form").querySelector(".ref-upload");
      if (label) { label.hidden = input.value !== "upload"; }
    }
  });

  // drag and drop onto the drop zone
  document.addEventListener("dragover", function (e) {
    var zone = e.target.closest && e.target.closest("#dropzone");
    if (zone) { e.preventDefault(); zone.classList.add("over"); }
  });
  document.addEventListener("dragleave", function (e) {
    var zone = e.target.closest && e.target.closest("#dropzone");
    if (zone) { zone.classList.remove("over"); }
  });
  document.addEventListener("drop", function (e) {
    var zone = e.target.closest && e.target.closest("#dropzone");
    if (!zone) { return; }
    e.preventDefault();
    zone.classList.remove("over");
    var input = zone.querySelector("input[type=file]:not([webkitdirectory])");
    if (input && e.dataTransfer && e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  document.addEventListener("htmx:xhr:progress", function (e) {
    var out = document.getElementById("upload-progress");
    if (out && e.detail && e.detail.total) {
      out.textContent = "Uploading… " + Math.round(100 * e.detail.loaded / e.detail.total) + "%";
      if (e.detail.loaded >= e.detail.total) { out.textContent = "Checking the files…"; }
    }
  });

  // sample list: PASS / FAIL / not reviewed buttons
  function applyKind(table, kind) {
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var hide = false;
      if (kind === "unreviewed") { hide = tr.dataset.reviewed === "1"; }
      else if (kind) { hide = tr.dataset.kind !== kind; }
      tr.dataset.kindHidden = hide ? "1" : "";
      tr.hidden = hide || tr.dataset.textHidden === "1";
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-kind-filter] button");
    if (!b) { return; }
    var nav = b.closest("[data-kind-filter]");
    nav.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
    var table = document.querySelector(nav.getAttribute("data-kind-filter"));
    nav.dataset.current = b.dataset.kind;
    if (table) { applyKind(table, b.dataset.kind); }
  });

  // highlight the shown sample; keep the list filter after a row is redrawn
  document.addEventListener("htmx:afterSettle", function () {
    var detail = document.getElementById("detail");
    var idx = detail && detail.dataset.idx;
    document.querySelectorAll("#samples tbody tr").forEach(function (tr) {
      tr.classList.toggle("sel", tr.dataset.idx === idx);
    });
    var nav = document.querySelector("[data-kind-filter]");
    var table = document.getElementById("samples");
    if (nav && table && nav.dataset.current) { applyKind(table, nav.dataset.current); }
  });

  // after Confirm PASS / FAIL: show the next sample in the list
  document.body && document.body.addEventListener("sample-reviewed", function () {
    var auto = document.getElementById("auto-next");
    if (!auto || !auto.checked) { return; }
    var rows = Array.prototype.filter.call(document.querySelectorAll("#samples tbody tr"),
      function (tr) { return !tr.hidden; });
    var detail = document.getElementById("detail");
    var at = rows.findIndex(function (tr) { return detail && tr.dataset.idx === detail.dataset.idx; });
    var next = rows[at + 1];
    if (next) { htmx.trigger(next, "click"); next.scrollIntoView({ block: "nearest" }); }
  });
})();
