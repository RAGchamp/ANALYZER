// Annual Report Foot Notes Analyzer - browser side.
// Steps: 1 pick report -> 2 ask -> 3 confirm notes (always pauses) ->
// 4-6 analyze and show the result, then optional follow-ups on the same thread.

const $ = (id) => document.getElementById(id);

const els = {
  reportSelect: $("report-select"),
  reportRefresh: $("report-refresh"),
  indexStatus: $("index-status"),
  formatRow: $("format-row"),
  formatReview: $("format-review"),
  formatChoice: $("format-choice"),
  ocrPanel: $("ocr-panel"),
  ocrText: $("ocr-text"),
  ocrNote: $("ocr-note"),
  ocrReview: $("ocr-review"),
  ocrStart: $("ocr-start"),
  ocrPages: $("ocr-pages"),
  ocrStartPages: $("ocr-start-pages"),
  progressCancel: $("progress-cancel"),
  formatText: $("format-text"),
  formatSelect: $("format-select"),
  scopeHint: $("scope-hint"),
  qualityPanel: $("quality-panel"),
  modelPanel: $("model-panel"),
  hintLine: $("hint-line"),
  statementsPanel: $("statements-panel"),
  viewStatementsRow: $("view-statements-row"),
  viewStatementsLink: $("view-statements-link"),
  question: $("question"),
  micBtn: $("mic-btn"),
  identifyBtn: $("identify-btn"),
  businessBtn: $("business-btn"),
  confirmTitle: $("confirm-title"),
  stepConfirm: $("step-confirm"),
  scopeLine: $("scope-line"),
  chips: $("note-chips"),
  selectReason: $("select-reason"),
  addNoteSelect: $("add-note-select"),
  addNoteBtn: $("add-note-btn"),
  pagesInput: $("pages-input"),
  sizeLine: $("size-line"),
  cancelBtn: $("cancel-btn"),
  analyzeBtn: $("analyze-btn"),
  progress: $("progress"),
  progressText: $("progress-text"),
  progressTime: $("progress-time"),
  liveAnswer: $("live-answer"),
  errorBox: $("error-box"),
  result: $("result"),
  resultMeta: $("result-meta"),
  turns: $("turns"),
  sourcePanel: $("source-panel"),
  sourceText: $("source-text"),
  followupBox: $("followup-box"),
  followupInput: $("followup-input"),
  followupBtn: $("followup-btn"),
  newAnalysisBtn: $("new-analysis-btn"),
  historyToggle: $("history-toggle"),
  historyPanel: $("history-panel"),
  historyList: $("history-list"),
  historyRefresh: $("history-refresh"),
  passageBox: $("passage-box"),
  passageList: $("passage-list"),
  passageSuggest: $("passage-suggest"),
  passageSearch: $("passage-search"),
  passageSearchBtn: $("passage-search-btn"),
  passageResults: $("passage-results"),
};

const state = {
  report: "",
  indexed: false,
  allNotes: [],       // every note in the report (both sections); old reports: schedules, notes, report sections
  format: "modern",   // "legacy" for old reports (Companies Act 1956 schedules), "transcribed" for scans
  jobId: null,        // the running background job (for Cancel)
  selected: [],       // note choice objects chosen for analysis
  hints: [],          // notes suggested by the financial statements: [{id, lines}]
  mode: "notes",      // "notes" (Identify notes) or "business" (Analyze non notes: report passages first)
  passages: [],       // report passages shown in step 3: [{id, path, pages, chars, lead, on}]
  passageSuggestions: [],
  noteChars: 0,       // the notes' extract size from step 3's estimate
  question: "",
  threadId: null,
  busy: false,
};

// ------------------------------------------------------------ helpers

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

function show(el, visible) { el.classList.toggle("hidden", !visible); }

function showError(message) {
  els.errorBox.textContent = message;
  show(els.errorBox, !!message);
}

function setBusy(busy, text) {
  state.busy = busy;
  show(els.progress, busy);
  if (text) els.progressText.textContent = text;
  refreshButtons();
}

function refreshButtons() {
  els.identifyBtn.disabled = state.busy || !state.indexed || !els.question.value.trim();
  els.businessBtn.disabled = els.identifyBtn.disabled;
  const passagesOn = state.mode === "business" && state.passages.some((p) => p.on);
  els.analyzeBtn.disabled = state.busy || (!state.selected.length && !passagesOn && !els.pagesInput.value.trim());
  els.followupBtn.disabled = state.busy || !state.threadId || !els.followupInput.value.trim();
  els.reportSelect.disabled = state.busy;
  els.ocrStart.disabled = state.busy;
  els.ocrStartPages.disabled = state.busy;
}

