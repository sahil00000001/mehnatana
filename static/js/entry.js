/* Entry pages (by days present / by take-home): the figures of a row are worked out again as you type.
   The server does the arithmetic (the same as Save), so what you see is what will be saved. Nothing is saved
   until the Save button is pressed. Without JavaScript the page still works: Save shows the figures. */
(function () {
  var table = document.querySelector("table.live[data-live]");
  if (!table || !window.fetch) { return; }
  var url = table.getAttribute("data-live");
  var method = table.getAttribute("data-method");
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var rupees = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  var timers = {};
  var asked = {};

  function show(cell, value) {
    var target = cell.querySelector(".pill") || cell;
    target.textContent = value === null ? "—" : rupees.format(value);
  }

  // Count from the old figure to the new one, then a short glow on the cell.
  function animate(cell, to) {
    var raw = cell.getAttribute("data-v");
    var from = raw === "" || raw === null ? null : Number(raw);
    cell.setAttribute("data-v", to === null ? "" : String(to));
    if (from === to) { return; }
    cell.classList.remove("changed", "went-up", "went-down");
    void cell.offsetWidth; // restart the glow
    cell.classList.add("changed", to !== null && from !== null && to < from ? "went-down" : "went-up");
    if (calm || from === null || to === null) { show(cell, to); return; }
    var start = null, span = 650;
    function step(now) {
      if (start === null) { start = now; }
      var t = Math.min(1, (now - start) / span);
      var eased = 1 - Math.pow(1 - t, 3);
      show(cell, Math.round(from + (to - from) * eased));
      if (t < 1) { window.requestAnimationFrame(step); }
    }
    window.requestAnimationFrame(step);
  }

  function totals() {
    var foot = table.querySelectorAll("[data-total]");
    for (var i = 0; i < foot.length; i++) {
      var key = foot[i].getAttribute("data-total"), sum = 0;
      var cells = table.querySelectorAll('tbody tr.entry-row:not(.row-error) [data-k="' + key + '"]');
      for (var j = 0; j < cells.length; j++) {
        var v = cells[j].getAttribute("data-v");
        if (v) { sum += Number(v); }
      }
      if (foot[i].getAttribute("data-v") === null) { foot[i].setAttribute("data-v", String(sum)); }
      animate(foot[i], sum);
    }
  }

  function noteRow(id) { return table.querySelector('tr[data-note-for="' + id + '"]'); }

  function fill(row, data) {
    var id = row.getAttribute("data-emp");
    var note = noteRow(id);
    var cell = note.querySelector("td:last-child");
    cell.textContent = "";
    var lines = (data.errors || []).concat(data.notes || []);
    for (var i = 0; i < lines.length; i++) {
      var div = document.createElement("div");
      if (i < (data.errors || []).length) { div.className = "field-error"; }
      div.textContent = lines[i];
      cell.appendChild(div);
    }
    note.hidden = lines.length === 0;
    row.classList.toggle("row-error", !data.ok);
    note.classList.toggle("row-error", !data.ok);
    row.classList.toggle("has-notes", data.ok && lines.length > 0);
    note.classList.toggle("has-notes", data.ok);
    var figs = row.querySelectorAll("[data-k]");
    for (var j = 0; j < figs.length; j++) {
      var key = figs[j].getAttribute("data-k");
      if (key === "paid_days") {
        var days = data.ok ? data.figures.paid_days : "—";
        if (figs[j].textContent !== days) {
          figs[j].textContent = days;
          figs[j].classList.remove("changed");
          void figs[j].offsetWidth;
          figs[j].classList.add("changed");
        }
        figs[j].classList.toggle("reduced", !!(data.ok && data.reduced));
      } else {
        animate(figs[j], data.ok ? data.figures[key] : null);
      }
    }
    totals();
  }

  function ask(row) {
    var id = row.getAttribute("data-emp");
    var days = row.querySelector('input[data-col="days"]');
    var pay = row.querySelector('input[data-col="pay"]');
    var query = "?method=" + encodeURIComponent(method) + "&emp=" + encodeURIComponent(id) +
      "&days=" + encodeURIComponent(days ? days.value : "") + "&pay=" + encodeURIComponent(pay ? pay.value : "");
    var ticket = (asked[id] || 0) + 1;
    asked[id] = ticket;
    row.classList.add("is-working");
    fetch(url + query, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) { throw new Error(String(response.status)); }
        return response.json();
      })
      .then(function (data) {
        if (asked[id] !== ticket) { return; } // a newer figure was typed meanwhile
        row.classList.remove("is-working");
        fill(row, data);
      })
      .catch(function () {
        if (asked[id] !== ticket) { return; }
        row.classList.remove("is-working"); // the figures come with Save instead
      });
  }

  table.addEventListener("input", function (event) {
    var row = event.target.closest("tr.entry-row");
    if (!row) { return; }
    var id = row.getAttribute("data-emp");
    window.clearTimeout(timers[id]);
    row.classList.add("is-working");
    timers[id] = window.setTimeout(function () { ask(row); }, 300);
  });

  // Enter moves to the same box in the next row (like a spreadsheet) instead of saving the page.
  table.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || !event.target.matches("input[data-col]")) { return; }
    event.preventDefault();
    var col = event.target.getAttribute("data-col");
    var rows = Array.prototype.slice.call(table.querySelectorAll("tr.entry-row"));
    var at = rows.indexOf(event.target.closest("tr.entry-row"));
    var next = rows[at + 1] && rows[at + 1].querySelector('input[data-col="' + col + '"]');
    if (next) { next.focus(); next.select(); }
  });

  table.addEventListener("focusin", function (event) {
    if (event.target.matches("input[data-col]")) { event.target.select(); }
  });
})();
