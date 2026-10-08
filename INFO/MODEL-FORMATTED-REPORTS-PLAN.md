# Model Formatted Reports — Plan (view a model document's whole extraction as one readable HTML document)

**Date:** 2026-09-28
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `INFO\MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md` (the Model testing screen),
`INFO\SPLIT-FUNCTIONALITY-PLAN.md` (report packages) and the statements page (`templates\statements_view.html`)
**Status:** **All phases implemented 2026-09-28.** Decisions in §12; what was built in §13.

---

## 0. Goal and summary

The Model testing screen shows the app's extraction **one page at a time**. That is right for
reporting a problem on a page, but it does not answer the bigger question: *"Taken as a whole, did
the app read this report correctly?"* Is every note there, in order, with its tables intact? Are the
statements complete? Did a note swallow the next one, or stop too early?

The goal:

1. On the Model testing screen, once a model document is being tested, a new link:
   **View formatted document ↗** (opens a new tab).
2. The new tab shows **the entire report as the app extracted it**, rebuilt as a clean, readable
   HTML document: **Bootstrap 5, responsive** (desktop, tablet, phone), with a table of contents,
   the four primary statements as proper tables, every note with its tables as proper tables, and
   the other pages of the report.
3. Everything comes **from the report package** — the same data the Analyzer sends to Claude — so
   what the user reads is exactly what the app "knows" about the report. Nothing is re-extracted
   from the PDF, and nothing is added by Claude.
4. Each page in the document links back to **Test this page** on the Model testing screen, so a
   problem seen while reading goes straight into the existing feedback flow.

It is a **read-only view**. No change to the ingestion rules, packages, prompts, `INDEX_VERSION` or
the Analyzer. No Claude call.

---

## 1. Where things stand today (verified 2026-09-28)

