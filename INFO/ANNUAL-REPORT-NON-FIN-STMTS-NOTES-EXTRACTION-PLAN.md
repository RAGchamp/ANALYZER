# Non-Financial-Statement Content — Extraction Plan (everything in an annual report that is not a primary statement or a note)

**Date:** 2026-09-28
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Triggered by:** model feedback `Model-Feedback\TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-105411.json`
(TYPE-1, PDF page 22 "Value Creation Model": *"Plain text extraction reads this page in a jumbled order… This
certainly is not in scope of current design and needs to be reviewed later as part of a broader work."*)
**Builds on:** `SPLIT-FUNCTIONALITY-PLAN.md` (packages), `MODEL-DOCS-FUNCTIONALITY-PLAN.md` (profiles and switches),
`MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md`, `MODEL-FORMATTED-REPORTS-PLAN.md` (the formatted document)
**Status:** **Phases 0–6 and 8 implemented 2026-09-28** (TYPE-1/3 in §15, the other six models in §16).
Phase 7 (the Analyzer, Q1) was planned and built separately in `ANALYZE-NON-FIN-DATA-PLAN.md`. Decisions in §14.

---

## 0. Goal and summary

The app was designed around **notes and primary statements**. Everything else in an annual report is
stored only as **flat page text**, rebuilt row by row across the whole page width. For text-heavy
pages this is readable. For **designed pages** (columns, boxes, infographics, KPI callouts, charts) it
mixes unrelated pieces, as the feedback on page 22 shows:

```
S1 Focusing on the Indian | S3 Adding layer of products to | S5 Financial prudence and
market | the core forging business | operational focus
...
Sources utilized | Engineering-driven
Rich culture of research and | product development | Manufacturing
```

And this "rest of the report" is **most of the report**:

| Model | Pages | Pages outside notes/statements | Share of the report's text |
|---|---|---|---|
| TYPE-1 Bharat Forge (Indian integrated report) | 508 | 222 | **42 %** |
| TYPE-3 Chubb (US 10-K) | 460 | 354 | **77 %** |
| TYPE-4 Berkshire 1994 (plain-text 10-K) | 56 | 34 | 62 % |
| TYPE-7 Infosys (20-F) | 256 | 171 | **73 %** |
| TYPE-8 BHP (20-F) | 363 | 287 | **78 %** |
| TYPE-2 Reliance 1977, TYPE-5 Berkshire 1968, TYPE-6 Magna | 33 / 39 / 46 | 4 / 4 / 4 | 0–9 % |

It holds the management discussion and analysis (MD&A), the board's report, corporate governance, risk
factors, business description, the auditor's report (key audit matters), sustainability and business
responsibility (BRSR), remuneration and segment KPIs. Much of it matters to financial analysis.

**The goal:** a deterministic Python stage in the ingester that turns every page outside the notes and
statements into **structured, correctly ordered content**, grouped into the report's own **sections**:

1. A **section map**: which pages form "Board's Report", "Item 7. MD&A", "Independent Auditor's
   Report", and so on, taken from the report's own structure (bookmarks, contents page, running headers,
   form item headings).
2. A **page layout reader** that finds the reading order (columns, boxes, panels) and turns each page into
   typed **blocks**: headings, paragraphs, lists, tables, KPI figures, panels, diagrams, figures.
3. Blocks and sections **stored in the report package**, shown properly in the **formatted document** and
   the **Model testing** screen, and, once confirmed (Q1), **available to the Analyzer** as selectable
   sections, the way notes are today.

The design principle stays the same: **Python finds and structures the content; Claude only analyzes
it.** No Claude call is needed to extract these pages.

---

## 1. What the report itself tells us (probed 2026-09-28)

### 1.1 Page 22 is readable at the block level

PyMuPDF's own text blocks on page 22 are each coherent. Only the row-by-row rebuild mixes them:

```
[ 63,169 →108,183]  Strategy
[ 60,211 →175,232]  S1  Focusing on the Indian market          ┐ a filled panel
[224,211 →360,232]  S3  Adding layer of products to the core…  │ (3 columns × 2 rows)
[390,211 →509,232]  S5  Financial prudence and operational…    ┘
[ 57,369 →132,379]  Sources utilized
[ 68,384 →186,417]  Rich culture of research and engineering…  ← list items (drawn bullet marks)
[378,432 →423,455]  Principal Activities                       ← centre of a drawn circle
[258,370 →540,392]  Engineering-driven product development / Manufacturing   ← diagram labels
[125,710 →371,750]  238,355 tons   (large font)                ← KPI figure
[125,750 →181,760]  Total tonnage                              ← its label
[450, 64 →541, 72]  CORPORATE OVERVIEW / Value Creation Model  ← running header = the section
```

