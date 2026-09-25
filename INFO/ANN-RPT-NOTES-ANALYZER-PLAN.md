# Annual Report Foot Notes Analyzer — Development Plan

A Python Flask web app that answers in-depth analytical questions about the
**footnotes (Notes to Financial Statements)** of an annual report PDF, by
locating the relevant note(s), extracting just those pages, and sending them to
**Claude Code** for analysis — reusing the Claude-Code-via-app pattern from
`C:\Daya\RAGchamp\basic-chatbot`.

## Implementation status (2026-09-24)

**Built** in `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP\`. Milestones 0–8 are implemented; see its `README.md`.

- The notes index finds **all 59 consolidated and 56 standalone notes** in the sample report, in about 6s (then cached). Headings are detected by font (bold, left margin, numbered) with a sequence check. It handles headings without a dot ("5 INTANGIBLE…"), notes that appear only as sub-headings (54.1/54.2), and a heading on a rotated landscape page (standalone Note 3, inferred from its "(CONTD.)" repeat).
- PyMuPDF's `find_tables()` can't read these borderless tables. The extractor instead rebuilds rows from text spans and writes `label | FY26 | FY25`.
- `claude -p` gets the prompt on stdin with `--tools "" --no-session-persistence`. The PowerShell route (`CLAUDE_VIA_POWERSHELL=1`) uses `--disallowedTools`, because PowerShell 5.1 drops empty-string arguments.
- **34 pytest tests pass** (Claude mocked). A real end-to-end run on the example question worked: identify took 6s and picked C21 (405–407) plus C41, C23 and C19 as related; analyzing C21 took 79s and returned a structured answer citing Note 21 and pp.405–407.

---

## 0. Decisions (confirmed 2026-09-24)

| # | Topic | Decision |
|---|---|---|
| 1 | Note confirmation | **Always pause.** Show the identified notes and pages, let the user edit them, then click **Analyze**. No auto-analyze mode. |
| 2 | Note picking | **Claude selector call + keyword fallback** |
| 3 | Report selection | **Dropdown of PDFs in `Annual-reports\`** only (no upload, no path box) |
| 4 | Model | **Claude Code CLI default.** No `--model` flag, so it follows the user's `/model` setting |
| 5 | Follow-ups | **Yes, chat-style.** Follow-ups reuse the same extracted notes plus earlier Q&A as context |
| 6 | Content scope | **Footnotes only** in v1 (no Balance Sheet / P&L pages) |
| 7 | Code location | **`C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP\`** |

---

## 1. User flow (the 6 steps)

| Step | What the user sees | What the app does |
|---|---|---|
| 1. Select report | Dropdown of PDFs in `Annual-reports\` | Builds/loads a cached **Notes Index** for that PDF |
| 2. Ask question | Text box, e.g. *"Analyze the company's Tax related liabilities and comment on it."* | Saves question to `claude-prompt-input.txt` |
| 3. Identify notes | "Identified: Note 21 – Income and Deferred Taxes (Consolidated), PDF pages 405–407" with **Edit** + **Analyze** buttons (always pauses here) | Detects scope (Consolidated by default) + picks relevant notes |
| 4. Extract | (spinner) "Extracting 3 pages…" | Pulls text/tables for exactly those note boundaries |
| 5. Analyze | (spinner) "Claude is analyzing…" | Sends question + extracted content to `claude -p` |
| 6. Present | Rendered analysis + collapsible "Source pages used" panel | Saves to `Claude-prompt-output.txt` and `Prompt-History\` |

---

## 2. Facts verified against the sample report (2026-09-24)

Checked directly on
`C:\Daya\RAGchamp\ANN-RPT-ANALYZER\Annual-reports\Bharat-Forge-IR-2026-conv-single-page.pdf`:

- **508 pages**, and it **has no PDF bookmarks/TOC** (`get_toc()` is empty), so
  note locations must come from the page text.
- **PDF page numbers ≠ printed page numbers.** PDF pages 405–407 are printed
  as pages 402–404 (offset of 3). The user's "405, 406, 407" are **PDF page
  numbers**. The app works in PDF page numbers internally and shows both in
  the UI.
- Every notes page carries a running header that identifies the section:
  - `Notes to Consolidated Financial Statements` → PDF pages **348–503** (156 pages)
  - `Notes to Standalone Financial Statements` → PDF pages **210–327** (118 pages)
- Note headings follow the pattern `21. INCOME AND DEFERRED TAXES`
  (number, dot, UPPER-CASE title). A simple regex found **56 of the 59**
  consolidated notes. Notes 5, 54 and 56 were missed (their headings likely
  wrap or are formatted differently), so the regex needs to allow for that (see Milestone 2).
- Detected: Note 21 starts on PDF page 405 and Note 22 starts on PDF page 408,
  so **Note 21 = PDF pages 405–407**. That matches the expected answer and
  becomes the "golden" acceptance test.
- Text extracts cleanly (about 2,000–3,000 chars per notes page). Tables come out
  as one value per line, which is readable but loses the column layout (see
  Milestone 3 for table handling).
- Environment: Python 3.12. `PyMuPDF` must be imported as **`import pymupdf`**
  (a conflicting `fitz` package breaks `import fitz`). `pdfplumber` and
  `pypdf` are also installed.

---

## 3. What is reused from `basic-chatbot`

| basic-chatbot element | Reuse in this app |
|---|---|
| Flask `app.py` with `/` + JSON `POST /ask` | Same shape: `/`, `/api/reports`, `/api/identify`, `/api/analyze` |
| `CLAUDE_EXE` env-overridable path (`C:\Users\dayam\.local\bin\claude.exe`) | Same, moved into `claude_client.py` |
| Fresh stateless `claude -p` per request via `subprocess.run` | Same, so no API key is needed and the local Claude Code login is used |
| `--system-prompt` to override the coding-assistant identity | Same, with a **financial analyst** system prompt |
| `claude-prompt-input.txt` / `Claude-prompt-output.txt` | Same files, holding the last prompt and the last analysis |
| `Prompt-History\` numbered Q&A files with `next_sno()` / `save_history()` | Same, with the extra metadata (report, scope, notes, pages) added to the question file |
| Error handling (FileNotFoundError / TimeoutExpired / non-zero exit → saved as reply) | Same |
| `templates/index.html` + `static/chat.js` + `static/style.css`, thinking-bubble UI | Same structure. Chat bubbles become a question panel + result panel. The voice mic button can be kept |

**One deliberate change:** basic-chatbot passes the prompt inside a PowerShell
`-Command` string. Extracted notes can be tens of thousands of characters,
which can exceed the Windows command-line limit (~32K chars). So the prompt
is sent through **stdin** instead:

```python
subprocess.run(
    [CLAUDE_EXE, "-p", "--system-prompt", SYSTEM_PROMPT, *extra_flags],
    input=full_prompt, capture_output=True, text=True,
    encoding="utf-8", timeout=CLAUDE_TIMEOUT_SECONDS,
)
```

This also removes the need for single-quote escaping. The PowerShell wrapper
stays available as a fallback flag (`CLAUDE_VIA_POWERSHELL=1`) in case it's needed.

---

## 4. Architecture

```
Browser ──► Flask (app.py)
              │
              ├─ notes_index.py   PDF ─► Notes Index JSON (cached per PDF)
              ├─ note_selector.py question + index ─► {scope, notes[]}   (Claude call #1, small)
              ├─ extractor.py     notes[] ─► clipped text + tables per page
              ├─ claude_client.py prompt ─► claude -p ─► text             (reused pattern)
              └─ history.py       Prompt-History, input/output files      (reused pattern)
```

**Two Claude calls per question:**

1. **Selector call** (cheap, fast). Claude gets the question plus the *titles*
   of all notes in the chosen scope, never page content, and returns JSON like
   `{"scope":"consolidated","notes":[21],"reason":"..."}`. This handles
   questions like "tax liabilities", which correspond to Note 21 and possibly
   Note 41 (Contingent Liabilities, since tax disputes often sit there).
2. **Analysis call.** Claude gets the question plus the extracted content of the
   selected notes, and returns the in-depth analysis.

A keyword-matching fallback (question words vs note titles) is used if the
selector call fails or returns invalid JSON.

---

## 5. Project layout

Create it at `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP\`:

```
WEB-APP\
  app.py                    Flask routes (pattern from basic-chatbot)
  config.py                 paths, CLAUDE_EXE, timeouts, model, limits
  notes_index.py            build + cache Notes Index
  note_selector.py          scope detection + Claude selector + keyword fallback
  extractor.py              page text/table extraction, note clipping
  claude_client.py          run_claude(prompt, system_prompt) — reused pattern
  history.py                next_sno / save_history — reused from basic-chatbot
  prompts\
    selector_system.txt
    analysis_system.txt
    analysis_template.txt
  templates\index.html
  static\app.js
  static\style.css
  cache\                    <pdf-stem>.notes-index.json, extracted page text
  Prompt-History\
  claude-prompt-input.txt
  Claude-prompt-output.txt
  tests\
    test_notes_index.py
    test_extractor.py
    test_selector.py
  requirements.txt          flask, pymupdf, pdfplumber, markdown
  README.md
```

Annual reports stay in `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\Annual-reports\`
(the path is configurable via `REPORTS_DIR`).

---

## 6. Build milestones

### Milestone 0 — Scaffold (reuse basic-chatbot)
1. Create the folder layout above. Copy `app.py`, `templates\`, `static\` from
   basic-chatbot as the starting point.
2. Extract `run_claude()` from basic-chatbot's `/ask` into `claude_client.py`,
   switch it to stdin input (Section 3), and raise the timeout to **600s**
   (in-depth analysis takes longer than a chat reply).
3. Move `next_sno()` / `save_history()` into `history.py`.
4. Run `claude --help` once to confirm the flag for disabling tools, so
   Claude analyzes only the text we give it and doesn't go reading files on
   its own. Record it in `config.py`. No `--model` flag is passed (Decision 4:
   use the CLI default).
5. **Done when:** `python app.py` serves the page, and a test route returns
   a Claude reply through `claude_client.run_claude()`.

### Milestone 1 — STEP 1: Report selection
1. `GET /api/reports` lists `*.pdf` in `REPORTS_DIR` (name, size, page count).
2. The UI shows these as a dropdown (Decision 3). To add a report, the user copies
   the PDF into `REPORTS_DIR` and refreshes the page.
3. On selection, `POST /api/index` builds or loads the Notes Index (Milestone 2)
   and returns a summary: *"Consolidated notes: 59 (PDF p.348–503) · Standalone
   notes: N (p.210–327)"*.
4. **Done when:** selecting the Bharat Forge PDF shows both note ranges.

### Milestone 2 — Notes Index (foundation for STEP 3)
Build this once per PDF and cache it as `cache\<pdf-stem>.notes-index.json`.
The cache is invalidated when the file's size or mtime changes.

1. **Page classification.** For each page, read the text with `pymupdf` and tag it:
   - `consolidated_notes` if it contains `Notes to Consolidated Financial Statements`
   - `standalone_notes` if it contains `Notes to Standalone Financial Statements`
   - also tag primary statements (Balance Sheet, P&L, Cash Flow) and other pages
   - make the header strings configurable (other companies may use "Notes forming
     part of the Consolidated Financial Statements").
2. **Printed page label.** Capture the printed page number from the header
   (e.g. `402`) for display. All logic uses PDF page numbers.
3. **Heading detection.** On notes pages, find note headings with a regex
   like `^(\d{1,3})\.\s+([A-Z][A-Z0-9 ,&()'/\-.]+)$`, plus:
   - allow headings that wrap onto the next line (to recover notes 5, 54, 56),
   - use font info (`page.get_text("dict")`: bold / larger size) to separate
     real headings from numbered list items inside a note,
   - enforce **monotonic note numbers** within a section to reject false hits,
   - record the heading's **y-position** so notes can be clipped mid-page.
4. **Note ranges.** Note *N* spans from its heading (page, y) to just before
   note *N+1*'s heading. If *N+1* starts at the top of a page, *N* ends on the
   previous page. So Note 21 = **PDF 405–407**.
5. **Sub-notes.** Also capture sub-headings (e.g. "Reconciliation of deferred
   tax liabilities and assets"). They are passed to the selector as extra
   context but don't split the note.
6. Output schema:
   ```json
   {"pdf": "...", "page_count": 508, "built_at": "...",
    "sections": {
      "consolidated": {"first_page": 348, "last_page": 503,
        "notes": [{"no": 21, "title": "INCOME AND DEFERRED TAXES",
                   "start_page": 405, "start_y": 120.5,
                   "end_page": 407, "end_y": null,
                   "printed_pages": "402-404",
                   "subheadings": ["...", "..."]}]},
      "standalone": {...}}}
   ```
7. **Done when:** `tests\test_notes_index.py` passes on the sample PDF:
   Note 21 consolidated = 405–407; 59 consolidated notes found, numbered 1..59
   with no gaps.

### Milestone 3 — STEP 3: Identify the relevant notes
1. **Scope detection (rule first).** Default = `consolidated`. Switch to
   `standalone` only if the question explicitly asks for it (e.g. "standalone",
   "not consolidated", "parent company only"). Use `both` if the question asks
   to compare them. Tell the selector the result so it doesn't override it.
2. **Claude selector call.** The prompt contains the question, the scope, and the list
   of `no. TITLE (+ subheadings)` for that scope. Claude must return only JSON
   `{"notes":[...], "reason":"..."}`, with **at most 5 notes**. Validate
   the note numbers against the index.
3. **Fallback.** If the call fails or returns bad JSON, use keyword and synonym matching
   against titles and subheadings (e.g. tax → "INCOME AND DEFERRED TAXES";
   debt → "BORROWINGS").
4. `POST /api/identify` returns the chosen notes, page ranges, the reason, and an
   estimated character count.
5. **UI confirmation:** show the identified notes as removable chips, plus an
   "add note" dropdown, and an **Analyze** button. The user can correct the
   selection before spending the big analysis call. This step **always** pauses
   (Decision 1).
6. **Done when:** the golden question selects Note 21 (consolidated, 405–407).
   Adding "standalone" to the question selects the standalone tax note instead.

### Milestone 4 — STEP 4: Extract content
1. For each selected note, go through its pages. On the first page keep text **below**
   the heading's y-position, and on the last page keep text **above** the next note's
   heading. Use `page.get_text("text", clip=rect)`.
2. Strip running headers/footers (report title, printed page number,
   "Notes to … Financial Statements / for the year ended …").
3. **Tables:** use `pymupdf`'s `page.find_tables()` (with `pdfplumber` as the
   fallback) and render each table as a **Markdown table**, so Claude sees
   Particulars | FY26 | FY25 aligned instead of one value per line. Replace the
   raw table text with the Markdown table.
4. Label each chunk: `=== Note 21 — INCOME AND DEFERRED TAXES (Consolidated) —
   PDF page 405 (printed 402) ===` so Claude can cite pages.
5. Keep the currency and unit line (`In ₹ Million`). Normalize the rupee glyph
   (the PDF uses a backtick `` ` `` for ₹) and ligatures like `ﬁ` → `fi`.
6. Guardrail: if the total exceeds `MAX_CONTEXT_CHARS` (e.g. 150K), warn in
   the UI and ask the user to drop notes.
7. Save the extracted content to `cache\` for debugging and show it in the
   "Source pages used" panel.
8. **Done when:** Note 21's extract contains the tax expense table (5,777.74 /
   5,425.50), the deferred tax table and the DTL reconciliation (1,198.28 →
   215.24), and contains nothing from Note 20 or Note 22.

### Milestone 5 — STEP 5: Send to Claude Code for analysis
1. `prompts\analysis_system.txt` is the system prompt. It should say:
   > You are a senior financial analyst and forensic accountant reviewing the
   > notes to financial statements of an annual report. Base every statement
   > on the provided extract. Cite note and PDF page numbers. Show
   > calculations. Flag anything unusual, inconsistent or requiring
   > follow-up. If information needed is not in the extract, say so rather
   > than assuming.
2. `prompts\analysis_template.txt` fills in the company, report, scope, the question,
   the notes list, and the extracted content, then asks for this structure:
   - **Summary** (3–5 bullets)
   - **Key figures** (table: item, current year, prior year, change, % change)
   - **Detailed analysis** (trends, drivers, effective tax rate / ratios where relevant)
   - **Red flags & areas for follow-up**
   - **Sources** (note and page references)
3. Call `claude_client.run_claude()` with tools disabled, using the CLI's default model.
   Write the full prompt to `claude-prompt-input.txt` first (as basic-chatbot does).
4. The Flask request can block for minutes. Run the analysis in a **background
   thread with a job id** (`POST /api/analyze` → `{job_id}`; `GET
   /api/jobs/<id>` → status/result) so the browser polls and doesn't time out.
5. **Done when:** the golden question returns a structured analysis that
   cites Note 21 and PDF pages 405–407.

### Milestone 6 — STEP 6: Present the analysis
1. Render Claude's Markdown to HTML (server-side `markdown` lib with the `tables`
   extension, or `marked.js` in the browser) and sanitize it.
2. Result panel: the question, report, scope, notes/pages chips, and the analysis. Add a
   collapsible **"Source extract"** panel showing exactly what was sent.
3. Actions: **Copy** and **Download as .md / .html**.
4. **Follow-up questions (Decision 5).** After an analysis, the input box stays
   open as a chat thread about the same notes. Each follow-up is a fresh
   `claude -p` call whose prompt contains the same extracted notes, then
   "Conversation so far" (previous Q&A in this thread, the same idea as
   basic-chatbot's `session_context()`), then the new question. A thread is
   keyed by `thread_id` (report + notes). **New analysis** starts a new
   thread and returns to Step 2. A follow-up can also add a note (e.g.
   "also look at Note 41"), which re-runs extraction for the new note and
   adds it to the thread.
5. Save to `Claude-prompt-output.txt` and `Prompt-History\` (the question file
   header records report, scope, notes, pages, thread_id and timestamp).
6. A **History** sidebar lists past analyses from `Prompt-History\` and reopens them.
6. **Done when:** a reviewer can reproduce any past answer's inputs from its
   history files.

### Milestone 7 — Hardening
- Errors: unreadable or encrypted PDF, scanned PDF with no text (show "OCR
  required", out of scope for v1), no notes found (let the user enter page
  numbers manually), Claude CLI missing, timeout, or non-zero exit.
- Manual override: an input "Use PDF pages: 405-407" that bypasses Steps 3 and 4
  identification.
- Logging to `logs\app.log`: timings per step, char counts, selected notes.
- Keep the app bound to `127.0.0.1` (a local tool, same as basic-chatbot).

### Milestone 8 — Testing
- **Unit:** notes index (golden Note 21, note count, monotonic numbering), scope
  detection rules, extractor clipping (no Note 20/22 bleed), header stripping,
  table-to-Markdown.
- **Selector:** a small table of question → expected notes, for example:
  | Question | Expected (consolidated) |
  |---|---|
  | Analyze tax related liabilities | 21 (+ optionally 41) |
  | Comment on the borrowings and debt maturity | 18 |
  | Analyze related party transactions | 48 |
  | What are the contingent liabilities? | 41 |
  | Analyze standalone tax liabilities | standalone tax note |
- **End-to-end:** the golden question through the UI, which covers Steps 1–6.
- Mock `run_claude()` in unit tests so they don't spend Claude calls.

---

## 7. Configuration (`config.py`)

| Setting | Default |
|---|---|
| `REPORTS_DIR` | `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\Annual-reports` |
| `CLAUDE_EXE` | `C:\Users\dayam\.local\bin\claude.exe` (env override) |
| `CLAUDE_MODEL` | `None`, meaning the CLI default and no `--model` flag (Decision 4). Can be set later if needed |
| `SELECTOR_TIMEOUT` / `ANALYSIS_TIMEOUT` | 120s / 600s |
| `MAX_NOTES` | 5 |
| `MAX_CONTEXT_CHARS` | 150,000 |
| `NOTES_HEADERS` | consolidated/standalone header strings (list, per company variants) |
| `MAX_FOLLOWUP_TURNS` | 10 (older turns dropped from the context) |

---

## 8. Suggested build order and effort

1. M0 Scaffold: ½ day
2. M2 Notes Index + tests: 1 day (the core; get the golden test green first)
3. M1 Report selection: ¼ day
4. M4 Extraction + table Markdown: 1 day
5. M3 Selector + confirmation UI: ½ day
6. M5 Analysis call + background jobs: ½ day
7. M6 Presentation + history: ½ day
8. M7/M8 Hardening + tests: ½–1 day

---

## 9. Out of scope for v1 (future ideas)
- OCR for scanned reports.
- Cross-year comparison (two reports, same note).
- Auto-including related primary-statement lines, e.g. the Balance Sheet DTL line (Decision 6: deferred).
- PDF upload from the browser (Decision 3: deferred).
- Pre-built analysis templates (Tax, Debt, Related parties, Contingencies).
- Export to Word/PDF.
