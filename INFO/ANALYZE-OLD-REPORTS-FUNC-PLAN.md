# Analyze Old Annual Reports — Functional Plan

**Date:** 2026-09-26
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `INFO\ANN-RPT-NOTES-ANALYZER-PLAN.md`
**Sample old report:** `Annual-reports\Reliance-1976-1977.pdf` (Reliance Textile Industries Ltd, year ended 30 Sep 1977)

---

## 0. Goal and summary

Let the analyzer answer the same in-depth questions about **old annual reports** (pre-2011 Indian GAAP, back to the 1970s) that it answers today for modern Ind AS reports.

Today the app **cannot open the sample at all**. Indexing it stops with *"No notes sections found"* (§2). This is not a text-extraction problem: the sample has a clean text layer. It is a **structure** problem. The app only understands the modern layout, where running headers read "Notes to Consolidated / Standalone Financial Statements" and notes have numbered bold headings. Old reports are organised as **statements → lettered Schedules → one "Notes" schedule**.

The plan:
1. **Detect the report format** when the report is indexed.
2. Keep the current modern indexer **unchanged**.
3. Add a **legacy (Schedule VI) indexer**. It builds the same kind of index out of schedules, numbered notes and the narrative sections (Directors' Report, Auditors' Report, Financial Summary).
4. Make the **prompts era-aware**: statements that were not required then, Indian number grouping, typos from retyping, and prior periods that aren't comparable.
5. Treat **scanned** old reports (image-only PDFs) as a separate, later phase that needs OCR (§10).

---

## 1. What the sample old report looks like (verified 2026-09-26)

| Aspect | Reliance 1976–77 | Modern report (Bharat Forge FY26) |
|---|---|---|
| File | 33 pages, 114 KB. Re-typeset in 1998 (PageMaker 6.0 → Acrobat Distiller 2.1) | Hundreds of pages, 18.7 MB |
| Text layer | **Yes, on every page. No images, so no OCR needed** | Yes |
| Accounts | **Standalone only** (no consolidation in 1977) | Consolidated + Standalone |
| Statements | **Balance Sheet** (p.13) and **Profit & Loss Account** (p.14) only. **No Cash Flow, no Statement of Changes in Equity** | All four |
| Supporting detail | **Schedules 'A'–'N'** "forming part of the Balance Sheet / Profit & Loss Account" (p.15–26). Statement lines point to them in a *Schedule* column ('A', 'E'…) | Numbered notes; statement lines point to them in a *Notes* column |
| Notes | **Schedule 'O'**, "Notes and Contingent Liabilities Forming Part of the Accounts": **34 numbered notes** on p.27–32. Most have **no title** (a paragraph starting "1.", "2.", …); some have bold captions ("25. CONTINGENT LIABILITIES:") | Bold numbered headings ("21. INCOME AND DEFERRED TAXES") under a running header |
| Page sharing | Several schedules share a page: A+B (p.15), H+I (p.22), J+K (p.23) | One note may span pages; pages are rarely shared by unrelated notes |
| Narrative sections | Financial Summary (10 years, Rs. in lakhs, p.3–4), Directors' Report (p.5–8), Auditors' Report with the 1975 Order annexure (p.9–11), company history (p.33) | Integrated report, MD&A, etc. (not used by the app today) |
| Headings / fonts | Statement titles are Helvetica-Bold **11–12 pt**. Schedule headings are bold "SCHEDULE 'E'". Note numbers are **not bold** | Statement titles ≥ 14 pt bold; note headings bold |
| Numbers | **Rs.** with Indian grouping (`5,95,11,000`), "––" for nil, "Rs. in Lakhs" in summaries | ₹ Million, western grouping |
| Year | Year ended **30 September 1977**. The prior "year" was **15 months** (note 2), so it is not comparable | Year ended 31 March |
| Text quirks | Retyping errors: `11,57,05.424` (dot for comma), "Dated: 25th January, 1918" (for 1978), "B0ARD" (zero), and the Balance Sheet total printed as 21,16,30,623 in one place and 21,18,30,623 in another. Quote marks come out as `‘ ’ ' \`` or `�` (`SCHEDULE �C�`) | Clean |
| Printed vs PDF pages | Printed numbers don't follow PDF order: PDF p.1 is printed page 37, and printed pages 9–11 are missing | Consistent offset |

