/* Shared chrome + article tabs for the static WikiDrift site. */
(function () {
  // Mobile nav
  var burger = document.querySelector(".nav-burger");
  var links = document.getElementById("site-nav");
  if (burger && links) {
    burger.addEventListener("click", function () {
      var open = links.classList.toggle("open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    });
  }

  // Tabs: click + hash deep-link (#framing, #facts, #diff, #sources, #receipts, #overview)
  document.querySelectorAll(".tabs").forEach(function (root) {
    var tabs = [].slice.call(root.querySelectorAll(".tabbar .tab"));
    var panels = [].slice.call(root.querySelectorAll(".panel"));
    function activate(id, historyMode) {
      var found = false;
      tabs.forEach(function (t) {
        var on = t.dataset.slug === id || t.dataset.t === id;
        if (on) found = true;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      if (!found && tabs[0]) {
        id = tabs[0].dataset.slug || tabs[0].dataset.t;
        tabs.forEach(function (t, i) {
          var on = i === 0;
          t.classList.toggle("active", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
        });
      }
      panels.forEach(function (p) {
        var on = p.dataset.slug === id || p.dataset.p === id;
        p.classList.toggle("active", on);
      });
      if (historyMode && history.pushState) {
        var slug = id;
        tabs.forEach(function (t) {
          if (t.classList.contains("active") && t.dataset.slug) slug = t.dataset.slug;
        });
        history[historyMode === "push" ? "pushState" : "replaceState"](null, "", "#" + slug);
      }
    }
    tabs.forEach(function (b) {
      b.addEventListener("click", function () {
        activate(b.dataset.slug || b.dataset.t, "push");
      });
      b.addEventListener("keydown", function (e) {
        var i = tabs.indexOf(b);
        var next = -1;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % tabs.length;
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + tabs.length) % tabs.length;
        if (e.key === "Home") next = 0;
        if (e.key === "End") next = tabs.length - 1;
        if (next >= 0) {
          e.preventDefault();
          tabs[next].focus();
          activate(tabs[next].dataset.slug || tabs[next].dataset.t, "replace");
        }
      });
    });
    var hash = (location.hash || "").replace(/^#/, "");
    if (hash) activate(hash, false);

    root.addEventListener("click", function (e) {
      var link = e.target.closest('a[href^="#"]');
      if (!link || !root.contains(link)) return;
      var slug = link.getAttribute("href").slice(1);
      if (!tabs.some(function (t) { return t.dataset.slug === slug; })) return;
      e.preventDefault();
      activate(slug, "push");
      var activeTab = tabs.find(function (t) { return t.dataset.slug === slug; });
      if (activeTab) activeTab.focus();
    });

    window.addEventListener("popstate", function () {
      activate((location.hash || "#overview").slice(1), false);
    });
    window.addEventListener("hashchange", function () {
      activate((location.hash || "#overview").slice(1), false);
    });
  });

  // Stance evidence: expand quote on click (not title-only)
  document.querySelectorAll(".cell-ev").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var id = btn.getAttribute("aria-controls");
      var panel = id && document.getElementById(id);
      if (!panel) return;
      var open = panel.hidden;
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
})();