The page also has **140 vector drawings** (panels, rules, bullets, the circle) and **105 images**. So
the geometry needed for reading order, panels, bullets and diagrams is **in the PDF**.

### 1.2 Section clues differ by format

| Clue | TYPE-1 Bharat Forge | TYPE-3 Chubb 10-K | TYPE-7 Infosys 20-F | TYPE-8 BHP 20-F | TYPE-4 BRK 1994 |
|---|---|---|---|---|---|
| **PDF bookmarks** (`doc.get_toc()`) | none | none | none | none | **38** (Part I, Business, …) |
| **Printed contents page** | PDF 7: "Management Discussion & Analysis 72, Board's Report 84, … Standalone Financial Statements 186" | not yet checked (Phase 0) | not yet checked | not yet checked | — |
| **Running headers** | top-right on every page: `STATUTORY REPORTS / Board's Report` (p.88–114), `CORPORATE OVERVIEW / Value Creation Model` (p.22–24) | not yet checked | browser print chrome (already cut) | not yet checked | — |
| **Form item headings** | — | 19 bold `ITEM 1.`, `ITEM 1A.` … `ITEM 16.` (Items 5, 7, 9, 12 not found bold: checked in Phase 0) | 21 `Item 1.` … `Item 19.` | none found; own numbering (Phase 0) | `Item 1.` in capitals |

No single clue works everywhere, so the section map combines them in a fixed order of trust (§3). Which
clues a format uses is a **profile switch**, like every other format-specific rule
(`MODEL-DOCS\README.md`).

### 1.3 Page kinds to handle

| Page kind | Examples | Today | Needed |
|---|---|---|---|
| **Prose, 1–3 columns** | Board's report, MD&A, risk factors, 10-K items | Mostly readable; two-column pages interleave the columns row by row | Column-aware reading order; paragraphs joined across lines, columns and pages |
| **Designed / infographic** | Corporate overview, value creation model, highlights | Jumbled (the feedback) | Panels, KPI figures, lists, diagram labels, in reading order |
| **Tables in narrative sections** | BRSR, corporate governance attendance, 10-K selected data, segment tables, auditor's fees | Rows rebuilt as `a \| b \| c` (fine for borderless; ruled grid tables lose their structure) | Real tables (ruled: `find_tables()`; borderless: the existing column finder) |
| **Charts** | Revenue bars, geography pies (p.20 "4% 14% 30% 52%") | Stray numbers | Labelled as a chart, labels kept, page-image crop shown; values not guessed |
| **Cover / photo / divider pages** | PDF 1–6, section dividers | Near-empty text | Marked "image page", thumbnail |
| **Contents page** | PDF 7 | Flat text | Parsed into the section map, and shown as a linked contents list |
| **Auditor's report, signatures, certifications** | Before each set of statements; 10-K exhibits | Flat text | Sections of their own ("Independent Auditor's Report", "Key Audit Matters") |
| **Scanned** (TYPE-5) | Whole report | Claude transcription, stored as Markdown | Blocks built from the Markdown (no layout analysis) |
| **Plain-text filing** (TYPE-4) | Whole report (Courier) | Monospaced split | Prose and tables from the monospaced rows; bookmarks for sections |

---

## 2. The design at a glance

```
PDF page ──► (1) page geometry: spans, lines, blocks, drawings, images      (PyMuPDF, per page)
         ──► (2) cut running header / footer (known today)
         ──► (3) regions: panels (filled / framed rectangles), image areas, table grids
         ──► (4) reading order: recursive XY-cut inside each region; columns
         ──► (5) block typing: heading · paragraph · list · table · metric · panel · diagram · figure · caption · footnote
         ──► (6) stitching: paragraphs across columns and pages; tables across pages
report   ──► (7) section map: bookmarks ▸ contents page ▸ running headers ▸ item headings ▸ profile headings
         ──► (8) blocks assigned to sections; section text rendered (Markdown-like) for Claude
package  ──► doc_sections + page_blocks tables (pages.text unchanged, Q3)
uses     ──► formatted document · Model testing · (Q1) Analyzer: narrative sections as selectable units
```

Pages **inside** notes and statements keep today's extraction exactly. Only pages outside every note and
statement unit go through the new reader, so **no note or statement prompt changes**.

---

## 3. The section map

### 3.1 Sources, in order of trust

1. **PDF bookmarks**: exact titles and PDF pages, with levels. Used as they are when present (BRK-1994).
2. **Printed contents page**: found in the first ~10 pages by a title (`Contents`, `Table of Contents`,
   `Index`) and ≥5 rows ending in a page number or range (`72`, `06-71`, `F-1`). Printed pages are
   mapped to PDF pages with the stored `pages.printed` (TYPE-1: printed 84 → PDF 87). Group rows
   ("Statutory Reports 72-185") become parents. Spaced-out titles (`S T R E N G T H E N I N G`) are
   joined.
