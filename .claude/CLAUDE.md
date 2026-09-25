# Annual Report Foot Notes Analyzer — Project Guide for Claude Code

This file briefs a Claude Code session (or a developer) working in
`C:\Daya\RAGchamp\ANN-RPT-ANALYZER`. It covers what the project is, how the
app is built, what has been done and verified so far, the pitfalls already
found, and how to reuse and extend the app.

---

## 1. What this project is

A local **Python Flask web app** for in-depth analysis of the **notes to the
financial statements** in an annual report PDF. The user:

1. **Selects** an annual report PDF. The app indexes every note and **loads
   the four primary financial statements** (Profit and Loss, Balance Sheet,
   Cash Flow, Changes in Equity) for both consolidated and standalone. It
   rotates sideways pages before reading them.
2. **Asks** a question, e.g. *"Analyze the company's Tax related liabilities
   and comment on it."*
3. **Confirms the notes** the app picked. The default is the consolidated
   notes; a small Claude call chooses notes from their titles. For the
   example question: Note 21, PDF pages 405–407.
4. The app **extracts** exactly those notes (clipped to each note's extent,
   with tables kept as `label | FY26 | FY25`).
5. It **sends** the notes plus the section's primary statements to **Claude
   Code** (`claude -p`).
6. It **shows** the analysis. The answer always includes an *Impact on the
   financial statements* section. Follow-up questions continue the same
   thread, and every answer is saved as a styled HTML report.

The design principle: **deterministic Python finds and extracts exactly the
right content**, and the LLM only analyzes that small extract. Claude never
sees the whole 508-page PDF.

Sample report used throughout:
`Annual-reports\Bharat-Forge-IR-2026-conv-single-page.pdf` (Bharat Forge
Integrated Annual Report FY2025-26, 508 pages).

---

## 2. Folder layout

```
ANN-RPT-ANALYZER\
  .claude\claude.md          this file
  Annual-reports\            input PDFs (the app lists every *.pdf here)
  INFO\
    ANN-RPT-NOTES-ANALYZER-PLAN.md       original plan + confirmed decisions + status
    HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html   full technical write-up (15 sections)
    logo.svg                             RAG Champ logo used by the HTML doc
    Requirements.txt                     the user's original requirement
  WEB-APP\                   the application (details below)
  S-PAD\                     user's scratch files (UI requirement snapshots as PDFs)
  OLD-STUFF\                 an earlier, unrelated pipeline attempt — ignore
```

### WEB-APP files