function pageRange(n) {
  const pdf = n.start_page === n.end_page ? `${n.start_page}` : `${n.start_page}–${n.end_page}`;
  const printed = n.printed_pages ? ` (printed ${n.printed_pages.replace("-", "–")})` : "";
  return `PDF p.${pdf}${printed}`;
}

function noteLabel(n) {
  return n.label || `${n.section_label} Note ${n.no} — ${n.title}`;
}

const SCOPE_HINTS = {
  modern: "Uses the notes to the <b>consolidated</b> financial statements unless your question asks for standalone.",
  "us-10k": "US Form 10-K: uses the notes to the <b>consolidated</b> financial statements.",
  legacy: "Old-format report: uses its <b>schedules, notes and report sections</b> (standalone accounts only).",
  transcribed: "Scanned report: uses the notes and schedules of the <b>company your question names</b> (default: the registrant).",
};

// The detected (or manually chosen) report format, with a way to override it.
function showFormat(data) {
  state.format = data.format;
  const how = data.format_source === "manual" ? "set manually" : "detected";
  const scanned = data.format === "transcribed";
  if (scanned) {
    const o = data.ocr || {};
    els.formatText.textContent = `Format: Scanned — transcribed by Claude (${o.pages} pages; ` +
      `${o.unreadable} unreadable characters, ${o.to_check} figures to check, ` +
      `${o.untied} total${o.untied === 1 ? "" : "s"} that ${o.untied === 1 ? "doesn't" : "don't"} add up)`;
    els.formatReview.href = data.review_url;
  } else {
    els.formatText.textContent = `Format: ${data.format_label} (${how})`;
  }
  if (data.model) els.formatText.textContent += ` · Model: ${data.model}`;
  show(els.formatReview, scanned);
  show(els.formatChoice, !scanned);
  els.formatSelect.value = data.format_source === "manual" ? data.format : "auto";
  els.scopeHint.innerHTML = SCOPE_HINTS[data.format] || SCOPE_HINTS.modern;
  show(els.formatRow, true);
}

// The spinner and error box normally sit near the top of the page (below
// step 3). For a follow-up they move to just below the follow-up question,
// so the user sees that the question they just asked is being processed.
const statusHome = document.createComment("status home");
els.progress.before(statusHome);

function placeStatus(anchor) {
  if (anchor) {
    anchor.after(els.progress, els.errorBox);
  } else {
    statusHome.after(els.progress, els.errorBox);
  }
}

// Start a background job and poll it until it finishes. With `anchor`,
// progress and errors are shown right below that element. While Claude is
// writing, the job carries the answer so far (partial_html), shown below the
// spinner; the finished answer then replaces it.
async function runJob(url, body, label, anchor = null, opts = {}) {
  placeStatus(anchor);
  setBusy(true, label);
  if (anchor) els.progress.scrollIntoView({ behavior: "smooth", block: "nearest" });
  const started = Date.now();
  const timer = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    els.progressTime.textContent = `${s}s elapsed`;
  }, 500);
  els.progressTime.textContent = "0s elapsed";
  try {
    const { job_id } = await api(url, body);
    state.jobId = job_id;
    show(els.progressCancel, !!opts.cancellable);
    for (;;) {
      await new Promise((r) => setTimeout(r, 1000));
      const job = await api(`/api/jobs/${job_id}`);
      if (job.status === "done") return job.result;
      if (job.status === "error") throw new Error(job.error);
      if (job.progress) {
        els.progressText.textContent = `${label} ${job.progress}`;
      }
      if (job.phase === "writing") {
        els.progressText.textContent = "Claude is writing the answer — you can start reading below…";
      } else if (job.phase === "thinking") {
        els.progressText.textContent = "Claude is thinking about your question…";
      }
      if (job.partial_html) {
        els.liveAnswer.innerHTML = job.partial_html; // rendered + HTML-escaped server-side
        show(els.liveAnswer, true);
      }
    }
  } finally {
    clearInterval(timer);
    state.jobId = null;
    show(els.progressCancel, false);
    els.liveAnswer.innerHTML = "";
    show(els.liveAnswer, false);
    setBusy(false);
  }
}

// ------------------------------------------------------------ STEP 1

async function loadReports() {
  showError("");
  try {
    const data = await api("/api/reports");
    els.reportSelect.innerHTML = "";
    if (!data.reports.length) {
      els.reportSelect.innerHTML = '<option value="">No PDFs found</option>';
      els.indexStatus.textContent = `Copy annual report PDFs into ${data.reports_dir} and click ↻.`;
      return;
    }
    const placeholder = new Option("— choose a report —", "");
    els.reportSelect.add(placeholder);
    for (const r of data.reports) {
      els.reportSelect.add(new Option(`${r.name}  (${r.size_mb} MB)`, r.name));
    }
    if (state.report && data.reports.some((r) => r.name === state.report)) {
      els.reportSelect.value = state.report;
    } else if (data.reports.length === 1) {
      els.reportSelect.value = data.reports[0].name;
      await selectReport(data.reports[0].name);
    }
  } catch (err) {
    showError(`Could not list reports: ${err.message}`);
  }
}

