/* A few conveniences only. Every page also works with JavaScript turned off. */
(function () {
  document.documentElement.classList.add("js");

  // Print buttons (hidden without JS; the page says "press Ctrl+P" instead).
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-print]");
    if (button) {
      event.preventDefault();
      window.print();
    }
  });

  // Filters apply as soon as a choice changes (the "Show" button still works without JS).
  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-autosubmit]")) {
      event.target.form.submit();
    }
    // "Tick all" box: ticks or clears every box with the same name in its table.
    if (event.target.matches("[data-tick-all]")) {
      var name = event.target.getAttribute("data-tick-all");
      var boxes = event.target.closest("table").querySelectorAll('input[type=checkbox][name="' + name + '"]');
      for (var i = 0; i < boxes.length; i++) { boxes[i].checked = event.target.checked; }
    }
  });

  // Forms that need a "Are you sure?" before they are sent.
  document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });
})();
