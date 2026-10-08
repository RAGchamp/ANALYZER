# Annual Report Foot Notes Analyzer

A local Flask app for in-depth analysis of the **notes to the financial
statements** in an annual report PDF. It finds the relevant note(s),
extracts exactly those pages, and asks **Claude Code** (`claude -p`) for
the analysis. The Claude interaction reuses the pattern from
`C:\Daya\RAGchamp\basic-chatbot`.

The app has two parts in one program (`..\INFO\SPLIT-FUNCTIONALITY-PLAN.md`):

- the **Ingester** (`ingest\`) reads a PDF once and writes a **report package**
  (`rptpkg\`, one SQLite file): its format, notes and statements, the text of every
  page, the statement figures, links between statement lines and notes, and
  automatic checks;
- the **Analyzer** (`analyzer\`) answers questions from the package with Claude. It
  never reads the PDF.

Plans: `..\INFO\ANN-RPT-NOTES-ANALYZER-PLAN.md`, `..\INFO\SPLIT-FUNCTIONALITY-PLAN.md`

## Run

```powershell
cd C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

Requirements: Python 3.12, and the Claude Code CLI logged in
(`C:\Users\dayam\.local\bin\claude.exe`, or set `CLAUDE_EXE`).

## How to use

1. **Select report**: pick a PDF from `ANN-RPT-ANALYZER\Annual-reports\`.
   To add a report, copy the PDF into that folder and click ↻. The first
   time a report is used, it is **ingested** into a report package (about 16s
   for a 500-page report; after that it opens instantly). Its notes are indexed
   and its **primary financial statements are loaded**: the Statement of Profit
   and Loss, Balance Sheet, Cash Flow Statement and Statement of Changes in
   Equity, for both consolidated and standalone. Sideways pages, such as the
   consolidated Statement of Changes in Equity on PDF pp.344–345, are rotated
   90° clockwise before extraction. Step 1 lists them (each can be expanded to
   see the extracted text) and shows what the ingester found and checked:
   statement figures and periods, how many lines are linked to a note, the
   automatic checks, and any warnings.
2. **Ask** a question, e.g. *Analyze the company's Tax related liabilities
   and comment on it.* The app uses the **consolidated** notes unless the
   question says standalone (or "not consolidated", "parent company only").
   It uses both if the question mentions both.
3. **Confirm the notes**: Claude picks the notes from their titles, helped by the
   notes *behind the statement lines your question is about* (a tax question: the
   lines Current tax, Deferred tax liabilities… are linked to Note 21). Those are
   shown as **Suggested by the financial statements** buttons; click one to add it.
   You can remove or add notes, or type PDF pages to analyze instead. Then click **Analyze**.
4. **Read the analysis** (it takes 1–3 minutes). The notes are always sent
   together with the primary statements for the same section, plus **LINKED
   FIGURES**: the statement lines linked to the chosen notes, with their figures,
   how each link was found, and the automatic checks (Claude is told to verify
   them against the text). The answer includes an **Impact on the financial
   statements** section. That section ties the note figures to statement line
   items and explains the effect on the P&L, Balance Sheet, Cash Flow and Changes
   in Equity. Expand *Source extract* to see exactly what was sent. Copy it or
   download it as .md or .html.
5. **Follow-ups**: each follow-up searches the whole report again (one small Claude call, ~8 s)
   and adds the notes it needs (at most 3 new ones) to the thread; the earlier notes stay, and
   the answer says what was added. Mention "Note 41" to add that note. A thread on manual PDF
   pages keeps its pages.
6. **History** (left panel): reopen any past analysis and continue its thread.

## Report packages and the Ingest screen

**Ingest screen ↗** (top right, or http://127.0.0.1:5000/ingest) lists every report
with its package status (*up to date*, *stale* = made by an older version of the app
and rebuilt when next opened, or *not ingested*) and what was found and checked. From
there you can:

- **Ingest / Re-ingest** one report at a time, optionally forcing a format
  (Modern, US Form 10-K, Old). Scanned reports get a **Transcribe** button first.
- **View statements ↗** and, for scanned reports, **Review scan ↗**.
- **View a page**: any PDF page as an image.
- **Export package** / **Import a package**: move a package to another PC. It is used
  for the PDF with the same content, whatever its file name (the PDF must still be
  in the reports folder). Most useful for a scanned report, whose transcription
  then isn't paid for twice.

A package (`cache\packages\<pdf name>-<hash>.rptpkg.db`; in the exe
`%LOCALAPPDATA%\ANN-RPT-ANALYZER\cache\packages`) holds:

| What | Where it comes from |
|---|---|
| Format, sections, notes / schedules, statements | the indexers (`ingest\notes_index.py`, `legacy_index.py`, `transcribed_index.py`, `statements.py`) |
| The clipped text of every note and statement, and of every page | `ingest\clip.py` — exactly what Claude receives |
| Statement tables, lines and figures, with periods ("FY2026") and units | `ingest\tables.py`, `ingest\figures.py` |
| Links: line → note (Notes column, same figure in the note, title match), figures carried between statements | `ingest\links.py` |
| Checks: subtotals, the balance sheet balances, carried figures, note figures | `ingest\links.py` |
| Quality report | `ingest\quality.py` |

From the command line (one report at a time):

```powershell
python -m ingest "..\Annual-reports\Chubb-10-K-2025.pdf"            # prints the quality report
python -m ingest "..\Annual-reports\Chubb-10-K-2025.pdf" --force    # rebuild
```

A package is rebuilt automatically when the PDF changes, when the ingester changes
(`INDEX_VERSION` in `ingest\notes_index.py` — bump it whenever what goes into a
package changes), when you choose another format, or when a scanned page is
re-transcribed.

## Model documents: every report must match a known format

`..\MODEL-DOCS\` holds one PDF per report format the app can read (8 today;
`..\INFO\MODEL-DOCS-FUNCTIONALITY-PLAN.md`, routine in `..\MODEL-DOCS\README.md`).
Before a report is ingested it is **matched** to them:

1. A PDF that **is** a model document (same checksum) uses that model's rules.
2. Otherwise its **fingerprint** (fonts, bold, cover form, accounting framework, currency, how
   the notes and statements are headed, …) is compared with every model's, and the three
   closest models with rules get a **trial extraction**. It must pass that model's checks
   (enough notes numbered from 1, enough statements, readable figures, a balancing balance
   sheet, the format's wording such as "Form 10-K" / "Item 8").
3. **No model passes** (or two formats pass equally well): it is a **New model document**. Step 1
   and the Ingest screen say so, name the nearest models and what their rules found, and it
   **can't be analyzed at all** until its format is added. *View gap report* shows what a
   developer needs; *Propose as a model document* copies it with its gap report to
   `MODEL-DOCS\_candidates\`; *Describe with Claude* (optional, uses your plan) adds a layout
   description from a few page images.

The matched model shows in step 1 ("Model: TYPE-3 USA 10-K 2025 Chubb (model document; checks
passed)"). Choosing a report format by hand only chooses which models to try: they must still
pass their checks. All eight model documents have rules: Magna (40-F, TYPE-6) is read with
`ca-40f-usgaap` (the Ind AS layout rules with Canadian wording checks), Infosys (20-F, TYPE-7) with
`in-20f-ifrs` (notes numbered 2.1, 2.2 …) and BHP (20-F, TYPE-8) with `au-20f-ifrs` (numbered
statement titles); both 20-Fs print their notes heading only once.

```powershell
python -m ingest.models list                       # the models and their rules
python -m ingest.models match "..\Annual-reports\X.pdf"
python -m ingest.models register "..\MODEL-DOCS\TYPE-9-….pdf" --profile <id> --description "…"
python -m ingest.models verify                     # each model matches itself and no other
```

To add a new format, follow `../INFO/HOW-TO-TRAIN-NEW-MODEL-FORMAT.html` (where to put and how to
name the PDF, the exact prompt for Claude Code, and a worked example).

## Model testing and feedback

**Model testing** (top right on every screen, or http://127.0.0.1:5000/model-testing) checks
what the app extracts from any page of a model document (`..\INFO\MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md`):

1. Choose a **model document** (every PDF in `..\MODEL-DOCS\`) and enter a **Page#** (the PDF page
   number; a number outside the PDF, e.g. 525 of 508, says *out of range*).
2. **Test extraction** shows the page exactly as it is in the PDF and, below it, the data the app
   extracted from it: what is on the page (notes, schedules, statements), the page text as Claude
   receives it, and on statement pages each line with its figures, periods and linked notes. It
   comes from the same report package the Analyze screen uses. For a model whose rules are not
   written yet (none today) the page's raw PDF text is shown, labelled as such.
3. If it is wrong, tick **Need correction**, describe the problem (max 400 characters) and click
   **Submit feedback**. The app writes:
   - `Model-Feedback\<pdf name>-<YYYYMMDD-HHMMSS>.json`: the PDF, page, the extracted data (made
     again by the server), your comment, and the rule versions that produced it;
   - `Model-Testing\<same name>-p<page>.png`: the page you looked at;
   - an entry in `Model-Feedback\feedback-status.json` with status **Reported**.

To fix a report, give its file name to Claude Code ("Fix the model feedback
`Model-Feedback\….json`"). The routine is in `..\.claude\CLAUDE.md`; when done it runs
`python -m ingest.feedback fixed <file> --summary "…"`, which sets the status to **Fixed by
Claude code** with a timestamp and keeps the history, so `feedback-status.json` is the audit trail.

**View formatted document ↗** (next to the document's page count, and *View this page in the
formatted document ↗* after a test) opens the **whole report as the app extracted it** in a new tab
(`/model-testing/formatted?doc=…`, `..\INFO\MODEL-FORMATTED-REPORTS-PLAN.md`): a responsive
Bootstrap page in PDF page order, with a contents panel (a side column on wide screens, a
*☰ Contents* button on phones), the statements as tables, every note with its `label | FY26 | FY25`
rows shown as tables, the pages outside any note or statement (collapsed), and the checks. Statement
Notes-column numbers link to their note; each note lists the statement lines *linked from* it; every
page has a **Test this page ↗** link back to this screen (`/model-testing?doc=…&page=N` fills in
the page and tests it). *Show as stored text* shows a note exactly as it is in the package, so a
display quirk can be told from an extraction error. **Print** and **Download HTML** are at the top.
Nothing is re-extracted and Claude is not called; a model without rules shows its raw PDF text,
labelled. The first view of a document that is not ingested yet shows *Preparing…* while it ingests.

**Pages outside the notes and statements** (MD&A, board's report, governance, risk factors, BRSR, 10-K
Items; `..\INFO\ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md`) are read into the report's own
**sections** (from its PDF bookmarks, contents page, running headers, `Item N.` headings or, as a last
resort, its large page headings) and **blocks in reading order**: headings, paragraphs, lists, tables,
KPI figures, boxed panels, and diagrams / charts (their labels, plus a crop of the page image; chart values
are never guessed). The formatted document shows them by section, and Model testing lists a page's blocks.
A page whose blocks would lose or add a single character keeps its flat text instead. This runs for every
model format (scanned reports: from their transcription).

**Two buttons in step 2** (`..\INFO\ANALYZE-NON-FIN-DATA-PLAN.md` §16). Each section is cut into passages
("MD&A › Company review of the exports auto market › Commercial Vehicles (CV)", PDF 76).

- **Identify notes** is the original financial-statement analysis: Claude picks notes only, and the notes,
  the four statements and LINKED FIGURES go to Claude with the original prompts (answer with *Impact on the
  financial statements*).
- **Analyze non notes** is a business analysis from the report's own sections (MD&A, Board's report, risk
  factors …). Claude picks up to 4 passages and at most 2 notes that confirm their figures (e.g. segment
  information). Step 3 still pauses: **Report passages** (untick, *Show*, *Also related*, *Add passage…*)
  and the notes. The prompt sends REPORT SECTIONS first, NARRATIVE FIGURES (each money amount converted to
  the statements' unit and matched to a statement line - exactly, or approximately when the line shares a
  word with the figure's context - or shown as not disclosed), the notes, and the statements for reference.
  The answer (500-900 words) covers Summary, What management says, Performance and drivers, Outlook and
  risks, a short *How it shows in the audited numbers* table, Questions for management and Sources.
  Follow-ups stay in that mode and search the report again: up to 2 new passages (and 2 confirming notes) are
  added to the thread; naming a section ("what does the Board's report say …") adds its best passage.

*Or analyze specific PDF pages* sends narrative pages in their reading order.

```powershell
python -m ingest.feedback list [--open]            # every report, or only those not fixed yet
python -m ingest.feedback show <feedback file>     # the report, and the page extracted again now (diff)
python -m ingest.feedback fixed <feedback file> --summary "…" [--changed a.py …] [--test tests\…::test_x]
```

## Old annual reports (Companies Act 1956)

Old reports, e.g. `Reliance-1976-1977.pdf`, have no "Notes to … Financial
Statements". Instead they have a Balance Sheet and Profit and Loss Account,
lettered **Schedules** ('A', 'B', …) and a schedule of numbered **notes**.
The app detects this when it indexes the report. Step 1 then shows
*Format: Old (schedules, Companies Act 1956)*, which you can change with the
**Report format** dropdown if it was detected wrongly.

- **What you can pick:** schedules (`Schedule 'E' — Fixed Assets`), numbered notes
  (`Note 25 — Contingent Liabilities`; untitled notes show their first words in
  italics) and report sections (Directors' Report, Auditors' Report, Financial
  Summary). Accounts are standalone only.
- **Follow-ups:** mention "Schedule E", "note 25" or "the Directors' Report" to
  add it to the thread.
- **What the prompt tells Claude:**
  - which statements exist (and which didn't at the time)
  - to work in Rs. lakhs/crores
  - to annualise periods that aren't 12 months
  - to flag totals that don't add up as possible transcription errors
  - to derive a funds-flow when cash matters
- **Sideways pages:** pages printed sideways, such as the Fixed Assets schedule,
  are turned before reading.

## US 10-K reports

Current US Form 10-K PDFs, such as `Chubb-10-K-2025.pdf`, are detected as the
**US Form 10-K** format ("FORM 10-K" on the cover) and index like the modern
Indian reports. Their prompts get a REPORT PROFILE (US GAAP, three years, units),
US statement names (Income Statement, Shareholders' Equity) and a short system
note. Their statements have no Notes column, so lines are linked to notes by
matching figures and titles. The other differences are handled automatically:

- **Statement titles** are small bold capitals ("CONSOLIDATED BALANCE SHEETS"), not
  large headings. They use US GAAP names: *Statements of Operations (and
  Comprehensive Income)*, *Balance Sheets*, *Statements of Cash Flows*, and
  *Statements of Shareholders' / Stockholders' Equity*.
- **The index page** that lists "Notes to Consolidated Financial Statements" isn't
  mistaken for the first notes page.
- **Page numbers** are shown as printed (`F-6`), and are cut off the extracted text.
- **Dollar signs** printed in their own column are joined to the amount
  (`Net income | $10,622 | $9,640`).
- **Note titles** stop at the heading line ("13. Debt", not "Debt December 31
  December 31"), and the "… and Subsidiaries" line under each page header is skipped.

Chubb 2025: 22 consolidated notes (PDF 111–212), with the P&L on p.108, the BS on
p.107, the CF on p.110 and Equity on p.109.

## Plain-text filings (EDGAR 10-Ks of the 1990s)

Old SEC filings such as `BRK-1994.pdf` (Berkshire Hathaway's 1994 10-K, converted from
EDGAR text by Thomson Reuters) are typewriter pages: one Courier font and size, no
bold, tables made of spaces and dot leaders. They are detected as US Form 10-K and
handled automatically:

- **Table rows** are one text span per line; they are split into columns at runs of
  spaces, dot leaders (`. . . .`) and underlines (`-----`) are dropped, and a stray
  page number printed mid-page is ignored.
- **Statement titles** are plain capitals ("CONSOLIDATED BALANCE SHEETS"), and **note
  headings** are `(1)   SIGNIFICANT ACCOUNTING POLICIES` in capitals, not bold.
- **Continuation pages:** a filing page that overflows continues on a PDF page without
  the notes' running header; such pages inside the notes are kept in the section.
- **Columns:** year headings and per-share figures are not exactly under their amounts,
  so a nearby column that never shares a row with the next one is merged into it.
- **Wrapped captions** ("Other than temporary decline in value of investment in" /
  "USAir Group, Inc. Preferred Stock ... 268,500") are joined into one line.

These rules only apply to text in a typewriter font, so other reports are unchanged
(the prompt baseline proves it). BRK-1994: 17 notes (PDF 26–44), BS p.23, P&L p.24,
CF p.25 (no separate equity statement), figures for FY1994–FY1992.

## Scanned reports (OCR)

Image-only PDFs, such as `BRK-1968.pdf` (Berkshire Hathaway's 1968 Form 10-K, a
microfilm scan), are **transcribed by Claude once**, page by page. Then they are
analyzed like any other report. See `..\INFO\OCR-CAPABILITY-PLAN.md`.

1. **Select the report.** Step 1 shows *"This report is scanned…"* with an estimate
   (BRK-1968: 39 pages, about 6 minutes, about $5 at API rates, on your Claude
   Code plan). Click **Transcribe**, or type a page range and click **Transcribe these
   pages**.
   - Progress shows page by page, and **Cancel** stops the job; pages already done
     are kept.
   - Everything is cached in `cache\<report>.ocr\`: the page images, Markdown and
     checks.
2. **Checks on the transcription:**
   - `[?]` marks characters Claude couldn't read.
   - Totals are checked against their figures automatically.
   - If Tesseract is installed (`C:\Program Files\Tesseract-OCR`, or set
     `TESSERACT_EXE`), each figure is cross-checked with it. Tesseract is optional
     and never changes the text.
3. **Review transcription ↗** opens the page image next to its transcription.
   - Unreadable characters are shown in red, and unconfirmed figures in yellow.
   - **Re-transcribe this page** (optionally at 200 dpi) redoes one page.
4. **Each company is a section.** A 10-K with subsidiaries' own statements gets
   one section per company, with ids such as `BH-N5`, `NI-SCH-V`, `NFMI-AUD`.
   Questions use the registrant unless they name a company or say "subsidiaries".
5. **What the prompt includes:** a REPORT PROFILE, the TRANSCRIPTION CHECKS for the
   pages used, and rules for treating `[?]` and totals that don't add up.

Settings (environment variables):

| Variable | Effect |
|---|---|
| `ANN_RPT_OCR_WORKERS` | Pages transcribed in parallel (default 4) |
| `ANN_RPT_OCR_EFFORT` | Effort for transcription (default `low`) |
| `ANN_RPT_OCR_MODEL` | Model for transcription (default: Claude Code's; `sonnet` was faster but broke more table structure in a trial) |

Page images are sent to Anthropic, just as the extracted text is.

## Files

| File | Purpose |
|---|---|
| `app.py` | Creates the Flask app, registers both parts, and holds the routes that join them: report list, **load a report** (`/api/index`: ingest if needed, then summarise the package), job status |
| `config.py` | Paths, CLI settings, limits, notes header strings |
| `claude_client.py` | `claude -p` via stdin, with tools disabled (used by both parts: analysis, and OCR transcription) |
| `webcommon.py` | Shared web plumbing: user errors, the background job runner, Markdown rendering, the report list |
| **`rptpkg\`** | **The report package** (one SQLite file per report): `schema.sql`, `writer.py` (ingester), `reader.py` (both), `store.py` (where packages live, keyed by the PDF's SHA-256), `model.py` (unit ids) |
| **`ingest\`** | **The Ingester** — PDF → report package |
| `ingest\pipeline.py` | The ingester's entry point: builds, stores, re-uses and imports packages |
| `ingest\__main__.py` | `python -m ingest <pdf>` |
| `ingest\routes.py` + `templates\ingest.html`, `static\ingest.js` | The **Ingest screen**, package export/import, page images, and the scanned-report routes (transcription jobs, `/ocr-review`, `/ocr-image`) |
| `ingest\model_testing.py`, `ingest\feedback.py`, `ingest\model_test_routes.py` + `templates\model_testing.html`, `static\model_testing.js` | The **Model testing** screen: one page of a model document and its extraction; feedback files, `feedback-status.json` and `python -m ingest.feedback` |
| `ingest\narrative\` (`geometry`, `layout`, `sections`, `kinds`, `render`) | Pages outside notes and statements: report sections and blocks in reading order (package tables `doc_sections`, `narrative_pages`, `page_blocks`) |
| `ingest\formatted_report.py` + `templates\formatted_report.html`, `formatted_preparing.html`, `static\formatted_report.js` | The **formatted document**: the whole extraction of a model document as one Bootstrap page (`format_unit_text` turns stored text into tables, headings and paragraphs; `build_document` puts it in page order with anchors and links) |
| `Model-Feedback\`, `Model-Testing\` | Feedback reports with their audit trail, and the page images saved with them (kept in git) |
| `ingest\report_format.py` | Detects modern / US 10-K / old (Companies Act 1956) reports |
| `ingest\notes_index.py` | Builds the Notes Index (note → pages + y) and the loaded statements; `INDEX_VERSION` |
| `ingest\statements.py` | Finds the 4 primary statements per section, rotates sideways pages, and extracts their text |
| `ingest\legacy_index.py` | Old reports: statements, schedules, numbered notes, Directors'/Auditors' Report, Financial Summary |
| `ingest\ocr_transcribe.py`, `ingest\ocr_quality.py`, `ingest\transcribed_index.py` | Scanned reports: page images → `claude -p --tools Read` → cached Markdown; checks; index (one section per company) |
| `ingest\clip.py` | Clips each note / page and rebuilds table rows as `a \| b \| c` |
| `ingest\tables.py` | Rebuilds statement tables by position (the statements page, and the lines and figures) |
| `ingest\figures.py` | Statement lines, figures, periods and units |
| `ingest\links.py` | Links between statement lines and notes, and the automatic checks |
| `ingest\quality.py` | The quality report |
| **`ingest\profiles\`** | **Format profiles**: the extraction rules per format (indexer, switches such as the typewriter rules, acceptance checks) |
| **`ingest\models\`** | **Model documents**: `registry.json` (one entry per MODEL-DOCS PDF), `fingerprint.py`, `match.py` (shortlist, trial extraction, verdict), `acceptance.py`, `gap_report.py`, `python -m ingest.models` |
| `templates\gap_report.html` | The gap report page (`/gap-report?report=…`) |
| `ingest\pdf_utils.py` | PyMuPDF helpers (rows, running header/footer detection) |
| **`analyzer\`** | **The Analyzer** — report package → Claude → answers. Never reads a PDF |
| `analyzer\routes.py` | The Analyze screen's routes: identify, analyze, follow-up, statements page, history |
| `analyzer\jobs.py` | The identify / analyze / follow-up jobs, threads, saved reports |
| `analyzer\note_selector.py` | Scope rules, Claude note picker (with the statements' suggestions), keyword fallback |
| `analyzer\linked.py` | Notes suggested by the statements, and the LINKED FIGURES block |
| `analyzer\context.py` | REPORT PROFILE and the other text around the notes in a prompt |
| `analyzer\extract.py`, `analyzer\statement_info.py`, `analyzer\statements_page.py` | Put together the notes / pages / statements for Claude, and the statements page, from the package |
| `analyzer\history.py`, `analyzer\report_html.py` + `templates\analysis_report.html` | `Prompt-History\` and the styled HTML reports |
| `templates\statements_view.html` | The **View the financial statements** page (`/statements?report=…`) |
| `templates\ocr_review.html` | The **Review transcription** page (`/ocr-review?report=…&page=…`) |
| `prompts\` | System prompts and prompt templates, editable without code changes (`*_legacy.txt` old reports, `*_transcribed.txt` scans, `analysis_system_us10k.txt` US 10-Ks) |
| `claude-prompt-input.txt` / `Claude-prompt-output.txt` | Last prompt and last reply |
| `Prompt-History\` | `-question.txt` (with metadata), `-answer.txt`, `-prompt.txt` (full input) |
| `Analysis-history\` | One styled HTML report per prompt: `<pdf name>-<prompt no>-<YYYYMMDD-HHMMSS>.html` (1 = first question, 2 = first follow-up, …; the time is when the prompt was sent) |
| `cache\` | `packages\` (report packages), `<report>.ocr\` (scanned-page transcriptions), `threads\` (follow-up threads) |
| `logs\app.log` | Timings, selected notes, prompt sizes |

## Tests

```powershell
python -m pytest -q          # 312 tests, about 5 minutes; no Claude calls
python -m pytest -q -m "not slow"   # 297 tests, about 2 minutes (skips the large MODEL-DOCS checks)
```

Claude is mocked in the tests, so running them costs nothing. They use the real
sample reports and skip the ones that are missing:

- `tests\test_prompt_identity.py` runs all four sample reports through the app
  (load, identify, analyze, follow-up, manual pages) and checks that **every prompt,
  system prompt, report-load answer and statements page is byte-identical** to
  `tests\fixtures\baseline\`. When a prompt is *meant* to change, re-freeze it with
  `python tests\prompt_baseline.py --freeze` and review the diff.
- `tests\test_package.py` (round trip, reproducible, renamed/changed PDFs, stale
  packages, the analyzer working with PDF reading switched off),
  `tests\test_boundary.py` (the analyzer never imports the ingester or PyMuPDF),
  `tests\test_figures_links.py`, `tests\test_linked.py`, `tests\test_ingest_screen.py`.
- `tests\test_notes_index.py`: consolidated Note 21 is PDF pages 405–407 (printed
  402–404). `tests\test_ten_k.py`: Chubb. `tests\test_legacy.py`: the old-report path
  (`tests\fixtures\Reliance-1976-1977.pdf`). `tests\test_ocr.py`: the scanned-report
  path, using the recorded transcriptions of BRK-1968 (`tests\fixtures\brk1968.ocr\`).

## Notes

- Page numbers are **PDF** page numbers. The printed page number is shown
  alongside, because in the sample report the printed number is 3 less.
- PyMuPDF must be imported as `pymupdf`: a conflicting `fitz` package is
  installed on this machine.
- Claude runs with `--tools ""` (no tools), so it only analyzes the text
  it's sent. The model is whatever Claude Code is set to; set `CLAUDE_MODEL`
  to override it.
- Scanned (image-only) PDFs are transcribed by Claude first (see above); after
  that they are analyzed from their package like any other report.