**Implication:** this file needs no OCR, but the app needs new *structure detection*. Other old reports will differ in details (numbered instead of lettered schedules, "Notes on Accounts" as a separate section, scanned pages), so detection must be pattern-based, not tuned to this one file.

---

## 2. Why the current app fails (code points)

| Where | Assumption that breaks on old reports |
|---|---|
| `config.NOTES_HEADERS` + `notes_index._section_of()` | A page is a notes page only if it has a running header like "Notes to Consolidated Financial Statements". Old reports have none, so there are no sections and indexing fails with "No notes sections found". |
| `notes_index._find_notes()` | Note headings are **bold**, numbered and at the left margin. In Schedule 'O' the numbers are regular weight and most notes have no title. |
| `statements.TITLE_RES` / `TITLE_MIN_SIZE = 14` | Titles like "PROFIT & **LOSS ACCOUNT** FOR THE YEAR ENDED…" set in 11–12 pt aren't recognised. "&" is not accepted in place of "and". |
| `statements.group_statements(…, notes_first_page)` | Statements are looked for within 15 pages *before a notes section*. With no notes section there is no anchor. |
| `statements_view` | It expects a numeric *Notes* column and "₹ Million" units. Old statements have a *Schedule* column of quoted letters, and Rs. with Indian grouping. "––" (two dashes) isn't recognised as nil. |
| `note_selector` | IDs are `C21` / `S21` (Consolidated / Standalone) and the default scope is Consolidated. The prompt says "IFRS / Ind AS". There are no schedules or narrative sections to pick from. |
| `prompts/analysis_template.txt` | It always asks for an impact section for **four** statements, including the Cash Flow Statement and the Statement of Changes in Equity, which did not exist in 1977. It says nothing about Indian number grouping, retyping typos or prior periods that aren't comparable. |
| Scanned-PDF guard in `build_index` | A report with almost no text is rejected ("OCR … out of scope for v1"). That is correct for now; see §10. |

---

## 3. Report formats to support

| Format key | Typical period (India) | Recognised by | Units of disclosure | Status |
|---|---|---|---|---|
| `modern` | Ind AS (FY2016-17 on) and revised Schedule III/VI (FY2011-12 on) | Running header "Notes to … Financial Statements", bold numbered notes | Notes (C/S) | **Works today; unchanged** |
| `legacy` | Old Schedule VI under the Companies Act 1956 (roughly the 1950s to FY2010-11) | "SCHEDULE 'X'" / "SCHEDULE n" headings, "forming part of the Balance Sheet / Profit and Loss Account", a *Schedule* column in the statements, a "Notes (on / forming part of the) Accounts" schedule or section | Schedules, numbered notes, narrative sections | **This plan, Phases 1–4** |
| `scanned` | Any year (mostly pre-2000 PDFs on BSE/NSE/company sites) | Little or no text layer (the existing check) | As `legacy` or `modern`, after OCR | **Phase 6, option analysis in §10** |

Historical milestones the prompts should know about (approximate; verify against more samples):
- Cash Flow Statements were required for listed companies from the mid-1990s (SEBI listing agreement / AS-3).
- Consolidated statements from about FY2001-02 (AS-21).
- Revised Schedule VI (notes replace schedules) from FY2011-12.

A 1998 legacy report will therefore often have a Cash Flow Statement; a 1977 one won't. Detection must look for each statement rather than assume the set.

---

## 4. Design overview

```
                    build_index(pdf)
                          |
          detect_format(doc)  -> "modern" | "legacy" | "scanned"
            /                          |                         \
  modern indexer (today)      legacy indexer (new)          scanned -> OCR (Phase 6)
  notes by running header     statements, schedules,         then legacy/modern indexer
  + statements                notes items, narratives
            \                          |
             +--------> common index v8 ("sections" -> "units") <--------+
                                        |
             selector / extractor / statements view / prompts (format-aware)
```

### 4.1 Format detection (`report_format.py`, new)

Score the document on cheap text signals:

| Signal | Points toward |
|---|---|
| ≥ 3 pages with the modern notes running header | `modern` |
| ≥ 3 bold lines matching `^SCHEDULE\s*[‘'"`�]?([A-Z]{1,2}\|\d{1,2}\|[IVX]+)[’'"`�]?\b` | `legacy` |
| "forming part of the (Balance Sheet\|Profit (and\|&) Loss Account\|Accounts)" | `legacy` |
| A statement page with a *Schedule* column header | `legacy` |
| Text on < 10% of pages (existing check) | `scanned` |

The result is stored in the index as `format`, together with `format_signals` for debugging. `modern` wins ties, so current behaviour cannot regress.

### 4.2 Index v8: from "notes" to "units"

Bump `INDEX_VERSION` to 8, so every cached index is rebuilt once. Each section keeps its current shape, adds `format`, and generalises `notes` into **units**:

```json
{
  "format": "legacy",
  "fiscal_year_end": "1977-09-30",
  "currency": "Rs.",
  "sections": {
    "standalone": {
      "label": "Standalone",
      "statements": [ {"type": "balance_sheet", "...": "..."}, {"type": "profit_loss"} ],
      "notes":  [ /* kept for modern reports; empty for legacy */ ],
      "units": [
        {"id": "SCH-E", "kind": "schedule", "ref": "E", "title": "Fixed Assets",
         "part_of": "balance_sheet", "start_page": 18, "start_y": 72.0, "end_page": 19, "end_y": 310.5},
        {"id": "N25", "kind": "note", "no": 25, "title": "Contingent liabilities",
         "parent": "SCH-O", "start_page": 30, "...": "..."},
        {"id": "DIR", "kind": "narrative", "title": "Directors' Report", "start_page": 5, "end_page": 8},
        {"id": "AUD", "kind": "narrative", "title": "Auditors' Report", "start_page": 9, "end_page": 11},
        {"id": "SUM", "kind": "narrative", "title": "Financial Summary (10 years)", "start_page": 3, "end_page": 4}
      ]
    }
  }
}
```

- For **modern** reports, `units` is simply the notes (`kind: "note"`, IDs `C21` / `S21` exactly as today), so the selector, extractor and UI work from one list.
- Every unit keeps exact **start/end page + y**, so the existing extractor (`_spans_in(page, y_from, y_to)`) can cut shared pages (A+B on p.15) with no new logic.
- IDs are chosen so the existing follow-up rule still works:
  - "Note 25" maps to `N25`.
  - A new rule maps "Schedule E" / "Sch. 'E'" to `SCH-E`.

---

## 5. Legacy indexer (`legacy_index.py`, new)

All steps use the existing helpers in `pdf_utils` (`page_rows`, `is_bold`, `clean_text`).

### 5.1 Text normalisation (applied before any matching)
- Quote variants `‘ ’ \` ´ �` and the Windows-1252 stray bytes → `'`. So `SCHEDULE �C�` becomes `SCHEDULE 'C'`.
- "––" / "—" / "-" alone in a cell → nil (`–`), recognised by the extractor and the statements view.
- Unicode NBSP / multiple spaces collapsed (e.g. `Rs.    96,000`).
- **Numbers are never "corrected"** (no fixing `11,57,05.424` or "1918"). Correcting them would hide exactly what a forensic analyst wants flagged. Instead the prompt tells Claude these files were retyped and may contain transcription errors (§8).

### 5.2 Primary statements
- Extend the title patterns:
  - `profit (and|&) loss account( for the (year|period) ended …)?`
  - `balance sheet as (at|on)`
  - `(cash flow statement|statement of (sources and applications|changes in financial position)|funds flow statement)`, for 1990s reports
- Replace the absolute `TITLE_MIN_SIZE = 14` with a **relative** rule: a bold row in the top third of the page whose size is at least the page's body size + 2 pt. This also keeps working on the modern report, where titles are ≥ 14 pt anyway.
- Require a statement page to contain numeric rows. This rejects divider pages like p.12 ("BALANCE SHEET AND PROFIT AND LOSS ACCOUNT", 66 characters).
- Continuation pages: the P&L starts on p.14 below a repeated signature block. Keep reading the page after a title until the next schedule/statement heading.
- Anchor: for legacy, look for statements **before the first schedule** instead of before the notes section.
- Record the **fiscal year end** from the title ("as at 30th September, 1977") and the column headings (`1977` / `1976`, "Previous Year").

### 5.3 Schedules
- A **schedule starts** at a bold line matching the SCHEDULE pattern (letters, numbers or roman numerals). Its **title** is the next bold/upper-case line ("SHARE CAPITAL:", "FIXED ASSETS"), with the trailing colon removed.
- A schedule **ends** where the next schedule starts, which may be on the same page. Repeated "SCHEDULE FORMING PART OF THE …" running headers and "(Contd.)" lines don't start a new schedule.
- `part_of` comes from the running header: *Balance Sheet* or *Profit & Loss Account*.
- Sequence check, like `MAX_NOTE_STEP`: letters/numbers must increase. This rejects in-text mentions ("Refer Schedule 'E'").
- Cross-link to the statements: the statement lines that cite `'E'` get `schedule_refs`, so when a schedule is selected its statement lines are known. This mirrors how note numbers work today.

### 5.4 Notes inside the notes schedule
- The notes schedule is the one whose title matches `notes|notes on accounts|notes forming part of the accounts|notes and contingent liabilities`. Some reports instead have a separate "NOTES ON ACCOUNTS" section, which the same detection handles.
- Split it into items at **left-margin lines starting `n.`** (`^\d{1,2}\.` at x ≈ the column's left edge), whether bold or not, with the note-number sequence check (1, 2, 3… with small steps; allow 11(a)-style sub-items).
- **Title per note:** the bold caption if there is one ("CONTINGENT LIABILITIES"). Otherwise the first ~10 words ("Depreciation is provided in accounts in accordance with…"), marked `title_is_excerpt: true` so the UI can show it in italics.
- Expected on the sample: **34 notes**, N1–N34 on PDF p.27–32, with N25 "Contingent liabilities" on p.30.

### 5.5 Narrative sections
Found by bold titles at the top of a page. Each ends at the next such title or statement/schedule start.

| ID | Title patterns |
|---|---|
| `SUM` | Financial Summary / Financial Highlights / Ten-year record |
| `DIR` | Directors' Report / Report of the Directors |
| `AUD` | Auditors' Report / Report of the Auditors, including the "Annexure" (Manufacturing and Other Companies (Auditor's Report) Order 1975 / CARO) |
| `CHR` | Chairman's statement / speech, where present |

