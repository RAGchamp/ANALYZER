# OCR Capability — Plan

**Date:** 2026-09-26
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `ANN-RPT-NOTES-ANALYZER-PLAN.md`, `ANALYZE-OLD-REPORTS-FUNC-PLAN.md` (§10 recommended *Claude transcription* for scanned reports; decision D4)
**Sample:** `Annual-reports\BRK-1968.pdf`: Berkshire Hathaway Inc., **SEC Form 10-K for 1968**, a microfilm scan

---

## 0. Goal and summary

Let the analyzer read **scanned, image-only reports**, such as the 1968 Berkshire Hathaway 10-K, and analyze them the same way it analyzes modern and old text-based reports.

Today such a PDF stops at *"This PDF has (almost) no extractable text - it looks scanned."*

The plan in one line: **transcribe each page once with Claude (vision), cache it as Markdown, then index, select, extract and analyze from that transcription.** A local Tesseract pass can optionally cross-check the figures. Every figure the analysis relies on must be traceable to a page image, so the app gets a **page review** view (image next to transcription), and uncertain characters are marked instead of guessed.

---

## 1. What the sample looks like (verified 2026-09-26)

| Aspect | BRK-1968.pdf |
|---|---|
| File | 39 pages, 1.6 MB. "Acrobat 5.0 Image Conversion Plug-in", created 2013 from microfilm (the last page is the microfilm "END — DATE FILMED MAY 7 1969" card) |
| Text layer | **None.** Each page is one 1-bit black-and-white (CCITT fax) image at **≈200 dpi**, ~2,000 × 3,000 px |
| Page sizes | Mostly portrait. **5 pages are two-page spreads** (PDF p.7, 14, 17, 20, ≈1,540 pt wide): the consolidated balance sheet and wide schedules |
| Typography | **Typewritten**, with underlined headings, dot leaders, hand signatures, scan noise and black borders |
| Document type | **Form 10-K**, not a glossy annual report |
| Contents (from a contact sheet) | p.1–5 10-K cover, items 1–10 and the index of financial statements · p.6 Peat, Marwick, Mitchell & Co. accountants' report · p.7 **Consolidated Balance Sheet** (spread) · p.8 **Consolidated Statement of Earnings and Retained Earnings** · p.9–12 **Notes to Consolidated Financial Statements**, numbered "(1) Basis of Consolidation" … · p.13–21 **SEC schedules**, roman-numbered (pages seen so far cite I, III, V, VI, XII, XVII; some are spreads; p.19 "BLANK PAGE") · p.21–30 **Exhibit I — National Indemnity Company**: accountants' report, Statement of Assets and Liabilities, Statement of Income, Paid-in and Unassigned Surplus, Adjusted Income, Adjusted Stockholders' Equity, notes · p.30–38 **Exhibit II — National Fire & Marine Insurance Company**: the same set · p.39 microfilm end card |
| Accounting | US GAAP (1968) for the parent. **Statutory insurance accounting** for the two subsidiaries, reconciled to "adjusted" GAAP-like statements. Currency **$** |
| Entities | **Three sets of financial statements in one file:** the registrant (consolidated) and two insurance subsidiaries (unconsolidated, equity-accounted) |

### OCR test results (same pages, 2026-09-26)

| Engine | Page 8 (Statement of Earnings) | Page 7 (balance sheet spread) | Speed / cost |
|---|---|---|---|
| **Tesseract 5.5** (installed at `C:\Program Files\Tesseract-OCR`, eng only; via PyMuPDF `get_textpage_ocr`, 300 dpi) | Words mostly right, **figures damaged**: `34.456` for 34,456, `728, 00C` for 728,000, `2 654,599` for **2,654,399**, `XVIT` for XVII. Subtotal lines lost. Scan-edge noise ("oN", "aaa", "4a i") | Readable text, same kinds of digit errors | 5–7 s/page, free, gives word positions |
| **Claude Code** (`claude -p --tools Read`, 150 dpi grey PNG, effort low) | Clean Markdown table, **every figure right**. **2,654,399** is confirmed by the page's own arithmetic (48,180,857 − 45,526,458), and 48,180,857 = the sum of the five income lines. Subtotals kept. The one unreadable digit is marked `728,00[?]` | Whole spread in one image: 37 figures, 0 unreadable. Current assets add to 27,122,551 exactly. (Cropping to the left half *lost* the title, so spreads don't need splitting.) | **10–13 s/page, ≈ $0.08–0.09/page** at API rates (on your Claude Code plan). The whole report ≈ **$3.3**, ≈ 8 min sequential, **≈ 2–3 min with 4 in parallel** |

