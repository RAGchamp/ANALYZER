# Annual Report Foot Notes Analyzer — Project Guide for Claude Code

This file briefs a Claude Code session (or a developer) working in
`C:\Daya\RAGchamp\ANN-RPT-ANALYZER`. It covers what the project is, how the
app is built, what has been done and verified so far, the pitfalls already
found, and how to reuse and extend the app.

---

## 1. What this project is

A local **Python Flask web app** for in-depth analysis of the **notes to the
financial statements** in an annual report PDF. The user:

1. **Selects** an annual report PDF. The app **ingests** it once into a
   **report package**: it indexes every note, **loads the four primary
   financial statements** (Profit and Loss, Balance Sheet, Cash Flow, Changes
   in Equity) for consolidated and standalone (rotating sideways pages first),
   stores the text of every note and page, parses the statement figures, links
   statement lines to notes, and runs automatic checks.
2. **Asks** a question, e.g. *"Analyze the company's Tax related liabilities
   and comment on it."*
3. **Confirms the notes** the app picked. The default is the consolidated
   notes; a small Claude call chooses notes from their titles, helped by the
   notes *linked to the statement lines the question is about*. For the
   example question: Note 21, PDF pages 405–407.
4. The app takes exactly those notes from the package (clipped to each note's
   extent, with tables kept as `label | FY26 | FY25`).
5. It **sends** the notes, the section's primary statements and the **LINKED
   FIGURES** (statement lines linked to those notes, with checks) to **Claude
   Code** (`claude -p`).
6. It **shows** the analysis. The answer always includes an *Impact on the
   financial statements* section. Follow-up questions continue the same
   thread, and every answer is saved as a styled HTML report.

The design principle: **deterministic Python finds and extracts exactly the
right content**, and the LLM only analyzes that small extract. Claude never
sees the whole 508-page PDF, has no tools, and always gets the verbatim notes.

