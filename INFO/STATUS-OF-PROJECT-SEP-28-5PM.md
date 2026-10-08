# Project Status — 2026-09-28, 5 PM

**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Purpose:** everything still open across all plans in `INFO\`, so we can refer back to it.

---

## Where things stand

| Plan | Status |
|---|---|
| `ANN-RPT-NOTES-ANALYZER-PLAN.md` (the original app) | Done |
| `ANALYZE-OLD-REPORTS-FUNC-PLAN.md` (Companies Act 1956 reports) | Done; its "scanned reports deferred" line is out of date (see item 5) |
| `OCR-CAPABILITY-PLAN.md` (scanned reports) | Done |
| `SPLIT-FUNCTIONALITY-PLAN.md` (Ingester / Analyzer, report packages) | Done |
| `MODEL-DOCS-FUNCTIONALITY-PLAN.md` (model documents) | Done, including Phase 4 (TYPE-6/7/8 rules); its status line is out of date (see item 5) |
| `MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md` (Model testing + feedback) | Done |
| `MODEL-FORMATTED-REPORTS-PLAN.md` (formatted document) | Done |
| `ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md` (pages outside notes and statements) | Phases 0–6 and 8 done for all 8 models; **Phase 7 open** (item 1) |

Verified on 2026-09-28:
- `python -m pytest -q`: **394 passed**. The frozen-prompt tests are unchanged.
- `python -m ingest.models verify`: **OK**. Every narrative page of every model passes the coverage check.

The exe was rebuilt with the dashboard builder at 14:16 and passed its smoke test:
`PYTHON-EXEC-BUILDER\OUTPUT\ANN-RPT-ANALYZER\ANN-RPT-ANALYZER.exe` (33.9 MB).

The user tested the new extraction on 2026-09-28 and reported "looks good".

---

## A. Planned but not built

### 1. Narrative Phase 7: report sections in the Analyzer — **DONE later on 2026-09-28** (`ANALYZE-NON-FIN-DATA-PLAN.md` §15)
**Plan:** `ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md` §6.3, decisions Q1 and Q3.

**What it adds:**
- Questions can pick report sections (MD&A, Board's report, risk factors …) the way they pick notes.
- The note picker gets a "Report sections" group, and the selector prompt lists the sections after the notes.
- The analysis and follow-up prompts get a `REPORT SECTIONS` part with the chosen sections' text, which is already stored in `doc_sections.text`.
- A size guard: a long section, such as a 60-page MD&A, is sent by subsection, never whole.

**Decision to make with it (Q3):** whether `pages.text`, the text sent by "Or analyze specific PDF pages", switches to the new reading order.

**Work involved:**
- `analyzer\note_selector.py`, `analyzer\extract.py`, `prompts\*.txt`.
- The prompt baseline is re-frozen after reviewing every diff.
- One real Claude run on an MD&A question.

**Why it waited:** you decided (Q1) to review the extraction first. That review is now done.

---

## B. Small fixes found in testing

### 2. The four defects found on TYPE-1 PDF pages 28 and 76
All the text on these pages was extracted, and the coverage check passed. These defects are about how the text is labelled and shown:

1. **A false `[Figure]` block from the facing page.** The PDF places the facing page's photo of a two-page spread outside this page, at x < 0. Off-page *drawings* are already ignored, but off-page *images* are not. Fix: filter images to the page area in `ingest\narrative\geometry.py`.
2. **Headings printed in colour or medium weight (not bold) are not recognised.**
   - p.76: "BUSINESS ENVIRONMENT / Automobile Business / Global Automotive Industry / Overview" became one paragraph.
   - p.28: "Moving up the value chain" and "Agility reflected in performance" are joined to the paragraph that follows each.
   - Fix: split text boxes at a change of colour or font weight, and treat short lines of that kind as headings.
3. **The large pull quote on p.28** ("When a nation can build its own carbines…") shows as an ordinary paragraph instead of a quotation.
4. **Model testing shows "Section: -"** on a narrative page. It should show the report section, e.g. "Management Discussion & Analysis".

Each fix gets a regression test on p.28 / p.76, and the gold pages of all models are re-checked.

### 3. Known limits recorded in the narrative plan
From §15–§16 of the narrative plan:
- BHP (TYPE-8) p.41: the first row keeps two figures in one cell ("25,978 7,710"). The table is ruled only around groups of rows.
- "Read more Pg. 28" style cross-references show as a small heading, not a link.
- Short label rows ("Automotive", "Industrial") stay paragraphs instead of headings.
- Map-like infographics (TYPE-1 p.20) have a plausible but not exact reading order.
- Reliance (TYPE-2): the contents sections are approximate, because the scan skips some printed page numbers.

---

## C. Housekeeping

### 4. Commit the work to git
Nothing has been committed since the second commit (`bb0892e`). This includes all of the following work:
- the Ingester / Analyzer split;
- the model documents;
- Model testing and feedback;
- the formatted document;
- the narrative extraction.

It needs a branch and a commit (or a few logical commits).

### 5. Correct two out-of-date plan status lines
- `MODEL-DOCS-FUNCTIONALITY-PLAN.md` says "Phase 4 (rules for TYPE-6/7/8) not started". It was done: CLAUDE.md §7 items 16–17.
- `ANALYZE-OLD-REPORTS-FUNC-PLAN.md` says "Phase 6 (scanned reports) is deferred". It was built by `OCR-CAPABILITY-PLAN.md`.

### 6. Berkshire 1968 (TYPE-5, scanned) has no report sections on this PC
Its package here was imported without the transcription cache, so the app keeps it rather than rebuilding it (a rebuild would need a paid re-transcription). The narrative reader for scanned reports is built and tested on its stored pages. It will produce sections wherever the report's transcription cache exists, or after re-transcribing on the Ingest screen.

---

## D. Backlog ideas (not planned in detail)

### 7. Ready-made question templates
Tax, Debt, Related parties, Contingencies. Easy on top of the report package: CLAUDE.md §10, original plan §9.

### 8. Export of an analysis to Word or PDF
From the original plan §9 and CLAUDE.md §10.

### 9. More patterns for the automatic checks
For example, profit = income − expenses. Today, subtotals that aren't a simple sum are marked "unverified" (22 on Bharat Forge).

### 10. A local copy of Bootstrap
This would let the formatted document, statements page and reports work offline, including in the exe on a PC without internet. The formatted-document plan chose the jsDelivr CDN for now (its Q3), with bundling "later, for all pages at once".

### 11. Model testing for reports that aren't model documents
Letting Model testing work on any PDF in `Annual-reports\`. Listed in the feedback plan §14 as "can be added later: same code, other folder".

### 12. KPI datasets from BRSR / ESG pages as structured data
Typed indicators rather than text. The narrative plan (§13) calls this "a follow-up plan".

---

## Decided against (not pending)

These were considered and rejected by decision. They are listed so they are not mistaken for open work:

- Browser PDF upload
- Batch / folder ingestion (Q7)
- Comparing reports across years (Q8)
- Any tool that lets Claude query the package or the PDF (Q9)
- Reading chart values (narrative Q4)
- Automatic writing of extraction rules
- Matching with an LLM
- An LLM knowledge graph instead of the report's text

Two items listed as future ideas in the original plan are **built**:
- OCR for scanned reports (`OCR-CAPABILITY-PLAN.md`);
- including related statement lines in Claude's prompts (LINKED FIGURES, `SPLIT-FUNCTIONALITY-PLAN.md` Phase 4).

---

## Suggested order

1. Items 4 and 5: commit and fix the status lines (a few minutes).
2. Item 2: the four page defects.
3. Item 1: narrative Phase 7.
4. Backlog items 7–12, as wanted.