In old reports these are often **where the substance is**: tax disputes, expansion plans, licences, auditors' qualifications. They are therefore selectable like notes. The whole narrative set is small (about 20k characters in the sample).

### 5.6 Company name and scope
- The company name comes from the existing auditor-addressee logic, with the running header "Reliance Textile Industries Limited" as a fallback.
- `sections` contains only `standalone` for a legacy report. **Consolidated** is added only if the report has a separately titled consolidated set (late-1990s/2000s reports).

---

## 6. Selection (`note_selector.py`)

- The selector lists **units** grouped by kind, with the old-style names:
  ```
  STATEMENTS PROVIDED: Balance Sheet (p.13), Profit & Loss Account (p.14)
  SCHEDULES:  SCH-A Share Capital · SCH-B Reserves & Surplus · … · SCH-E Fixed Assets · …
  NOTES (Schedule 'O'):  N1 Amalgamation with Reliance Textile Industries, Bombay … · N25 Contingent liabilities · …
  REPORTS:    DIR Directors' Report · AUD Auditors' Report · SUM Financial Summary (10 years)
  ```
- Selector system prompt: "…annual reports prepared under **Indian GAAP / the Companies Act 1956 (old Schedule VI)** or Ind AS…", taken from `format`.
- `MAX_NOTES` → `MAX_UNITS`, default 6 for legacy. Schedules are short, and a good answer usually needs *schedule + note + Directors' Report paragraph*.
- **Keyword fallback synonyms** for old terminology:

  | Old term | Modern equivalent |
  |---|---|
  | Reserves & Surplus | Other equity |
  | Sundry Debtors | Trade receivables |
  | Sundry Creditors | Trade payables |
  | Fixed Assets / Gross Block / Net Block | Property, plant and equipment |
  | Loans & Advances | Other assets |
  | Investment Allowance Reserve / Development Rebate Reserve | Tax-incentive reserves |
  | Provision for Taxation | Current tax |
  | Capital work-in-progress | Unchanged |