**Conclusion:** Claude transcription is the primary engine: the figures are right, the table structure is kept, and it marks what it can't read. Tesseract is useful only as an **independent cross-check of figures** (§6.2), not as the text source.

---

## 2. Why the current app can't do it

| Where | Problem |
|---|---|
| `notes_index._check_text_layer` | Rejects PDFs with text on < 10% of pages |
| `legacy_index`, modern indexer, `extractor`, `statements_view` | All work from **PDF text spans with x/y positions**. A transcription has text but no positions |
| `report_format` | Knows *modern* (Ind AS notes headers) and *legacy* (Indian Schedule VI). A **US 10-K from 1968** is neither: statements + "Notes to Consolidated Financial Statements" with `(n)` numbering + roman-numbered SEC schedules + subsidiary exhibits |
| Sections | Only `consolidated` / `standalone`. The sample has **three entities**, each with its own statements and notes |
| Prompts | They assume ₹ or Rs. (Indian) conventions. This report is $, US GAAP 1968 plus statutory insurance accounting |
| `claude_client` | Runs Claude with `--tools ""` (no tools). Reading an image needs the **Read** tool |

---

## 3. Design overview

```
 scanned PDF ──► detect (no text layer) ──► "Transcribe this report" (button, with estimate)
                                                  │
                       ┌──────────────────────────┴──────────────────────────┐
                       │ transcription job (background, resumable, cancellable)│
                       │ per page: render PNG ─► claude -p (Read) ─► JSON + MD │
                       │           4 pages in parallel, cached per page         │
                       │           optional Tesseract figure cross-check        │
                       └──────────────────────────┬──────────────────────────┘
                                                  ▼
                      cache\<report>.ocr\page-007.md / .json / .png
                                                  │
                         transcribed indexer (text lines, not x/y)
                  entities ► statements ► notes ► schedules ► narratives
                                                  │
                 index v9: format "transcribed", sections = entities, units with
                           (page, line) extents
                                                  │
        selector / extractor / statements view / prompts (source-quality rules)
```

Key choices:
1. **Transcribe once, analyze many times.** The expensive vision step runs once per page and is cached with a hash of the page image plus the prompt version. Analyses read the cached Markdown and never send images again.
2. **Claude also classifies each page** during the same call (page type, entity, statement title, notes/schedule numbers, printed page number). This makes indexing robust across decades and countries, without brittle layout rules for typewritten pages.
3. **Positions are replaced by lines.** Units have `(page, line)` extents in the page Markdown instead of `(page, y)`, so the rest of the pipeline keeps its shape.
4. **Traceability.** Every extracted page carries its PDF page number. The review view shows the page image next to its transcription, and any page can be re-transcribed.

---

## 4. Transcription engine (`ocr_transcribe.py`, new)

### 4.1 Page images
- Render with PyMuPDF at **150 dpi, greyscale PNG** (~200 KB/page). This is enough for typewritten text, and the 4,320-px spread stayed fully readable.
- If a page comes back with many `[?]` marks, retry once at 200 dpi (the native resolution), and for spreads as **two overlapping halves** (the header strip is repeated on both).
- Stored in `cache\<pdf stem>.ocr\page-NNN.png`, so the review view can show the same image Claude saw.

### 4.2 The Claude call
- `claude -p --tools Read --no-session-persistence --effort low --output-format json`, with **cwd = the page-image folder**, so only that folder's files are named. The prompt goes on stdin. This is a new `claude_client.run_claude_with_image()`. Analysis calls keep `--tools ""`.
- The prompt (`prompts/transcribe_page.txt`) asks for **two blocks**:
  1. **A JSON header:**
     ```json
     {"printed_page": "9", "page_type": "statement|notes|schedule|auditors_report|narrative|cover|index|exhibit_cover|blank|other",
      "entity": "Berkshire Hathaway Inc.", "statement_title": "Consolidated Statement of Earnings and Retained Earnings",
      "period": "Year ended December 28, 1968", "notes_started": ["(1) Basis of Consolidation"], "schedule": "Schedule V",
      "currency": "$", "unreadable": 1}
     ```
  2. **The page as Markdown:**
     - headings as lines
     - every table as a Markdown table, one column per printed column, subtotal and total rows kept
     - figures **exactly as printed** (commas, $, parentheses, even misprints)
     - `[?]` for each unreadable character
     - no corrections, no commentary
