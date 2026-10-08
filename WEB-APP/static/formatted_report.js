// The formatted document of a model document (INFO/MODEL-FORMATTED-REPORTS-PLAN.md):
// contents filter, expand / collapse, jump into collapsed page runs, print, stored text.

(function () {
  const doc = window.FORMATTED_DOC || "";

  // ---------------------------------------------------------------- contents filter
  const filter = document.getElementById("toc-filter");
  if (filter) {
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      document.querySelectorAll("#toc-nav [data-filter]").forEach((item) => {
        item.classList.toggle("toc-hidden", !!q && !item.dataset.filter.includes(q));
      });
    });
  }

  // ---------------------------------------------------------------- expand / collapse
  function setAll(open) {
    document.querySelectorAll("#doc details").forEach((d) => { d.open = open; });
  }
  const expand = document.getElementById("expand-all");
  const collapse = document.getElementById("collapse-all");
  if (expand) expand.addEventListener("click", () => setAll(true));
  if (collapse) collapse.addEventListener("click", () => setAll(false));

  // #page-N may sit inside a collapsed run of other pages: open it, then scroll
  function revealHash() {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    const target = document.getElementById(id);
    if (!target) return;
    let node = target.parentElement;
    let opened = false;
    while (node) {
      if (node.tagName === "DETAILS" && !node.open) { node.open = true; opened = true; }
      node = node.parentElement;
    }
    if (opened) target.scrollIntoView();
  }
  window.addEventListener("hashchange", revealHash);
  revealHash();

  // contents links on phones: close the offcanvas after a jump
  document.querySelectorAll("#toc-nav a").forEach((a) => {
    a.addEventListener("click", () => {
      const panel = document.getElementById("toc");
      if (window.bootstrap && panel && panel.classList.contains("show")) {
        window.bootstrap.Offcanvas.getOrCreateInstance(panel).hide();
      }
    });
  });

  // ---------------------------------------------------------------- print
  let closedBeforePrint = [];
  window.addEventListener("beforeprint", () => {
    closedBeforePrint = [...document.querySelectorAll("#doc details:not([open])")];
    closedBeforePrint.forEach((d) => { d.open = true; });
  });
  window.addEventListener("afterprint", () => {
    closedBeforePrint.forEach((d) => { d.open = false; });
    closedBeforePrint = [];
  });
  const printBtn = document.getElementById("print-btn");
  if (printBtn) printBtn.addEventListener("click", () => window.print());

  // ---------------------------------------------------------------- stored text
  document.querySelectorAll(".show-stored").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest("article");
      const box = card.querySelector(".stored-text");
      const pre = box.querySelector("pre");
      if (!box.classList.contains("d-none")) {
        box.classList.add("d-none");
        btn.textContent = "Show as stored text";
        return;
      }
      if (!pre.dataset.loaded) {
        pre.textContent = "Loading…";
        const params = new URLSearchParams(card.dataset.pages
          ? { doc, pages: card.dataset.pages }
          : { doc, section: card.dataset.section, unit: card.dataset.unit });
        try {
          const res = await fetch(`/api/model-testing/formatted/stored-text?${params}`);
          const data = await res.json();
          pre.textContent = res.ok ? data.text : (data.error || `Request failed (${res.status})`);
          if (res.ok) pre.dataset.loaded = "1";
        } catch (err) {
          pre.textContent = err.message;
        }
      }
      box.classList.remove("d-none");
      btn.textContent = "Hide stored text";
    });
  });

  // ---------------------------------------------------------------- remember where the reader was
  const key = `formatted-scroll:${doc}`;
  if (!location.hash) {
    try {
      const y = Number(sessionStorage.getItem(key));
      if (y > 0) window.scrollTo(0, y);
    } catch (_) { /* storage blocked */ }
  }
  let timer = null;
  window.addEventListener("scroll", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      try { sessionStorage.setItem(key, String(Math.round(window.scrollY))); } catch (_) { /* storage blocked */ }
    }, 300);
  });
})();