Since 2026-09-27 the app is **split into two parts in one program**
(`INFO\SPLIT-FUNCTIONALITY-PLAN.md`): the **Ingester** (`ingest\`: PDF → report
package) and the **Analyzer** (`analyzer\`: package → Claude → answers; it never
reads a PDF). They share the package library `rptpkg\`.

Sample reports: `Annual-reports\Bharat-Forge-IR-2026-conv-single-page.pdf`
(Bharat Forge FY2025-26, 508 pages, Ind AS), `Chubb-10-K-2025.pdf` (US 10-K),
`Reliance-1976-1977.pdf` (old Schedule VI), `BRK-1968.pdf` (scanned 1968 10-K),
`BRK-1994.pdf` (plain-text EDGAR 10-K in a typewriter font).

---

## 2. Folder layout

```
ANN-RPT-ANALYZER\
  .claude\claude.md          this file
  Annual-reports\            input PDFs (the app lists every *.pdf here)
  INFO\
    ANN-RPT-NOTES-ANALYZER-PLAN.md       original plan + confirmed decisions + status
    ANALYZE-OLD-REPORTS-FUNC-PLAN.md     old (Companies Act 1956) reports
    OCR-CAPABILITY-PLAN.md               scanned reports
    SPLIT-FUNCTIONALITY-PLAN.md          ingester / analyzer split, report packages (implemented)
    MODEL-DOCS-FUNCTIONALITY-PLAN.md     match every report to a model document (implemented)
    MODEL-FORMATTED-REPORTS-PLAN.md      the whole extraction as one formatted HTML page (implemented)
    ANALYZE-NON-FIN-DATA-PLAN.md         questions use report passages (MD&A, Board's report …) (implemented)
    STATUS-OF-PROJECT-SEP-28-5PM.md      open items across all plans at that time
    STATUS-OF-PROJ-SEP29-7AM.md          latest status: all features, 2026-09-29 work, open items (read to resume)
    ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md   report sections + page blocks (TYPE-1/3 done)
    HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html   full technical write-up
    HOW-TO-TRAIN-NEW-MODEL-FORMAT.html          user guide: add a format (naming, the prompt, worked example)
    logo.svg, Requirements.txt
  MODEL-DOCS\                one PDF per report format the app can read (TYPE-1 … TYPE-8), README.md
                             (the routine to add a format), _candidates\ (proposed from the app);
                             not in git, not in the exe
  WEB-APP\                   the application (details below)
  WEB-APP-v2\                a copy the user made; not the working app
  S-PAD\                     user's scratch files
  OLD-STUFF\                 an earlier, unrelated pipeline attempt — ignore
```

### WEB-APP files

| File | Role |
|---|---|
| `app.py` | Creates the Flask app, registers both blueprints, wires the analyzer to the ingester (`source.set_loader`), and holds the routes that join them: `/`, `/api/reports`, `/api/index` (ingest if needed → summary), `/api/jobs/<id>` |
| `config.py` | Paths, Claude CLI settings, limits, notes header strings (env-overridable) |
| `claude_client.py` | `run_claude()`: `claude -p` via **stdin** with **all tools disabled**; `fill_prompt()` |
| `webcommon.py` | Shared web plumbing: `UserError`, the job runner, `render_markdown`, report list, `parse_page_spec` |
| **`rptpkg\`** | **Report package** (SQLite): `schema.sql`, `writer.py`, `reader.py` (`Package`: `.index`, `.meta`, `unit_text`, `page`, `ocr_page`, `statement_view`, `lines`, `values`, `edges`, `checks`, `query`), `store.py` (location, SHA-256 key), `model.py` (unit ids) |
| **`ingest\`** | **Ingester** |
| `ingest\pipeline.py` | `ensure_package` / `open_report` / `build_package` / `package_status` / `import_package`; `INGESTER_VERSION` |
| `ingest\notes_index.py` | Notes Index (every note → pages + y), company name, statements; **`INDEX_VERSION`** |
| `ingest\statements.py` | Finds the 4 primary statements per section, **rotates sideways pages**, extracts their text |
| `ingest\clip.py` (was `extractor.py`) | Clips each note / page, rebuilds rows as `a \| b \| c`; `page_record` |
| `ingest\tables.py` (was `statements_view.py`) | Rebuilds statement tables and multi-line column headings **by position** |
| `ingest\figures.py` | Statement lines and figures, periods (FY2026), units |
| `ingest\links.py` | Links line → note (Notes column, same figure, title match), carried figures; checks |
| `ingest\quality.py` | Quality report (stored in the package, shown in step 1, Ingest screen, CLI) |
| `ingest\report_format.py`, `legacy_index.py`, `transcribed_index.py`, `ocr_transcribe.py`, `ocr_quality.py`, `pdf_utils.py` | Formats (modern / us-10k / legacy; transcribed), old reports, scanned reports, PyMuPDF helpers |
| **`ingest\profiles\`** | **Format profiles** (`in-indas-2020s`, `in-schedule-vi-1970s`, `us-10k-edgar`, `us-10k-typewriter`, `scanned-ocr`, `ca-40f-usgaap`, `in-20f-ifrs`, `au-20f-ifrs`): indexer, **switches** (read with `profiles.active()` during ingestion), acceptance checks, `version` |
| **`ingest\models\`** | **Model documents**: `registry.json`, `fingerprint.py`, `match.py` (sha → fingerprint shortlist → trial extraction → verdict; `NewModelDocument`), `acceptance.py`, `gap_report.py`, `python -m ingest.models list/fingerprint/match/register/refresh/verify` |
| `ingest\routes.py` + `templates/ingest.html`, `static/ingest.js` | **Ingest screen** (`/ingest`), export/import, `/page-image`, OCR routes, `/ocr-review` |
| `ingest\__main__.py` | `python -m ingest <pdf> [--format X] [--force]` |
| **`ingest\model_testing.py`**, `ingest\feedback.py`, `ingest\model_test_routes.py` + `templates/model_testing.html`, `static/model_testing.js` | **Model testing screen** (`/model-testing`): one page of a model document + its extraction (read from the package); feedback files, `feedback-status.json`, `python -m ingest.feedback list/show/fixed` |
| **`ingest\narrative\`** | Pages outside notes/statements: `geometry.py` (page → segments, drawings, images), `layout.py` (blocks in reading order, coverage check), `sections.py` (section map), `kinds.py`, `render.py` (section text); `build()` called by the pipeline when the profile switches it on |
| **`ingest\formatted_report.py`** + `templates/formatted_report.html`, `formatted_preparing.html`, `static/formatted_report.js` | **Formatted document** (`/model-testing/formatted?doc=`): a model document's whole extraction as one Bootstrap page; `format_unit_text` (stored text → blocks), `build_document`, `FORMATTER_VERSION`, in-memory `CACHE` |
| `Model-Feedback\`, `Model-Testing\` | Feedback JSONs + audit trail `feedback-status.json`; page PNGs saved with each feedback. **In git** |
| **`analyzer\`** | **Analyzer** (never imports `ingest` or `pymupdf`; `tests/test_boundary.py`) |
| `analyzer\routes.py`, `analyzer\jobs.py` | Routes and the identify / analyze / follow-up jobs, threads, history, saved reports |
| `analyzer\note_selector.py` | Scope rules, Claude note-picker (+ statement hints), keyword fallback |
| `analyzer\linked.py` | `statement_hints` (question → lines → notes), `linked_block` (LINKED FIGURES + CHECKS), `narrative_figures_block` (NARRATIVE FIGURES) |
| `analyzer\passages.py`, `passage_terms.py` | Ranking the report passages for a question (BM25, synonyms, kind boosts, named sections) |
| `analyzer\context.py` | REPORT PROFILE (legacy, transcribed, us-10k), statement names, system prompt |
| `analyzer\source.py`, `extract.py`, `statement_info.py`, `statements_page.py`, `history.py`, `report_html.py` | Package source, extracts from the package, statement labels, statements page, history, HTML reports |
| `prompts/*.txt` | Selector, analysis and follow-up prompts (edit these, not code, to change Claude's behaviour) |
| `templates/`, `static/` | UI (`index.html` + `app.js`: Analyze; `ingest.html` + `ingest.js`: Ingest) |
| `tests/` | 455 pytest tests (Claude mocked); `tests/fixtures/baseline/` = frozen prompts |
| `cache/` | `packages/*.rptpkg.db`, `<pdf>.ocr/` (transcriptions), `threads/*.json` |
| `Prompt-History/`, `Analysis-history/`, `logs/app.log` | Outputs (see §5) |
| `README.md` | User-facing run/usage guide |

---

## 3. How to run

```powershell
cd C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP
pip install -r requirements.txt          # flask, pymupdf, markdown, pytest
python app.py                            # http://127.0.0.1:5000  (Ingest screen: /ingest)
python -m ingest "..\Annual-reports\Chubb-10-K-2025.pdf"    # ingest one report, print its quality report
python -m pytest -q                      # 455 tests, ~8 min, no Claude calls
```

Requirements: Python 3.12, and the Claude Code CLI installed and logged in
(`C:\Users\dayam\.local\bin\claude.exe` by default; override with the
`CLAUDE_EXE` env var).

Environment overrides: `REPORTS_DIR`, `CLAUDE_EXE`, `CLAUDE_MODEL` (unset =
CLI default model), `CLAUDE_VIA_POWERSHELL=1` (use basic-chatbot's original
PowerShell invocation), `PORT`.

The exe is built with the **dashboard builder** `C:\Daya\RAGchamp\PYTHON-EXEC-BUILDER\PYTHON-APP-DASHBOARD`
(launcher style `dashboard`: the Start/Stop control window). **Not** `PYTHON-EXEC-BUILDER\APP`, the classic
builder, whose exe opens a console and the browser directly. Build from a **copy** in
`PYTHON-EXEC-BUILDER\INPUT\PROJ-2\ANN-RPT-ANALYZER\WEB-APP` (refresh it first, e.g. `robocopy /MIR` excluding
the runtime folders), output `PYTHON-EXEC-BUILDER\OUTPUT`, excludes `Analysis-history, Prompt-History,
claude-prompt-input.txt, Claude-prompt-output.txt, Model-Feedback, Model-Testing` (`cache`, `logs` ship empty).
A correct build logs "Style : dashboard window" and a "Dashboard smoke test PASSED" (~34 MB exe). Its scanner
treats `ingest\`, `analyzer\`, `rptpkg\` as local packages and bundles `sqlite3`.

---

## 4. How it works (short version)

Full detail: `INFO\HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html` and `INFO\SPLIT-FUNCTIONALITY-PLAN.md`.

```
POST /api/index     pipeline.ensure_package(): index → clipped texts (notes, statements, every page)
                    → statement tables → lines + figures → links + checks → quality → .rptpkg.db
                    then analyzer.routes.report_summary(package)
POST /api/identify  job: detect_scope() → statement_hints() → Claude call #1 (note titles + hints) → note ids
                    (browser ALWAYS pauses here for the user to confirm/edit)
POST /api/analyze   job: notes from the package + statements + LINKED FIGURES → Claude call #2 → history + HTML + thread
POST /api/followup  job: selector rescan (adds new notes/passages) → thread's notes + statements + LINKED FIGURES + conversation → claude -p
GET  /api/jobs/<id> browser polls every 1-1.5s
GET  /statements    statements page, from the tables stored in the package
GET  /ingest        Ingest screen; /api/ingest/*, /api/packages/export|import, /page-image
```

- **Notes Index:** notes pages are identified by their running header ("Notes to
  Consolidated/Standalone Financial Statements") in the top 20% of the page,
  with at most 4 rows above it. Note headings are bold, left-margin rows in the
  dominant heading font that start with a number, and the numbers must be in
  sequence. A note ends just before the next note's heading.
- **Statements:** the pages within the 15 pages before a section's first notes
  page whose title (≥14pt, or a bold row naming a statement, top 25%) *starts
  with* a statement name. Sideways pages (text direction `(0,-1)`) are redrawn
  with `show_pdf_page(rotate=-90)` before extraction.
- **Package:** one SQLite file per PDF, `cache\packages\<stem>-<sha256[:16]>.rptpkg.db`
  (exe: `%LOCALAPPDATA%\ANN-RPT-ANALYZER\cache\packages`). The reader rebuilds the index
  dict exactly, so note picking and prompts work on it unchanged. Unit ids: `C21`,
  `SCH-E`, `BH-N5`; statements `C-PL`, `C-BS`, `C-CF`, `C-EQ`; lines `C-PL-L019`.
- **Links** (`ingest\links.py`): *references* (Notes / Schedule column, "(note 3)"),
  *ties_to* (a ≥5-digit figure, or two figures of the line, printed in ≤3 notes),
  *mentions* (title match with strong words only), *carries*. **Checks:** subtotal
  (ok / unverified — not a simple sum), balance, carries, note_tie (warn).
- **Claude calls** follow `C:\Daya\RAGchamp\basic-chatbot\app.py`: a fresh
  stateless `claude -p` per request, `--system-prompt`, the last prompt/reply
  in `claude-prompt-input.txt` / `Claude-prompt-output.txt`, and numbered
  `Prompt-History`. The prompt goes on **stdin** (Windows' ~32K command-line
  limit), with `--tools "" --no-session-persistence`.

---

## 5. Outputs the app writes

| Where | What |
|---|---|
| `cache\packages\*.rptpkg.db` | Report packages (see §4); rebuilt when stale |
| `Prompt-History\Claude-code-prompt-N-question.txt` | Metadata header (time sent, kind, report, scope, notes, PDF pages, financial statements, thread, saved HTML) + the question |
| `…-answer.txt` / `…-prompt.txt` | Claude's reply / the full prompt sent (reproducible) |
| `Analysis-history\<pdf name>-<prompt no>-<YYYYMMDD-HHMMSS>.html` | Styled report: RAG Champ nav, light-blue context box, analysis. Prompt no 1 = first question, 2 = first follow-up… Timestamp = when the prompt was sent |
| `cache\threads\<id>.json` | Follow-up thread: notes, extract, statements, turns, `pdf_sha256`, `schema_version` |
| `logs\app.log` | Timings, selected notes, prompt sizes (job-polling lines filtered out) |

---

## 6. Decisions confirmed with the user (do not change without asking)

| Topic | Decision |
|---|---|
| Note confirmation | **Always pause** after identification; user edits notes, then clicks Analyze |
| Note picking | Claude selector call on titles (+ statement hints) + keyword fallback |
| Report selection | Dropdown of PDFs in `Annual-reports\` (no PDF upload) |
| Model | Claude Code CLI default (no `--model`) |
| Follow-ups | Chat-style threads; **every follow-up searches the whole report again** (changed 2026-09-29 at the user's request): what the selector picks is **added** to the thread's notes/passages (earlier ones stay), **no pause**, the answer shows what was added; both flows; "Note 41" adds that note |
| Scope default | Consolidated unless the question says standalone |
| Statements | All 4 primary statements are loaded and sent with every question; the answer must include *Impact on the financial statements* ("Analyze non notes": a short *How it shows in the audited numbers* table instead) |
| Two flows | **Identify notes** = notes only, original prompts; **Analyze non notes** = report passages + ≤ 2 confirming notes; both pause in step 3 |
| Code location | `ANN-RPT-ANALYZER\WEB-APP` |
| History sidebar | Keep it; the user finds it very useful |
| Page numbers | Always **PDF** page numbers (show printed page alongside) |
| Split (Q1) | **One exe, permanently**; the split is in the code (`ingest\` / `analyzer\`) |
| Package (Q2, Q3) | **SQLite**, in the **app cache**; export / import on the Ingest screen |
| Analyzer and PDFs (Q4) | The analyzer **never opens a PDF**; page images and OCR review are on the Ingest screen |
| Prompts (Q5) | All four statements **plus** LINKED FIGURES |
| US 10-K (Q6) | Its own format, `us-10k` |
| Batch (Q7) | **No**: one report at a time |
| Cross-year (Q8) | **Not needed** |
| Claude tools (Q9) | **Never**: no MCP / query tool; Python decides what Claude sees |
| Model documents | Every report is matched to a MODEL-DOCS model first (`INFO\MODEL-DOCS-FUNCTIONALITY-PLAN.md` §14) |
| No match (MD-Q1) | **Blocked completely**: no analysis at all, not even manual pages, until its model is added |
| Model (MD-Q2, Q5) | **One document = one model**, id = file name `TYPE-<n>-<COUNTRY>-<FORM>-<YEAR>-<Company>`; models may share a profile |
| New models (MD-Q3) | The app proposes (`MODEL-DOCS\_candidates\` + gap report); the developer registers |
| Storage (MD-Q6) | MODEL-DOCS outside git; the registry keeps each PDF's SHA-256 |
| Re-match (MD-Q7) | Automatic when the registry version changes |
| Doubt (MD-Q8) | Say "new model document" |
| Claude in gap reports (MD-Q9) | Optional "Describe with Claude" button only; matching stays deterministic |
| Model feedback (MF-Q1…Q7) | `Model-Testing\` = page PNGs saved with feedback; nav = home style without History; statuses Reported / Fixed by Claude code only, fix = same entry + history; feedback allowed on pending models; both folders in git; feedback JSON carries a `context` block. Fixing stays **manual** (§7a) |

---

## 7. Work done so far (chronological)

1. **Plan** written (`INFO\ANN-RPT-NOTES-ANALYZER-PLAN.md`) after probing the
   PDF, and 7 open questions resolved with the user.
2. **Core app built** (Milestones 0–8). Real bugs found and fixed:
   - Sections started too early (the auditor's report mentions "Notes to…") → the header must be the top-of-page row.
   - Notes 3/5/9/11/14/18 were missed ("As at" table headers moved the header cut-off down) → only the line directly under the header counts.
   - Headings without a dot (`5 INTANGIBLE…`), a note that only appears as `54.1/54.2`, and standalone Note 3's heading on a rotated page (inferred from its `(CONTD.)` repeat).
   - PyMuPDF `find_tables()` failed on these borderless tables → rows rebuilt from spans, with `" | "` wherever the gap is over 12pt.
3. **Follow-up spinner** moved to just below the follow-up question.
4. **Analysis-history HTML reports**, company-name detection, and a fix for
   Markdown lists that followed a bold line without a blank line.
5. **HOW document** created in `INFO\`.
6. **Primary financial statements** loaded and sent with every question
   (sideways pages redrawn clockwise; `page.set_rotation()` did **not** work).
7. **View the financial statements** page (new tab, tables, consolidated first).
8. HOW document updated; README kept current.
9. **Equity column headings fixed**: statement rows cached *with x-positions*;
   tables rebuilt by position (now `ingest\clip._rows_with_positions`, `ingest\tables.py`).
10. **Old reports** (Companies Act 1956) and **scanned reports** (OCR by Claude):
    see their plans in `INFO\`.
11. **US 10-K statements and cosmetics** (Chubb-10-K-2025.pdf): small bold titles and
    US GAAP names; index page not a notes page (`MAX_ROWS_ABOVE_HEADER`); `F-6` page
    numbers; lone `$` cells; note titles only wrap when the line reaches the right
    margin (`WRAP_MARGIN_SHARE`); "… and Subsidiaries" sub-line skipped.
12. **Ingester / Analyzer split** (2026-09-27, `INFO\SPLIT-FUNCTIONALITY-PLAN.md`,
    all phases, INDEX_VERSION 17):
    - Phase 0: `tests/prompt_baseline.py` froze every prompt of the 4 samples.
    - Phase 1: modules moved into `ingest\` / `analyzer\`; `rptpkg\` SQLite package
      with the index, every unit's clipped text and every page's text. **Prompts
      byte-identical** (proved by `tests/test_prompt_identity.py`). Page text doubled
      ingest time → `pdf_utils.reuse_page_text()` (Bharat: 16s first time).
    - Phases 2–3: lines, figures, periods, units; links and checks; quality report.
      Precision work: weak words (`WEAK`), whole-title rule, ≥5-digit/two-figure
      ties, "before tax" is incidental, schedule refs need a space/quote.
    - Phase 4: statement hints in the selector and step 3; LINKED FIGURES + CHECKS in
      analysis and follow-up prompts; `us-10k` format (profile, names, system note).
      Baseline re-frozen after reviewing every diff. Real run (Chubb tax, C12 + C14):
      8s to pick, 67s to analyze; Claude confirmed the Note 12 links.
    - Phase 5: Ingest screen, export / import (an imported scanned package is kept
      without a local OCR cache), page images.
13. **Plain-text filings** (BRK-1994.pdf, INDEX_VERSION 24): Courier only, no bold, each
    table row one span. `pdf_utils.split_monospace` splits monospaced spans into columns
    (dot leaders, underlines dropped; enumerators like `(10)` kept with their text);
    statement titles in plain capitals; note headings `(1) TITLE` in capitals when a
    section has no bold headings; continuation pages without the running header filled
    in (`_fill_plain_text_gaps`, 90% Courier rows); in `tables.py`, misaligned columns
    merged and wrapped captions joined - **only for cells marked `m` (monospaced)**, so
    the other reports stay byte-identical (a first, general version changed Bharat's
    equity table and Reliance). BRK-1994 added to the prompt baseline (57 files).
14. **Model documents** (`INFO\MODEL-DOCS-FUNCTIONALITY-PLAN.md`, Phases 1-3 and 5; INDEX_VERSION 25):
    - Found first: `report_format.detect` never said "unknown" - Magna 40-F was "legacy", BHP 20-F
      "modern" with no evidence. Now every report is matched to a model document or refused.
    - Profiles: today's rules became 5 named profiles; the BRK-1994 rules are switches on for
      `us-10k-typewriter` only. `build_index(pdf, profile=…)`. Prompts byte-identical (the only
      baseline change: the new "model" line in `/api/index`).
    - Registry: TYPE-1…5 with profiles, TYPE-6/7/8 **pending** (no rules yet). `verify`: each
      model matches itself, none is accepted by another format's rules (~1.5 min).
    - Matching: sha match; else fingerprint (6-10 s for 500 pages, cached) → hard constraints
      (scanned, typewriter, cover form) → weighted similarity → trial extraction of the best 3
      models *with rules* above 0.55 → acceptance checks; two profiles within 0.05: new model.
      "New model" verdicts are remembered (`cache\matches\`) until registry / ingester /
      `MATCHER_VERSION` change.
    - Found while testing: pending models took shortlist places, so a supported model ranked 4th
      was never tried - fixed (trials go to the best 3 *with rules*).
    - Magna's first PDF was its Annual Information Form (no statements or notes). **Replaced 2026-09-28** by the
      40-F's Exhibit 99.3 (consolidated financial statements, 46 pages), re-registered as pending (registry v9).
      The `in-indas-2020s` rules already find its 26 notes and 4 statements; only the Ind AS / rupee wording check fails.
    - Phase 4: **done** - Magna (item 16), Infosys and BHP (item 17).
15. **Model testing and feedback** (2026-09-28, `INFO\MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md`, all phases):
    `/model-testing` screen (model document dropdown, Page# validated 1…page count, page image at 150 dpi,
    extracted data from the package; pending models show raw PDF text). *Need correction* → comment
    (≤ 400 chars) → `Model-Feedback\<pdf stem>-<YYYYMMDD-HHMMSS>.json` + `Model-Testing\…-p<page>.png` +
    `feedback-status.json` entry (Reported). No change to packages, prompts or `INDEX_VERSION`.
16. **TYPE-6 Magna rules** (2026-09-28, INDEX_VERSION 27, registry v10): TYPE-6 replaced by the 40-F's
    Exhibit 99.3 (financial statements, 46 pages). Profile `ca-40f-usgaap` = `derive(IN_INDAS)` with
    Canadian wording checks ("Chartered Professional Accountants" - in no other model - and U.S./Canadian
    dollars); format `modern`. 26 notes (PDF 10-46), P&L 5-6, BS 7, CF 8, Equity 9; `tests/test_ca_40f.py`.
    Found: the Indian unit patterns matched the "rs" of "dollars in millions" -> `\b` added and a
    "U.S./Canadian dollars in millions" pattern last. A first version without the required currency gave
    BRK-1994 a new units line - caught by the prompt baseline; now every other report is unchanged.
    Magna's balance-sheet totals are unlabeled rows ("(total)"), so no balance check runs yet.
17. **TYPE-7 Infosys and TYPE-8 BHP rules** (2026-09-28, INDEX_VERSION 28, registry v12) - every model
    document now has rules. Both are 20-F filings printed from EDGAR; new profile **switches**:
    - `notes_run_on` (both): the notes heading is printed once; later pages stay in the section until
      another section or a stop page (`Item 19.`, `SIGNATURES`, the auditor's report). The browser's
      print chrome (date line, "Table of Contents", URL footer) is cut from those pages' layout.
    - `decimal_notes` (Infosys): the notes are the `N.M` headings, `no` = "2.1", unit id `C2.1`,
      cited as "2.18" in the Note column (`tables._note_ref`, `links._unit_lookup`).
    - `untitled_statement_pages` (Infosys): p.172 continues the Changes in Equity with no title.
    - `numbered_statement_titles`, `extra_notes_headers` (BHP): "1.1 Consolidated Income Statement",
      "1.6 Notes to the Financial Statements".
    Profiles `in-20f-ifrs` (Form 20-F + Ind AS/rupee) and `au-20f-ifrs` (Form 20-F + Corporations Act
    2001). Infosys: 27 notes 1.1-2.21 (PDF 174-253); BHP: 37 notes (PDF 288-358), a note's "Cash flow
    statement" table (p.295) does not end the run. Shared fixes: a unit row ("US$M") under the years
    keeps the years as headings; `US$M` unit. `tests/test_20f.py`; the "pending" tests now take a
    model's rules away in a patched registry (`make_pending`). Prompt baseline unchanged.
18. **Formatted document** (2026-09-28, `INFO\MODEL-FORMATTED-REPORTS-PLAN.md`, all phases): Model testing
    links *View formatted document ↗* (document) and *View this page in the formatted document ↗* (`#page-N`).
    `/model-testing/formatted?doc=` renders the whole package in PDF page order (Bootstrap 5.3.3 CDN,
    contents panel = `offcanvas-lg` + Scrollspy, print CSS, Download HTML). Anchors: `unit-<id>`,
    `line-<id>`, `page-N` (exactly one per page; ids sanitised, so `C2.1` → `unit-C2_1`). Parser rules
    are display-only; *Show as stored text* fetches `/api/model-testing/formatted/stored-text`. Stale
    package → preparing page + job; pending model → raw PDF text; `?doc=&page=` pre-fills Model testing.
    No change to packages, prompts or `INDEX_VERSION`. `tests/test_formatted_report.py`.
19. **Non-financial-statement content** (2026-09-28, `INFO\ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md`,
    Phases 0–5 for TYPE-1 and TYPE-3; INDEX_VERSION 29, schema 1.1, profiles `in-indas-2020s` v2 / `us-10k-edgar` v2).
    New stage `ingest\narrative\` reads every page outside notes/statements: a **section map** (contents page,
    running headers, Item headings, bookmarks; printed → PDF pages from the footers) and **blocks in reading
    order** (XY-cut; tables incl. row-ruled BRSR tables; lists, badges, panels, KPI metrics, diagrams/charts
    with labels only). A **coverage check** per page: blocks hold exactly the page's characters, else flat text.
    Package tables `doc_sections`, `narrative_pages`, `page_blocks`; **`pages.text` unchanged** (prompts
    identical). Switches `narrative_sections` / `narrative_designed_pages` (off for the other profiles: Phase 6).
    Formatted document shows report sections (+ page-image crops, `clip=`); Model testing shows the blocks.
    Found while building: the contents page's title can sit in the running-header band (Chubb); a divider's
    page number in the next column must not pair with a title (one number style per page); a figure at the foot
    of a designed page is not a page number (footers must agree with the offset); rules drawn one piece per
    cell give the columns. Gold pages: `tests\fixtures\narrative_gold\gold.json`; `tests\test_narrative.py`.
    Feedback `…-105411.json` (p.22) fixed.
20. **Narrative Phase 6: all models** (2026-09-28, plan §16; INDEX_VERSION 31, every profile v2). Sources per
    profile (`narrative_sections`): Infosys Item headings; BHP a two-page contents with numbered groups (its
    running headers are browser print lines: off); Magna / Reliance the new `headings` source (large bold
    page titles) and dot-leader contents; BRK-1994 bookmarks (first level with ≥3 entries = parts);
    BRK-1968 blocks from its Markdown transcription (`ingest\narrative\markdown.py`). New shared rules:
    footer page number above a print footer; top lines on ≥30% of pages are chrome at any size; typewriter
    rows split into cells and caps headings; tables ruled around row groups; paragraphs split at sentence
    ends / wider gaps; section kinds by title words only (never Item numbers). `ingest.models verify` checks
    each model's narrative pages; the gap report lists section clues. 13 gold pages. Every narrative page
    of every model passes the coverage check.
21. **Questions use the report's own sections** (2026-09-28, `INFO\ANALYZE-NON-FIN-DATA-PLAN.md`, all phases;
    INDEX_VERSION 33, schema 1.2). Ingest cuts each section into **passages** (1.5-8 K chars, heading path, money
    figures; `ingest\narrative\passages.py`, table `doc_passages`). The analyzer ranks them per question (BM25 +
    synonyms + kind boosts, `analyzer\passages.py`, `passage_terms.py`); the one selector call picks notes **and**
    passages (`prompts\selector_passages.txt`); step 3 shows a "Report passages" group (Show, suggestions, search).
    Analyses / follow-ups send REPORT SECTIONS + NARRATIVE FIGURES (unit-converting ties, tolerance = half the
    figure's last digit) + a "Business context" part + `analysis_system_sections.txt` - **all empty when no
    passage is chosen, so notes-only prompts are byte-identical**. Questions may use passages without notes.
    Manual pages on narrative pages go in reading order. Block rendering moved to `rptpkg\blocks.py` (the analyzer
    must not import `ingest`). Only the selector baseline files changed. Real run: the CV question on TYPE-1.
22. **Two flows: "Identify notes" / "Analyze non notes"** (2026-09-28, plan §16). The first CV answer was too
    long and accounting-heavy, so item 21's mixed flow was split by `mode`: **notes** = the original flow,
    prompts byte-identical to before item 21 (baseline restored); **business** = `select_for_business`
    (≤ 4 passages, ≤ 2 confirming notes) and `prompts\business_*.txt` (REPORT SECTIONS first, NARRATIVE
    FIGURES, notes, statements for reference; a "How it shows in the audited numbers" table instead of the
    Impact section). Threads keep their mode. Ties: approximate only with a shared word, exact alone
    (`linked._ties`). Step 3 always pauses in both modes. Real run: 8 s + 31 s, 965 words.
23. **Narrative units become passages** (2026-09-29, INDEX_VERSION 34). Found by the user: on BRK-1968,
    "Analyze non notes" said the report "has not been read into passages" although it had been re-ingested.
    Old and scanned reports keep their prose as `kind: "narrative"` *units* (BRK-1968 `BH-TXT` filing text
    p.1-5, the auditors' reports; Reliance `SUM`, `DIR`, `AUD`), and `covered_pages` keeps unit pages out of
    the narrative stage, so no passage held them. `narrative._unit_pages` now also cuts those units' pages into
    passages (section ids `U-<unit id>`, kind from the title, the company in the path when a report has
    several); their blocks are **not** stored and their pages stay units, so notes prompts are unchanged.
    `analyzer.jobs._identify_business` now says "none of the passages uses the words of the question" when
    passages exist but none match, and asks for a re-ingest only when there are none.
    Same day: step 3 showed only a passage's path (its lead was the cover page), so each candidate now carries
    `snippet` (the sentence with most question words, `analyzer.passages.snippet`) and `matched` (the words as
    printed); `static/app.js` shows the snippet under the passage and marks the words there and in *Show*
    (DOM nodes, no innerHTML). Prompts unchanged. Checked in Edge with Playwright: follow-up works after a
    business answer (the button is disabled only while the follow-up box is empty).
24. **Follow-ups search the report again** (2026-09-29, user's choices: add, no pause, both flows). `analyzer.jobs`:
    `_rescan_notes` runs `select_notes` on the follow-up + the thread's first question (`_followup_question`)
    and adds up to `MAX_FOLLOWUP_NEW_NOTES` (3) new notes (keyword fallback adds none; statements follow a new
    section); `_rescan_business` ranks passages for the follow-up, runs `select_for_business` and adds up to
    `MAX_FOLLOWUP_NEW_PASSAGES` (2) passages + `MAX_BUSINESS_NOTES` notes. Additions that would pass
    `MAX_CONTEXT_CHARS` are skipped. Results carry `rescanned`; the UI says what was added or "nothing new".
    Baseline: only `Reliance-1976-1977.3-followup.prompt.txt` changed (its canned reply adds the Directors' Report).

---

## 7a. Fixing a model feedback report (the routine)

When the user says *"fix the model feedback `Model-Feedback\X.json`"* (work in `WEB-APP\`):

1. **Read** `Model-Feedback\X.json` (PDF, page, `user_feedback`, `extracted_data`, `context` with the
   profile / index versions) and look at the page image named in `context.page_image` (`Model-Testing\`).
2. **Reproduce**: `python -m ingest.feedback show X.json` re-extracts the page now and diffs it with the
   report. If it already changed, tell the user instead of fixing blindly.
3. **Fix the rules** per `MODEL-DOCS\README.md`: the model's profile; a rule only that format needs goes
   behind a profile **switch**, never an `if` in shared code. Bump the profile's `version` (or
   `INDEX_VERSION` for shared code) so packages rebuild. A model registered `--pending` needs a profile first.
4. **Regression test** in `tests\test_feedback_regressions.py`, one test per feedback file, named after it,
   asserting the corrected output for that page (use `ingest.model_testing.extract_page`).
5. **Verify**: `python -m pytest -q` and `python -m ingest.models verify`. Re-freeze the prompt baseline
   only for the documents the fix was meant to change; any other model changing means the fix leaked.
6. **Record it**: `python -m ingest.feedback fixed X.json --summary "…" --changed <files> --test <test id>`
   (never edit `feedback-status.json` or the feedback file by hand).
7. Tell the user what changed; they can re-run *Test extraction* on the same page.

---

## 8. Testing and verification

- `python -m pytest -q` → **455 passed** (~8 min); `-m "not slow"` skips the
  large MODEL-DOCS checks. Claude is mocked (`run_claude`
  monkeypatched on `analyzer.jobs` / `analyzer.note_selector`), so the tests are
  free. They run against the real sample PDFs and **skip** those that are missing.
- **`tests/test_prompt_identity.py`** compares every prompt, system prompt,
  `/api/index` answer and statements page of the 5 samples with
  `tests/fixtures/baseline/`. When a prompt is *meant* to change:
  `python tests/prompt_baseline.py --freeze`, then review `git diff` of the baseline.
- Main facts the tests pin down, which should stay true:
  - Consolidated **Note 21 = PDF 405–407** (printed 402–404); 59 consolidated notes (PDF 348–503), 56 standalone (PDF 210–327).
  - Consolidated statements: P&L 342–343, BS 341, CF 346–347, **Changes in Equity 344–345 (rotated −90°)**. Standalone: 206, 205, 208–209, 207.
  - The P&L has `Current tax | 5,606.07 | 5,848.54`; the BS line "(c) Deferred tax liabilities (net)" cites note 21 and links to C21.
  - Chubb: format `us-10k`, 22 notes (PDF 111–212), statements p.107–110, Income tax expense 2,422 (FY2025) → C12; both balance sheets balance.
- **Visual checks:** headless Edge screenshots:
  ```bash
  "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
     --hide-scrollbars --window-size=1280,2400 --virtual-time-budget=8000 \
     --screenshot="<scratchpad>/shot.png" "http://127.0.0.1:5000/ingest"
  ```
  To screenshot the Analyze screen with a report selected, a temporary same-origin
  page in `static/` can load `/` in an iframe and set the report select (delete it after).
  Run `node --check static/app.js static/ingest.js` after JS edits.

---

## 9. Gotchas already learned (read before changing code)

- **`import pymupdf`, never `import fitz`**: a conflicting `fitz` package on
  this machine breaks it.
- **PDF page ≠ printed page** (offset 3 in the sample). The code uses PDF page numbers throughout.
- **Rotated content:** check line `dir`, not `page.rotation`. Redraw with `show_pdf_page(rotate=…)`.
- **`--tools ""`** disables all Claude tools. PowerShell 5.1 **drops empty-string
  arguments**, so the PowerShell fallback uses `--disallowedTools` instead.
- Jobs live **in memory**; the app runs with `use_reloader=False`. Restart the
  server after Python changes. Static JS/CSS changes need only a browser **Ctrl+F5**.
- **Stale servers on Windows:** stopping the shell task may leave Python on the port:
  `Get-NetTCPConnection -LocalPort 5000 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }`
- **Which version to bump:** a profile's rules changed → that profile's `version` (only its reports
  rebuild); the fingerprint features → `FINGERPRINT_VERSION` + `python -m ingest.models refresh`;
  the matcher or acceptance checks → `MATCHER_VERSION` (re-judges "new model" verdicts); shared
  ingestion code → `INDEX_VERSION`. Adding or changing a model goes through `python -m ingest.models`
  (it bumps the registry version, which re-matches packages).
- Rules for one format go behind a **profile switch**, never an `if` in shared code (see
  `MODEL-DOCS\README.md`).
- Narrative quality lines start with `Narrative`, **never `Warning:`**: the `/api/index` summary (frozen in the
  prompt baseline) shows the `Warning:` lines.
- Packages are rebuilt when `INDEX_VERSION` (`ingest\notes_index.py`, currently **34**)
  changes. **Bump it whenever you change what goes into a package** — otherwise tests
  and the app silently use old packages.
- `CURRENT_JOB` is thread-local: capture `current_job_id()` before handing work to a
  thread pool (the OCR job does).
- The analyzer must not import `ingest` or `pymupdf` (`tests/test_boundary.py`); it
  gets packages through `analyzer.source` (the loader is set in `app.py`).
- Claude's Markdown is HTML-escaped before rendering (`render_markdown`);
  keep it that way. `_separate_lists()` fixes lists without a blank line before them.
- **Editing via Python heredocs in Git Bash mangles backslashes:** `\n`, `\d` change,
  and **`\b` becomes a literal backspace character** (a regex then silently never
  matches). Use the Edit tool, or write the patch script to a file. Scan for control
  characters after bulk edits.

---

## 10. Reusing this app / handing it to another Claude Code

### To run it on another machine
1. Copy the `WEB-APP` folder (and optionally `INFO\` for the docs).
2. `pip install -r requirements.txt`; install and log in to Claude Code.
3. Set `REPORTS_DIR` to your PDFs folder and `CLAUDE_EXE` to your `claude`
   binary (or edit the defaults in `config.py`).
4. Start with `python app.py`. Delete `cache\`, `Prompt-History\`,
   `Analysis-history\` and `logs\` contents for a clean start. To keep a scanned
   report's paid transcription, export its package first (Ingest screen) and import it.

### To use it on a different company's report
1. Drop the PDF in `REPORTS_DIR`, select it (or `python -m ingest <pdf>`), and read
   the quality report: note counts and ranges, statements found, links, checks, warnings.
2. If notes aren't found, the report probably words its running header
   differently. Add the wording to `NOTES_HEADERS` in `config.py`.
3. If statements aren't found, check that their titles fit
   `STATEMENT_TYPES` in `ingest\statements.py` and sit within `STATEMENTS_WINDOW`
   (15) pages before the notes.
4. Add a golden test for that report, like `tests/test_ten_k.py`, and add it to
   `tests/prompt_baseline.py` if its prompts should be frozen.
   Until then, the manual "Or analyze specific PDF pages" box always works.

### Suggested first prompt for another Claude Code session
> Read `.claude/claude.md`, `WEB-APP/README.md`, `INFO/SPLIT-FUNCTIONALITY-PLAN.md` and
> `INFO/HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html`. Run `python -m pytest -q` in
> `WEB-APP` to confirm 455 tests pass. Then <describe the enhancement>. Keep the
> decisions in §6, add tests for the change, and update README and the HOW doc.

### Conventions for enhancements
- Match the surrounding style: small modules, docstrings that explain *why*,
  and comments only where logic is non-obvious.
- PDF work belongs in `ingest\` (and goes into the package); question/answer work in
  `analyzer\`, reading only the package.
- Change Claude's behaviour in `prompts/*.txt` first; templates use
  `{{KEY}}` placeholders filled by `fill_prompt()` in one pass.
- Every change gets tests. Mock `run_claude` (see `tests/test_app.py`); use
  the real PDFs for extraction logic. Re-freeze the baseline only for intended prompt changes.
- Verify UI changes visually (headless Edge screenshot) and, for prompt
  changes, with **one** real run (each costs Claude usage: ~6s to pick notes, ~1-2 min to analyze).
- Keep `README.md`, the HOW doc and the plans' status sections current.
- Keep the app local-only (`127.0.0.1`); there is no authentication.

### Ideas not yet built (backlog)
- Pre-built question templates (Tax, Debt, Related parties, Contingencies) — easy on top of the package.
- Export to Word/PDF; browser PDF upload (deliberately left out for now).
- Streaming is already used for answers; more subtotal patterns (profit = income − expenses) in the checks.