| File | Role |
|---|---|
| `app.py` | Flask routes, background jobs, threads, Markdown rendering, history and report saving |
| `config.py` | Paths, Claude CLI settings, limits, notes header strings (env-overridable) |
| `pdf_utils.py` | PyMuPDF helpers: rows from spans, running header/footer detection, text clean-up |
| `notes_index.py` | Builds and caches the **Notes Index** (every note → pages + y-positions), the company name, and (via `statements.py`) the statements |
| `statements.py` | Finds the 4 primary statements per section, **rotates sideways pages**, extracts and caches their text |
| `statements_view.py` + `templates/statements_view.html` | **View the financial statements** page (`/statements?report=…`, new tab); rebuilds tables and multi-line column headings **by position** |
| `note_selector.py` | Scope rules (consolidated / standalone / both), Claude note-picker, keyword fallback |
| `extractor.py` | Clips each note and rebuilds table rows as `a \| b \| c` |
| `claude_client.py` | `run_claude()`: `claude -p` via **stdin** with **all tools disabled**; `fill_prompt()` |
| `history.py` | `Prompt-History\` numbering and read-back (reused from basic-chatbot) |
| `report_html.py` + `templates/analysis_report.html` | Styled per-prompt HTML report in `Analysis-history\` |
| `prompts/*.txt` | Selector, analysis and follow-up prompts (edit these, not code, to change Claude's behaviour) |
| `templates/index.html`, `static/app.js`, `static/style.css`, `static/logo.svg` | Single-page UI |
| `tests/` | 72 pytest tests (Claude mocked) |
| `cache/` | `<pdf>.notes-index.json` (index + statement text), `threads/*.json` |
| `Prompt-History/`, `Analysis-history/`, `logs/app.log` | Outputs (see §5) |
| `README.md` | User-facing run/usage guide |

---

## 3. How to run

```powershell
cd C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP
pip install -r requirements.txt          # flask, pymupdf, markdown, pytest
python app.py                            # http://127.0.0.1:5000
python -m pytest -q                      # 72 tests, ~3s, no Claude calls
```

Requirements: Python 3.12, and the Claude Code CLI installed and logged in
(`C:\Users\dayam\.local\bin\claude.exe` by default; override with the
`CLAUDE_EXE` env var).

Environment overrides: `REPORTS_DIR`, `CLAUDE_EXE`, `CLAUDE_MODEL` (unset =
CLI default model), `CLAUDE_VIA_POWERSHELL=1` (use basic-chatbot's original
PowerShell invocation), `PORT`.

---

## 4. How it works (short version)

Full detail: `INFO\HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html`.

```
POST /api/index     notes_index.build_index()  → notes + statements (cached JSON)
POST /api/identify  job: detect_scope() → Claude call #1 (note titles only) → JSON note ids
                    (browser ALWAYS pauses here for the user to confirm/edit)
POST /api/analyze   job: extractor (notes) + cached statements → Claude call #2 → history + HTML report + thread
POST /api/followup  job: same notes + statements + conversation → claude -p
GET  /api/jobs/<id> browser polls every 1.5s
GET  /statements    tables view of all loaded statements (new tab)
GET  /analysis-history/<file>, /api/history, /api/threads/<id>
```

- **Notes Index:** notes pages are identified by their running header ("Notes to
  Consolidated/Standalone Financial Statements") in the top 20% of the page.
  Note headings are bold, left-margin rows in the dominant heading font
  (`Lato-Black` here) that start with a number, and the numbers must be in
  sequence. A note ends just before the next note's heading.
- **Statements:** the pages within the 15 pages before a section's first notes
  page whose large title (≥14pt, top 25%) *starts with* a statement name.
  Sideways pages (text direction `(0,-1)`) are redrawn onto a new page with
  `show_pdf_page(rotate=-90)`, i.e. turned 90° clockwise, before extraction.
- **Claude calls** follow `C:\Daya\RAGchamp\basic-chatbot\app.py`: a fresh
  stateless `claude -p` per request, `--system-prompt`, the last prompt/reply
  in `claude-prompt-input.txt` / `Claude-prompt-output.txt`, and numbered
  `Prompt-History`. The difference is that the prompt goes on **stdin**
  (Windows' ~32K command-line limit), with `--tools "" --no-session-persistence`.

---

## 5. Outputs the app writes

| Where | What |
|---|---|
| `Prompt-History\Claude-code-prompt-N-question.txt` | Metadata header (time sent, kind, report, scope, notes, PDF pages, financial statements, thread, saved HTML) + the question |
| `…-answer.txt` / `…-prompt.txt` | Claude's reply / the full prompt sent (reproducible) |
| `Analysis-history\<pdf name>-<prompt no>-<YYYYMMDD-HHMMSS>.html` | Styled report: RAG Champ nav, light-blue context box, analysis. Prompt no 1 = first question, 2 = first follow-up… Timestamp = when the prompt was sent |
| `cache\threads\<id>.json` | Follow-up thread: notes, extract, statements, turns |
| `logs\app.log` | Timings, selected notes, prompt sizes (job-polling lines filtered out) |

---

## 6. Decisions confirmed with the user (do not change without asking)

| Topic | Decision |
|---|---|
| Note confirmation | **Always pause** after identification; user edits notes, then clicks Analyze |
| Note picking | Claude selector call on titles + keyword fallback |
| Report selection | Dropdown of PDFs in `Annual-reports\` (no upload) |
| Model | Claude Code CLI default (no `--model`) |
| Follow-ups | Chat-style threads on the same notes; "Note 41" in a follow-up adds that note |
| Scope default | Consolidated unless the question says standalone |
| Statements | All 4 primary statements are loaded on report selection and sent with every question; the answer must include *Impact on the financial statements* |
| Code location | `ANN-RPT-ANALYZER\WEB-APP` |
| History sidebar | Keep it; the user finds it very useful |
| Page numbers | Always **PDF** page numbers (show printed page alongside) |

---

## 7. Work done so far (chronological)

1. **Plan** written (`INFO\ANN-RPT-NOTES-ANALYZER-PLAN.md`) after probing the
   PDF, and 7 open questions resolved with the user.
2. **Core app built** (Milestones 0–8). Real bugs found and fixed:
   - Sections started too early (the auditor's report mentions "Notes to…") → the header must be the top-of-page row.
   - Notes 3/5/9/11/14/18 were missed ("As at" table headers moved the header cut-off down) → only the line directly under the header counts.
   - Headings without a dot (`5 INTANGIBLE…`), a note that only appears as `54.1/54.2`, and standalone Note 3's heading on a rotated page (inferred from its `(CONTD.)` repeat).
   - PyMuPDF `find_tables()` failed on these borderless tables → rows rebuilt from spans, with `" | "` wherever the gap is over 12pt.
3. **Follow-up spinner** moved to just below the follow-up question (it was
   appearing off-screen at the top of the page).
4. **Analysis-history HTML reports** (RAG Champ look based on
   `OLD-STUFF\ANALYSIS-REPORTS\BHARATFORGE-ANN-REPORT-ANALYSIS-2025-26.html`),
   company-name detection, and a fix for Markdown lists that followed a bold
   line without a blank line.
5. **HOW document** created in `INFO\` (style copied from
   `C:\Daya\RAGchamp\CODING-AGENTS-FRAMEWORK\INFO\HOW-COBOL-TO-JAVA-WORKS.html`).
6. **Primary financial statements** loaded and sent with every question.
   Fixes: sideways pages redrawn clockwise (`page.set_rotation()` did **not**
   work); p.345 missed because the sideways running header joined the title
   row → prefix match; adjacent numbers merged in the wide equity table →
   two numbers are always separate columns. Prompts now require *Impact on the
   financial statements*.
7. **View the financial statements** page (new tab, tables, consolidated first).
   Fixes: equity parts A (3 columns) and B (12 columns) as separate tables;
   "(…)" line-continuation rules.
8. HOW document updated with 6 and 7; README kept current.
9. **Equity column headings fixed.** The user reported that the 12-column
   equity table was missing its headings. Statement rows are now cached
   *with x-positions* (`extractor._rows_with_positions`, INDEX_VERSION 7), and
   `statements_view` rebuilds every table by position: columns come from the
   numbers' right edges, stacked heading words are joined per column, and
   centred group headings are widened symmetrically. Claude's statement text
   also gets a `[Column headings, left to right: …]` line for wide tables.

---

## 8. Testing and verification

- `python -m pytest -q` → **72 passed**. Claude is mocked (`run_claude`
  monkeypatched), so the tests are free and fast. They run against the real
  sample PDF, and the PDF-dependent tests **skip** if it is missing
  (`tests/conftest.py`).
- Main facts the tests pin down, which should stay true:
  - Consolidated **Note 21 = PDF 405–407** (printed 402–404); 59 consolidated notes (PDF 348–503), 56 standalone (PDF 210–327).
  - Consolidated statements: P&L 342–343, BS 341, CF 346–347, **Changes in Equity 344–345 (rotated −90°)**. Standalone: 206, 205, 208–209, 207.
  - The Note 21 extract has `Total deferred tax liability | 215.24 | 1,198.28` and nothing from Notes 20 or 22.
  - The P&L has `Current tax | 5,606.07 | 5,848.54` (= Note 21's 5,701.41 − 95.34).
- **Real end-to-end runs** (with Claude, through the running server): identify
  took 6s and picked C21 (+C41, C23, C19); analysis of C21 took 79s before the
  statements were added and **127s** after, with a full impact section.
- **Visual checks:** the pages were screenshotted with headless Edge (no
  browser tool needed):
  ```bash
  "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
     --hide-scrollbars --window-size=1280,2400 --virtual-time-budget=8000 \
     --screenshot="<scratchpad>/shot.png" "http://127.0.0.1:5000/"
  ```
  Then crop with PIL and view the PNG. Also run `node --check static/app.js` after JS edits.

---

## 9. Gotchas already learned (read before changing code)

- **`import pymupdf`, never `import fitz`**: a conflicting `fitz` package on
  this machine breaks it.
- **PDF page ≠ printed page** (offset 3 in the sample). The code uses PDF page numbers throughout.
- The sample PDF has **no bookmarks**, so everything comes from the page text and fonts.
- **Rotated content:** check line `dir`, not `page.rotation` (which is 0 on
  the sideways pages). Redraw with `show_pdf_page(rotate=…)`; `set_rotation()` does not change the extracted text direction.
- **`--tools ""`** disables all Claude tools. PowerShell 5.1 **drops empty-string
  arguments**, so the PowerShell fallback uses `--disallowedTools` instead.
- Jobs live **in memory**; the app runs with `use_reloader=False`. Restart the
  server after Python changes. Static JS/CSS changes need only a browser **Ctrl+F5**.
- **Stale servers on Windows:** stopping the shell task that launched
  `python app.py` may leave the Python process alive on port 5000, still
  serving old code. Find and stop it:
  `Get-NetTCPConnection -LocalPort 5000 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }`
- Index cache is keyed by `INDEX_VERSION` (currently **7**) + PDF size/mtime.
  **Bump `INDEX_VERSION`** whenever you change what goes into the index, or old caches will be reused.
- Claude's Markdown is HTML-escaped before rendering (`render_markdown`);
  keep it that way. `_separate_lists()` fixes lists without a blank line before them.
- Editing via Python heredocs in Git Bash mangles `\n` and `\d` escapes; prefer
  the Edit tool or a patch script written to a file with raw strings.

---

## 10. Reusing this app / handing it to another Claude Code

### To run it on another machine
1. Copy the `WEB-APP` folder (and optionally `INFO\` for the docs).
2. `pip install -r requirements.txt`; install and log in to Claude Code.
3. Set `REPORTS_DIR` to your PDFs folder and `CLAUDE_EXE` to your `claude`
   binary (or edit the defaults in `config.py`).
4. Start with `python app.py`. Delete `cache\`, `Prompt-History\`,
   `Analysis-history\` and `logs\` contents for a clean start. They are
   recreated automatically.

### To use it on a different company's report
1. Drop the PDF in `REPORTS_DIR`, select it, and check the Step 1 summary:
   the note counts and ranges, and the 8 statements found.
2. If notes aren't found, the report probably words its running header
   differently. Add the wording to `NOTES_HEADERS` in `config.py`.
3. If statements aren't found, check that their titles fit
   `STATEMENT_TYPES` in `statements.py` and sit within `STATEMENTS_WINDOW`
   (15) pages before the notes.
4. Add a golden test for that report, like `tests/test_notes_index.py`.
   Until then, the manual "Or analyze specific PDF pages" box always works.

### Suggested first prompt for another Claude Code session
> Read `.claude/claude.md`, `WEB-APP/README.md` and
> `INFO/HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html`. Run `python -m pytest -q` in
> `WEB-APP` to confirm 72 tests pass. Then <describe the enhancement>. Keep the
> decisions in §6, add tests for the change, and update README and the HOW doc.

### Conventions for enhancements
- Match the surrounding style: small modules, docstrings that explain *why*,
  and comments only where logic is non-obvious.
- Change Claude's behaviour in `prompts/*.txt` first; templates use
  `{{KEY}}` placeholders filled by `fill_prompt()` in one pass.
- Every change gets tests. Mock `run_claude` (see `tests/test_app.py`); use
  the real PDF for extraction logic.
- Verify UI changes visually (headless Edge screenshot) and, for prompt
  changes, with **one** real run (each costs Claude usage: ~6s to pick notes, ~2 min to analyze).
- Keep `README.md`, the HOW doc and the plan's status section current.
- Keep the app local-only (`127.0.0.1`); there is no authentication.

### Ideas not yet built (backlog)
- OCR for scanned PDFs (currently refused if under 10% of pages have text).
- Cross-year comparison (two reports, same note).
- Pre-built question templates (Tax, Debt, Related parties, Contingencies).
- Export to Word/PDF; browser PDF upload (deliberately left out for now).
- Streaming Claude output (`--output-format stream-json`) instead of polling a finished job.