3. **Running headers**: the top-of-page text (already cut from page text today) repeated across a run
   of pages names the section on every page, and the part on the facing line (`CORPORATE OVERVIEW` /
   `Value Creation Model`). It confirms and sharpens the contents page and fills gaps. On TYPE-1 it gives
   about 20 runs of two or more pages, from `Board's Report` p.88–114 to `Business Responsibility & Sustainability Report` p.144–188.
4. **Form item headings**: 10-K / 20-F `Item N.` / `ITEM 1A.` in bold on the left margin, in sequence
   (the notes-heading rules, reused). Subsections are the bold headings inside an item.
5. **Profile headings**: formats with their own numbering (BHP's `1.6`, Magna) get a profile switch with
   the pattern.
6. **Fallback**: big-font headings (≥1.4× the body size) start sections; pages before the first one
   are "Front matter".

Each section records **which source found it**, and disagreements go into the quality report (e.g.
"contents says Board's Report starts at printed 84, running header starts at PDF 88"). The section map
**never overlaps** notes / statements units: those keep their existing units, and the map labels the
pages around them (auditor's report before the statements, annexures after the notes).

### 3.2 Section kinds (a small fixed vocabulary)

Titles are matched to a **kind**, so the Analyzer and the UI can treat sections alike across formats:

`front_matter` · `contents` · `highlights` · `letter` (chairman / CEO / CMD) · `business` (overview,
Item 1) · `strategy` · `mdna` (MD&A, Item 7, Operating and Financial Review) · `risk` (Risk
factors, risk management) · `board_report` (Board's / Directors' report) · `governance` (corporate
governance, Items 10–14) · `remuneration` · `sustainability` (BRSR, ESG, capitals) · `auditor_report`
(incl. key audit matters) · `legal` · `controls` (Item 9A) · `shareholder_info` · `exhibits` ·
`signatures` · `other`.

The keyword table per kind lives in one module (`ingest\narrative\kinds.py`); a profile can add
words. An unknown title is `other`: never dropped, just not specially labelled.

---

## 4. The page layout reader

Module `ingest\narrative\layout.py`, pure functions over one page's geometry, so they can be tested on
recorded fixtures without the PDF.

### 4.1 Geometry

`page.get_text("dict")` (blocks → lines → spans with font, size, flags, colour, bbox),
`page.get_drawings()` (filled / stroked rectangles, lines, curves), `page.get_image_info()` (image
bboxes). Running header / footer bands come from the index, as today. The body font size is the most
common span size on the page (weighted by characters).

### 4.2 Regions

- **Panels**: filled or framed rectangles large enough to hold text (≥ 2 lines). The text inside one
  panel is read together (the "Strategy" box with S1–S6 is one panel).
- **Table grids**: ruled grids from `page.find_tables()` (strategy "lines"); borderless numeric tables from
  the existing right-edge column finder (`tables._find_columns`), only where ≥3 rows share ≥2 numeric
  columns.
- **Image areas**: images larger than ~15 % of the page; text inside is a caption or chart labels.
- **Diagram areas**: clusters of curves or many short lines with ≥3 short text labels around them (the
  circle on page 22). The labels are kept, in clockwise order from the top-left, and the block says
  "diagram".

### 4.3 Reading order

A **recursive XY-cut** over the text blocks inside each region: split at the widest horizontal white
band; if none, at the widest vertical white band (columns); recurse. Regions are ordered the same way.
Rules that the probes show are needed:

- A full-width heading above columns is read before the columns; a heading that spans two columns
  belongs to both.
- **Panels are atomic**: their content is read before moving on, and its inner grid is read row by row
  when the cells line up (S1, S3, S5 / S2, S4, S6) and column by column otherwise. This is a switch,
  decided in Phase 0 on the gold pages.
- Side notes and pull quotes (narrow blocks in a margin column) are read after the main column they sit
  beside.

### 4.4 Block types

| Block | Detected by | Stored as |
|---|---|---|
| `heading` (level 1–3) | size ≥ 1.2× body, or bold + short + followed by body text; level by size rank on the page and in the section | `{text, level}` |
| `paragraph` | body-size lines; consecutive lines joined; hyphenation at line end undone only when the joined word exists elsewhere in the report | `{text}` |
| `list` | a drawn bullet mark or a glyph (`•`, `▪`, `–`, `(a)`, `i)`) at a consistent x left of the text | `{items: [text]}` |
| `table` | §4.2 grids | `{head: [[cell]], rows: [[cell]], caption}` (same shape as the formatted document's tables) |
| `metric` | a figure in a large font (≥1.8× body) with a short label directly under or beside it | `{value: "238,355", unit: "tons", label: "Total tonnage"}` |
| `panel` | §4.2 | `{title, children: [blocks]}` |
| `diagram` | §4.2 | `{title, labels: [text]}` + page-image crop box |
| `figure` / `chart` | image area or vector chart (axes, bars, arcs) | `{caption, labels: [text]}` + crop box; **values are not guessed** (Q4) |
| `caption` / `footnote` | small font under a table / figure, or at the page bottom with a marker | `{text, ref}` |

### 4.5 Stitching

- A paragraph that ends without closing punctuation at the bottom of a column continues at the top of
  the next column or page, when the next text starts in lower case or mid-sentence.
- A table whose next page starts with the same column structure (and "(contd.)" / no heading) continues.
- Page numbers, "Read more Pg. 28" style cross-references are kept as `xref` (target page linked in the
  formatted document).

### 4.6 The invariant: nothing lost, nothing added

For every page, the multiset of words in the blocks must equal the multiset of words in the page's text
spans outside the header / footer bands. This **coverage check** runs on every page of every model in
the tests and during ingestion (a failing page falls back to today's flat text and is listed in the
quality report). It makes the heuristics safe: they can only reorder and label, never drop or invent.

### 4.7 Format-specific input

- **Scanned (TYPE-5)**: the stored Markdown transcription is parsed into the same blocks (headings `#`,
  tables `|---|`, lists); no geometry.
- **Plain-text / typewriter (TYPE-4)**: the monospaced rows from `pdf_utils.split_monospace` give tables;
  prose is joined from full-width Courier lines; sections come from bookmarks.

---

## 5. Storage in the report package

New tables. `SCHEMA_VERSION` gets a **minor** bump, since readers ignore tables they don't know:

```sql
CREATE TABLE doc_sections (
    id          TEXT PRIMARY KEY,     -- N-BOARD-REPORT, N-ITEM-7, N-AUDITOR-C (unique, stable per report)
    parent_id   TEXT,                 -- the part: "Statutory Reports", "Part II"
    seq         INTEGER NOT NULL,
    title       TEXT NOT NULL,        -- as printed
    kind        TEXT NOT NULL,        -- §3.2 vocabulary
    level       INTEGER NOT NULL,
    start_page  INTEGER, end_page INTEGER, printed_pages TEXT,
    source      TEXT NOT NULL,        -- bookmarks | contents | running_header | item_heading | profile | fallback
    text        TEXT                  -- the section rendered for Claude (Markdown-like, tables as a | b | c)
);
CREATE TABLE page_blocks (
    pdf_page    INTEGER NOT NULL,
    seq         INTEGER NOT NULL,     -- reading order on the page
    section_id  TEXT,                 -- doc_sections.id
    kind        TEXT NOT NULL,        -- §4.4
    bbox        TEXT NOT NULL,        -- JSON [x0, y0, x1, y1] in PDF points (crops, highlights)
    data        TEXT NOT NULL,        -- JSON per kind
    PRIMARY KEY (pdf_page, seq)
);
```

- `pages.text` stays **exactly as it is** (Q3), so "Or analyze specific PDF pages" and every frozen
  prompt stay byte-identical. A new column `pages.layout_kind` (prose / designed / table / image /
  contents / scanned) is added for the UI and quality report.
- `Package` (`rptpkg\reader.py`) gains `doc_sections()`, `section_text(id)`, `page_blocks(pno)`.
- Page-image crops are **not** stored (packages stay small): the Ingest side renders them on demand from
  the PDF, and the Analyzer never needs them.
- `INDEX_VERSION` is bumped once, when Phase 5 starts storing blocks, so every package rebuilds.

---

## 6. Where it shows

### 6.1 Formatted document (`/model-testing/formatted`)

The "Other pages" runs are replaced by **real sections** in the contents panel ("Statutory Reports ›
Board's Report", "Item 7. MD&A"), rendered from blocks:

- headings / paragraphs / lists as text; tables with the existing table styles;
- **panels** as Bootstrap cards; **metrics** as KPI tiles (`238,355 tons` / Total tonnage);
- **diagrams, charts, figures** as a labelled card with the label list and a small **page-image crop**
  (`/api/model-testing/page-image?…&clip=x0,y0,x1,y1`), marked "graphic: labels only, see image";
- image-only pages as a thumbnail; the contents page as a linked list;
- "Show as stored text" still shows `pages.text`, so the old and new readings can be compared.

**Page 22 acceptance example:** the page shows, in order: *Strategy* (panel: S1…S6 as a list),
*Value created through*, *Sources utilized* (list of 5), *Principal activities* (diagram: 4 labels, crop),
*Key Segments and Products* (Automotive: Commercial · Passenger; Industrial: …), *Output generated*
(metric **238,355 tons**, Total tonnage), under the section *Corporate Overview › Value Creation Model
(PDF 22–24)*. That is the reading given in chat on 2026-09-28.

### 6.2 Model testing

The "Extracted data" box for a page outside notes / statements shows the **section** the page belongs to
and its **blocks** in order (kind + text), next to today's page text. Feedback files carry the blocks in
`extracted_data.blocks`, so a later report can say "block 7 should be a list".

### 6.3 Analyzer (Q1)

If confirmed, narrative sections become **selectable like notes**:

- The note picker's list gains a "Report sections" group (title, kind, pages); the Claude selector
  prompt lists them after the notes, and questions like *"What does management say about the
  demand outlook?"* pick `mdna` / `letter` sections. Scope rules unchanged (consolidated by default
  for notes; narrative sections are report-wide).
- The analysis prompt gets the chosen sections' `text` (verbatim, clipped to the section, like notes)
  under a `REPORT SECTIONS` heading; the four statements and LINKED FIGURES stay as today.
- Size guard: a long section (a 60-page MD&A) is sent by subsection, chosen by the same selector, never
  whole reports (the same principle as notes: Claude never sees the whole PDF).
- This changes prompts, so it is its own phase with a baseline re-freeze reviewed diff by diff.

---

## 7. Profiles and switches

New switches (default off; turned on per profile):

| Switch | On for | Does |
|---|---|---|
| `narrative_contents_page` | in-indas-2020s, in-20f-ifrs, au-20f-ifrs, us-10k-edgar, ca-40f-usgaap | Parse the printed contents page |
| `narrative_running_headers` | in-indas-2020s, au-20f-ifrs | Sections from top-of-page runs |
| `narrative_form_items` | us-10k-edgar, us-10k-typewriter, in-20f-ifrs | `Item N.` headings |
| `narrative_bookmarks` | all (used when present) | PDF outline |
| `narrative_designed_pages` | in-indas-2020s, au-20f-ifrs | Panels, metrics, diagrams (§4.2–4.4) |
| `narrative_from_markdown` | scanned-ocr | Blocks from the transcription |
| `narrative_panel_order` | per profile | Row-first vs column-first inside panels (§4.3) |

Adding a new model later means checking its narrative pages too; the gap report and
`python -m ingest.models verify` get a narrative section (sections found, coverage-check failures).

---

## 8. Quality report additions

```
Narrative: 14 sections (contents page 12, running headers 14, agree 12); 222 pages → 4,310 blocks
           designed pages 38 · tables 61 · metrics 44 · diagrams/charts 29 · image pages 11
           coverage check: 222/222 pages OK
Warning:   contents says "Board's Report" starts at printed 84 (PDF 87); running header from PDF 88
```

---

## 9. Evaluation: a gold set

A small hand-checked **gold set** drives every heuristic and stops regressions:

- ~40 pages across the 8 models, chosen to cover §1.3's page kinds: TYPE-1 p.7 (contents), 12
  (highlights), 20 (geography chart), **22 (the feedback page)**, 26 (CMD letter), 75 (MD&A two-column),
  90 (board's report), 120 (governance table), 150 (BRSR table), 190 (auditor's report); Chubb Item 1,
  1A, 7 pages and a ruled table; Infosys Item 5 and a 20-F table; BHP designed and table pages;
  BRK-1994 Item 1; TYPE-5 transcription page.
- Each gold page is a small JSON in `tests\fixtures\narrative_gold\`: the expected block kinds and text
  starts in order, headings, the section id.
- Metrics per page: **reading-order agreement** (share of adjacent block pairs in gold order), block-kind
  accuracy, heading recall, table cell match, and the coverage invariant (§4.6, must be 100 %).
- Targets before the formatted document switches to blocks: reading order ≥ 95 % on prose pages, ≥ 85 %
  on designed pages; coverage 100 % on all pages of all models.
- The feedback on page 22 becomes `tests\test_feedback_regressions.py::test_type1_20260928_105411_p22`
  and is recorded fixed with `python -m ingest.feedback fixed … --test …` (routine in `CLAUDE.md` §7a)
  when Phase 5 ships.

---

## 10. Code

| File | Content |
|---|---|
| `ingest\narrative\__init__.py` **(new package)** | `build_narrative(index, doc, profile)` → sections + blocks |
| `ingest\narrative\geometry.py` | Page geometry capture (spans, drawings, images), header / footer bands; fixture recorder for tests |
| `ingest\narrative\layout.py` | Regions, XY-cut reading order, block typing, stitching, coverage check |
| `ingest\narrative\sections.py` | Section map: bookmarks, contents page, running headers, items, profile headings, merge + conflicts |
| `ingest\narrative\kinds.py` | Section-kind vocabulary and keywords |
| `ingest\narrative\render.py` | Section text for Claude (Markdown-like, tables as `a \| b \| c`) |
| `ingest\pipeline.py`, `rptpkg\schema.sql`, `writer.py`, `reader.py` | Store / read `doc_sections`, `page_blocks`, `pages.layout_kind` |
| `ingest\profiles\*` | Switches (§7) |
| `ingest\quality.py` | §8 lines |
| `ingest\formatted_report.py`, `templates\formatted_report.html` | Sections and block rendering (§6.1); crop route |
| `ingest\model_testing.py`, `static\model_testing.js` | Section + blocks for a page (§6.2) |
| `analyzer\note_selector.py`, `analyzer\extract.py`, `prompts\*.txt` | Only in Phase 7 (Q1) |
| `tests\test_narrative_*.py`, `tests\fixtures\narrative_gold\` | §9 |

The Analyzer boundary is kept: `analyzer\` reads sections from the package, never the PDF.

---

## 11. Phases

| Phase | Content | Done when |
|---|---|---|
| **0. Spike + gold set** | Record geometry for the gold pages (TYPE-1 and TYPE-3 first, Q5); hand-write their gold JSON; a first pure-PyMuPDF XY-cut (Q2) on them; measure time per page; check the unmeasured clues of §1.2 | Gold set in git; baseline scores measured for today's flat text; time budget confirmed |
| **1. Section map** | Bookmarks, contents page, running headers, form items; merge + conflicts; section kinds; quality lines | TYPE-1: 14+ sections matching the contents page; Chubb: every Item found |
| **2. Reading order + text blocks** | Geometry, header / footer, XY-cut, headings, paragraphs, lists, stitching, **coverage invariant** | Prose gold pages ≥ 95 % order; coverage 100 % on all model pages |
| **3. Designed pages** | Panels, metrics, diagrams, figures / charts, image pages, cross-references | Page 22 gives the §6.1 reading; designed gold pages ≥ 85 % |
| **4. Narrative tables** | Ruled (`find_tables`) and borderless tables, multi-page tables, captions / footnotes | Table gold pages: cell match ≥ 95 % |
| **5. Package + display** | Schema, writer / reader, `INDEX_VERSION` bump, formatted document sections, Model testing blocks, crop route, feedback 105411 regression test + marked fixed | All model packages rebuild; prompt baseline **unchanged**; screenshots at 1280 / 390 px |
| **6. The other models** | TYPE-7 Infosys and TYPE-8 BHP (20-F sections, BHP's numbering), TYPE-6 Magna, TYPE-2 Reliance; blocks from Markdown (TYPE-5), monospaced prose + bookmarks (TYPE-4) | Their gold pages pass; coverage 100 % on every model |
| **7. Analyzer (Q1)** | Report sections in the picker and prompts, size guard; baseline re-frozen with reviewed diffs; one real Claude run on an MD&A question | Tests + one real run reviewed with the user |
| **8. Docs** | README, HOW doc, CLAUDE.md, `MODEL-DOCS\README.md` (narrative checks when adding a model), this plan's status | Docs current |

---

## 12. Performance

Geometry + layout on every page outside notes / statements: estimated 20–40 ms per page (drawings are
the costly part), i.e. **+5–10 s** on first ingestion of TYPE-1 (today 22 s) and Chubb. It runs inside
the existing `reuse_page_text` pass (one page load), is skipped for pages inside units, and is
measured in Phase 0 against a hard budget of +50 % ingest time. Packages grow by ~1–3 MB (blocks JSON).

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Heuristic layout reading is wrong on unusual designs | Gold set + coverage invariant; failing pages fall back to flat text (never worse than today); Model testing feedback now reports blocks |
| Rules for one format leak into another | Profile switches only; the prompt baseline and `models verify` guard every change (§7a routine) |
| Contents page and running headers disagree | Both recorded; the quality report warns; running headers win for page ranges, contents for titles |
| Charts invite guessed numbers | Labels only, plus the image crop; a later, separate decision if chart data is ever needed |
| Analyzer prompts grow too large with narrative text | Subsection selection + size guard; sections only when picked (Q1) |
| Pure heuristics fall short on some designed pages | Decided (Q2): no extra library; such pages fall back to flat text and are listed in the quality report, and a library could be proposed later as its own decision |
| Scope creep (ESG metrics, BRSR indicators as data) | Out of scope here: this plan extracts and structures; typed KPI datasets would be a follow-up plan |

---

## 14. Decisions (confirmed 2026-09-28)

| # | Question | Decision |
|---|---|---|
| Q1 | **Use by the Analyzer** | **Display first, Analyzer later**: formatted document + Model testing in Phases 0–6; narrative sections selectable in questions in Phase 7, after the user has reviewed the extraction |
| Q2 | **Layout engine** | **Pure PyMuPDF** heuristics (XY-cut, drawings, panels), measured on the gold set; no extra library, no Claude in extraction |
| Q3 | **`pages.text`** | **Kept unchanged until Phase 7**; the switch is decided together with the Analyzer change |
| Q4 | **Charts and infographics** | **Labels + image crop only**; chart values are never guessed |
| Q5 | **Models first** | **TYPE-1 Bharat Forge + TYPE-3 Chubb first**; the 20-Fs and the other models follow in Phase 6 |
| Q6 | **Feedback `…-105411.json`** | **Stays Reported** until Phase 5 ships the page-22 fix; then marked fixed by the §7a routine with its regression test |

---

## 15. What was built (2026-09-28)

| Piece | Where |
|---|---|
| Page geometry (segments split at gaps > 10 pt, drawings via `get_cdrawings` - off-page drawings of a two-page spread dropped - images) | `ingest\narrative\geometry.py` |
| Layout: tables (ruled grids; **row-ruled text tables** whose per-cell rule pieces give the columns - BRSR; aligned figure columns), bullets (glyphs; thick ticks / filled marks that line up; **badges** "S2", "M1", "8.", "a." joined to their text), panels, graphics (curve clusters + short connector lines), text boxes, XY-cut with gutter-aligned bands, block typing, joins, **coverage check** | `ingest\narrative\layout.py` |
| Section map: bookmarks, contents page (numbers before *or* after titles, one style per page; groups; a page number printed 10 pt from its title split), running headers, form items; printed → PDF by the footers' page numbers (a stray footer figure that disagrees with the offset is ignored) | `ingest\narrative\sections.py` |
| Section kinds, section text for Claude | `ingest\narrative\kinds.py`, `render.py` |
| Stage + running header / footer bands | `ingest\narrative\__init__.py` (`build`), called from `pipeline._package_content` |
| Switches `narrative_sections`, `narrative_designed_pages`: on for `in-indas-2020s` v2 and `us-10k-edgar` v2 only (Q5; `ca-40f-usgaap` sets its own, empty) | `ingest\profiles\__init__.py` |
| Package: `doc_sections`, `narrative_pages`, `page_blocks` (schema **1.1**; `pages` unchanged, Q3); `Package.doc_sections()`, `section_text()`, `narrative_pages()`, `page_blocks()`; `INDEX_VERSION` 29 | `rptpkg\schema.sql`, `writer.py`, `reader.py` |
| Quality lines `Narrative:` / `Narrative pages:` / `Narrative warning:` (never `Warning:`, which the Analyze summary shows) | `ingest\quality.py` |
| Formatted document: report sections in the contents panel and as cards; KPI tiles, panels, lists, tables, graphics with a **page-image crop** (`/api/model-testing/page-image?…&clip=x0,y0,x1,y1`, `size=thumb`); "Show as stored text" = the old page text (`stored-text?pages=a-b`) | `ingest\formatted_report.py` (`FORMATTER_VERSION` 2), `templates\formatted_report.html`, `static\formatted_report.js` |
| Model testing: the page's report section and its blocks (also stored in new feedback files) | `ingest\model_testing.py` |
| Tests: made-up pages (columns, bullets, badges, KPI, tables, contents styles, printed pages, kinds), **7 gold pages** (TYPE-1 p.12, 22, 90, 120, 150; TYPE-3 p.4, 60 - checked against the page images), both section maps, coverage on every narrative page, package tables, profiles off, Model testing; feedback regression `test_type1_20260928_105411_p22` | `tests\test_narrative.py`, `tests\fixtures\narrative_gold\gold.json`, `tests\test_feedback_regressions.py` |

Measured:

| | TYPE-1 Bharat Forge | TYPE-3 Chubb |
|---|---|---|
| Sections (leaves) | 25 from the contents page, 15 confirmed by running headers, no warnings | 25 from the "INDEX TO FORM 10-K", 23 confirmed by Item headings |
| Pages read | 222 → 2,544 blocks (prose 122, designed 72, table 22, image 5, contents 1) | 354 → 3,219 blocks (prose 324, table 29, contents 1) |
| Coverage check | 222 / 222 pages OK | 354 / 354 pages OK |
| Time added to ingestion | ~6 s (22 s → 28 s, +27 %) | ~5 s |

Differences from the plan:

- **Gold set: 7 pages**, not ~40 (TYPE-1 and TYPE-3 only, Q5). The rest come with Phase 6.
- Cards in a panel are read **column by column** (the committee cards on TYPE-1 p.120; S1…S6 on p.22 are
  numbered that way). No `narrative_panel_order` switch yet: none of the gold pages needs row order.
- A short line right before a list, table, KPI or graphic is its heading ("Sources utilized", "Output generated").
- `pages.layout_kind` became its own table (`narrative_pages`) with the cut running header and footer.
- `python -m ingest.models verify` and the gap report have no narrative section yet (Phase 6).

Known limits seen on the gold pages (acceptable, reportable through Model testing): "Read more Pg. 28" is
shown as a small heading; short lines in a row of labels ("Automotive", "Industrial") stay paragraphs;
map-like infographics (TYPE-1 p.20) keep a plausible but not perfect order.

---

## 16. Phase 6: the other six models (2026-09-28)

Every profile now reads its narrative pages, with the section sources that fit the format
(`narrative_sections`; all profiles version 2, `INDEX_VERSION` 31):

| Model | Profile | Sources | Result |
|---|---|---|---|
| TYPE-7 Infosys 20-F | `in-20f-ifrs` | bookmarks, contents, form_items | 21 Items from their headings (no contents page); 171 pages |
| TYPE-8 BHP 20-F | `au-20f-ifrs` (+ designed pages) | bookmarks, contents | 120 sections from a **two-page** contents page, numbered groups ("4. Our assets" › "4.1 Copper"); 287 pages. Its running headers are the browser's print lines, so they are not used |
| TYPE-6 Magna 40-F | `ca-40f-usgaap` | bookmarks, contents, headings | the cover and the auditor's report (from the page headings); 4 pages |
| TYPE-2 Reliance 1977 | `in-schedule-vi-1970s` | bookmarks, contents, headings | its dot-leader contents ("Directors' Report …… 5 - 8"); 4 pages |
| TYPE-4 Berkshire 1994 | `us-10k-typewriter` | bookmarks, contents, form_items | 19 sections from the PDF bookmarks (Part I › Business …); typewriter tables split into cells; 34 pages |
| TYPE-5 Berkshire 1968 (scanned) | `scanned-ocr` | headings | blocks and sections from the stored transcription (Markdown); its 4 pages outside units: "EXHIBIT 1 NATIONAL INDEMNITY COMPANY", "EXHIBIT 2 …" |

Every narrative page of every model passes the coverage check (Berkshire 1968: checked on its stored
pages; its package here is an imported one, kept without its transcription cache, so it gets its
narrative tables only when it is rebuilt where the transcription is).

New rules (shared code, each only where the format produces it):

- **Contents page**: continues over the following pages without a title (BHP); numbered entries give their
  groups, with the numbering restarting in each part ("Sustainability Report" › "1. Introduction");
  dot-leader lines and a title anywhere on the page (Reliance); trailing "." / "," cut from titles.
- **Bookmarks**: the first outline level with three or more entries gives the parts, the next the sections.
- **Headings** (new last-resort source): a bold line near the top of a page, clearly larger than the
  report's usual text size; stacked title lines joined.
- **Scanned reports** (`ingest\narrative\markdown.py`): blocks from the Markdown, coverage checked on the
  text without its Markdown marks, sections from the transcription's headings.
- **Page chrome**: the printed page number just above a browser's print footer (URL, "20/363") is the
  page number (the footer mapping of BHP); a line at the top of a third of the pages is chrome whatever
  its size; a large bold title repeated on two pages is not a running header (Magna).
- **Typewriter pages**: a row padded with spaces is split into cells, placed by character; `----` / `====`
  rule rows stay in their table; headings are short lines in capitals (no bold in a typewriter).
- **Tables ruled around groups of rows** (BHP): inside a band, each first-column line with a figure starts a row.
- **Paragraphs with no space between them** (EDGAR prints): split where a sentence ends short of the column
  width or before a clearly larger line gap.
- **Section kinds** come from the title's words only: "Item 3" is Legal Proceedings in a 10-K and Key
  Information in a 20-F. A source never confirms itself.
- `python -m ingest.models verify` checks each model's narrative pages (sections found, coverage); the gap
  report lists the section clues a new format offers (bookmarks, contents page, Item headings, running
  headers, page headings).

Gold set: 13 pages (added Infosys p.70, BHP p.19 and p.41, Magna p.2, Reliance p.2, Berkshire 1994 p.12),
all checked against the page images.

Known limits: BHP p.41's first row keeps two figures in one cell ("25,978 7,710" - the table is ruled only
around groups of rows and its first label sits outside the rules); BHP p.19's orange panel is an image with
no text layer (shown as a figure with its crop); Reliance's printed page numbers are not continuous in the
scan, so its contents sections are approximate.
