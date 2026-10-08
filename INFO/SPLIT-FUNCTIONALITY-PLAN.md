# Split Functionality — Plan (Report Ingester + Analyzer)

**Date:** 2026-09-27
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `INFO\ANN-RPT-NOTES-ANALYZER-PLAN.md`, `INFO\ANALYZE-OLD-REPORTS-FUNC-PLAN.md`, `INFO\OCR-CAPABILITY-PLAN.md`
**Sample reports:** `Bharat-Forge-IR-2026-conv-single-page.pdf` (modern Ind AS), `Chubb-10-K-2025.pdf` (US 10-K), `Reliance-1976-1977.pdf` (legacy Schedule VI), `BRK-1968.pdf` (scanned)
**Status:** **Implemented** 2026-09-27 (all phases; 226 tests pass). Decisions in §14; what was built and how it differs from this plan in §16.

---

## 0. Goal and summary

Split the app into two functions, **inside one app and one exe**, with a documented, versioned file between them:

1. **Report Ingester.** It detects the report format, finds and extracts **everything** once, and checks it. It writes a **report package**.
2. **Analyzer.** Today's Flask app, minus all PDF logic. It reads only report packages and handles the Claude conversation and the HTML reports.

The idea started as "build a knowledge graph". This plan keeps what is valuable in that idea, **explicit links between the parts of a report**, and avoids what is risky: facts extracted by an LLM *replacing* the source text. The package is a **structured document model with a small, typed set of links**. It is not a free-form entity–relation graph (§3).

The design principle stays the same: **deterministic Python finds and extracts exactly the right content, and Claude analyzes that small extract and always sees the real note text.**

The plan:
1. **Phase 1:** split the code inside the repo, with **no change in behaviour** (prompts byte-for-byte the same).
2. **Phases 2–3:** add parsed numbers, periods, links and checks to the package, plus a quality report.
3. **Phase 4:** let the Analyzer use the links for note picking and the *Impact on the financial statements* section.
4. **Phase 5:** an **Ingest screen** in the same app (status, quality report, OCR review and page views), one report at a time.

Decided **not** to do (§14): a second exe, batch ingestion of a folder, cross-year comparison, and any query tool for Claude.

---

## 1. Where things stand today (verified 2026-09-27)

### 1.1 Modules by responsibility

| Responsibility | Modules | Lines | Destination |
|---|---|---|---|
| PDF reading, rows, layout, page numbers | `pdf_utils.py` | 163 | Ingester |
| Format detection (modern / legacy) | `report_format.py` | 50 | Ingester |
| Modern index (notes, sections, company) | `notes_index.py` | 490 | Ingester |
| Primary statements (find, rotate, extract) | `statements.py` | 243 | Ingester |
| Legacy index (schedules, notes, narrative) | `legacy_index.py` | 527 | Ingester |
| Scanned reports (OCR, checks, index) | `ocr_transcribe.py`, `ocr_quality.py`, `transcribed_index.py` | 896 | Ingester |
| Clipping note text, rebuilding table rows | `extractor.py` | 282 | Ingester (clipping); Analyzer (assembling the extract) |
| Statement tables rebuilt by position | `statements_view.py` | 499 | Ingester (parsing); Analyzer (HTML) |
| Picking notes (rules, Claude, keywords) | `note_selector.py` | 423 | Analyzer |
| Calling Claude Code | `claude_client.py` | 248 | Shared: the Analyzer, plus the Ingester for OCR |
| History, HTML reports, prompts, UI | `history.py`, `report_html.py`, `prompts/`, `templates/`, `static/` | — | Analyzer |
| Routes, jobs, threads | `app.py` | 862 | Split: the OCR routes go to the Ingester |

About 60% of the code (by lines) is ingestion, which is where each new format adds work. In this session alone, supporting the US 10-K touched `statements.py`, `pdf_utils.py`, `notes_index.py`, `extractor.py` and `statements_view.py`.

### 1.2 The boundary that already exists

`notes_index.load_or_build()` writes `cache\<pdf>.notes-index.json` (`INDEX_VERSION` 13). It holds sections, notes (page and y ranges), page layouts, and the four statements **with extracted text and positioned rows**. Most of the Analyzer already works from this JSON.

### 1.3 Where the boundary leaks (these must be fixed for a clean split)