async function selectReport(name, format = null) {
  state.report = name;
  state.indexed = false;
  state.allNotes = [];
  refreshButtons();
  show(els.formatRow, false);
  if (!name) {
    els.indexStatus.textContent = "";
    return;
  }
  els.indexStatus.textContent =
    "Reading the report, indexing its notes and loading the financial statements (first time can take ~15s)…";
  show(els.statementsPanel, false);
  show(els.viewStatementsRow, false);
  show(els.qualityPanel, false);
  show(els.modelPanel, false);
  try {
    const body = format ? { report: name, format } : { report: name };
    const data = await api("/api/index", body);
    show(els.ocrPanel, false);
    if (data.needs_transcription) {
      showOcrPanel(data);
      refreshButtons();
      return;
    }
    if (data.new_model) {
      showNewModel(data);       // blocked: state.indexed stays false
      refreshButtons();
      return;
    }
    state.allNotes = data.notes;
    state.indexed = true;
    els.indexStatus.textContent = `${data.page_count} pages · ${data.summary}`;
    showFormat(data);
    renderQuality(data.quality || []);
    fillAddNoteSelect();
    renderStatements(data.sections);
  } catch (err) {
    els.indexStatus.textContent = "";
    showError(err.message);
  }
  refreshButtons();
}

// What the ingester found and checked: figures, links to notes, automatic
// checks and warnings (from the report package).
function renderQuality(lines) {
  els.qualityPanel.innerHTML = "";
  for (const line of lines) {
    const li = document.createElement("li");
    li.textContent = line;
    if (line.startsWith("Warning:")) li.className = "warning";
    els.qualityPanel.appendChild(li);
  }
  show(els.qualityPanel, lines.length > 0);
}

// A report that matches no model document can't be analyzed until its model is
// added (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §5.2): say why, and offer the gap report.
function showNewModel(data) {
  const nm = data.new_model;
  els.indexStatus.textContent = "";
  els.modelPanel.innerHTML = "";
  els.modelPanel.appendChild(Object.assign(document.createElement("div"),
    { className: "model-title", textContent: "New model document" }));
  els.modelPanel.appendChild(Object.assign(document.createElement("div"), { textContent: nm.message }));
  const list = document.createElement("ul");
  for (const n of nm.nearest.slice(0, 4)) {
    const why = n.pending ? "its rules are not written yet" : n.trial ? n.trial.summary : (n.conflict || "too different");
    list.appendChild(Object.assign(document.createElement("li"),
      { textContent: `${n.short} — similarity ${n.score.toFixed(2)}: ${why}` }));
  }
  els.modelPanel.appendChild(list);
  const link = Object.assign(document.createElement("a"),
    { href: data.gap_report_url, target: "_blank", rel: "noopener", textContent: "View gap report ↗" });
  const ingest = Object.assign(document.createElement("a"),
    { href: "/ingest", textContent: "Ingest screen (propose it as a model document) ↗" });
  const row = Object.assign(document.createElement("div"), { className: "row" });
  row.append(link, ingest);
  els.modelPanel.appendChild(row);
  show(els.modelPanel, true);
}

// A scanned report must be transcribed (once) before it can be analyzed.
function showOcrPanel(data) {
  const est = data.estimate;
  const st = data.status || {};
  els.indexStatus.textContent = `${data.page_count} pages · scanned report (no text layer)`;
  els.ocrText.innerHTML = "";
  const line = document.createElement("div");
  line.textContent = `This report is scanned. Transcribe it with Claude to analyze it: ${est.pages} page(s) to go, ` +
    `about ${est.minutes} min, ≈ $${est.usd.toFixed(2)} at API rates (uses your Claude Code plan). ` +
    "Pages are cached, so this is needed only once.";
  els.ocrText.appendChild(line);
  const notes = [];
  if (st.done) notes.push(`${st.done} page(s) already transcribed.`);
  if (st.failed && st.failed.length) notes.push(`Failed pages: ${st.failed.join(", ")} (click Transcribe to retry).`);
  notes.push(data.tesseract ? "Figures are cross-checked with Tesseract." : "Tesseract isn't installed, so figures are checked by totals only.");
  els.ocrNote.textContent = notes.join(" ") + " ";
  els.ocrReview.href = data.review_url;
  show(els.ocrPanel, true);
}

async function transcribe(pages) {
  showError("");
  try {
    await runJob("/api/ocr/start", { report: state.report, pages },
      "Transcribing the scanned pages with Claude…", null, { cancellable: true });
  } catch (err) {
    showError(err.message);
  }
  await selectReport(state.report);
}