- **Scope:** for legacy reports the scope question is skipped (standalone only), and the confirm step says so.
- Explicit references in the question or follow-ups: "Note 25" maps to N25; "Schedule E" / "Sch. 'E'" / "schedule E" maps to SCH-E.

---

## 7. Extraction and the statements view

- **Extractor:** unchanged in principle, because units carry exact page/y ranges.
  - Add the legacy nil tokens and Indian grouping to `NUMBER_CELL_RE`. It already accepts `5,95,11,000`; widen the nil pattern to `^[-–—]{1,2}$`.
  - Print unit headers as `SCHEDULE 'E' – FIXED ASSETS (PDF p.18–19, printed 21–22)`.
- **Statements view** (`statements_view.py`):
  - Accept a *Schedule* column: cells like `'A'`, `‘E’`, `` `L' ``.
  - Accept "Rs." / "Rs. in Lakhs" units.
  - Rename the column heading to "Schedule" when the report is legacy.
  - The existing column-finding logic doesn't need to change.
- **Printed page numbers:** keep the per-page printed number (already stored). Don't assume a constant offset; PDF p.1 is printed page 37.

---

## 8. Prompts: era-aware analysis

Add a **report profile block** to both templates, filled from the index:

```
REPORT PROFILE
Format: Old Indian GAAP (Companies Act 1956, old Schedule VI) - standalone accounts only
Year ended: 30 September 1977 (previous period: see notes - may not be 12 months)
Currency/units: Rs. with Indian digit grouping (1,00,000 = 1 lakh; 1,00,00,000 = 1 crore)
Statements in this report: Balance Sheet, Profit & Loss Account
Statements NOT in this report: Cash Flow Statement, Statement of Changes in Equity (not required at the time)
Source quality: re-typed from the printed report; figures may contain transcription errors
```

Add rules to `analysis_system.txt` that apply when `format == legacy`, kept in a separate `analysis_system_legacy.txt` appended by code:
- Cite schedules as *(Schedule 'E', p.18)*, notes as *(Note 25, p.30)*, and reports as *(Directors' Report, p.6)*.
- Read Indian grouping correctly, and state figures in **Rs. lakhs or crores** in the answer, with the conversion shown once.
- **Check that totals add up.** Where a total doesn't match its components, or the same figure differs between two places (e.g. the Balance Sheet total 21,16,30,623 vs 21,18,30,623), report it as *"possible transcription error or error in the original"*. Don't silently pick one.
- Check the **comparability of periods** (e.g. a 15-month prior period) and annualise when comparing, saying that you did.
- **Missing statements:**
  - For "Impact on the financial statements", include **only the statements present**.
  - If cash flow matters to the question, build a **derived funds-flow statement** (sources and uses from the Balance Sheet changes + P&L), clearly labelled *derived by the analyst, not published*.
- Use the terminology and law of the era (Companies Act 1956, Income-tax Act 1961 provisions such as section 80J / investment allowance, the 1975 auditors' Order). **Don't apply Ind AS concepts that didn't exist then**; where it helps, explain what a modern equivalent would be.
- Use the Financial Summary (10-year table) as a cross-check and for trends when it is provided.

`analysis_template.txt` / `followup_template.txt`:
- `{{STATEMENT_SECTIONS}}` replaces the fixed four `### …` sub-headings. It lists only the statements the report contains.
- `{{PROFILE}}` goes at the top.
- Modern reports render exactly the same text as today (tested).

The effort level stays at the current default (`low`). Old reports are short, so prompts will be smaller than for modern reports: the whole sample is about 55k characters.

---

## 9. UI changes (small)

- **Report card after indexing:** `Format: Old Indian GAAP (1956 Act) · Standalone · Year ended 30-Sep-1977 · Balance Sheet + P&L · 15 schedules · 34 notes · 3 reports`.
- **Confirm step chips:**
  - labelled by kind: `Schedule E – Fixed Assets`, `Note 25 – Contingent liabilities`, `Directors' Report`
  - excerpt titles in italics
  - the "Add note" dropdown grouped by kind
- **Statements panel** shows the two statements with a Schedule column. A small line says which statements this report doesn't have and why.
- The **scope** wording ("Consolidated by default") is hidden for legacy reports.