- **Effort `low`** (measured: correct figures on both test pages). The model follows the Claude Code default unless `ANN_RPT_OCR_MODEL` is set (decision D3).

### 4.3 Job behaviour
- **Background job** (the existing `start_job` mechanism): the progress shows `Transcribing page 17 of 39…` plus time left. The browser's 1-second poll already supports progress text.
- **4 pages in parallel** (`OCR_WORKERS`, config).
- **Resumable:** pages already in the cache are skipped, so a failed or cancelled job restarts where it stopped.
- **Cancellable:** a Cancel button kills the running `claude` processes.
- **Estimate before starting:** pages × measured seconds/page and × cost/page, from the running average (initially 12 s and $0.09).
- **Skips pages Claude marks `blank`** (or near-empty: a black microfilm card). They are recorded, not analyzed.

### 4.4 Cache format
`cache\<stem>.ocr\`:
- `manifest.json`: PDF size/mtime, prompt version, per-page status, timing, cost
- `page-NNN.png`, `page-NNN.md`, `page-NNN.json`

A changed PDF, or a new prompt version, invalidates the affected pages only.

---

## 5. Indexing transcribed reports (`transcribed_index.py`, new)

Input: the 39 page JSON headers + Markdown. No PDF geometry.

1. **Entities (→ sections).** Group consecutive pages by `entity`.
   - "Exhibit I/II" cover pages and a new accountants' report start a new entity.
   - The registrant's consolidated statements come first.
   - Section keys are slugs (`berkshire-hathaway-inc`, `national-indemnity-company`, …), with labels like "Berkshire Hathaway Inc. (consolidated)".
   - This generalises today's consolidated/standalone pair. Modern and legacy reports keep their existing keys.
2. **Statements** (per entity): pages with `page_type=statement`, grouped by `statement_title`. Continuation pages are joined. Types map to the existing ones, plus insurance/US titles:

   | Title (1968 examples) | Type |
   |---|---|
   | Balance Sheet / Statement of Assets and Liabilities | `balance_sheet` |
   | Statement of Earnings (and Retained Earnings) / Statement of Income / Statement of Adjusted Income | `profit_loss` |
   | Statement of Paid-in and Unassigned Surplus / Adjusted Stockholders' Equity / Retained Earnings | `equity` |
   | Statement of Source and Application of Funds / Changes in Financial Position | `cash_flow` |

   Statement **text** = the page Markdown, which already contains Markdown tables.
3. **Notes:** within `notes` pages, split at note headings.
   - Heading forms: `(1) Basis of Consolidation`, `1. …`, `Note 1 — …`.
   - The number sequence is checked (the same rule as today).
   - The `notes_started` hints from the page JSON cross-check the split.
   - Units: `N1…` with `(page, line)` extents.
4. **Schedules:** SEC schedules (roman numerals, e.g. I, III, V, VI, XII, XVII) or Indian lettered schedules, from `page_type=schedule` + `schedule`. Units: `SCH-I`, `SCH-V`, …, titled from the Markdown heading ("Marketable Securities", "Property, Plant and Equipment").
5. **Narratives:** accountants'/auditors' report(s) per entity (`AUD`), the 10-K items (`10K`, cover + items), and the directors'/president's letter if present (`DIR`, `CHR`).
6. **Facts:**
   - country/currency ($ vs Rs./₹)
   - fiscal year end (from `period`: "December 28, 1968")
   - GAAP era (US 1968)
   - statements present/missing per entity
   - OCR quality totals: pages, `[?]` count, cross-check mismatches (§6)
7. **Index v9:** `format: "transcribed"`, `source: "ocr-claude"`, `ocr: {pages, unreadable, mismatches, prompt_version}`. Units keep the fields used today (`id`, `kind`, `label`, `title`, `start_page`, `end_page`, `printed_pages`, `subheadings`, `digest`), plus `start_line` / `end_line` instead of y.

Unit ids carry an entity prefix when there is more than one entity: `BHI-N5`, `NIC-SCH-III`, `NFM-AUD`. The prefix is built from the entity's initials, and is unique.

---

## 6. Quality and traceability

### 6.1 Built into the transcription
- `[?]` marks: a count per page in the manifest. Pages over a threshold get the automatic 200-dpi retry (§4.1).
- Figures are never corrected (the same rule as for old reports).

### 6.2 Figure cross-check with Tesseract (optional, local, free)
- If Tesseract is found (config `TESSERACT_EXE`; auto-detected at `C:\Program Files\Tesseract-OCR`), OCR the same page.
- Compare the **multiset of figures** (numbers with ≥ 4 digits). A Claude figure with no Tesseract figure within one character of it is flagged `check`.
- Tesseract's own digit errors cause some false flags. They are shown for review, never used to change text.
- Without Tesseract, this step is skipped; the exe must work without it.

### 6.3 Arithmetic tie-outs (automatic, no model)
- For each Markdown table column, look for rows that equal the sum of the rows directly above them since the previous subtotal. For example, on p.8, 48,180,857 = the five income lines, and 45,526,458 = the seven cost lines.
- Record per page: totals found, totals that tie, totals that don't.
- Totals that don't tie go into the prompt's source-quality notes, e.g. "p.8: Earnings from operations 2,654,399 ties; Interest–other printed 34.456 (dot)".

### 6.4 Page review view (`/ocr-review?report=…&page=7`)
- The page image on the left, the rendered transcription on the right, and flags (`[?]`, cross-check, tie-out) highlighted.
- Prev/next page navigation and a **Re-transcribe this page** button (optionally at 200 dpi).
- Linked from the report card ("Review transcription ↗") and from each page reference in the source extract.

---

## 7. Selection, extraction and prompts

- **Scope:**
  - Default: the registrant's consolidated statements.
  - A question naming a subsidiary ("National Indemnity", "the insurance subsidiaries") uses that entity or entities.
  - Detection: entity names and short forms from the index (rule-based, as for consolidated/standalone today).
- **Selector:** units listed per entity and kind (notes, schedules, auditors' report, 10-K items). The prompt `selector_template_transcribed.txt` shows each entity and, for notes, their headings.
- **Extractor:** for `transcribed`, slice the cached Markdown by `(page, line)`. Headers look like `=== Berkshire Hathaway Inc. — Note 5 — Investment in Unconsolidated Subsidiaries (PDF p.11, printed 12) ===`.
- **Statements view:** the Markdown tables are rendered directly (no position-based rebuild), with a link to the page image for each page.
- **Prompt profile** (`report_profile()` extended):
  ```
  REPORT PROFILE
  Format: Scanned report, transcribed by AI from page images (SEC Form 10-K, 1968)
  Entities: Berkshire Hathaway Inc. (consolidated); National Indemnity Company; National Fire & Marine Insurance Company
  Year ended: December 28, 1968 (subsidiaries: December 31, 1968)
  Currency: US dollars
  Accounting: US GAAP of 1968; the insurance subsidiaries' statements are statutory, with "adjusted" statements reconciling them
  Statements in this report: …   Statements NOT in this report: …
  Source quality: OCR transcription - [?] marks unreadable characters; N figures failed the automatic cross-check (listed below)
  ```
- **System rules** (`analysis_system_transcribed.txt`), added to the base rules:
  - Figures come from an AI transcription of a scan. Before relying on a figure, check that it agrees with totals and with the same figure elsewhere.
  - Treat `[?]` as unknown, never guess it; say which conclusions depend on it.
  - Cite PDF pages so the user can check the page image.
  - Use the era's terminology: 1968 US GAAP, APB Opinions of the time, statutory insurance accounting ("unassigned surplus", "non-admitted assets", "adjusted" equity). Don't apply ASC or IFRS concepts that didn't exist.
  - Missing statements: the same derived funds-flow rule as for old reports (a 1968 filing may have a *Statement of Source and Application of Funds*, or none).
- **Country rules:** the Rs./lakh rules stay for Indian legacy reports. `$` reports get US conventions: thousands and millions, and parentheses meaning negatives.

---

## 8. UI changes

- **Report card for a scanned report not yet transcribed:**
  - *"Scanned report: 39 pages, no text layer. Transcribe it with Claude to analyze it: about 3 minutes, ≈ $3.30 at API rates (uses your Claude Code plan). Pages are cached; this is needed once."*
  - Buttons: **Transcribe** / **Transcribe pages…** (a range, for a quick trial).
- **During transcription:**
  - page-by-page progress with thumbnails turning green
  - the running cost/time
  - Cancel (resumable)
- **After transcription:**
  - the normal flow; the format line reads *"Format: Scanned — transcribed (39 pages, 4 characters unreadable, 2 figures to check)"* with **Review transcription ↗**
  - the "Add" list is grouped by entity, then kind

---

## 9. Packaging (the exe)

- **Claude transcription** needs only the Claude Code CLI with the `Read` tool, which is already a prerequisite. The rendered PNGs live in the app's cache folder under `%LOCALAPPDATA%\ANN-RPT-ANALYZER`.
- **Tesseract is optional.** It is detected at run time and never bundled; about 50 MB plus licence considerations make it a poor fit for "just send the exe". Without it, the cross-check (§6.2) is skipped and the review view says so.
- Exclude `cache` (which includes `.ocr` folders) from builds, as now.

---

## 10. Testing plan

| Test | How |
|---|---|
| Transcription parsing | Canned `claude -p` JSON outputs (recorded from the real run on BRK p.7, 8, 9, 13, 22) → header JSON + Markdown split, `[?]` count, bad or missing JSON handled |
| Job | Fake transcriber: resumable (a crash after page 5 resumes at 6), cancel, 4 workers, cache invalidation on prompt-version change |
| Indexer | Fixture = recorded transcriptions of all 39 BRK pages (text, checked into `tests/fixtures/brk1968.ocr/`). Expect: 3 entities; BHI balance sheet p.7 and earnings p.8; notes (1)…(n) on p.9–12; every roman-numbered schedule on p.13–21 (the exact list is taken from the p.5 index of financial statements); blank p.19 and end card p.39 skipped; NIC statements p.23–29 and NFM p.32–38; year end 1968-12-28 |
| Tie-out checker | p.8 table → 48,180,857 and 45,526,458 tie; an edited digit fails |
| Cross-check | Figure sets from p.8 (Claude vs Tesseract) → `2,654,399` flagged (Tesseract read 2 654,599), no false flag on figures both read alike |
| Prompts | Profile lists 3 entities, $, "Scanned — transcribed"; the modern and legacy prompts stay byte-identical (the existing checks) |
| App flow | Claude mocked: index a transcribed fixture, select `BHI-N5` + `BHI-SCH-III`, analyze, follow up naming "National Indemnity" |
| Existing suite | All 92 current tests pass |
| Manual (you) | 4 questions on BRK 1968: (1) textile operations vs the insurance subsidiaries' contribution to earnings, (2) the marketable securities portfolio (Schedule I) and investment gains, (3) the insurance subsidiaries' statutory vs adjusted equity, (4) liquidity and the bank note |

---

## 11. Phases

| Phase | Work | Done when |
|---|---|---|
| **0. Spike** (done 2026-09-26) | Tesseract vs Claude on p.7 and p.8; timings, cost, spreads | Results in §1 |
| **1. Transcription engine** | `ocr_transcribe.py`, `run_claude_with_image`, prompt, cache/manifest, parallel resumable job, estimate | All 39 BRK pages transcribed and cached; parsing tests pass |
| **2. Transcription UI + review view** | Report-card flow, progress, cancel, `/ocr-review` page, re-transcribe page | You can transcribe BRK from the browser and review any page side by side |
| **3. Transcribed indexer** | Entities → sections, statements, notes, schedules, narratives, facts, index v9 | Index tests on the recorded BRK transcripts pass |
| **4. Quality checks** | `[?]` retry at 200 dpi, tie-out checker, optional Tesseract cross-check, flags in the manifest and review view | Tie-out/cross-check tests pass; p.8 flags as expected |
| **5. Selection, extraction, prompts** | Entity scope, unit ids, selector template, Markdown extractor, statements view, profile + system rules | Mocked app-flow test passes; the 4 manual questions give answers you judge sound |
| **6. Docs + exe** | README, HOW-… HTML section, rebuild the exe (Tesseract optional) | The exe transcribes and analyzes BRK 1968 |

No new Python packages are needed; PyMuPDF already renders pages and drives Tesseract. Tesseract itself stays an optional local install.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| A misread figure drives a wrong conclusion | `[?]` marks; tie-outs; optional Tesseract cross-check; prompt rule to verify figures before relying on them; page citations + the review view; per-page re-transcription |
| Cost/time on long reports (a 200-page scan ≈ $18, ≈ 10–15 min) | Estimate and confirmation first; "Transcribe pages…" for part of a report; one-time cost (cached); Sonnet option (D3) |
| Claude declines or times out on a page | Retry once; the page is marked failed in the manifest and shown in the UI; the job still completes |
| Rate limits with parallel calls | 4 workers by default, back-off on errors, configurable |
| Page classification wrong (e.g. a notes page tagged as schedule) | The indexer cross-checks JSON hints against Markdown headings; the review view lets you re-transcribe; manual PDF-pages selection still works |
| Handwriting, stamps, signatures | Ignored or marked `[?]`; they rarely carry figures |
| Other scanned formats (Indian scans, glossy annual reports) | The page-type/entity approach is format-neutral; the transcribed indexer maps Indian lettered schedules and US roman schedules alike; test with an Indian scanned sample when available |
| Sending page images to Anthropic | The same data-handling as the text sent today; note it in the README |

---

## 13. Decisions (confirmed 2026-09-26)

| # | Question | Decision |
|---|---|---|
| D1 | OCR engine | **Claude transcription is the source text.** **Tesseract** cross-checks the figures when it is installed; it flags mismatches for review and never changes the text |
| D2 | When to transcribe | **An explicit "Transcribe" button** (and "Transcribe pages…") showing the page count and a time/cost estimate first. Cached after the first run |
| D3 | Model for transcription | **Claude Code default model at effort low.** Benchmark Sonnet on 5 pages in Phase 1, and switch only if every figure stays correct |
| D4 | Parallel pages | **4 at a time**, backing off on rate-limit errors, configurable |
| D5 | Multi-entity reports | **One section per entity.** The default scope is the registrant's consolidated statements; a question naming a subsidiary uses that entity's section |
| D6 | Page review view | **Yes.** Page image next to its transcription, with flags and a "Re-transcribe this page" button |

---

## 14. Implementation notes (2026-09-26)

Phases 1–6 are built in `WEB-APP`.

| Item | Result |
|---|---|
| New modules | `ocr_transcribe.py` (engine, cache/manifest, parallel resumable job, retry at 200 dpi, `recheck()`), `ocr_quality.py` (tie-outs, Tesseract cross-check), `transcribed_index.py` (entities → sections, statements, notes, schedules, reports), `templates/ocr_review.html`, `prompts/transcribe_page.txt`, `analysis_system_transcribed.txt`, `selector_system_transcribed.txt`, `selector_template_transcribed.txt` |
| Changed | `claude_client.run_claude_with_image` (`--tools Read`, the page folder as cwd); `notes_index` (index v10, `NeedsTranscription`, rebuilt when the transcription revision changes); `extractor` (Markdown line ranges); `note_selector` (entity scope, `BH-N5`-style ids, transcribed selector prompt); `app` (REPORT PROFILE + TRANSCRIPTION CHECKS, `/api/ocr/start`, `/api/ocr/page`, `/api/jobs/<id>/cancel`, `/ocr-image`, `/ocr-review`); `statements_view` (Markdown statements, section labels from the index); UI (Transcribe panel, Cancel, format line + review link, "Add" list grouped by company) |
| BRK-1968 run | 39 pages in **5.8 min** (4 workers), **$5.36** at API rates (≈ $0.14/page including 200-dpi retries). Every page was classified correctly: 3 entities; BH 2 statements, notes 1–14, Schedules I, III, V, VI, IX, XII, XVII; NI 5 statements, 8 notes; NFMI 5 statements, 5 notes; blank p.19 and p.39 |
| Quality checks | 137 totals tie and **1 doesn't** (NI p.23 liabilities, $50). Viewing the scan shows an ambiguous glyph "405,5?1": 405,581 makes the column add up, so Claude most likely misread one digit, and the cross-check had already flagged 405,531. 29 `[?]` in the report; 17 figures unconfirmed by Tesseract |
| D3 Sonnet trial (5 pages) | Same figures as the default model plus 3 figures it could read (one, 728,000, is confirmed by its column total), 0 `[?]`, ≈ 40% cheaper. But its table layout broke several tie-outs on p.23 and p.32 → **kept the default model**; `ANN_RPT_OCR_MODEL=sonnet` is available |
| Check fixes found on real data | Only "Total…" or unlabelled rows are expected to add up ("Net income" is often an ordinary line); a subtotal replaces its parts but keeps the lines above for a grand total; totals in another column (difference of the two figures above); a lone dash is nil; years are not flagged |
| Live analysis | "National Indemnity's balance sheet…": the selector picked NI notes 1–6 (9 s); the analysis took 92 s. It reported the $50 mismatch (naming the unconfirmed figures as candidates) and found a second inconsistency on p.27 (opening adjusted equity 11,503,920 vs 11,508,920) |
| Tests | 105 pass (92 existing + 13 in `tests/test_ocr.py`, using the recorded BRK transcriptions in `tests/fixtures/brk1968.ocr/`) |
| Modern / old reports | Their prompts are byte-identical to before |
| Still to do | The 4 manual questions in §10, reviewed by you; an Indian scanned sample |