// The primary statements loaded with the report, per section. Each one can
// be expanded to see exactly what was extracted (and sent to Claude).
function renderStatements(sections) {
  els.statementsPanel.innerHTML = "";
  let any = false;
  for (const section of Object.values(sections)) {
    const group = document.createElement("div");
    group.className = "statements-group";
    const head = document.createElement("div");
    head.className = "statements-head";
    head.textContent = `${section.label} financial statements loaded:`;
    group.appendChild(head);
    if (!section.statements.length) {
      const none = document.createElement("div");
      none.className = "muted small-text";
      none.textContent = "None found before the notes.";
      group.appendChild(none);
    }
    for (const st of section.statements) {
      any = true;
      const details = document.createElement("details");
      details.className = "statement";
      const summary = document.createElement("summary");
      summary.textContent = `✓ ${st.title} · PDF p.${st.pages}`;
      if (st.rotated) {
        const tag = document.createElement("span");
        tag.className = "rotated-tag";
        tag.textContent = "rotated";
        tag.title = "Printed sideways; rotated before extraction";
        summary.appendChild(tag);
      }
      const pre = document.createElement("pre");
      pre.textContent = st.text;
      details.append(summary, pre);
      group.appendChild(details);
    }
    if (section.missing_statements && section.missing_statements.length) {
      const missing = document.createElement("div");
      missing.className = "muted small-text";
      missing.textContent = `Not in this report: ${section.missing_statements.join(", ")} (not required at the time).`;
      group.appendChild(missing);
    }
    els.statementsPanel.appendChild(group);
  }
  show(els.statementsPanel, any || Object.keys(sections).length > 0);
  // "View the financial statements": all of them as tables, in a new tab.
  els.viewStatementsLink.href = `/statements?report=${encodeURIComponent(state.report)}`;
  show(els.viewStatementsRow, any);
}

const KIND_GROUPS = { schedule: "Schedules", note: "Numbered notes", narrative: "Report sections" };

function fillAddNoteSelect() {
  els.addNoteSelect.innerHTML = "";
  const groups = {};
  const legacy = state.format === "legacy";
  for (const n of state.allNotes) {
    const scanned = state.format === "transcribed";
    const key = legacy ? n.kind : n.section_label;
    if (!groups[key]) {
      groups[key] = document.createElement("optgroup");
      groups[key].label = legacy ? (KIND_GROUPS[n.kind] || n.kind) : (scanned ? n.section_label : `${n.section_label} notes`);
      els.addNoteSelect.appendChild(groups[key]);
    }
    const text = legacy || scanned ? `${n.label} (${pageRange(n)})` : `Note ${n.no} — ${n.title} (${pageRange(n)})`;
    groups[key].appendChild(new Option(text, n.id));
  }
}

// ------------------------------------------------------------ STEPS 2-3

// "Identify notes" analyzes the notes and statements; "Analyze non notes" the report's own
// sections (MD&A, Board's report …) with one or two notes to confirm figures. Both pause in step 3.
async function identify(mode = "notes") {
  const question = els.question.value.trim();
  if (!question || !state.indexed) return;
  showError("");
  state.question = question;
  state.mode = mode;
  show(els.stepConfirm, false);
  try {
    const sel = await runJob("/api/identify", { report: state.report, question, mode },
      mode === "business" ? "Finding the report passages about your question…" : "Identifying the relevant notes…");
    showConfirm(sel);
  } catch (err) {
    showError(err.message);
  }
}

