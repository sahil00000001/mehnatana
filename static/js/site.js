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
  });
})();