---

## 10. Scanned old reports (OCR): options

Many pre-2000 reports exist only as page images. The sample is **not** one, so this phase is independent and can wait for a real scanned sample.

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Tesseract OCR** via PyMuPDF (`page.get_textpage_ocr`) | Needs the Tesseract program installed on each PC | Free, offline, gives word positions, so the legacy indexer works unchanged | Tables and digits are error-prone on old typewriter/letterpress scans. The extra install breaks the "just send the exe" model (§12). |
| **B. Claude transcription** | Render each page to PNG and ask Claude Code to transcribe it to text/markdown tables, once per page, cached next to the index | Much better on tables and faded digits; no install | Minutes and tokens per report (a 40-page report ≈ 40 short calls, parallelisable). Transcriptions carry no word positions, so indexing uses text patterns only. Needs Claude Code's `Read` tool enabled for the image folder, a change from today's `--tools ""`. |
| **C. Hybrid** | Tesseract for layout + Claude to correct the numbers on statement/schedule pages | Best accuracy | Most complex |

**Recommendation:** B, as an explicit **"Transcribe scanned report"** button (with a cost/time estimate), when a scanned sample arrives. Keep today's clear "looks scanned" message until then.

---

## 11. Testing plan

Fixture: `Annual-reports\Reliance-1976-1977.pdf` (114 KB, fine to keep in `tests\fixtures`).

| Test | Expect |
|---|---|
| `detect_format` | Reliance → `legacy`; Bharat Forge → `modern` (no regression) |
| Statements | BS on p.13 and P&L on p.14 (title found at 11–12 pt); the divider page p.12 is rejected; year end 1977-09-30 |
| Schedules | 15 schedules A–O with start pages A:15, B:15, C:16, D:17, E:18, F:19, G:20, H:22, I:22, J:23, K:23, L:24, M:25, N:26, O:27; `part_of` BS for A–I and P&L for J–N; A and B split correctly on shared p.15 |
| Notes | N1–N34 on p.27–32; N25 caption "Contingent liabilities"; untitled notes get excerpt titles |
| Narratives | SUM p.3–4, DIR p.5–8, AUD p.9–11 |
| Normalisation | `SCHEDULE �C�` → `SCHEDULE 'C'`; "––" read as nil; numbers unchanged (`11,57,05.424` kept) |
| Selector | Claude mocked: IDs `SCH-E` / `N25` / `DIR` accepted, unknown IDs dropped; keyword fallback maps "debtors" → Schedule G/H |
| Follow-up refs | "see Schedule E and note 25" adds SCH-E and N25 |
| Prompts | Legacy prompt lists only BS + P&L impact sections and contains the profile block; the modern prompt is byte-identical to today's |
| Existing suite | All 78 current tests still pass |
| Manual | 4 real questions on the sample, reviewed by you: (1) contingent liabilities and tax disputes, (2) fixed assets and the expansion capex, (3) borrowings — secured loans and their security, (4) profitability vs the 15-month prior period |

---

## 12. Phases

| Phase | Work | Done when |
|---|---|---|
| **1. Format detection + index v8** | `report_format.py`, `units` in the index, modern units = notes, cache version bump | Both samples index; modern results identical to today |
| **2. Legacy indexer** | Normalisation, statements (relative title size), schedules, the notes schedule, narratives, schedule refs | Every row of the §11 index tests passes on the sample |
| **3. Selection + extraction** | Unit IDs, selector prompt/synonyms, follow-up refs, extractor headers, statements view Schedule column | You can identify and confirm units for the 4 manual questions |
| **4. Era-aware prompts + UI** | Profile block, legacy rules, dynamic statement sections, report card, chips | The 4 manual questions give answers you judge sound; modern answers unchanged |
| **5. Package** | Update the README and the HOW-… HTML; rebuild the exe with PYTHON-APP-DASHBOARD. Exclude `Prompt-History, Analysis-history, cache` so your history isn't shipped | The exe analyses both reports |
| *6. Scanned reports (optional)* | §10 option B, when a scanned sample is available | A scanned report indexes as `legacy` after transcription |