function showConfirm(sel) {
  state.selected = sel.notes.slice();
  const scopeName = { consolidated: "Consolidated", standalone: "Standalone", both: "Consolidated + Standalone" }[sel.scope];
  els.scopeLine.innerHTML = "";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = sel.scope_label || scopeName;
  els.scopeLine.append(badge, ` ${sel.scope_reason}`);

  const how = sel.method === "claude" ? "Chosen by Claude" : "Chosen by keyword match";
  const chosenPassages = sel.passages || [];
  const business = state.mode === "business";
  els.confirmTitle.textContent = business
    ? "Confirm the report passages (and the notes that confirm their figures)"
    : "Confirm the notes to analyze";
  els.selectReason.textContent = sel.notes.length || chosenPassages.length
    ? `${how}: ${sel.reason}`
    : business ? "No matching report passages were found. Search for one below."
    : "No matching notes were found. Add a note below, or enter PDF pages.";
  state.maxChars = sel.max_chars;
  state.noteChars = sel.chars - chosenPassages.reduce((sum, p) => sum + p.chars, 0);
  state.passages = chosenPassages.map((p) => ({ ...p, on: true }));
  state.passageSuggestions = sel.passage_suggestions || [];
  els.passageResults.innerHTML = "";
  els.passageSearch.value = "";
  renderPassages();
  els.pagesInput.value = "";
  // Pre-select the add-note dropdown on the scope's section.
  const first = state.allNotes.find((n) => sel.scope === "standalone" ? n.section === "standalone" : n.section === "consolidated")
    || state.allNotes[0];
  if (first) els.addNoteSelect.value = first.id;
  state.hints = sel.hints || [];
  renderChips();
  show(els.stepConfirm, true);
  els.stepConfirm.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderChips() {
  els.chips.innerHTML = "";
  if (!state.selected.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "No notes selected.";
    els.chips.appendChild(empty);
  }
  for (const n of state.selected) {
    const chip = document.createElement("span");
    chip.className = "chip";
    const label = document.createElement("span");
    label.innerHTML = "";
    const strong = document.createElement("b");
    strong.textContent = noteLabel(n);
    if (n.title_is_excerpt) {
      strong.classList.add("excerpt");
      strong.title = "This note has no title; these are its first words.";
    }
    const pages = document.createElement("span");
    pages.className = "chip-pages";
    pages.textContent = ` · ${pageRange(n)}`;
    label.append(strong, pages);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-x";
    remove.title = "Remove";
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.selected = state.selected.filter((s) => s.id !== n.id);
      renderChips();
    });
    chip.append(label, remove);
    els.chips.appendChild(chip);
  }
  renderHints();
  refreshButtons();
}

// Notes behind the statement lines the question is about, found through the
// report package's links. A hint only: click one to add it.
function renderHints() {
  els.hintLine.innerHTML = "";
  const hints = (state.hints || []).filter((h) => state.allNotes.some((n) => n.id === h.id));
  if (!hints.length) {
    show(els.hintLine, false);
    return;
  }
  els.hintLine.append("Suggested by the financial statements: ");
  for (const h of hints) {
    const note = state.allNotes.find((n) => n.id === h.id);
    const chosen = state.selected.some((s) => s.id === h.id);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost small hint" + (chosen ? " chosen" : "");
    btn.textContent = `${chosen ? "✓" : "+"} ${note.kind === "note" && Number.isInteger(note.no) ? `Note ${note.no}` : note.id}`;
    btn.title = `${noteLabel(note)}\nLinked to: ${h.lines.join("; ")}`;
    btn.disabled = chosen;
    btn.addEventListener("click", () => {
      if (!state.selected.some((s) => s.id === h.id)) {
        state.selected.push(note);
        renderChips();
      }
    });
    els.hintLine.appendChild(btn);
  }
  show(els.hintLine, true);
}

// ------------------------------------------------------------ report passages (step 3)

function updateSizeLine() {
  const passageChars = state.passages.filter((p) => p.on).reduce((sum, p) => sum + p.chars, 0);
  const total = (state.noteChars || 0) + passageChars;
  els.sizeLine.textContent = total && state.maxChars
    ? `Estimated extract: ${total.toLocaleString()} characters`
      + (passageChars ? ` (report passages ${passageChars.toLocaleString()})` : "")
      + ` (limit ${state.maxChars.toLocaleString()}).`
    : "";
}

// text with the question's words in <mark> (DOM nodes, never innerHTML: the text is the report's)
function appendHighlighted(el, text, words) {
  const wanted = (words || []).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!wanted.length) {
    el.append(text);
    return;
  }
  const re = new RegExp(`\\b(${wanted.join("|")})\\b`, "gi");
  let last = 0;
  for (const m of text.matchAll(re)) {
    el.append(text.slice(last, m.index));
    const mark = document.createElement("mark");
    mark.textContent = m[0];
    el.appendChild(mark);
    last = m.index + m[0].length;
  }
  el.append(text.slice(last));
}

