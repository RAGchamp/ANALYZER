// Model testing screen (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md §2): pick a model
// document and a page, see the page and what the app extracted, report a wrong extraction.

const $ = (id) => document.getElementById(id);
const MAX_CHARS = window.FEEDBACK_MAX_CHARS || 400;

let docs = [];
let current = null;      // the result shown: {doc, page, ...}
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

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(message) {
  $("error-box").textContent = message || "";
  $("error-box").classList.toggle("hidden", !message);
}

function selectedDoc() {
  return docs.find((d) => d.name === $("doc-select").value) || null;
}

// ---------------------------------------------------------------- documents

async function loadDocs() {
  const select = $("doc-select");
  try {
    const data = await api("/api/model-testing/docs");
    docs = data.docs;
    select.innerHTML = "";
    if (data.error || !docs.length) {
      select.appendChild(el("option", "", "No model documents"));
      select.disabled = true;
      showError(data.error || `No PDFs in ${data.model_docs_dir}`);
      return;
    }
    select.appendChild(el("option", "", "Choose a model document…")).value = "";
    for (const d of docs) {
      const option = el("option", "", d.name);
      option.value = d.name;
      select.appendChild(option);
    }
  } catch (err) {
    showError(err.message);
  }
  onDocChange();
  prefillFromUrl();
}

// "Test this page" in the formatted document opens /model-testing?doc=&page=N:
// choose that document and page, and test it at once.
function prefillFromUrl() {
  const params = new URLSearchParams(location.search);
  const doc = params.get("doc");
  if (!doc || !docs.some((d) => d.name === doc)) return;
  $("doc-select").value = doc;
  onDocChange();
  const page = params.get("page");
  if (page) {
    $("page-input").value = page;
    if (validatePage()) testExtraction();
  }
}

function formattedUrl(name, page) {
  const url = `/model-testing/formatted?${new URLSearchParams({ doc: name })}`;
  return page ? `${url}#page-${page}` : url;
}

function onDocChange() {
  const d = selectedDoc();
  const facts = $("doc-facts");
  facts.textContent = "";
  const link = $("formatted-link");
  link.classList.toggle("hidden", !(d && d.pages));
  if (d) link.href = formattedUrl(d.name);
  if (d) {
    const parts = [d.pages ? `${d.pages} pages` : (d.error || "can't be read")];
    if (!d.registered) parts.push("not in the model registry — no extraction rules");
    else if (d.pending) parts.push("pending — no extraction rules yet (raw PDF text is shown)");
    else parts.push(`rules: ${d.profile}`);
    facts.textContent = parts.join(" · ") + (d.pages ? " · " : "");
    if (d.description) facts.title = d.description;
  }
  $("page-range").textContent = d && d.pages ? `PDF page number (1–${d.pages})` : "";
  validatePage();
}

// ---------------------------------------------------------------- Page#

// The same rules as the server (ingest/model_testing.validate_page).
function pageProblem() {
  const d = selectedDoc();
  const text = $("page-input").value.trim();
  if (!d) return { quiet: true, message: "Choose a model document." };
  if (!d.pages) return { message: d.error || "This PDF can't be read." };
  if (!text) return { quiet: true, message: `Enter a PDF page number (1–${d.pages}).` };
  if (!/^\d+$/.test(text)) return { message: `Enter a PDF page number (1–${d.pages}).` };
  const page = Number(text);
  if (page < 1 || page > d.pages) {
    return { message: `Page ${page} is out of range: this PDF has ${d.pages} pages (1–${d.pages}).` };
  }
  return null;
}

function validatePage() {
  const problem = pageProblem();
  const box = $("page-message");
  box.textContent = problem && !problem.quiet ? problem.message : "";
  box.classList.toggle("hidden", !box.textContent);
  $("page-input").classList.toggle("invalid", !!box.textContent);
  $("test-btn").disabled = busy || !!problem;
  return !problem;
}

// ---------------------------------------------------------------- Test extraction

async function runJob(url, body, status, label) {
  const started = Date.now();
  const { job_id } = await api(url, body);
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const job = await api(`/api/jobs/${job_id}`);
    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error);
    const s = Math.round((Date.now() - started) / 1000);
    status.textContent = s >= 4
      ? `${label} (${s}s — the first test of a document ingests it, up to ~1.5 min)`
      : `${label} (${s}s)`;
  }
}

async function testExtraction() {
  if (!validatePage()) return;
  const d = selectedDoc();
  const page = Number($("page-input").value.trim());
  busy = true;
  validatePage();
  showError("");
  const status = $("test-status");
  status.textContent = "Reading the model document…";
  try {
    const result = await runJob("/api/model-testing/extract", { doc: d.name, page }, status,
      "Reading the model document…");
    showResult(result);
  } catch (err) {
    showError(err.message);
  } finally {
    status.textContent = "";
    busy = false;
    validatePage();
  }
}

