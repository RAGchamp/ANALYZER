// Ingest screen: every report with its package status and quality report.
// One report is ingested at a time (INFO/SPLIT-FUNCTIONALITY-PLAN.md, Q7).

const $ = (id) => document.getElementById(id);

const FORMATS = [
  ["auto", "Detect automatically"],
  ["modern", "Modern (notes to financial statements)"],
  ["us-10k", "US Form 10-K (US GAAP)"],
  ["legacy", "Old (schedules, Companies Act 1956)"],
];

let busy = false;

async function api(url, body) {
  const opts = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function showError(message) {
  $("error-box").textContent = message;
  $("error-box").classList.toggle("hidden", !message);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll("button[data-job]").forEach((b) => { b.disabled = value; });
}

// Start a job and poll it; `status` shows progress.
async function runJob(url, body, status, label) {
  setBusy(true);
  status.textContent = label;
  const started = Date.now();
  try {
    const { job_id } = await api(url, body);
    for (;;) {
      await new Promise((r) => setTimeout(r, 1000));
      const job = await api(`/api/jobs/${job_id}`);
      const s = Math.round((Date.now() - started) / 1000);
      if (job.status === "done") { status.textContent = ""; return job.result; }
      if (job.status === "error") throw new Error(job.error);
      status.textContent = `${label} ${job.progress || ""} (${s}s)`;
    }
  } finally {
    setBusy(false);
  }
}

function renderReport(r) {
  const box = el("div", "ingest-report");
  box.dataset.report = r.name;

  const head = el("div", "ingest-head");
  head.append(el("b", "", r.name), el("span", "muted", ` (${r.size_mb} MB)`));
  const status = r.needs_transcription ? "needs transcription" : r.status;
  head.append(el("span", `status-badge status-${status.replace(/ /g, "-")}`, status));
  if (r.format_label) {
    const facts = [r.model ? `Model: ${r.model}` : r.format_label];
    if (r.ingested_at) facts.push(`ingested ${r.ingested_at}`);
    head.append(el("span", "muted small-text", facts.join(" · ")));
  }
  box.appendChild(head);

  if (r.status === "stale") {
    box.appendChild(el("div", "muted small-text",
      "This package was made by an older version of the app or with another format; it is rebuilt when the report is next opened."));
  }
  // the first line repeats the name, format and page count shown above
  const quality = (r.quality || []).filter((line) => !line.startsWith(r.name) && !line.startsWith("Model:"));
  if (quality.length) {
    const list = el("ul", "quality-panel");
    for (const line of quality) {
      list.appendChild(el("li", line.startsWith("Warning:") ? "warning" : "", line));
    }
    box.appendChild(list);
  }
  if (r.new_model) box.appendChild(newModelPanel(r));
  if (r.needs_transcription) {
    const est = r.estimate;
    box.appendChild(el("div", "small-text",
      `Scanned report: transcribe it with Claude first — ${est.pages} page(s), about ${est.minutes} min, ` +
      `≈ $${est.usd.toFixed(2)} at API rates (uses your Claude Code plan). Pages are cached.`));
  }

  const actions = el("div", "row");
  const format = el("select", "small-select");
  format.title = "Report format: change only if it was detected wrongly";
  // Re-ingesting keeps the report's format (detected or set by hand) unless changed here.
  if (r.format_label) format.add(new Option(`Keep: ${r.format_label}`, ""));
  for (const [value, text] of FORMATS) format.add(new Option(text, value));

  const ingest = el("button", "", r.status === "not ingested" ? "Ingest"
    : r.status === "new model document" ? "Try again" : "Re-ingest");
  ingest.type = "button";
  ingest.dataset.job = "1";
  const jobStatus = el("div", "muted small-text job-status");
  ingest.addEventListener("click", async () => {
    showError("");
    try {
      const result = await runJob("/api/ingest/run",
        { report: r.name, format: format.value, force: r.status !== "not ingested" },
        jobStatus, "Ingesting…");
      box.replaceWith(renderReport({ ...r, ...result }));
      fillPageReports();
    } catch (err) {
      showError(`${r.name}: ${err.message}`);
    }
  });
  actions.appendChild(ingest);
  if (!r.scanned) actions.appendChild(format);   // a scanned report's format comes from its transcription

  if (r.needs_transcription) {
    const transcribe = el("button", "", "Transcribe");
    transcribe.type = "button";
    transcribe.dataset.job = "1";
    transcribe.addEventListener("click", async () => {
      showError("");
      try {
        await runJob("/api/ocr/start", { report: r.name }, jobStatus, "Transcribing with Claude…");
        const result = await runJob("/api/ingest/run", { report: r.name }, jobStatus, "Ingesting…");
        box.replaceWith(renderReport({ ...r, ...result, needs_transcription: !!result.needs_transcription }));
      } catch (err) {
        showError(`${r.name}: ${err.message}`);
      }
    });
    actions.appendChild(transcribe);
  }
  if (r.status === "up to date" || r.status === "stale") {
    const view = el("a", "", "View statements ↗");
    view.href = `/statements?report=${encodeURIComponent(r.name)}`;
    view.target = "_blank";
    view.rel = "noopener";
    const exportLink = el("a", "", "Export package");
    exportLink.href = `/api/packages/export?report=${encodeURIComponent(r.name)}`;
    actions.append(view, exportLink);
  }
  if (r.review_url) {
    const review = el("a", "", "Review scan ↗");
    review.href = r.review_url;
    review.target = "_blank";
    review.rel = "noopener";
    actions.appendChild(review);
  }
  box.append(actions, jobStatus);
  return box;
}

// A report that matches no model document (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §5.2).
function newModelPanel(r) {
  const nm = r.new_model;
  const panel = el("div", "model-panel");
  panel.append(el("div", "model-title", "New model document — can't be analyzed until its model is added"),
    el("div", "", nm.message));
  const list = el("ul");
  for (const n of nm.nearest.slice(0, 4)) {
    const why = n.pending ? "its rules are not written yet" : n.trial ? n.trial.summary : (n.conflict || "too different");
    list.appendChild(el("li", "", `${n.short} — similarity ${n.score.toFixed(2)}: ${why}`));
  }
  panel.appendChild(list);
  const row = el("div", "row");
  const gap = el("a", "", "View gap report ↗");
  gap.href = `/gap-report?report=${encodeURIComponent(r.name)}`;
  gap.target = "_blank";
  gap.rel = "noopener";
  const out = el("div", "muted small-text");
  const propose = el("button", "ghost small", "Propose as a model document");
  propose.type = "button";
  propose.title = "Copy the PDF and its gap report to MODEL-DOCS\\_candidates for the developer";
  propose.addEventListener("click", async () => {
    showError("");
    try {
      const res = await api("/api/models/propose", { report: r.name });
      out.textContent = `Copied to ${res.folder}. It becomes a model once its extraction rules are written and registered.`;
    } catch (err) { showError(err.message); }
  });
  const describe = el("button", "ghost small", "Describe with Claude");
  describe.type = "button";
  describe.dataset.job = "1";
  describe.title = "Adds Claude's description of a few pages to the gap report (uses your Claude plan)";
  describe.addEventListener("click", async () => {
    if (!confirm("Claude will read up to 4 page images of this report. This uses your Claude plan. Continue?")) return;
    showError("");
    try {
      await runJob("/api/models/describe", { report: r.name }, out, "Claude is reading the pages…");
      out.textContent = "Claude's description was added to the gap report.";
    } catch (err) { showError(err.message); }
  });
  row.append(gap, propose, describe);
  panel.append(row, out);
  return panel;
}

async function loadModels() {
  try {
    const data = await api("/api/models");
    const box = $("model-list");
    box.innerHTML = "";
    const table = el("table", "models-table");
    table.innerHTML = "<tr><th>Model document</th><th>Extraction rules</th><th>Description</th></tr>";
    for (const m of data.models) {
      const tr = document.createElement("tr");
      tr.append(el("td", "", m.short + (m.pdf_present ? "" : " (PDF missing)")),
        el("td", "", m.pending ? "not written yet" : m.profile), el("td", "muted", m.description));
      table.appendChild(tr);
    }
    box.append(el("div", "muted", `${data.models.length} model documents in ${data.model_docs_dir} (registry version ${data.version})`), table);
  } catch (err) {
    showError(`Could not list the model documents: ${err.message}`);
  }
}

let reports = [];

function fillPageReports() {
  const select = $("page-report");
  const current = select.value;
  select.innerHTML = "";
  for (const r of reports) select.add(new Option(r.name, r.name));
  if (current) select.value = current;
}

async function loadReports() {
  showError("");
  try {
    const data = await api("/api/ingest/reports");
    reports = data.reports;
    const list = $("report-list");
    list.innerHTML = "";
    if (!reports.length) {
      list.appendChild(el("div", "muted", `No PDFs found. Copy annual reports into ${data.reports_dir} and click ↻.`));
    }
    for (const r of reports) list.appendChild(renderReport(r));
    fillPageReports();
  } catch (err) {
    showError(`Could not list reports: ${err.message}`);
  }
}

$("refresh").addEventListener("click", () => { if (!busy) loadReports(); });

$("page-view").addEventListener("click", () => {
  const report = $("page-report").value;
  const page = parseInt($("page-number").value, 10) || 1;
  if (report) window.open(`/page-image?report=${encodeURIComponent(report)}&page=${page}`, "_blank", "noopener");
});

$("import-btn").addEventListener("click", async () => {
  const file = $("import-file").files[0];
  const out = $("import-result");
  showError("");
  out.textContent = "";
  if (!file) { showError("Choose a .rptpkg.db file to import."); return; }
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/packages/import", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Import failed (${res.status})`);
    out.textContent = data.matching_reports.length
      ? `Imported the package of ${data.pdf_name} (${data.format_label}). It is used for: ${data.matching_reports.join(", ")}.`
      : `Imported the package of ${data.pdf_name} (${data.format_label}). Copy that PDF into the reports folder to analyze it.`;
    await loadReports();
  } catch (err) {
    showError(err.message);
  }
});

loadReports();
loadModels();