function passageRow(p, { chosen }) {
  const row = document.createElement("div");
  row.className = "passage-row";
  const label = document.createElement("label");
  if (chosen) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = p.on;
    box.addEventListener("change", () => {
      p.on = box.checked;
      updateSizeLine();
      refreshButtons();
    });
    label.appendChild(box);
  }
  const path = document.createElement("span");
  path.className = "passage-path";
  path.textContent = ` ${p.path}`;
  const meta = document.createElement("span");
  meta.className = "chip-pages";
  meta.textContent = ` · PDF ${p.pages} · ${(p.chars / 1000).toFixed(1)} K`;
  label.append(path, meta);
  row.appendChild(label);
  if (!chosen) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "ghost small";
    add.textContent = "+ Add";
    add.addEventListener("click", () => addPassage(p));
    row.appendChild(add);
  }
  const showBtn = document.createElement("button");
  showBtn.type = "button";
  showBtn.className = "link-btn small-text";
  showBtn.textContent = "Show";
  const pre = document.createElement("pre");
  pre.className = "passage-text hidden";
  showBtn.addEventListener("click", async () => {
    if (pre.classList.contains("hidden") && !pre.textContent) {
      try {
        const data = await api(`/api/passages/${encodeURIComponent(p.id)}?report=${encodeURIComponent(state.report)}`);
        appendHighlighted(pre, data.text, p.matched);
        const first = pre.querySelector("mark");
        if (first) requestAnimationFrame(() => { pre.scrollTop = first.offsetTop - pre.offsetTop - 40; });
      } catch (err) {
        pre.textContent = err.message;
      }
    }
    pre.classList.toggle("hidden");
    showBtn.textContent = pre.classList.contains("hidden") ? "Show" : "Hide";
  });
  row.append(showBtn);
  if (p.snippet) {
    // the sentence that holds the question's words: why this passage was found
    const snip = document.createElement("div");
    snip.className = "passage-snippet";
    appendHighlighted(snip, `“${p.snippet}”`, p.matched);
    row.appendChild(snip);
  }
  row.appendChild(pre);
  if (p.lead) row.title = p.lead;
  return row;
}

function renderPassages() {
  els.passageList.innerHTML = "";
  for (const p of state.passages) els.passageList.appendChild(passageRow(p, { chosen: true }));
  if (!state.passages.length) {
    const empty = document.createElement("span");
    empty.className = "muted small-text";
    empty.textContent = "No report passages chosen. Add one from the suggestions or search below.";
    els.passageList.appendChild(empty);
  }
  const suggestions = state.passageSuggestions.filter((s) => !state.passages.some((p) => p.id === s.id));
  els.passageSuggest.innerHTML = "";
  if (suggestions.length) {
    els.passageSuggest.append("Also related: ");
    for (const s of suggestions.slice(0, 4)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ghost small hint";
      const parts = s.path.split(" › ");
      btn.textContent = `+ ${parts[parts.length - 1].slice(0, 48)} (p.${s.pages})`;
      btn.title = `${s.path}\n${s.lead}`;
      btn.addEventListener("click", () => addPassage(s));
      els.passageSuggest.appendChild(btn);
    }
  }
  show(els.passageSuggest, suggestions.length > 0);
  // passages belong to "Analyze non notes" only
  show(els.passageBox, state.mode === "business");
  updateSizeLine();
  refreshButtons();
}

function addPassage(p) {
  if (!state.passages.some((x) => x.id === p.id)) state.passages.push({ ...p, on: true });
  renderPassages();
}

async function searchPassages() {
  const words = els.passageSearch.value.trim();
  if (!words) return;
  els.passageResults.innerHTML = "";
  try {
    const data = await api(`/api/passages/search?report=${encodeURIComponent(state.report)}&q=${encodeURIComponent(words)}`);
    if (!data.passages.length) {
      els.passageResults.textContent = "No passage matches those words.";
      return;
    }
    for (const p of data.passages) els.passageResults.appendChild(passageRow(p, { chosen: false }));
  } catch (err) {
    els.passageResults.textContent = err.message;
  }
}

function addNote() {
  const id = els.addNoteSelect.value;
  const note = state.allNotes.find((n) => n.id === id);
  if (note && !state.selected.some((s) => s.id === id)) {
    state.selected.push(note);
    renderChips();
  }
}

// ------------------------------------------------------------ STEPS 4-6

async function analyze() {
  showError("");
  const pages = els.pagesInput.value.trim();
  try {
    const result = await runJob("/api/analyze", {
      report: state.report,
      question: state.question,
      notes: state.selected.map((n) => n.id),
      passages: state.mode === "business" ? state.passages.filter((p) => p.on).map((p) => p.id) : [],
      pages,
      mode: state.mode,
    }, state.mode === "business"
      ? "Sending the report passages to Claude for a business analysis… (this can take a few minutes)"
      : "Extracting the notes and asking Claude for an in-depth analysis… (this can take a few minutes)");
    show(els.stepConfirm, false);
    startThread(result);
  } catch (err) {
    showError(err.message);
  }
}

function startThread(result) {
  state.threadId = result.thread_id;
  els.turns.innerHTML = "";
  addTurn(result);
  updateThreadInfo(result.notes, result.pages, result.extract, result.statements, result.statements_list,
    result.passages);
  if (result.dropped_passages && result.dropped_passages.length) {
    showError(`Left out to stay within the size limit: ${result.dropped_passages.length} report passage(s) (the lowest-ranked).`);
  }
  show(els.result, true);
  show(els.followupBox, true);
  els.result.scrollIntoView({ behavior: "smooth", block: "start" });
  loadHistory();
  refreshButtons();
}