No new Python packages are needed for Phases 1–5.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Old reports vary a lot (numbered schedules, "Notes on Accounts" as a separate section, two-column pages, landscape fixed-asset schedules) | Pattern-based detection with sequence checks; the existing rotated-page support; collect 3–5 more old samples from different decades before Phase 2 is closed |
| Mis-detection makes a modern report `legacy` | Modern wins ties; a regression test on Bharat Forge; the format is shown on the report card, with a manual override in the dropdown (decision D3) |
| Retyping errors mislead the analysis | The prompt treats inconsistencies as findings; the totals checks are explicit; numbers are never auto-corrected |
| Untitled notes are hard for the selector to choose from | Excerpt titles + a short keyword digest per note (first 200 chars) in the selector list; legacy reports are small, so the selector list stays short |
| No cash flow statement for cash questions | Derived funds-flow, clearly labelled as derived |

---

## 14. Decisions (confirmed 2026-09-26)

| # | Question | Decision |
|---|---|---|
| D1 | Should the **narrative sections** (Directors' Report, Auditors' Report, Financial Summary) be selectable units? | **Yes.** DIR / AUD / SUM are listed with schedules and notes, and both the selector and you can pick them |
| D2 | When cash flow is relevant but no statement was published, what should Claude do? | **Build a derived funds-flow** from the Balance Sheet changes + P&L, labelled *derived by the analyst, not published* |
| D3 | Should the detected format be shown, and can it be overridden? | **Shown on the report card, with a manual override** dropdown (Modern / Old) |
| D4 | Which OCR route for scanned reports, and when? | **Option B (Claude transcription)**, deferred until a scanned sample is available (Phase 6) |
| D5 | How should answers state amounts? | **Rs. lakhs / crores** in narrative and tables, with the conversion shown once; **exact printed figures** quoted in citations |

---

## 15. Implementation notes (2026-09-26)

Phases 1–5 are built in `WEB-APP`. Phase 6 (scanned reports) is deferred, as decided in D4.

| Item | Result |
|---|---|
| New modules | `report_format.py` (detection), `legacy_index.py` (old-report index), `prompts/analysis_system_legacy.txt`, `selector_system_legacy.txt`, `selector_template_legacy.txt` |
| Changed | `notes_index.py` (index v8, format + manual override kept in the cache), `extractor.py` (turns sideways old-report pages, old-report text clean-up, unit headers), `note_selector.py` (unit ids, old-report prompt, synonyms, "Schedule E" / "note 25" / "Directors' Report" references), `app.py` (REPORT PROFILE, statement sections, format info and override on `/api/index`), `statements_view.py` (Schedule column), UI (format line + override, grouped "Add" list, italic excerpt titles, missing statements) |
| Deviation from §4.2 | Units are stored in each section's existing **`notes`** list, with `id` / `kind` / `label`, instead of a separate `units` list. Every consumer already reads `notes`, and modern notes are unchanged. |
| Selector choice | When Claude picks a whole notes schedule *and* some of its notes, the specific notes are kept. When a user confirms both, the whole schedule wins, so nothing is sent twice. |
| Modern reports | Prompts are **byte-identical** to before (checked against the committed templates). Bharat Forge still indexes 59 + 56 notes and four statements per section. |
| Pre-existing bug fixed | `notes_index.COMPANY_NAME_RE` contained literal backspace characters instead of `\b`, so the company name fell back to the file name when the auditor's "To the Members of…" line was missing. |
| Sample results | Reliance 1976-77: the Balance Sheet (p.13) and P&L (p.14) are found; divider p.12 is rejected; 15 schedules A–O on the expected pages; N1–N34 on p.27–32; SUM p.3–4, DIR p.5–8, AUD p.9–11; year end 1977-09-30; company "Reliance Textile Industries Limited" |
| Tests | 92 pass (78 existing + 14 in `tests/test_legacy.py`, fixture `tests/fixtures/Reliance-1976-1977.pdf`) |
| Live check | "Analyze the contingent liabilities and tax disputes…": the selector picked N25, N10, N9, AUD, DIR (7 s); the analysis took 61 s. The answer used Rs. lakhs, annualised the 15-month prior period, flagged the mis-typed "5,16,85,59 9", and discussed Section 80J and investment allowance. |
| Still to do | The four manual questions in §11, reviewed by you. More old samples from other decades (§13). |