| Leak | Where | Fix in this plan |
|---|---|---|
| Note text is clipped from the PDF **at question time** | `extractor.extract_note()` opens the PDF | The Ingester stores every unit's clipped text in the package |
| Manual page ranges ("Or analyze specific PDF pages") read the PDF | `extractor` page mode | The package stores every page's cleaned text |
| Legacy clean-up is applied at extraction time | `extractor._finish_text()` → `legacy_index.legacy_clean` | Applied once, at ingestion |
| Statement tables are re-derived from positions on every view | `statements_view.parse_page()` | Parsed once; the package stores lines and values |
| The OCR review page and the transcription jobs live in the Analyzer | `/api/ocr/*`, `/ocr-review`, `/ocr-image` | Move to the Ingester UI |
| The cache is keyed by PDF size and mtime | `notes_index.load_or_build` | The package is keyed by **SHA-256** of the PDF |

---

## 2. Why split

| Benefit | Detail |
|---|---|
| **Format handling in one place** | Modern Ind AS, legacy Schedule VI, scanned and US 10-K formats already exist, and more will follow (IFRS in Europe, 20-F, other Indian layouts). Each needs golden tests. The Analyzer should never need to know which format a report is. |
| **Different cost and timing** | Ingestion runs once per report, can be slow and can cost money (OCR is about $5 for BRK-1968). Analysis is interactive. Split up, ingestion happens once per report and every later question opens the package instantly. |
| **Problems surface early** | A quality report at ingestion ("22 notes, 4 statements, 2 subtotals don't add up, 71% of statement lines linked to a note") shows problems **before** anyone asks a question. The 10-K problem fixed on 2026-09-27 only showed up as "no statements" in the Analyzer. |
| **Reuse** | The backlog item *question templates* and any future feature (export, dashboards) become easy once every report is a validated, structured package. |
| **A smaller, stable Analyzer** | It drops PyMuPDF and all the geometry rules. Its tests become fast and don't need the PDFs. |
| **Can be tested** | A package is a plain file that can be inspected, diffed between versions and checked into golden tests. |

Costs, stated honestly:
- The two functions must agree on a **schema contract** (`schema_version`). `INDEX_VERSION` becomes the package schema version and follows the same "bump when content changes" rule. Because both ship in one exe, they always change together, so the contract is simple to keep.
- One more screen (Ingest) in the UI (Phase 5).
- Phase 1 is a refactor with no visible benefit on its own. Its value is in what it enables.

---

## 3. Knowledge graph: what to take from the idea and what to avoid

### 3.1 The options

| Option | What it is | Verdict |
|---|---|---|
| **A. LLM-extracted knowledge graph** | Claude reads the report and emits triples such as `(Chubb, deferred_tax_liability, 1,741, 2025)` into a graph. The analysis queries the triples. | **No.** Notes are mostly narrative: tax positions, contingencies and policies depend on wording, qualifications and context, and triples lose that. Every triple is a possible error that Claude would then treat as fact. Extracting all of a 500-page report with an LLM is slow and expensive. It also breaks the "Claude sees the real text" principle. |
| **B. Graph database (Neo4j and the like)** | Store the document model in a graph DB and query it with Cypher. | **Not needed.** The graph is small (thousands of nodes per report) and has a fixed shape. A graph DB would be one more server to install, and a problem for the exe. |
| **C. Structured report package with a typed links table (recommended)** | A document tree (report → section → statement/note → line/table → value), plus an `edges` table of a few link types, extracted **deterministically** and each with evidence and confidence. The source text is stored alongside, word for word. | **Yes.** It keeps the value of a graph (the links) and costs little. SQLite handles it fine, and it can be exported to a graph format later if ever needed. |

### 3.2 What the "graph" actually looks like

```
Report ─┬─ Section (consolidated | standalone | legacy | registrant)
        │     ├─ Statement (P&L | BS | CF | Equity) ─ Line ─ Value (per period)
        │     └─ Note / Schedule ─ Sub-note ─ Table ─ Row ─ Value (per period)
        └─ Narrative (Directors' Report, Auditor's Report, MD&A …)

Links (edges):
  Line  ──references──►  Note            "Note 21" in the statements' Notes column
  Line  ──ties_to────►   Note row        same figure, same period (5,606.07)
  Line  ──sum_of─────►   Lines           "Total revenues" = the lines above it
  Line  ──carries────►   Line            P&L net income → Equity retained earnings
  Note  ──part_of────►   Note            21(a) inside 21
```

Most of it is a **tree**, the natural structure of the report. The **links** are what add value, and most of them can be found without an LLM.

---

## 4. Target architecture

```
                 ┌──────────────────────── REPORT INGESTER ────────────────────────┐
  PDF ──────────►│ 1 fingerprint  2 text layer? ──no──► OCR (Claude) ─┐            │
                 │ 3 detect format (modern | legacy | 10-K | scanned) ◄┘           │
                 │ 4 index units   5 extract text   6 parse tables & numbers       │
                 │ 7 links         8 checks          9 quality report              │
                 └───────────────────────────────┬─────────────────────────────────┘
                                                 ▼
                              <report>.rptpkg.db   (SQLite, schema_version N)
                                                 │  read-only
                 ┌───────────────────────────────▼──────────── ANALYZER ───────────┐
  Question ─────►│ scope → candidate notes (links + Claude on titles) → user confirms│
                 │ assemble extract: note text + linked lines + statements + checks │
                 │ claude -p (tools disabled) → answer → history + HTML + thread    │
                 └─────────────────────────────────────────────────────────────────┘
```