function updateThreadInfo(notes, pages, extract, statementsText, statementsList, passages) {
  const parts = [state.report];
  if (notes && notes.length) {
    parts.push(notes.map((n) => `${noteLabel(n)} (${pageRange(n)})`).join("; "));
  } else if (pages) {
    parts.push(`PDF pages ${pages}`);
  }
  if (passages && passages.length) {
    parts.push("report passages: " + passages.map((p) => {
      const bits = p.path.split(" › ");
      return `${bits[bits.length - 1]} (p.${p.pages})`;
    }).join("; "));
  }
  if (statementsList) parts.push(`with ${statementsList}`);
  els.resultMeta.textContent = parts.join(" · ");
  const source = [statementsText, extract].filter(Boolean).join("\n\n");
  els.sourceText.textContent = source;
  show(els.sourcePanel, !!source);
}

function download(filename, content, type) {
  const blob = new Blob([content], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function addTurn(turn) {
  const wrap = document.createElement("article");
  wrap.className = "turn";

  const q = document.createElement("div");
  q.className = "bubble user";
  q.textContent = turn.question;

  const answer = document.createElement("div");
  answer.className = "answer markdown";
  answer.innerHTML = turn.answer_html; // rendered + HTML-escaped server-side

  const actions = document.createElement("div");
  actions.className = "turn-actions";
  const meta = document.createElement("span");
  meta.className = "muted small-text";
  meta.textContent = [turn.timestamp, turn.sno ? `#${turn.sno}` : ""].filter(Boolean).join(" · ");
  const base = `footnote-analysis-${turn.sno || "result"}`;
  const buttons = [
    ["Copy", () => navigator.clipboard.writeText(turn.answer)],
    [".md", () => download(`${base}.md`, `# ${turn.question}\n\n${turn.answer}\n`, "text/markdown")],
    [".html", () => download(`${base}.html`,
      `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Foot note analysis</title>` +
      `<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 16px;line-height:1.5}` +
      `table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}</style></head><body>` +
      `<p><b>Report:</b> ${escapeHtml(state.report)}</p><h1>${escapeHtml(turn.question)}</h1>${turn.answer_html}</body></html>`,
      "text/html")],
  ];
  for (const [text, fn] of buttons) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ghost small";
    b.textContent = text;
    b.addEventListener("click", fn);
    actions.appendChild(b);
  }
  if (turn.saved_html) {
    // Styled report saved in WEB-APP/Analysis-history/
    const link = document.createElement("a");
    link.className = "report-link";
    link.href = `/analysis-history/${encodeURIComponent(turn.saved_html)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.title = `Saved as Analysis-history\\${turn.saved_html}`;
    link.textContent = "Open saved report ↗";
    actions.appendChild(link);
  }
  actions.appendChild(meta);

  wrap.append(q, answer, actions);
  els.turns.appendChild(wrap);
  return wrap;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function followup() {
  const question = els.followupInput.value.trim();
  if (!question || !state.threadId) return;
  showError("");
  els.followupInput.readOnly = true; // keep the question visible while it runs
  try {
    const result = await runJob("/api/followup", { thread_id: state.threadId, question },
      "Searching the report again for your follow-up and asking Claude…", els.followupBox);
    els.followupInput.value = "";
    const node = addTurn(result);
    if (result.added_notes && result.added_notes.length) {
      const note = document.createElement("div");
      note.className = "muted small-text";
      note.textContent = "Found in the report for this follow-up, added to the thread: " + result.added_notes.map(noteLabel).join(", ");
      node.insertBefore(note, node.children[1]);
    }
    if (result.added_passages && result.added_passages.length) {
      const note = document.createElement("div");
      note.className = "muted small-text";
      note.textContent = "Report passages found for this follow-up, added to the thread: " + result.added_passages.map((p) => p.path).join("; ");
      node.insertBefore(note, node.children[1]);
    }
    if (result.rescanned && !(result.added_notes || []).length && !(result.added_passages || []).length) {
      const note = document.createElement("div");
      note.className = "muted small-text";
      note.textContent = "Searched the report again: nothing new was needed, so this answer uses the thread's notes and passages.";
      node.insertBefore(note, node.children[1]);
    }
    updateThreadInfo(result.notes, null, result.extract, result.statements, result.statements_list, result.passages);
    node.scrollIntoView({ behavior: "smooth", block: "start" });
    loadHistory();
  } catch (err) {
    showError(err.message);
  } finally {
    els.followupInput.readOnly = false;
  }
  refreshButtons();
}

function newAnalysis() {
  state.threadId = null;
  show(els.result, false);
  show(els.stepConfirm, false);
  showError("");
  placeStatus(null);
  els.question.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
  refreshButtons();
}

// ------------------------------------------------------------ history

async function loadHistory() {
  try {
    const { entries } = await api("/api/history");
    els.historyList.innerHTML = "";
    if (!entries.length) {
      els.historyList.innerHTML = '<li class="muted">No analyses yet.</li>';
      return;
    }
    for (const e of entries) {
      const li = document.createElement("li");
      li.className = "history-item";
      const q = document.createElement("div");
      q.className = "history-q";
      q.textContent = (e.kind === "follow-up" ? "↳ " : "") + e.question;
      const m = document.createElement("div");
      m.className = "muted small-text";
      m.textContent = `#${e.sno} · ${e.asked_at} · ${e.report}`;
      li.append(q, m);
      li.addEventListener("click", () => openHistory(e.sno));
      els.historyList.appendChild(li);
    }
  } catch (_) { /* history is optional */ }
}