function showResult(result) {
  current = result;
  const printed = result.printed ? ` (printed ${result.printed})` : "";
  $("result-title").textContent = `PDF page ${result.page}${printed} — ${result.doc}`;
  $("page-image").src = result.image_url;
  $("image-link").href = result.image_url;
  $("formatted-page-link").href = formattedUrl(result.doc, result.page);
  const notices = $("notices");
  notices.innerHTML = "";
  for (const n of result.notices || []) notices.appendChild(el("div", "notice", n));
  $("extracted").textContent = result.extracted_text;
  resetFeedback();
  $("result").classList.remove("hidden");
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- feedback

function resetFeedback() {
  $("need-correction").checked = false;
  $("feedback-form").classList.add("hidden");
  $("comment").value = "";
  $("comment").disabled = false;
  $("submit-btn").textContent = "Submit feedback";
  $("submit-btn").dataset.again = "";
  $("submit-status").textContent = "";
  $("submit-status").className = "small-text";
  updateCount();
}

function updateCount() {
  const n = $("comment").value.length;
  $("char-count").textContent = `${n}/${MAX_CHARS}`;
  $("char-count").classList.toggle("over", n > MAX_CHARS);
  if (!$("submit-btn").dataset.again) {
    $("submit-btn").disabled = !$("comment").value.trim() || n > MAX_CHARS;
  }
}

async function submitFeedback() {
  const btn = $("submit-btn");
  if (btn.dataset.again) {           // "Report another problem on this page"
    resetFeedback();
    $("need-correction").checked = true;
    $("feedback-form").classList.remove("hidden");
    $("comment").focus();
    return;
  }
  const status = $("submit-status");
  btn.disabled = true;
  status.className = "small-text muted";
  status.textContent = "Saving…";
  try {
    const saved = await api("/api/model-testing/feedback",
      { doc: current.doc, page: current.page, comment: $("comment").value });
    status.className = "small-text saved";
    status.textContent = `✓ Saved as ${saved.feedback_file} (status: ${saved.status})`;
    $("comment").disabled = true;
    btn.textContent = "Report another problem on this page";
    btn.dataset.again = "1";
    btn.disabled = false;
    loadFeedback();
  } catch (err) {
    status.className = "small-text field-error";
    status.textContent = err.message;
    btn.disabled = false;
  }
}

async function loadFeedback() {
  const box = $("feedback-list");
  try {
    const { entries, folder } = await api("/api/model-testing/feedback");
    $("feedback-count").textContent = `(${entries.length})`;
    box.innerHTML = "";
    if (!entries.length) {
      box.appendChild(el("div", "muted", `No feedback yet. Files are saved in ${folder}.`));
      return;
    }
    const table = el("table", "models-table");
    const head = table.createTHead().insertRow();
    for (const h of ["Feedback file", "Page", "Reported", "Status", "Fixed"]) head.appendChild(el("th", "", h));
    const body = table.createTBody();
    for (const e of entries) {
      const row = body.insertRow();
      const link = el("a", "", e.feedback_file);
      link.href = `/api/model-testing/feedback/${encodeURIComponent(e.feedback_file)}`;
      link.target = "_blank";
      link.rel = "noopener";
      row.insertCell().appendChild(link);
      row.insertCell().textContent = e.page;
      row.insertCell().textContent = (e.date_reported || "").replace("T", " ").slice(0, 19);
      const status = el("span", `status-badge ${e.status === "Reported" ? "status-reported" : "status-fixed"}`, e.status);
      row.insertCell().appendChild(status);
      row.insertCell().textContent = (e.date_fixed || "").replace("T", " ").slice(0, 19);
    }
    box.appendChild(table);
  } catch (err) {
    box.textContent = err.message;
  }
}

// ---------------------------------------------------------------- wiring

$("doc-select").addEventListener("change", onDocChange);
$("page-input").addEventListener("input", validatePage);
$("page-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !$("test-btn").disabled) testExtraction();
});
$("test-btn").addEventListener("click", testExtraction);
$("need-correction").addEventListener("change", () => {
  $("feedback-form").classList.toggle("hidden", !$("need-correction").checked);
  if ($("need-correction").checked) $("comment").focus();
});
$("comment").addEventListener("input", updateCount);
$("submit-btn").addEventListener("click", submitFeedback);
$("copy-btn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("extracted").textContent);
    $("copy-btn").textContent = "Copied";
    setTimeout(() => { $("copy-btn").textContent = "Copy"; }, 1500);
  } catch (_) { /* clipboard not allowed */ }
});

loadDocs();
loadFeedback();