- **One-way dependency.** The Analyzer imports only `rptpkg` (the package reader), never PyMuPDF or the indexers.
- **Shared library `rptpkg`:** the schema (`schema.sql`), `writer.py` (Ingester only), `reader.py` (both), and a version check.
- **`claude_client.py`** is shared: the Analyzer uses it for analysis, the Ingester only for OCR transcription.

---

## 5. The report package

### 5.1 Format and location

- **SQLite, one file per report:** `<pdf stem>.rptpkg.db`. It's a single file, has no server, can be queried with SQL, and Python's standard library reads it.
- Scanned reports: the existing OCR folder (`<pdf>.ocr\`: page images, Markdown, checks) stays next to it, and the package points to it.
- **Location (decided, Q3):** the app cache. That's `WEB-APP\cache\packages\` when run from source and `%LOCALAPPDATA%\ANN-RPT-ANALYZER\cache\packages\` in the exe. The PDFs folder stays read-only. The Ingest screen offers **export / import** of a package file, to move it to another PC.
- **Key: SHA-256 of the PDF.** If the PDF is renamed or copied, its package is still found. If its content changes, it is re-ingested.

### 5.2 Tables

| Table | Columns (main) | Notes |
|---|---|---|
| `meta` | key, value | `schema_version`, `ingester_version`, `pdf_name`, `pdf_sha256`, `page_count`, `built_at`, `company`, `format`, `format_source` (detected/manual), `format_signals` (JSON), `currency`, `unit_scale` ("million", "lakh", …), `fiscal_year_end`, `periods` (JSON) |
| `pages` | pdf_page, printed, section_id, kind, rotation, top, bottom, text_source, text | `kind`: notes / statement / narrative / other. `text_source`: pdf / ocr. `text` is the cleaned text of the whole page, which the manual page-range mode uses. |
| `sections` | id, kind, label, first_page, last_page | e.g. `C` consolidated, `S` standalone, `L` legacy, `BH` (BRK-1968 companies) |
| `units` | id, section_id, kind, number, title, parent_id, start_page, start_y, end_page, end_y, printed_pages, inferred_start | One row per note, sub-note, schedule, narrative section **and statement**. IDs keep today's scheme: `C21`, `S21`, `L-SCH-E`, `BH-N5`, `C-BS`, `C-PL` |
| `unit_text` | unit_id, pdf_page, seq, text | The clipped text per page, **exactly what Claude receives today** (tables as `label \| FY26 \| FY25`, the `[Column headings, left to right: …]` line for wide tables) |
| `rows` | id, unit_id, pdf_page, seq, cells (JSON with x0/x1), is_heading, is_total, bold, indent | Positioned rows: today's `_rows_with_positions`, for statements and note tables |
| `lines` | id, unit_id, seq, label, note_ref, level, is_total, row_id | A parsed statement line, e.g. `C-PL-L023` "Current tax", `note_ref` "21" |
| `values` | id, owner_id (line or row), column, period_end, period_label, value, raw, scale, currency, sign_source | `raw` `(1,828)` → `value` −1828.0. `period_end` 2026-03-31 or 2025-12-31 |
| `edges` | id, src, dst, kind, method, confidence, evidence | See §7 |
| `checks` | id, kind, subject_id, expected, actual, diff, status, message | Subtotals, ties, balance-sheet balance, OCR checks. Status: ok / warn / fail |
| `ocr_pages` | pdf_page, status, dpi, unreadable_count, tesseract_checked | Scanned reports only; points to the existing OCR cache |

### 5.3 Rules

- **The source text is always stored.** Parsed numbers and links are *extra*, never a replacement.
- **Everything is traceable to a page:** every line, value, edge and check points back to `pdf_page` (+ y), so the Analyzer and the reports can cite it. As today, page numbers are always **PDF page numbers**, with the printed page alongside.
- **Reproducible:** the same PDF and the same ingester version give the same package, apart from `built_at`. A test checks this (§11).
- **Versioning:** a `schema_version` major bump means the Analyzer refuses the package and offers to re-ingest. A minor bump means only new, optional columns or tables were added.

### 5.4 Example: what the Analyzer can ask the package

```sql
-- Which notes explain the tax lines of the consolidated P&L and BS?
SELECT l.label, v.period_label, v.value, e.kind, e.dst AS note, e.confidence
FROM lines l
JOIN values v ON v.owner_id = l.id
JOIN edges  e ON e.src = l.id AND e.kind IN ('references', 'ties_to')
WHERE l.unit_id IN ('C-PL', 'C-BS') AND l.label LIKE '%tax%';
```

---

## 6. Report Ingester

### 6.1 Pipeline

| Step | What | Source today |
|---|---|---|
| 1 Fingerprint | SHA-256, page count, text-layer share | `notes_index.build_index`, `_check_text_layer` |
| 2 OCR (if scanned) | Page-by-page transcription with Claude, `[?]` marks, Tesseract cross-check | `ocr_transcribe`, `ocr_quality` |
| 3 Format | modern / legacy / **us-10k** / scanned. A manual override stays available | `report_format` (10-K becomes a named format instead of "modern") |
| 4 Units | Sections, notes, schedules, narrative sections, statements, with page and y extents | `notes_index`, `legacy_index`, `transcribed_index`, `statements` |
| 5 Text | Clip every unit, rotate sideways pages, clean legacy text | `extractor`, `statements.extract_statement` |
| 6 Tables and numbers | Positioned rows → lines and values; periods from the column headings; unit scale from "(₹ in Million)" / "(in millions of U.S. dollars)" | `statements_view.parse_page`, plus new number parsing |
| 7 Links | §7 | New |
| 8 Checks | §7.3 | New, plus the existing OCR checks |
| 9 Quality report | Summary for people and a JSON file | New |

### 6.2 Number parsing (new, Phase 2)

| Input | Value | Rule |
|---|---|---|
| `1,198.28` | 1198.28 | Western grouping |
| `5,95,11,000` | 59511000 | Indian grouping (legacy) |
| `(1,828)` | −1828 | Brackets mean negative |
| `$10,622`, `₹ 215.24` | 10622, 215.24 | The currency symbol is removed and recorded |
| `—`, `–`, `-`, `––` | 0 (marked nil) | Nil markers |
| `11,57,05.424` | **warning** | Legacy retyping typo: kept raw, flagged in `checks` |
| `25.93` on an EPS line | 25.93, scale 1 | Per-share lines are not multiplied by the unit scale |

### 6.3 Interfaces

- **CLI** (for development and tests): `python -m ingest "Annual-reports\Chubb-10-K-2025.pdf"`. Options: `--format`, `--force`, `--pages` (OCR ranges). **One report at a time**; there is no folder or batch mode (Q7).
- **Ingest screen** (Phase 5, in the same app and exe): report list with status (not ingested / ingested / stale / needs OCR), an **Ingest** button for the selected report, the quality report, the OCR review page and page-image views (moved from the Analyzer, Q4), a **View statements** page, and package export / import.
- Selecting a report that has no package in the Analyzer still works as today: it offers "Ingest now" and runs the Ingester for that one report.
- **Exit codes / status:** ok, ok-with-warnings, failed (with the reason, e.g. "no notes header found — add the wording to `NOTES_HEADERS`").

### 6.4 Quality report (example layout; the link and check counts are illustrative, not measured)

```
Chubb-10-K-2025.pdf  ·  format us-10k (detected)  ·  460 pages  ·  ingested in 14s
Sections    Consolidated: 22 notes (PDF 111–212)
Statements  P&L p.108 · BS p.107 · CF p.110 · Equity p.109   (4/4)
Periods     2025-12-31, 2024-12-31 (BS)  ·  FY2025, FY2024, FY2023 (P&L, CF, Equity)
Links       118 of 164 statement lines linked to a note (72%): 0 by Notes column, 61 by figure match, 57 by title match
Checks      subtotals 38/38 ok · BS balances ok · 2 warnings: "Other investments" (BS 10,749) not found in Note 3
```

---

## 7. Links and checks

### 7.1 Link types

| Kind | From → to | Method | Confidence |
|---|---|---|---|
| `references` | Statement line → note | The **Notes column** ("21", "21(a)"); a legacy **Schedule column** ('E') | High |
| `ties_to` | Statement line → note table row | **Same figure, same period** (a rounding tolerance for figures in different units) | High if the figure is distinctive and there's a single match; medium if several rows match |
| `mentions` | Statement line → note | **Title match**: line label words against note titles and sub-headings (e.g. "Income tax expense" → "Taxation") | Medium |
| `sum_of` | Total line → component lines | Arithmetic check of the lines since the previous total or heading | High when the check passes |
| `carries` | Line → line in another statement | P&L net income → Equity; CF closing cash → BS cash | High when the figures are equal |
| `part_of` | Sub-note → note | Numbering (21.1 in 21) or sub-headings | High |

Each edge stores `evidence`, e.g. `"figure 5,606.07 FY26 on PDF 342 and PDF 406"`, so a person (or Claude) can check it.

### 7.2 By format

| Format | Main link source | Note |
|---|---|---|
| Modern Ind AS (Bharat Forge) | Notes column in every statement | Almost every line is linked with high confidence |
| US 10-K (Chubb) | **No Notes column.** Figure matching plus title matching | Lower coverage; confidence and evidence matter most here |
| Legacy (Reliance 1976–77) | Schedule column ('A'–'N') | Schedule 'O' notes are linked by title match |
| Scanned (BRK-1968) | As the underlying format, from the transcription | Values that fail OCR checks don't create `ties_to` links |

### 7.3 Checks

- **Subtotals:** every total line equals the sum of its components (a tolerance of ±1 in the last digit for rounding).
- **Balance sheet:** total assets = total equity + liabilities, for every period.
- **Across statements:** P&L profit ↔ Equity; CF closing cash ↔ BS cash (when the definitions match).
- **Statement ↔ note:** a line with a `references` link whose figure is not found in the note → a **warning**, not an error (notes often show components only).
- **OCR:** existing `[?]` counts, totals and Tesseract results are brought in as check rows.

---

## 8. Analyzer changes

### 8.1 Unchanged (decisions in `.claude/CLAUDE.md` §6 stay in force)

The pause to confirm notes, the Claude selector with a keyword fallback, the report dropdown, the default CLI model, follow-up threads (with "Note 41" adding a note), Consolidated as the default scope, all four statements sent with every question and the required *Impact on the financial statements* section, the history sidebar, and PDF page numbers with the printed page alongside.

### 8.2 What changes

| Area | Today | After the split |
|---|---|---|
| Loading a report | Builds or loads the index; can take 12s or more, or start OCR | Opens the package, which is instant. With no package: "Not ingested — Ingest now", which runs the Ingester for that report, in the same app |
| Step 1 summary | Note counts and statements | Plus the quality report (format, link coverage, failed checks) |
| Note picking | Claude reads note titles | **Link-assisted:** question keywords → statement lines → linked notes are marked "suggested by the statements" in the selector prompt and in the confirm list. Claude still decides, and the user still confirms |
| Extract sent to Claude | Note text clipped from the PDF at question time | `unit_text` from the package (the same text) |
| Impact section | Claude reads all four statements | The same, plus a **LINKED FIGURES** block: each linked line with its values, the link's kind and evidence, and any related failed checks |
| Statements view | Re-parses positioned rows on every view | Renders `lines` / `values` / `rows` from the package |
| Threads | Store note ids and the extract | Also store `pdf_sha256` and `schema_version`, so an old thread can tell when its package has changed |
| Dependencies | PyMuPDF | **None.** The `analyzer\` code never opens a PDF (Q4); page images and OCR review are on the Ingest screen. PyMuPDF stays in the exe for the Ingester |

### 8.3 Prompt structure (Phase 4)

```
REPORT PROFILE        company, format, periods, currency and unit scale, data-quality summary
NOTES (verbatim)      === Note 21 — Income and deferred taxes (Consolidated) === …   ← unchanged
LINKED FIGURES        C-PL-L023 Current tax | FY26 5,606.07 | FY25 5,848.54 → Note 21 (references; ties_to Note 21 row "Current tax" 5,701.41 − 95.34)
CHECKS                ok: P&L subtotals · warn: …
FINANCIAL STATEMENTS  (all four, as today)
QUESTION              …
```

The rule stays: **the verbatim note text is always sent.** The linked figures only help Claude find its way around; they never replace the text.

Claude's tools stay **disabled** (`--tools ""`). Python always decides exactly what Claude sees; there will be no query tool (MCP or otherwise) over the package (Q9).

---

## 9. Code layout (one folder, one app, one exe — permanent, Q1)

```
WEB-APP\
  app.py                    creates the Flask app and registers both blueprints (URLs unchanged)
  rptpkg\                   schema.sql, reader.py, writer.py, version.py
  ingest\                   __main__.py (CLI), pipeline.py, routes.py (Ingest screen, OCR review, page images),
                            pdf_utils.py, report_format.py, notes_index.py, statements.py, legacy_index.py,
                            transcribed_index.py, ocr_transcribe.py, ocr_quality.py,
                            clip.py (from extractor), tables.py (from statements_view)
  analyzer\                 routes.py, note_selector.py, extract.py (assembly only), statements_html.py,
                            history.py, report_html.py
  claude_client.py          shared (analysis; OCR transcription)
  prompts\ templates\ static\ tests\
```

- The split is **in the code, not in the download**. `ingest\` and `analyzer\` are separate packages with one rule: `analyzer\` imports only `rptpkg` and `claude_client`. A test fails if any `analyzer\` module imports `pymupdf` or anything in `ingest\`.
- There is only one copy of `rptpkg`, so there's no shared-code sync.
- It keeps the single input folder that PYTHON-EXEC-BUILDER needs (one folder, `app.py` as the entry point).

---

## 10. Packaging (the exe) — decided: one exe, permanently (Q1)

- One download with two screens (**Ingest** / **Analyze**). The user never has to handle package files unless they choose to export or import one.
- The exe stays about the same size (PyMuPDF is still needed, by the Ingester).
- `HOW-TO-RUN.txt` must say where packages live (`%LOCALAPPDATA%\ANN-RPT-ANALYZER\cache\packages`).
- Build input: `PYTHON-EXEC-BUILDER\INPUT\PROJ-2\ANN-RPT-ANALYZER\WEB-APP`, with the excludes `Analysis-history`, `Prompt-History`, `cache`, `claude-prompt-input.txt`, `Claude-prompt-output.txt` (as in the 2026-09-27 build). Refresh that input copy from `WEB-APP` before every build.

---

## 11. Testing plan

| Test | What it proves |
|---|---|
| **Baseline freeze (Phase 0)** | Save the current index JSON and the **full prompt text** for fixed questions on all four samples (Bharat Note 21 tax, Chubb Note 12 tax, Reliance Schedule 'O', BRK-1968) |
| **Prompt identity (Phase 1)** | Built from the package, the prompts are **byte-identical** to the baseline. This is the main proof that the refactor changed nothing |
| Existing 114 tests | Stay green throughout; moved to `tests\ingest\` and `tests\analyzer\` |
| Golden package facts | Bharat: `C21` = PDF 405–407; `C-PL` "Current tax" FY26 = 5606.07. Chubb: `C12` Taxation = PDF 175–180; `C-PL` "Income tax expense" 2025 = 2422 |
| Number parsing | A table of the inputs in §6.2 |
| Links | Bharat: every Notes-column reference resolves to an existing unit. Chubb: "Income tax expense" → `C12`, "Deferred tax liabilities" → `C12`, "Goodwill" → `C7` |
| Checks | Bharat and Chubb: every statement subtotal passes; both balance sheets balance |
| Reproducibility | Ingesting twice gives the same package content (excluding `built_at`) |
| Analyzer without PDFs | The Analyzer's test suite runs with the `Annual-reports\` folder absent, using stored packages as fixtures |
| Import boundary | No `analyzer\` module imports `pymupdf` or `ingest\` (Q1, Q4) |
| Export / import | An exported package, imported on a clean cache, opens and gives the same prompts |
| Version guard | The Analyzer refuses a package with a newer major `schema_version` and shows the message |
| Real runs (one per phase that changes prompts) | Bharat Note 21 and Chubb tax questions, old vs new answer, compared by the user |

---

## 12. Phases

| Phase | Deliverable | Visible to the user | Effort (rough) |
|---|---|---|---|
| **0** | This plan agreed and §14 answered (done 2026-09-27); the baseline frozen | — | small |
| **1** | `rptpkg` + `ingest\` + `analyzer\` inside WEB-APP. The package holds today's index plus the pre-extracted `unit_text` and page text. The Analyzer reads only packages. **Prompts byte-identical** | Faster report loading after the first time; otherwise nothing | medium |
| **2** | Lines, values, periods, unit scale; the statements view rendered from the package | Cleaner statements view; periods shown | medium |
| **3** | Links and checks; the quality report in Step 1 and in the CLI | Quality report | medium |
| **4** | Link-assisted note picking; LINKED FIGURES and CHECKS blocks in the prompts; one real run per sample | Better note suggestions; more precise impact sections | small–medium |
| **5** | **Ingest screen** in the same app: report status, Ingest (one report), quality report, OCR review and page images moved from the Analyzer, package export / import | Ingest screen | medium |

Each phase ends with: tests green, README / HOW doc / `.claude/CLAUDE.md` updated, and the exe rebuilt (refresh the builder's input copy first, §10).

Afterwards (not part of this plan): *question templates* from the backlog are easier on top of the package.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| **Scope creep** ("extract all data" grows without end) | Extract only what has a consumer: units, text, statement lines, values, links, checks. Note tables are stored as rows; parsing them further happens only when a feature needs it |
| **Schema churn** early on | Both sides live in one folder and ship in one exe, so they always change together; the version guard catches packages from older builds |
| **The split erodes over time** (Analyzer code starts reading PDFs again) | The import-boundary test (§11) |
| **Wrong numbers** from parsing (typos, footnote markers "(1)", scale, EPS) | Store `raw` next to `value`; checks flag problems; the prompt always carries the verbatim text |
| **False links** (10-K title matching) | Confidence and evidence on every link; low-confidence links are hints only, never automatic note selection; precision tests on Chubb |
| **Refactor breaks behaviour** | Byte-identical prompt test (Phase 1) plus the existing tests |
| **Stale packages** after an ingester fix | `ingester_version` in `meta`; the Analyzer shows "package built by an older ingester — re-ingest" when a minor fix touches that format |
| **OCR still costs money in the Ingester** | Unchanged from today: estimate first, then confirm, page ranges and cancel |
| **The exe builder expects one folder** | The layout keeps one folder permanently (§9) |

---

## 14. Decisions (confirmed 2026-09-27)

| # | Question | Decision | Where applied |
|---|---|---|---|
| Q1 | One exe or two? | **One exe, permanently.** The split is in the code (`ingest\` / `analyzer\`), not the download | §0, §9, §10 |
| Q2 | Package format? | **SQLite**, one `.rptpkg.db` file per report | §5.1 |
| Q3 | Where do packages live? | **App cache**: `WEB-APP\cache\packages\` from source, `%LOCALAPPDATA%\ANN-RPT-ANALYZER\cache\packages\` in the exe; export / import on the Ingest screen | §5.1, §6.3 |
| Q4 | Should the Analyzer open PDFs? | **No.** Page images and OCR review move to the Ingest screen; the Analyzer code is PDF-free | §8.2, §9, §11 |
| Q5 | What goes to Claude from the statements? | **All four statements + a LINKED FIGURES block** (the existing decision stays) | §8.3 |
| Q6 | Is the 10-K its own format? | **Yes, `us-10k`** | §6.1, §7.2 |
| Q7 | Batch ingestion of a folder? | **No, one report at a time** (CLI and UI) | §6.3 |
| Q8 | Cross-year comparison? | **Not needed**; dropped (no `same_as` links, no year keys) | §3.2, §7.1, §12 |
| Q9 | A query tool (MCP) for Claude? | **No, never.** Claude's tools stay disabled; Python decides what Claude sees | §8.3, §15 |

---

## 15. Out of scope

- LLM-generated entity–relation triples as a replacement for the source text (§3.1 A).
- A graph database server (§3.1 B).
- A second exe or a separate Ingester app (Q1).
- Batch / folder ingestion (Q7).
- Cross-year comparison (Q8).
- Any tool (MCP or otherwise) that lets Claude query the package or the PDF (Q9).
- Browser PDF upload, authentication, and network access beyond `127.0.0.1` (unchanged decisions).
- Streaming Claude output (a separate backlog item; independent of this split).

---

## 16. Implementation notes (2026-09-27)

All five phases were built in `WEB-APP` in one session. `python -m pytest -q`: **226 passed**.

### 16.1 Phase 0–1: the split, prompts byte-identical

- `tests/prompt_baseline.py` runs the four samples through the Flask routes with Claude
  mocked (load, identify, analyze, follow-up, manual pages, a standalone analysis) and
  writes 46 files to `tests/fixtures/baseline/`: every prompt, system prompt,
  `/api/index` answer and statements page. `tests/test_prompt_identity.py` compares
  them byte for byte. It passed on the old code, then on the split code **before any
  intended change** — the proof that Phase 1 changed nothing.
- Modules moved with `git mv`: `ingest/` (pdf_utils, report_format, notes_index,
  statements, legacy_index, transcribed_index, ocr_transcribe, ocr_quality,
  `extractor.py` → `clip.py`, `statements_view.py` → `tables.py`) and `analyzer/`
  (note_selector, history, report_html). New: `rptpkg/`, `webcommon.py`,
  `ingest/pipeline.py`, `ingest/routes.py`, `analyzer/{jobs, routes, context, extract,
  source, statement_info, statements_page}.py`. `app.py` only wires the two parts.
- The analyzer gets packages through `analyzer.source`; `app.py` registers the loader
  (`pipeline.open_report`), so the analyzer never imports the ingester.
  `tests/test_boundary.py` enforces the import rules; `tests/test_package.py` runs a
  whole analysis with `pymupdf.open` switched off.
- Storing every page's text doubled ingestion time; `pdf_utils.reuse_page_text()` reuses
  a page's extraction when it is read twice in a row. Bharat Forge: 16s first time (12s
  before the split), then instant.

### 16.2 How the package differs from §5

| Plan | Built |
|---|---|
| `unit_text` and `rows` tables | The clipped text is a column of `units` (`text`, `text_pages`). Positioned statement rows stay inside the statement's JSON (`units.data`), so the index comes back exactly |
| — | `units.view`: each statement's parsed tables, so the statements page needs no parsing |
| `values.scale`, `values.currency` | The report's unit is in `meta.units` ("In ₹Million", "(in millions of U.S. dollars…)"); figures are stored as printed |
| check status ok / warn / fail | Also **unverified**: a total that isn't a simple sum of the lines above it (profit = income − expenses) |
| File `<report>.rptpkg.db` | `<pdf stem>-<first 16 hex of SHA-256>.rptpkg.db`: a renamed or copied PDF finds its package; a changed PDF gets a new one (the old one is deleted) |
| — | `meta.quality.lines_text`: the rendered quality report, so the analyzer can show it without ingester code |

Unit ids: notes keep `C21`, `SCH-E`, `BH-N5`; statements are `C-PL`, `C-BS`, `C-CF`,
`C-EQ` (`-2` for a second statement of one type); lines `C-PL-L019`.

### 16.3 Phases 2–3: figures, links, checks — measured

| Report | Lines / figures | Linked to a note | Checks |
|---|---|---|---|
| Bharat Forge (Ind AS) | 331 / 903; FY2026, FY2025 | 254 (77%): 103 Notes column, 216 figure, 68 title | 125 of 152 ok, 5 warnings; both balance sheets balance (both years) |
| Chubb (US 10-K) | 166 / 452; FY2025–FY2023 | 80 (48%): 41 figure, 59 title (no Notes column) | 12 of 19 ok; BS balances; net income carries P&L → CF |
| Reliance 1976–77 | 34 / 76; "previous year" only | 20 (59%) | 15 of 20 ok; 5 retyping errors flagged as unreadable figures |
| BRK-1968 (scanned) | 232 / 404; FY1968, FY1967 | 53 (23%): 24 by "(note 3)" / "(Schedule I)" in labels | 33 of 88 ok; 21 warnings (1968 notes rarely repeat figures) |

Precision rules added after reviewing the links (each has a test):
- A title match needs a strong shared word: "Interest income" is not about "Interest in
  Joint Ventures" (`WEAK` words); the whole title must be in the label, or the label's
  words in the title; only the best-fitting titles are kept ("Deferred policy
  acquisition costs" → Note 6, not "Acquisitions").
- A figure match needs a ≥5-digit figure, or two figures of the line in the same note
  (a 4-digit coincidence in a 23-page note was dropped).
- A verified subtotal stands for its lines in the totals below it (Total assets verifies).
- "(notes 5 and 6)" and "(Schedule XVII)" are read from labels; "Scheduled" is not a schedule.

### 16.4 Phase 4: the analyzer uses the links

- **Hints** (`analyzer/linked.statement_hints`): question words (+ `SYNONYMS`) → statement
  lines → their linked notes, strongest first. "Profit before tax" doesn't count as a tax
  line. They go into the selector prompt as *SUGGESTED BY THE FINANCIAL STATEMENTS* (an
  empty block leaves the prompt unchanged), come first in the keyword fallback, and
  show in step 3 as **Suggested by the financial statements** buttons.
- **LINKED FIGURES** (`linked_block`): up to 30 lines linked to the chosen notes (notes
  inside a chosen notes schedule count for it), with figures by period, how each link
  was found, and CHECKS (balance, and warnings about those lines). In the analysis and
  follow-up prompts between the statements and the notes; one new system-prompt rule
  says the text is authoritative.
- **us-10k** (Q6): detected by "FORM 10-K" on the first 5 pages. REPORT PROFILE
  (US GAAP, periods, year-end, units), US statement names, `analysis_system_us10k.txt`.
- The baseline was re-frozen after reviewing every changed line (only the intended
  blocks, rules and Chubb's format changed).
- **Real run** (Chubb, "Analyze the company's tax position"): the selector took 8s and
  chose C12 + C14, citing the hinted lines; the analysis took 67s, used the US names,
  reconciled tax across all four statements, found the deferred tax in the cash flow
  statement differs from the note, and confirmed "the app's links to Note 12 are correct".

### 16.5 Phase 5: Ingest screen

`/ingest`: each report with its status (*up to date*, *stale*, *not ingested*), quality
report, Ingest / Re-ingest (keep, detect or force a format), Transcribe (scanned), View
statements, Review scan, Export package; *View a page* (`/page-image`) and *Import a
package*. An imported package of a scanned report is kept as it is when this PC has no
transcription of it (rebuilding would mean paying for OCR again). CLI:
`python -m ingest <pdf> [--format] [--force]`.

### 16.6 Left as they are

- Legacy statements' "Rs." columns get no period unless headed "Previous Year": the
  layout doesn't say more.
- Title matches can still be loose on generic titles (Chubb "Private debt
  held-for-investment" → Debt note). They are labelled *title match*, and Claude is told
  to verify links.