async function openHistory(sno) {
  showError("");
  try {
    const entry = await api(`/api/history/${sno}`);
    const threadId = entry.meta.Thread;
    if (entry.thread_available) {
      const thread = await api(`/api/threads/${threadId}`);
      if (thread.report !== state.report || !state.indexed) {
        els.reportSelect.value = thread.report;
        await selectReport(thread.report);
      }
      state.threadId = thread.id;
      els.turns.innerHTML = "";
      for (const t of thread.turns) addTurn(t);
      updateThreadInfo(thread.notes, thread.page_spec,
        [thread.sections_text, thread.extract].filter(Boolean).join("\n\n"),
        thread.statements, thread.statements_list, thread.passages);
      show(els.followupBox, true);
    } else {
      state.threadId = null;
      els.turns.innerHTML = "";
      addTurn({
        question: entry.question, answer: entry.answer, answer_html: entry.answer_html,
        sno: entry.sno, timestamp: entry.meta["Asked at"],
        saved_html: entry.meta["Saved HTML"],
      });
      els.resultMeta.textContent = [entry.meta.Report, entry.meta.Notes].filter(Boolean).join(" · ");
      show(els.sourcePanel, false);
      show(els.followupBox, false);
    }
    show(els.stepConfirm, false);
    show(els.result, true);
    els.result.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showError(err.message);
  }
  refreshButtons();
}

// ------------------------------------------------------------ voice input

(function setupVoice() {
  const Impl = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Impl) {
    els.micBtn.disabled = true;
    els.micBtn.title = "Voice input not supported in this browser";
    return;
  }
  const rec = new Impl();
  rec.lang = "en-US";
  rec.continuous = false;
  rec.interimResults = true;
  let listening = false;
  rec.addEventListener("start", () => { listening = true; els.micBtn.classList.add("listening"); });
  rec.addEventListener("end", () => { listening = false; els.micBtn.classList.remove("listening"); refreshButtons(); });
  rec.addEventListener("error", () => { listening = false; els.micBtn.classList.remove("listening"); });
  rec.addEventListener("result", (event) => {
    let text = "";
    for (let i = 0; i < event.results.length; i++) text += event.results[i][0].transcript;
    els.question.value = text;
  });
  els.micBtn.addEventListener("click", () => (listening ? rec.stop() : rec.start()));
})();

// ------------------------------------------------------------ wiring

els.ocrStart.addEventListener("click", () => transcribe(""));
els.ocrStartPages.addEventListener("click", () => {
  const pages = els.ocrPages.value.trim();
  if (pages) transcribe(pages);
});
els.progressCancel.addEventListener("click", () => {
  if (state.jobId) api(`/api/jobs/${state.jobId}/cancel`, {}).catch(() => {});
  els.progressText.textContent = "Cancelling… (pages already being read will finish)";
});
els.formatSelect.addEventListener("change", () => {
  if (state.report) selectReport(state.report, els.formatSelect.value);
});
els.reportSelect.addEventListener("change", () => selectReport(els.reportSelect.value));
els.reportRefresh.addEventListener("click", loadReports);
els.question.addEventListener("input", refreshButtons);
els.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) identify("notes");
});
els.identifyBtn.addEventListener("click", () => identify("notes"));
els.businessBtn.addEventListener("click", () => identify("business"));
els.addNoteBtn.addEventListener("click", addNote);
els.passageSearchBtn.addEventListener("click", searchPassages);
els.passageSearch.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    searchPassages();
  }
});
els.pagesInput.addEventListener("input", refreshButtons);
els.cancelBtn.addEventListener("click", () => show(els.stepConfirm, false));
els.analyzeBtn.addEventListener("click", analyze);
els.followupInput.addEventListener("input", refreshButtons);
els.followupInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) followup();
});
els.followupBtn.addEventListener("click", followup);
els.newAnalysisBtn.addEventListener("click", newAnalysis);
els.historyRefresh.addEventListener("click", loadHistory);
els.historyToggle.addEventListener("click", () => document.body.classList.toggle("history-hidden"));

loadReports();
loadHistory();