| What | Where | Use for this plan |
|---|---|---|
| Model testing screen | `/model-testing`, `templates\model_testing.html`, `static\model_testing.js`, routes in `ingest\model_test_routes.py` | The link is added here |
| Model documents | `ingest\model_testing.list_model_docs()` / `resolve_model_doc(name)` (only a PDF listed directly in `MODEL-DOCS\`; no path traversal) | Same resolution for the new route |
| Packages | All 8 model documents already have packages in `cache\packages\` (TYPE-6/7/8 now have rules) | Source of every word shown |
| Page text | `pages.text` for all pages (TYPE-1: 508 pages, 1.24 M characters) | "Other pages" (§3.4) |
| Units | `units`: notes, schedules, narrative sections, statements, with `start_page`, `end_page`, clipped `text`, `text_pages` (TYPE-1: 115 notes, 8 statements) | Notes (§3.3) |
| Note text shape | `=== Note 21 — TITLE (Consolidated) ===`, then `--- PDF page 405 (printed page 402) ---` markers, then rows; table rows are `label \| FY26 \| FY25` | Parsed into paragraphs, headings and tables (§4) |
| Statement tables | `Package.statement_view(section, id)` → `{id, title, label, pages:[{pdf_page, printed, note, tables:[{width, notes_col, rows:[{kind, label, cells/segments}]}]}], rotated}` | Rendered exactly like the statements page (§3.2) |
| Lines, links, checks | `lines`, `values`, `edges` (`references`, `ties_to`, …), `checks` (TYPE-1: 125 ok, 22 unverified, 5 warn) | Statement line → note links; checks appendix |
| Statements page | `/statements` (`analyzer\routes.py`, `statements_view.html`): Bootstrap 5.3.3 from jsDelivr, RAG Champ top nav, context box, `table.fs` styles | Same look, same table markup |
| Page coverage | TYPE-1: 286 of 508 pages fall inside a unit; the other 222 are the front part (corporate overview, directors' report, …) | Shown as "Other pages" |
| Bootstrap | Already loaded from `cdn.jsdelivr.net/npm/bootstrap@5.3.3` by `statements_view.html`, `analysis_report.html`, `ocr_review.html` | Same version, same CDN (see Q3) |

Boundary: the new view needs to resolve a model document and may have to build its package, so it
lives on the **ingester side** (`ingest\`), like the rest of Model testing. `analyzer\` does not
import it (`tests\test_boundary.py` unchanged).

---

## 2. What the user sees

### 2.1 The link on the Model testing screen

```
Model document  [ TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf  ▼ ]
508 pages · rules: in-indas-2020s · View formatted document ↗          ← as soon as a document is chosen
...
PDF page 405 (printed 402) — TYPE-1-…pdf        Open full size ↗ · View this page in the formatted document ↗
```

- **In the document facts line**, next to the page count and profile: `View formatted document ↗`
  → `/model-testing/formatted?doc=<name>` in a new tab. Shown as soon as a document is selected
  (hidden for "No model documents" / an unreadable PDF).
- **In the result header** after *Test extraction*: `View this page in the formatted document ↗`
  → the same URL with `#page-405`, so the user jumps straight to where page 405's content sits in
  the whole document.

### 2.2 The formatted document (new tab)

```
┌ RAG Champ logo ─────────────── ANNUAL REPORT ANALYZER ─────────────── [☰ Contents] (phones) ┐
├──────────────────────────┬──────────────────────────────────────────────────────────────────┤
│ CONTENTS (sticky,        │ ┌ context box ───────────────────────────────────────────────┐   │
│ scrollspy highlights     │ │ Bharat Forge Limited — Formatted extraction                │   │
│ where you are)           │ │ TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf · 508 pages     │   │
│ [ filter notes…      ]   │ │ Model TYPE-1 · profile in-indas-2020s v1 · format modern   │   │
│ ▸ About this extraction  │ │ Package built 2026-09-28 10:02 · INDEX_VERSION 28          │   │
│ ▸ Front part (p.1–204)   │ │ [115 notes] [8 statements] [286/508 pages indexed]         │   │
│ ▾ Standalone             │ │ [checks: 125 ok · 22 unverified · 5 warn]                  │   │
│   Statements             │ │ This is what the app extracted - not the PDF. Verify       │   │
│     Balance Sheet 205    │ │ against the filed report. [Print] [Download HTML]          │   │
│     …                    │ └────────────────────────────────────────────────────────────┘   │
│   Notes                  │                                                                   │
│     1 Corporate info     │ ══ Standalone Financial Statements ══                            │
│     …                    │ ┌ Balance Sheet ── PDF 205 (printed 202) ─ [Test this page] ┐    │
│ ▾ Consolidated           │ │ table.fs as on the statements page; Notes column cells     │    │
│   …                      │ │ are links (21 → #C21)                                      │    │
│     21 Income and        │ └────────────────────────────────────────────────────────────┘    │
│        deferred taxes    │ ┌ Note 21 — Income and deferred taxes · PDF 405–407 ─────────┐    │
│ ▸ Other pages            │ │ ⟶ Linked from: C-BS-L034 Deferred tax liabilities (net)    │    │
│ ▸ Checks                 │ │ paragraphs, sub-headings, Bootstrap tables (numbers right, │    │
│                          │ │ header rows shaded), page markers "— PDF 406 (403) —"      │    │
│                          │ └────────────────────────────────────────────────────────────┘    │
└──────────────────────────┴──────────────────────────────────────────────────────────────────┘
```

Layout (Bootstrap grid):

- **≥ 992 px (lg):** two columns — `col-lg-3` sticky contents panel (`position-sticky`, own
  scroll), `col-lg-9` document. Bootstrap **Scrollspy** highlights the current section/note.
- **< 992 px:** one column; the contents panel becomes an **Offcanvas** opened from a
  "☰ Contents" button in the top bar. Tables sit in `.table-responsive` (scroll sideways inside the
  card, never the whole page). Wide statements (the 12-column Changes in Equity) use the compact
  `table.fs.wide` style of the statements page.
- **Print** (`@media print`): top bar, contents and buttons hidden; every collapsed block expanded;
  each statement and section starts on a new page; tables don't split mid-row where the browser
  allows (`break-inside: avoid` on rows).

---

## 3. What the document contains, in order

The document follows the **PDF page order**, as the report itself does, so reading it top to bottom
is reading the report. Built by walking pages 1 … N:

| Part | Content | Default state |
|---|---|---|
| 3.1 **About this extraction** | The context box (§2.2): company, file, model id, profile + version, format, package build time and versions, counts, quality warnings from `meta.quality`, checks summary | open |
| 3.2 **Primary statements** | Per section, each statement from `statement_view()`, rendered with the statements page markup (header rows, group headings, sections, totals, units, rotated badge) | open |
| 3.3 **Notes / schedules / narrative units** | Each unit in its section's order, from its clipped `text` (§4) | open |
| 3.4 **Other pages** | Runs of pages that no unit covers (front part, auditor's report, back pages), from `pages.text`, grouped into collapsible runs ("PDF pages 1–204") with one block per page | **collapsed** (they are long and not what the Analyzer uses for notes; one click opens a run) |
| 3.5 **Checks** | Table of `checks` (kind, subject with link, status badge, expected / actual, message), warn/fail first | collapsed |

Details:

- **Anchors.** Every unit gets `id="<unit id>"` (`C21`, `S-BS`, `SCH-E`, `BH-N5`, `C2.1` → escaped to a
  valid id). **Every PDF page gets exactly one `id="page-N"`**: on the page marker inside the unit
  that starts on or covers it first, or on its block in "Other pages". `#page-405` always works.
- **Page markers.** The `--- PDF page N (printed page P) ---` lines become a thin rule with a small
  badge `PDF 406 · printed 403` and a **Test this page** link →
  `/model-testing?doc=<name>&page=406` (Model testing pre-fills and runs, §5.3). Page numbers are
  always **PDF** page numbers with the printed one alongside (decision in CLAUDE.md §6).
- **Sections in order.** Sections appear where their pages are (TYPE-1: standalone p.205–327 before
  consolidated p.341–503). Statements come before their section's notes, as printed.
- **Pages shared by two units** (Note 20 ends and Note 21 starts on the same page): each unit shows
  only its own clipped part — that is exactly the clip the Analyzer uses, so an overlap or a gap is
  visible and reportable.
- **Links between statements and notes** (from `edges`): a statement's Notes-column cell becomes a
  link to the note (`21` → `#C21`); each note card lists **"Linked from"** the statement lines that
  reference / tie to it, each a link back to that line's row (`id="<line id>"` on the `<tr>` when the
  row can be matched to a stored line by label and page). Unmatched rows simply have no id.
- **Scanned reports (TYPE-5):** pages whose `text_source` is `ocr` get a badge "OCR transcription";
  an untranscribed report shows the notice of §6 instead of a document.
- **Legacy / transcribed reports (TYPE-2, TYPE-5):** schedules and narrative units are rendered like
  notes, labelled with their kind ("Schedule E", "Directors' report").

---

## 4. Turning clipped text into HTML (`ingest\formatted_report.py`)

The package stores notes as text lines. A small, deterministic parser turns each unit's text into
blocks; **it never changes a word or a figure**, it only decides how to display it.

| Input line | Becomes |
|---|---|
| `=== Note 21 — TITLE (Consolidated) ===` | Dropped (the card header already says it) |
| `--- PDF page 405 (printed page 402) ---` | Page marker (§3) with `id="page-405"` if this is the page's first occurrence |
| A run of consecutive lines containing ` \| ` | One `<table class="table table-sm fmt">` |
| A line equal to the unit's heading (`21. INCOME AND DEFERRED TAXES`) | Dropped when it is the first line (already in the card header), else a sub-heading |
| A short line (≤ 90 chars) that is a numbered/lettered heading (`(a) …`, `21.3 …`, `A. …`) or in capitals, not ending in a figure | `<h4>` / `<h5>` sub-heading |
| A unit line (`In ₹Million`, `₹ in Million`, `(US$ in millions)` — reuse `tables.UNIT_RE` / profile units) | Small right-aligned muted caption above the next table |
| Anything else | Paragraph text; consecutive lines joined into one `<p>` unless the previous line ends a sentence or the next starts a list item |

Table rules (one table = one run of pipe lines, plus directly preceding non-pipe caption lines that
belong to it, e.g. `Particulars | Year ended | Year ended` then `March 31, 2026 | March 31, 2025`):

- **Cells:** split on ` | `, trimmed. Columns = the widest row; short rows are **right-aligned** to the
  figure columns (a heading continuation `March 31, 2026 | March 31, 2025` sits over the two figure
  columns, not under the label) — the same "figures are on the right" rule the statements page uses.
- **Header rows:** leading rows with no figure cells (`tables.VALUE_RE` / `_is_value`) → `<thead>`,
  shaded like the statements page's header. A lone row with no figures in the middle of a table
  (`Current income tax:`) → a section row spanning the table.
- **Figures:** cells matching the value pattern → `class="num"` (right-aligned, tabular numbers,
  nowrap); negatives `(95.34)` stay as printed.
- **Totals:** a label matching `tables.TOTAL_RE` (or starting "Total") → bold with a top border.
- **Plain-text / monospaced reports (BRK-1994)** already come as `a | b | c` rows from
  `split_monospace`, so the same rules apply.

Escaping: every string goes through Jinja autoescaping (no `|safe` on package text). Only the logo
SVG is inserted raw, as on the statements page.

The parser is a pure function `format_unit_text(text, title) -> list[block]` so it is unit-tested
without PDFs (§9).

---

## 5. Routes and code

### 5.1 Routes (added to `ingest\model_test_routes.py`)

| Route | Does |
|---|---|
| `GET /model-testing/formatted?doc=<name>` | The formatted document. If the package is up to date → render it. If not (never ingested, or stale) → a small "Preparing the formatted document…" page that starts the job below, polls `/api/jobs/<id>` and reloads when done (a first ingestion takes 15 s – 1.5 min). Unknown `doc` → 404 with the "Model document not found" message |
| `POST /api/model-testing/formatted/prepare` `{doc}` | Job: `pipeline.open_report(path)` (same as *Test extraction*) → `{ready: true}` |
| `GET /model-testing/formatted?doc=<name>&download=1` | The same HTML with `Content-Disposition: attachment; filename="<pdf stem>-formatted.html"` |

### 5.2 Files

| File | Change |
|---|---|
| `ingest\formatted_report.py` **(new)** | `build_document(package, doc_name)` → view model (context, contents tree, ordered parts, page anchors, links, checks); `format_unit_text()` (§4); `_page_runs()` for "Other pages"; `FORMATTER_VERSION` |
| `templates\formatted_report.html` **(new)** | Bootstrap 5.3.3 CSS + bundle JS (Scrollspy, Offcanvas, Collapse), RAG Champ top nav and footer like `statements_view.html`, contents panel, cards, print CSS |
| `templates\_fs_table.html` **(new, optional)** | Jinja macro for a statement table, shared with `statements_view.html` **only if** the statements page output stays byte-identical (`tests\test_prompt_identity.py` freezes it); otherwise the markup is copied |
| `templates\formatted_preparing.html` **(new)** | The "preparing" page (spinner, elapsed seconds, error box) |
| `ingest\model_test_routes.py` | The routes in §5.1 |
| `templates\model_testing.html`, `static\model_testing.js` | The two links (§2.1); **pre-fill from the URL**: `?doc=&page=` selects the document, fills Page# and runs *Test extraction* (for "Test this page") |
| `static\formatted_report.js` **(new, small)** | Contents filter box, "expand all / collapse all", Print button, remember the last scroll position per document (`sessionStorage`, wrapped in try/catch) |
| `tests\test_formatted_report.py` **(new)** | §9 |
| `README.md`, `INFO\HOW-…WORKS.html`, `.claude\CLAUDE.md` (§2 table, §7 work done), `INFO\MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md` (a pointer) | Docs |

### 5.3 Performance

- The whole document is built from one package: TYPE-1 is ~1.3 M characters of text (Chubb 1.4 M).
  Expected HTML: **3–5 MB**, built in about 1–2 s. Fine for a local app; the "Other pages" runs are
  collapsed (`<details>` / Bootstrap Collapse), so the browser only lays out what is open.
- The rendered HTML is **cached in memory** (small LRU, 4 documents) keyed by
  `(package file, meta.built_at, FORMATTER_VERSION)`; a rebuilt package or a formatter change
  invalidates it automatically.
- If a report ever makes the page too heavy, the fallback (not in this plan) is to load "Other
  pages" runs on demand from `/api/model-testing/formatted/pages?doc=&from=&to=`.

---

## 6. Documents the app can't fully format

Same cases as the Model testing screen (`MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md` §4.3):

| Case | What the formatted document does |
|---|---|
| **Pending model** / **not in the registry** / rules did not accept it (`NewModelDocument`) | A banner "**No extraction rules for this model — this is the raw PDF text, not the app's extraction**", then every page's raw text (`get_text`) as "Other pages", expanded, with Test this page links (see Q5) |
| **Scanned, not transcribed** (`NeedsTranscription`) | Only the context box and the notice "Transcribe it on the Ingest screen" — model testing never starts a paid transcription |
| **Matched to a different model** than its own name | A warning in the context box ("this PDF matched TYPE-3, not itself"), then the document as usual |
| **Section with no statements / no notes found** | The section heading with "No statements found" / "No notes found" in the contents and the body — gaps must be visible, not hidden |

---

## 7. Design details

- **Look:** the same identity as the saved analysis reports and the statements page — dark navy top
  bar with the RAG Champ logo and yellow tagline, light-blue context box, gold section underline,
  white cards with a soft shadow, `table.fs` styles for statements. Notes tables use Bootstrap
  `table table-sm table-hover` with the same header shade (`#ffdf7e`) and zebra rows (`#eef4fc`).
- **Typography:** body 1rem on phones, 0.95rem ≥ lg; tables 0.86rem (0.74rem wide); figures
  `font-variant-numeric: tabular-nums`.
- **Card header per unit:** kind badge (Note / Schedule / Statement / Narrative), number, title,
  `PDF 405–407 (printed 402–404)`, section badge, and the unit id in small monospace (it's what the
  feedback and the Analyzer's prompts refer to).
- **Contents panel:** sections → Statements / Notes / Other pages / Checks; each note shows number,
  title (ellipsis) and page; a filter box narrows the list by number or title as you type.
- **Accessibility:** landmarks (`nav`, `main`, `aside`), real `<table>` with `<th scope>`, visible
  focus, skip link "Skip to document", sufficient contrast for badges.
- **No dark mode** (the other generated pages have none; keeps the look consistent).

---

## 8. What does not change

- Ingestion rules, profiles, packages, `INDEX_VERSION`, registry — untouched.
- Prompts: `tests\test_prompt_identity.py` stays byte-identical (including the statements page, if the
  shared macro is used).
- The Analyzer and its routes — untouched; `tests\test_boundary.py` unchanged.
- No new files written on disk (the Download button streams the HTML to the browser).
- No Claude calls.

---

## 9. Testing plan

`tests\test_formatted_report.py` (real packages of the model documents; skips missing ones; no Claude):

- **Parser (no PDF):** pipe runs → one table; header rows detected (`Particulars | Year ended | Year
  ended` + `March 31, 2026 | March 31, 2025` right-aligned over the figure columns); `Current income
  tax:` → section row; `(95.34)`, `-`, `5,701.41`, `$10,622`, `25.168%` → `num`; ragged rows padded;
  paragraph joining; unit caption; the note's own heading dropped once.
- **Escaping:** a unit text containing `<script>alert(1)</script>` and `&` → escaped in the HTML.
- **TYPE-1 Bharat Forge:** `id="C21"` present with `PDF 405–407` and a table cell `5,701.41` in
  `td.num`; the P&L contains `Current tax` with `5,606.07`; the BS row "(c) Deferred tax liabilities
  (net)" links to `#C21`, and C21 lists it under "Linked from"; **every page 1–508 has exactly one
  `id="page-N"`**; the number of note cards equals the package's note units (115); standalone comes
  before consolidated; page 344 (rotated equity) carries the rotated badge.
- **TYPE-3 Chubb:** 22 notes, statements p.107–110, `$` cells right-aligned.
- **TYPE-4 BRK-1994 and TYPE-5 BRK-1968:** render without errors; TYPE-5 shows OCR badges and three
  company sections; TYPE-2 Reliance shows schedules and narrative units.
- **TYPE-6/7/8:** decimal note ids (`C2.1`) produce valid, unique anchors.
- **Pending:** with a patched registry (`make_pending`, as in the existing tests) → raw-text banner.
- **Routes:** unknown / traversal `doc` → 404; a stale package → the preparing page; `download=1` →
  attachment header; cache hit on the second request; `/model-testing?doc=&page=` pre-fill (JS, via
  a headless check).
- **Unchanged:** `test_prompt_identity.py`, `test_boundary.py`, the full suite (`python -m pytest -q`).
- **Visual:** headless Edge screenshots of the formatted TYPE-1 document at **1280 px** (contents
  panel + Note 21) and **390 px** (phone: offcanvas closed, a wide table scrolling inside its card),
  and the Model testing screen with the new links; `node --check static/*.js`.
- **Timing:** log build time and HTML size for all 8 documents; target < 3 s and < 6 MB each.

---

## 10. Phases

| Phase | Content | Done when |
|---|---|---|
| 1 | `format_unit_text()` parser + its tests | Parser tests pass on sample note texts of all 8 models |
| 2 | `build_document()` (order, anchors, page runs, links, checks), template, route, cache | TYPE-1 renders; anchors / links tests pass; screenshots at 1280 and 390 px OK |
| 3 | Links on Model testing, `?doc=&page=` pre-fill, preparing page + job, download, pending / scanned cases | Route tests pass; a click from Model testing opens the right document at the right page |
| 4 | All 8 models checked by eye (one screenshot each), print view, timing | Every model renders; sizes and times logged |
| 5 | README, HOW doc, CLAUDE.md, this plan's status | Docs current, full suite passes |

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Heuristic table/heading layout mis-displays some text | The parser only changes *presentation*; every word and figure is shown as stored. A "Show as stored text" toggle per card shows the raw clipped text in a `<pre>`, so the user can always tell a display quirk from an extraction error |
| Very large pages (500-page reports) are slow in the browser | "Other pages" collapsed; in-memory cache; on-demand loading as the fallback (§5.3) |
| Bootstrap CDN not reachable (offline PC, the exe) | Page is still readable unstyled-but-semantic; see Q3 for bundling it locally |
| Users mistake the view for the real report | Context box and footer say "extracted by the app — verify against the filed report", as the statements page does |
| Statement row ↔ line matching fails for some rows | Link only when matched; no guessed links |

---

## 12. Decisions (confirmed 2026-09-28)

| # | Question | Decision |
|---|---|---|
| Q1 | **Scope** | **Entire report** (statements + notes + all other pages), "Other pages" collapsed by default |
| Q2 | **Where the link appears** | **Both**: the document link once a document is chosen, and "View this page in the formatted document" (`#page-N`) after *Test extraction* |
| Q3 | **Bootstrap source** | **jsDelivr CDN** (5.3.3), as the statements page, reports and OCR review do today |
| Q4 | **Saving** | **View + Download HTML button** (streamed to the browser); no folder on disk |
| Q5 | **Pending / unregistered models** | **Raw PDF text, clearly labelled** (same choice as the Model testing screen) |
| Q6 | **"Test this page" links** back to Model testing | **Yes**: `/model-testing?doc=&page=N` pre-fills and runs *Test extraction* |
| Q7 | **Checks appendix and "Linked from" lines** | **Both**: checks appendix (collapsed), "Linked from" on each note, Notes-column cells link to notes |

---

## 13. What was built (2026-09-28)

| Piece | Where |
|---|---|
| Parser (stored text → markers, headings, paragraphs, unit captions, tables; Markdown tables of transcribed reports use their `\|---\|` row as the heading boundary) | `ingest\formatted_report.py`: `format_unit_text()` |
| Whole document (page order, sections, statements from `statement_view`, notes, other-page runs, anchors, links, checks) | `build_document()`; `build_raw_document()` (no rules); `build_notice_document()` (not transcribed) |
| Routes: `/model-testing/formatted`, `/api/model-testing/formatted/prepare`, `/api/model-testing/formatted/stored-text` | `ingest\model_test_routes.py` |
| Page, preparing page, script | `templates\formatted_report.html`, `templates\formatted_preparing.html`, `static\formatted_report.js` |
| Links + `?doc=&page=` pre-fill | `templates\model_testing.html`, `static\model_testing.js` |
| Tests | `tests\test_formatted_report.py` (34) |

Differences from the plan, found while building:

- **Anchors are prefixed and sanitised**: `unit-C21`, `line-C-BS-L034`, `sec-consolidated`, `pages-1-204`
  (`page-N` as planned). Infosys's decimal notes (`C2.1`) would otherwise give ids that CSS selectors
  - and so Bootstrap Scrollspy - read as a class: `C2.1` → `unit-C2_1`.
- **Statement table markup is copied**, not shared with `statements_view.html` through a macro, so the
  frozen statements page cannot change.
- **"Show as stored text"** loads the text on demand (`stored-text` route) instead of embedding a second
  copy of every note in the page.
- A run of other pages inside a section does not repeat the section heading.

Measured (all 8 model documents): every PDF page has exactly one `#page-N`; build + render **0.1–1.1 s**,
**0.3–3.1 MB** (TYPE-1 Bharat Forge: 1.1 s, 3.0 MB; 115 notes, 8 statements, 286/508 pages in a unit).
