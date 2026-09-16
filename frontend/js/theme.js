// Theme boot and toggle; runs before the stylesheet so the page never flashes the wrong theme
(function () {
  var KEY = "cryogram-theme";
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try { var v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : null; }
    catch (e) { return null; }
  }
  function resolved() { return stored() || (mq && mq.matches ? "dark" : "light"); }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var label = document.getElementById("themeToggleLabel");
    var ico = document.getElementById("themeToggleIco");
    if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    if (ico) ico.textContent = theme === "dark" ? "☀" : "☾";
  }

  apply(resolved());

  if (mq && mq.addEventListener) {
    mq.addEventListener("change", function () { if (!stored()) apply(resolved()); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    apply(resolved());
    var btn = document.getElementById("themeToggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var next = resolved() === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch (e) { }
      apply(next);
    });
  });
})();
