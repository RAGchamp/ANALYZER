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
  statementsPanel: $("statements-panel"),
  viewStatementsRow: $("view-statements-row"),
  viewStatementsLink: $("view-statements-link"),
  question: $("question"),
  micBtn: $("mic-btn"),
  identifyBtn: $("identify-btn"),
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
};

const state = {
  report: "",
  indexed: false,
  allNotes: [],       // every note in the report (both sections); old reports: schedules, notes, report sections
  format: "modern",   // "legacy" for old reports (Companies Act 1956 schedules), "transcribed" for scans
  jobId: null,        // the running background job (for Cancel)
  selected: [],       // note choice objects chosen for analysis
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
  els.analyzeBtn.disabled = state.busy || (!state.selected.length && !els.pagesInput.value.trim());
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
  try {
    const body = format ? { report: name, format } : { report: name };
    const data = await api("/api/index", body);
    show(els.ocrPanel, false);
    if (data.needs_transcription) {
      showOcrPanel(data);
      refreshButtons();
      return;
    }
    state.allNotes = data.notes;
    state.indexed = true;
    els.indexStatus.textContent = `${data.page_count} pages · ${data.summary}`;
    showFormat(data);
    fillAddNoteSelect();
    renderStatements(data.sections);
  } catch (err) {
    els.indexStatus.textContent = "";
    showError(err.message);
  }
  refreshButtons();
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

async function identify() {
  const question = els.question.value.trim();
  if (!question || !state.indexed) return;
  showError("");
  state.question = question;
  show(els.stepConfirm, false);
  try {
    const sel = await runJob("/api/identify", { report: state.report, question },
      "Identifying the relevant notes…");
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
  els.selectReason.textContent = sel.notes.length
    ? `${how}: ${sel.reason}`
    : "No matching notes were found. Add a note below or enter PDF pages.";
  els.sizeLine.textContent = sel.chars
    ? `Estimated extract: ${sel.chars.toLocaleString()} characters (limit ${sel.max_chars.toLocaleString()}).`
    : "";
  els.pagesInput.value = "";
  // Pre-select the add-note dropdown on the scope's section.
  const first = state.allNotes.find((n) => sel.scope === "standalone" ? n.section === "standalone" : n.section === "consolidated")
    || state.allNotes[0];
  if (first) els.addNoteSelect.value = first.id;
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
  refreshButtons();
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
      pages,
    }, "Extracting the notes and asking Claude for an in-depth analysis… (this can take a few minutes)");
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
  updateThreadInfo(result.notes, result.pages, result.extract, result.statements, result.statements_list);
  show(els.result, true);
  show(els.followupBox, true);
  els.result.scrollIntoView({ behavior: "smooth", block: "start" });
  loadHistory();
  refreshButtons();
}

function updateThreadInfo(notes, pages, extract, statementsText, statementsList) {
  const parts = [state.report];
  if (notes && notes.length) {
    parts.push(notes.map((n) => `${noteLabel(n)} (${pageRange(n)})`).join("; "));
  } else if (pages) {
    parts.push(`PDF pages ${pages}`);
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
      "Claude is analyzing your follow-up question…", els.followupBox);
    els.followupInput.value = "";
    const node = addTurn(result);
    if (result.added_notes && result.added_notes.length) {
      const note = document.createElement("div");
      note.className = "muted small-text";
      note.textContent = "Added to this thread: " + result.added_notes.map(noteLabel).join(", ");
      node.insertBefore(note, node.children[1]);
    }
    updateThreadInfo(result.notes, null, result.extract, result.statements, result.statements_list);
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
      updateThreadInfo(thread.notes, thread.page_spec, thread.extract, thread.statements, thread.statements_list);
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
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) identify();
});
els.identifyBtn.addEventListener("click", identify);
els.addNoteBtn.addEventListener("click", addNote);
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
