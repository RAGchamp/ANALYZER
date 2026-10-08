# Analyze Non-Financial-Statement Data — Plan (let questions use the report's own sections: MD&A, Board's report, risk factors …)

**Date:** 2026-09-28
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md` (Phases 0–6 built: every model's pages
outside notes and statements are read into report sections and blocks; this is its **Phase 7**),
`SPLIT-FUNCTIONALITY-PLAN.md` (packages, the Analyzer never opens a PDF), `STATUS-OF-PROJECT-SEP-28-5PM.md` item 1
**Status:** **All phases implemented 2026-09-28** (§15). Decisions in §14.

---

## 0. Goal and thesis

Today the Analyzer answers questions from **the notes plus the four primary statements**. Those say *what*
happened in the numbers. They rarely say *why*, or how each business line did. That story is in the parts
of the annual report the app already extracts but never sends to Claude: the MD&A, the Board's report,
the CMD's letter, risk factors, the business description, and the 10-K / 20-F Items.

**Thesis: the narrative sections hold business information that the financial statements do not, and
sending the right passages with the notes lets the app give real insight into the company's business.**

The example you gave: TYPE-1 Bharat Forge, **PDF page 76** (MD&A, "Company review of the exports auto market",
"Commercial Vehicles (CV)"):

> The Commercial vehicle export business was impacted by the de-stocking in the North American truck market.
> For the Full Year revenue at ₹1,324 crore was lower 34% YoY … a 50% YoY drop in North American CV revenue …
> the current tariff regime in the US has resulted in no business disruptions as well as no impact on market
> share for Bharat Forge.

Checked in the package on 2026-09-28:

| Where "commercial vehicle" / CV business appears | Result |
|---|---|
| Narrative pages (MD&A, Board's report, CMD's letter) | PDF 76 (6 mentions), 77, 78, 88, 89, 17, 18, 27, 28, 80, 95 |
| Heading | Only one: p.76 "COMPANY REVIEW OF THE EXPORTS AUTO MARKET / Commercial Vehicles (CV)" |
| Notes C24 / S24 (Revenue from operations), C49 / S36 (Segment information) | **No** commercial-vehicle breakdown, and ₹1,324 crore (₹13,240 million) appears in no note |

So a question like *"How is the commercial vehicle export business doing?"* can **only** be answered from the
narrative. Today the app would pick the revenue and segment notes and answer "the notes do not break revenue
down by vehicle segment". After this plan it answers from p.76–78 and connects the story to the numbers: the
revenue note, the segment note, and the P&L.

What changes for the user:

1. Ask any question, financial or about the business.
2. Step 3 (confirm) shows the chosen **notes** *and* the chosen **report passages** ("MD&A › Company review of
   the exports auto market › Commercial Vehicles (CV), PDF 76–77"), each with a tick box, as notes have today.
3. The answer uses both. Every figure is cited to its note, statement or passage page, and the answer connects
   management's commentary to the audited figures, or says where it cannot.

The design principles stay the same:
- **Python decides what Claude sees.** Claude gets small verbatim extracts, never the whole report, and has no
  tools (§6 decision Q9).
- **The confirm step always pauses.**
- **A question that picks no passage gets byte-identical prompts to today** (§7.4).

---

## 1. What exists today (verified 2026-09-28)

| Piece | Where | Use |
|---|---|---|
| Report sections per model | package `doc_sections` (all 8 models; e.g. TYPE-1 25, Chubb 25, BHP 120) with `kind` (mdna, board_report, risk, governance, letter, business, sustainability …) | The section map |
| Blocks in reading order | `page_blocks` (heading, paragraph, list, table, metric, panel, diagram …) | The passages are cut from these |
| Section text for Claude | `doc_sections.text` (leaf sections; Markdown-like, page markers) | Too coarse to send whole: see sizes below |
| Note picker | `analyzer\note_selector.py`: one Claude call on note titles + statement hints (`selector_template.txt`), keyword fallback, **max 5 notes** | Extended to passages |
| Analysis / follow-up | `analyzer\jobs.py` (`analyze_job`, `followup_job`), `prompts\analysis_template.txt`, `followup_template.txt`, `analysis_system.txt` | Gain a REPORT SECTIONS block |
| Context budget | `config.MAX_CONTEXT_CHARS = 150_000` (statements + notes) | Shared with passages |
| Step 3 UI | `templates\index.html` + `static\app.js`: notes with tick boxes, size estimate | Gains a "Report passages" group |

**Sizes decide the design.** Sections are far too big to send whole:

| Model | Leaf sections with text | Total narrative text | Largest sections |
|---|---|---|---|
| TYPE-1 Bharat Forge | 26 | 537 K chars | BRSR 131 K, Board's report 93 K, Governance 79 K |
| TYPE-3 Chubb | 26 | 1,128 K | Item 7 MD&A **182 K** (more than the whole 150 K budget), Item 1 81 K, Item 1A 69 K |
| TYPE-7 Infosys | 22 | 686 K | Item 3 156 K, Item 10 132 K, Item 4 108 K |
| TYPE-8 BHP | 121 | 960 K | 57 K, 43 K, 40 K |

So the unit Claude receives is a **passage**: a heading-bounded piece of a section, a few thousand
characters long. For example, "MD&A › Company review of the exports auto market › Commercial Vehicles (CV)".

---

## 2. The design at a glance

```
INGEST (once per report, deterministic)
  doc_sections + page_blocks ──► passages: split at headings, merged / split to 1.5–8 K chars,
                                 each with its heading path, pages, text, key figures (+ units)
                                 ──► package table doc_passages (+ a search index)

QUESTION (step 3, identify)
  question ──► detect scope (as today) ──► statement hints (as today)
           ──► passage search (BM25 over passage text + heading path, synonyms) → top ~30 candidates
           ──► ONE Claude selector call: note titles + candidate passages (path, pages, first words)
               → {"notes": [...], "passages": [...], "reason": ...}     (keyword fallback: top BM25)
           ──► step 3 shows notes + passages with tick boxes (the user edits, then Analyze)

ANALYZE / FOLLOW-UP
  statements (as today) + LINKED FIGURES (as today, + NARRATIVE FIGURES §8)
  + REPORT SECTIONS (the chosen passages, verbatim, cited by section and PDF page)
  + NOTES (as today) ──► claude -p ──► answer (+ "Business context" section when passages were used)
```

---

## 3. Passages (ingester; `ingest\narrative\passages.py`)

A passage is the smallest piece that still reads on its own.

**Building passages** (deterministic, from `page_blocks` of each leaf section):
1. Start a new passage at every heading of level 1–2 inside the section, and at a level-3 heading when the
   current passage is already ≥ 3 K characters.
2. Merge a passage under **1.5 K** characters into the next one, keeping both headings in its path.
3. Split a passage over **8 K** characters at paragraph or page boundaries into parts ("… (part 2)").
4. A table stays whole. A table over 8 K becomes its own passage.
5. Pages with no heading (Chubb's long Item 7 has many) become one passage per 1–2 pages, named after the
   last heading above them.

**Stored per passage:**
- the **heading path** ("MD&A › Company review of the exports auto market › Commercial Vehicles (CV)");
- the **pages** (76–77) and their printed pages;
- the **section id** and **kind** (mdna …);
- the **text**, in the same Markdown-like form as `doc_sections.text`, with page markers;
- the **lead**: its first ~25 words, for the selector and step 3;
- its **figures** (§8).

**Package:** new table `doc_passages(id, section_id, seq, path, start_page, end_page, kind, text, chars,
lead, figures JSON)`. This is schema **1.2**; readers ignore the table when it is absent. Ids are
`P-<section slug>-<nn>`, stable for a given package. `INDEX_VERSION` is bumped, so every package rebuilds.

**Quality report:**
```
Narrative passages: 612 (median 3.1 K chars, largest 8.0 K); 38 split, 104 merged
```

**Model testing:** a narrative page's box lists the passages on it. **Formatted document:** the passage id
appears on hover over each passage heading, so a user can say "passage P-MDA-07".

---

## 4. Finding candidate passages (analyzer; `analyzer\passages.py`)

A report has hundreds of passages, so they cannot all go to the selector. Python first ranks them.

**Ranking:**
- **BM25** over each passage's heading path (weighted ×3) and text, in pure Python with no new dependency.
  The index is built when a package is first opened and kept in memory (≈1 s for TYPE-3).
- **Synonyms and abbreviations** from a small table (`analyzer\passage_terms.py`): CV ↔ commercial vehicle(s),
  PV ↔ passenger vehicle, CY / FY, US ↔ United States / North America, capex ↔ capital expenditure,
  EBITDA, ROCE, tariff(s), defence ↔ defense …. The existing notes `SYNONYMS` are reused where they fit.
- **Section-kind boosts** by question type:
  - "why / drivers / outlook / strategy / segment / business / market / demand" → mdna, letter, strategy, business;
  - "risk / exposure / uncertainty" → risk;
  - "governance / board / remuneration / related party" → governance, remuneration.
- **Diversity:** at most 4 candidates from one section, so one long section does not crowd the list.

**Output:** the top **30** candidates, with score, path, pages and lead. They go to the selector (§5) and
are the keyword fallback when Claude is unavailable (top 3 with a clear score gap).

**Worked example**, *"How is the commercial vehicle export business doing?"*: the p.76 passage ranks first
on "commercial vehicle(s)" + "CV" + "export(s)" in its heading path. The p.77–78 continuation (CV and
passenger vehicle exports, outlook) and the p.88–89 Board's report paragraphs follow.

---

## 5. Choosing notes and passages together (the selector call)

There is still **one** Claude call in step 3, with one prompt:

- **Notes:** as today (titles, sub-headings, statement hints).
- **New block:**
  ```
  REPORT PASSAGES (from the annual report's own sections, not the notes; choose only if they help answer):
  P-MDA-07 | MD&A › Company review of the exports auto market › Commercial Vehicles (CV) | PDF 76–77 |
             "The Commercial vehicle export business was impacted by the de-stocking in the North American…"
  …
  ```
- **Reply:** `{"notes": [...], "passages": [...], "reason": "..."}`, with at most **5 notes** (as today) and
  at most **4 passages**. Both lists may be empty, but not both at once.
- **Rules in the prompt:**
  - Pick passages when the question asks about the business, its segments, markets, drivers, strategy,
    outlook, risks or management's explanation of the numbers.
  - Pick none for a purely accounting question such as "reconcile the tax expense".

**A question may now select no notes.** *"What is management's outlook for the aerospace business?"* can be
answered from passages + statements. Today `analyze_job` refuses a request without notes; the rule becomes
"at least one note **or** one passage (or manual pages)".

**Keyword fallback:** notes as today, plus the top BM25 passages.

**Explicit references in the question** work like "Note 21" today: "MD&A", "Board's report", "Item 7",
"risk factors" name a section, and its best-ranked passages are added.

**Step 3 UI** (`index.html`, `app.js`):
- A second group, **"Report passages"**: tick box, path, PDF pages (and printed), size.
- A "Show" link opens the passage text; an "Add passage…" search box runs the same ranking on the user's words.
- The existing size meter counts passages too.

---

## 6. Sending passages to Claude

### 6.1 The prompt

A new block, **only when passages were chosen**, placed after LINKED FIGURES and before the NOTES:

```
----- REPORT SECTIONS: MANAGEMENT'S COMMENTARY AND OTHER NARRATIVE (PDF page numbers; not audited) -----
=== MD&A › Company review of the exports auto market › Commercial Vehicles (CV) (PDF 76–77) ===
--- PDF page 76 (printed page 73) ---
#### COMPANY REVIEW OF THE EXPORTS AUTO MARKET Commercial Vehicles (CV)
The Commercial vehicle export business was impacted by …
----- END OF REPORT SECTIONS -----
```

The prompt header gains a line `Report sections provided: MD&A (PDF 76–77), Board's report (PDF 88–89)`.

### 6.2 Instructions (a system-prompt addition, used only when passages are sent)

Kept in a separate file, `prompts\analysis_system_sections.txt`, and appended only when passages are
present, so notes-only prompts stay byte-identical:

- The REPORT SECTIONS are **management's commentary and other unaudited narrative**. Use them for business
  context: segments, markets, drivers, strategy, outlook, risks, and management's explanations.
- Cite them like `(MD&A, p.76)` / `(Board's report, p.88)` / `(Item 7, p.52)`.
- **Connect the narrative to the audited figures.**
  - Tie a narrative figure to the statements or notes when possible, converting units:
    ₹1,324 crore = ₹13,240 million; the statements are in ₹ million. NARRATIVE FIGURES (§8) lists the
    candidates.
  - Say plainly when a narrative figure has **no** counterpart in the notes (e.g. revenue by vehicle segment).
  - Flag any narrative figure that **contradicts** a statement or note.
- Keep management's claims distinct from facts, as in "management states that … (MD&A, p.76)".
  Forward-looking statements are expectations, not results.

### 6.3 Answer structure (`analysis_template.txt`, a conditional part)

When passages are sent, the template gains:

```
## Business context
What management's commentary adds: the segment / market story, drivers, outlook and risks, cited to its pages,
and how it connects to the figures.
```

It comes after *Detailed analysis*. **Impact on the financial statements** stays mandatory (decision §6),
worded for business questions as "which statement lines this business drives (revenue, receivables,
inventory …) and what the commentary implies for them". The Sources section lists passages too.

### 6.4 Budget

- Passages share `MAX_CONTEXT_CHARS` with statements and notes.
- The default cap is **4 passages / 32 K characters**. If the total would exceed the budget, the lowest-ranked
  passage is dropped first and step 3 says so.

---

## 7. Follow-ups, threads, history, saved reports

- **Threads:**
  - `cache\threads\<id>.json` gains `passages` (ids + text as sent), so follow-ups send the same REPORT SECTIONS.
  - A follow-up naming a section ("what does the Board's report say about exports?") adds its best passage,
    as "Note 41" adds a note today.
  - The follow-up template gets the same block and citation style.
- **Prompt-History metadata:** a new line `Report passages: P-MDA-07 (MD&A › … , PDF 76–77)`.
- **Analysis-history HTML:** the light-blue context box lists the passages with their pages.
- **History sidebar:** unchanged.

### 7.4 Prompts that must not change

When no passage is chosen, which will be the case for most accounting questions:
- the analysis and follow-up prompts are **byte-identical** to today;
- so is the system prompt (the addition is appended only when passages are sent).

Two baselines do change, each reviewed diff by diff before re-freezing:
- **Selector prompts**, because they now list candidate passages.
- **"Pages" prompts for narrative pages only (decision Q6).** "Or analyze specific PDF pages" sends the block
  reading order (`render.page_text` of the page's blocks) for a page outside the notes and statements, so
  two-column pages are no longer interleaved. Pages inside notes and statements, and the stored `pages.text`,
  are unchanged. The frozen "pages" samples that land on notes pages stay byte-identical.

`tests\test_prompt_identity.py` must stay green for all analysis and follow-up prompts.

---

## 8. Narrative figures (connecting the story to the numbers)

At ingest, each passage's figures are captured with their unit and period, when stated nearby:
`₹1,324 crore` (FY2026), `34%`, `50%`, `16.36 million units` (CY2025) ….

At question time, `analyzer\linked.py` adds a **NARRATIVE FIGURES** part to LINKED FIGURES for the chosen passages:

```
NARRATIVE FIGURES (from the report sections; the app's automatic matching - verify):
- ₹1,324 crore (CV export revenue FY2026, MD&A p.76) = ₹13,240 million: no statement line or note figure matches
- ₹16,812 crore (consolidated revenue, CMD's letter p.28) = ₹168,120 million: P&L "Revenue from
  operations" ₹168,116.53 million FY2026 (p.342)  → ties (0.002%)
```

- **Unit conversion:** crore ↔ lakh ↔ million ↔ billion, and US$ M / B. Only same-currency figures are compared.
- **Tolerance:** ±0.5% for rounded narrative figures.
- A **tie** is a strong confirmation. **No match** is information too: the segment detail lives only in the MD&A.
- The same check shows in Model testing for a narrative page ("figures on this page and what they tie to").

---

## 9. What the user sees (example)

**Q:** *"How did the commercial vehicle export business perform this year, and how does it show in the numbers?"*

**Step 3:**
- Notes: C24 Revenue from operations, C49 Segment information.
- Passages:
  - MD&A › Company review of the exports auto market › Commercial Vehicles (CV) (PDF 76–77);
  - MD&A › … Passenger Vehicles / Outlook (PDF 77–78);
  - Board's report › Operations (PDF 88–89).

**Answer** (shape):
- **Summary:** CV exports fell 34% to ₹1,324 crore (MD&A, p.76) on North American truck de-stocking; North
  American CV revenue fell 50%. Management reports no tariff-related loss of share.
- **Key figures:** CV export revenue FY26 / FY25 (derived: ₹1,324 cr ÷ 0.66 ≈ ₹2,006 cr), consolidated
  revenue ₹168,116.53 million (P&L p.342), export share (Note C24).
- **Business context:** Class 7 & 8 trucks (US), HCV (Europe), safety-critical engine / driveline /
  transmission parts, OEM relationships, outlook.
- **Impact on the financial statements:**
  - revenue (P&L) and the geography split in Note C49;
  - the CV segment figure is not separately disclosed in the notes (NARRATIVE FIGURES: no tie);
  - receivables and inventory implications.
- **Red flags:** management's "no impact on market share" is not verifiable from the statements.

---

## 10. Code

| File | Change |
|---|---|
| `ingest\narrative\passages.py` **(new)** | Build passages from `page_blocks` + sections (§3); figures with units (§8) |
| `ingest\narrative\__init__.py`, `ingest\pipeline.py` | Call it; quality line |
| `rptpkg\schema.sql`, `writer.py`, `reader.py` | `doc_passages` (schema 1.2); `Package.passages()`, `passage(id)` |
| `ingest\notes_index.py` | `INDEX_VERSION` bump |
| `analyzer\passages.py` **(new)** | BM25 index (cached per package), synonyms, kind boosts, diversity, `candidates(question)`, `explicit_sections(question)` |
| `analyzer\passage_terms.py` **(new)** | Synonyms / abbreviations table |
| `analyzer\note_selector.py` | Candidates in the selector prompt; `passages` in the reply; fallback; no-note questions |
| `analyzer\jobs.py` | identify / analyze / follow-up carry passages; budget; thread, history, HTML |
| `analyzer\linked.py` | NARRATIVE FIGURES |
| `analyzer\extract.py` | `extract_passages(package, ids)` → the REPORT SECTIONS text |
| `analyzer\report_html.py`, `history.py` | Passages in the context box / metadata |
| `prompts\selector_template*.txt` | The REPORT PASSAGES block and rules |
| `prompts\analysis_template.txt`, `followup_template.txt` | `{{SECTIONS}}` / `{{SECTIONS_LIST}}` / business-context part, all empty when no passages |
| `prompts\analysis_system_sections.txt` **(new)** | §6.2 |
| `templates\index.html`, `static\app.js`, `static\style.css` | Step 3 "Report passages" group, Show, Add passage… search |
| `ingest\model_testing.py`, `ingest\formatted_report.py` | Passages on a page; passage ids on headings |
| `tests\test_passages.py`, `tests\test_analyze_sections.py` **(new)**, `tests\fixtures\passage_questions.json` **(new)** | §11 |

The boundary stays intact: the analyzer reads passages from the package only (`tests\test_boundary.py`).

---

## 11. Evaluation and tests

**A question set** (`tests\fixtures\passage_questions.json`): ~20 questions across the models, each with the
passage(s) that must be found, checked by hand against the pages:

| Model | Question | Must find |
|---|---|---|
| TYPE-1 | How did the commercial vehicle export business perform? | MD&A p.76 CV passage |
| TYPE-1 | What is management's outlook for the Indian economy? | MD&A p.76 "Outlook" (Indian economy) |
| TYPE-1 | How is the defence business growing? | CMD's letter p.28 "Moving up the value chain"; MD&A defence |
| TYPE-3 | What drove net premiums written by region? | Item 7 p.60 "Net Premiums Written by Region" / "Premiums" |
| TYPE-3 | What are the main risks from climate / catastrophes? | Item 1A passages |
| TYPE-7 | What is the industry outlook for IT services? | Item 5 p.70 "Industry structure and developments" |
| TYPE-8 | What drove copper results? | 7.1 Copper (p.56–57), 4.1 Copper |
| TYPE-6 | What was the critical audit matter? | Auditor's report p.2–3 |

**Measures:**
- **Recall@30** of the ranking (target 100%: the right passage must reach the selector).
- **Recall@4** of the keyword fallback (target ≥ 80%).
- One real selector run per question (Phase 5, costs usage) to measure Claude's picks.

**Tests** (Claude mocked, as today):
- **Passages:** sizes stay within bounds; every narrative character is in exactly one passage (the coverage
  idea again); ids are stable; paths are right on the gold pages.
- **Ranking:** the question set's recall, synonyms, diversity.
- **Selector:** the reply is parsed with and without passages; no-note questions work; the fallback works;
  explicit "MD&A" / "Item 7" references are honoured.
- **Prompts:** the REPORT SECTIONS block and system addition appear only with passages. **Notes-only
  analysis, follow-up and pages prompts are byte-identical** to the baseline. Selector baselines are
  re-frozen after review.
- **Budget:** the lowest-ranked passage is dropped first, with a message.
- **Narrative figures:** unit conversions (crore / lakh / million / billion, US$), ties, no-ties, tolerance.
- **Threads, history, HTML:** passages carried through; follow-up "what does the Board's report say" adds one.
- **UI:** headless Edge screenshots of step 3 with passages; `node --check static/app.js`.
- **Real runs:** the p.76 CV question (TYPE-1) and one Chubb MD&A question, reviewed with you.

---

## 12. Phases

| Phase | Content | Done when |
|---|---|---|
| **0. Question set** | ~20 questions with hand-checked passages (§11) | Fixture in git |
| **1. Passages at ingest** | `passages.py`, `doc_passages`, schema 1.2, quality line, Model testing / formatted-document ids | Every model: passages within bounds, full coverage |
| **2. Ranking** | BM25, synonyms, kind boosts, diversity | Recall@30 = 100% on the question set |
| **3. Selector + step 3** | Candidates in the selector, `passages` in the reply, fallback, no-note questions, explicit section refs; step 3 group, Show, Add passage… | Selector tests; screenshots |
| **4. Analysis + follow-up** | REPORT SECTIONS block, system addition, business-context part, budget, threads, history, HTML; manual pages on narrative pages send the block reading order (Q6) | Notes-only prompts byte-identical; section and narrative-page prompts reviewed |
| **5. Narrative figures** | Figures with units at ingest; NARRATIVE FIGURES with conversion | Conversion / tie tests; p.76 example shows "no tie" for ₹1,324 crore |
| **6. Real runs + tuning** | CV question on TYPE-1, one Chubb MD&A question; tune the prompt wording with you | Answers reviewed |
| **7. Docs** | README, HOW doc, CLAUDE.md, plans' status, `STATUS-OF-PROJECT` item 1 closed | Docs current, full suite green |

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| The ranking misses the right passage (different wording) | Synonym table, heading-path weighting, the question set's recall target; the user can "Add passage…" in step 3 |
| Claude over-selects passages for accounting questions | Selector rules + cap of 4; the user can untick in step 3; notes-only prompts unchanged |
| Management's claims presented as facts | System addition (§6.2): unaudited, attribute claims, tie figures, flag contradictions |
| Unit confusion (crore vs ₹ million, US$ M vs B) | NARRATIVE FIGURES converts explicitly; the prompt names the statements' unit |
| Prompt size / cost grows | Budget and cap (§6.4); passages only when chosen |
| Chubb-size sections (182 K) | Passages of ≤ 8 K; diversity cap per section |
| Designed pages with poor reading order feed Claude badly | Passages come from blocks, which pass the coverage check; known limits are listed in the narrative plan |
| Baseline churn | Only selector baselines change; everything else is protected by §7.4 |

---

## 14. Decisions (confirmed 2026-09-28)

| # | Question | Decision |
|---|---|---|
| Q1 | **How passages are chosen** | **Python ranks the top ~30 and the existing single Claude selector call picks** notes and passages together (no extra call) |
| Q2 | **Ranking engine** | **Pure-Python BM25 + a synonym table**; no embeddings, no new dependency |
| Q3 | **Budget** | **Up to 4 passages, ~32 K characters** by default; the user can add more in step 3 within the 150 K total |
| Q4 | **Questions with no notes** | **Allowed**: step 3 needs at least one note *or* one passage (or manual pages) |
| Q5 | **Answer format** | **Keep *Impact on the financial statements* mandatory and add *Business context*** when passages are used |
| Q6 | **"Or analyze specific PDF pages" on narrative pages** | **Send the block reading order for narrative pages only**; notes / statements pages and `pages.text` unchanged. This settles Q3 of the narrative-extraction plan |
| Q7 | **Narrative figures (unit-converting tie check)** | **In this plan, Phase 5** |
| Q8 | **Labelling** | **Yes**: passages are management's unaudited commentary, and Claude attributes claims ("management states that …") and treats outlooks as expectations |

---

## 15. What was built (2026-09-28)

| Piece | Where |
|---|---|
| Passages at ingest: cut at every real heading (pull quotes typed as headings by their size, and the section's own title, are ignored), small ones merged ("Outlook · Commercial Vehicles (CV)"), large ones split ("(part 2)"); path, pages, text with page markers, lead, blocks, **money figures** (currency, scale incl. crore / lakh / lakh crore / million / billion) | `ingest\narrative\passages.py` |
| Package table `doc_passages` (schema **1.2**), `Package.passages()`, `passage(id)`, `passage_texts()`; quality line `Narrative passages: …`; `INDEX_VERSION` 33 | `rptpkg\schema.sql`, `writer.py`, `reader.py`, `ingest\quality.py` |
| Block rendering moved to the shared library, so the analyzer can use it without importing the ingester | `rptpkg\blocks.py` (`ingest\narrative\render.py` re-exports it) |
| Ranking: BM25 over path ×3 + text, synonym groups, kind boosts, named sections ("MD&A", "Board's report", "risk factors"), ≤ 4 per section, top 30; cached per package | `analyzer\passages.py`, `analyzer\passage_terms.py` |
| Selector: a `{{PASSAGES}}` block (empty for reports without passages, so their selector prompt is unchanged), `"passages"` in the reply, passages-only replies accepted, keyword fallback picks the best passages; named sections come first | `analyzer\note_selector.py`, `prompts\selector_passages.txt`, `selector_template*.txt`, `analyzer\jobs.identify_job` |
| Analysis / follow-up: `{{SECTIONS_LINE}}`, `{{SECTIONS}}`, `{{BUSINESS_CONTEXT}}` (all empty without passages), system addition `analysis_system_sections.txt`, budget (fills what the statements and notes leave, lowest-ranked dropped first), threads, Prompt-History "Report passages" line, HTML report line; a follow-up naming a section adds its best passage | `analyzer\jobs.py`, `analyzer\extract.extract_passages`, `prompts\analysis_template.txt`, `followup_template.txt`, `analysis_business_context.txt`, `analyzer\report_html.py`, `templates\analysis_report.html` |
| NARRATIVE FIGURES: money figures converted to the statements' unit; the tolerance is **half of the figure's last printed digit** (a percentage tied unrelated lines: ₹1,324 crore "tied" to Borrowings 13,193.66 at 0.35 %) | `analyzer\linked.narrative_figures_block`, `statement_unit`, `_tolerance` |
| Manual pages on narrative pages in reading order (Q6) | `analyzer\extract.extract_pages` |
| Step 3: "Report passages" group (tick boxes, Show, "Also related" suggestions, "Add passage…" search), size line; result header lists passages; routes `/api/passages/search`, `/api/passages/<id>` | `templates\index.html`, `static\app.js`, `static\style.css`, `analyzer\routes.py` |
| Model testing lists the passages on a page; the formatted document marks where each passage starts | `ingest\model_testing.py`, `ingest\formatted_report.py` (`FORMATTER_VERSION` 3) |
| Tests: `tests\test_passages.py`, `tests\test_analyze_sections.py`, question set `tests\fixtures\passage_questions.json` (15 questions, 6 models) | |

Measured:

| | Result |
|---|---|
| Passages | TYPE-1 188, Chubb 222, Infosys 176, BHP 329, BRK-1994 28, Magna 5, Reliance 3; every narrative block in exactly one passage |
| Question set | recall@30 **15/15**; top-4 (keyword fallback) **13/15 = 87 %** |
| Ranking time | < 0.25 s per question (index built once per package) |
| Prompt baseline | only the 8 selector files changed (reviewed, re-frozen); every analysis, follow-up, pages, report-load and statements-page file byte-identical |
| Real run (TYPE-1, the CV question) | 8 s to pick (Claude chose C49 + C24 and 4 passages: MD&A p.76, Board's report p.89 and p.95, CMD's letter p.30), 49 s to analyze. The answer cites the passages, attributes management's claims, back-calculates the prior-year CV revenue, shows that Note 49 does not disclose CV separately, and flags that US revenue rose 3.4 % while North American CV revenue reportedly halved (the narrative figures appear to be for the parent company). History #14. |

Not done: the Chubb MD&A real run (only the TYPE-1 run was made, to limit Claude usage); path names still carry
some noise from designed pages ("D80,061 crore · Expertise across …", "In ₹ Million").

## 16. Two flows: "Identify notes" and "Analyze non notes" (2026-09-28)

The first real run of "Analyze company's Commercial Vehicles business" (the §15 design: one selector call for
notes and passages, sections added to the forensic analysis prompt) gave a long, accounting-heavy answer: four
notes (incl. receivables and exceptional items), the 4-statement *Impact* section, and passages only ~15 % of
the prompt. Decided with the user:

| Question | Decision |
|---|---|
| Keep both logics | Two buttons: **Identify notes** (as originally) and **Analyze non notes** |
| Identify notes | **Notes only, as originally** - the selector, analysis and follow-up prompts are byte-identical to before §15 (the prompt baseline equals the copy saved before this plan) |
| Analyze non notes | **Passages + 1-2 confirming notes**, statements for reference; a business-focused answer with a short "How it shows in the audited numbers" table instead of the 4-statement Impact section |
| Confirm step | **Yes**: step 3 always pauses (passages and notes) |
| Figure ties | An approximate tie needs a word shared by the line label and the figure's context; an exact one (within 0.5 statement units) stands alone; note figures tie only exactly |

Built:

| Part | Where |
|---|---|
| `mode` = `notes` / `business` on `/api/identify` and `/api/analyze`; threads carry `mode`, follow-ups keep it | `analyzer\routes.py`, `analyzer\jobs.py` |
| `select_notes` notes only (`{{PASSAGES}}` removed); `select_for_business` (passages ≤ `MAX_PASSAGES`, notes ≤ `MAX_BUSINESS_NOTES` = 2, keyword fallback) | `analyzer\note_selector.py`, `prompts\business_selector_template.txt`, `business_selector_system.txt` |
| Business prompts: REPORT SECTIONS, NARRATIVE FIGURES, confirming notes, statements (for reference) | `prompts\business_template.txt`, `business_followup_template.txt`, `business_system.txt` |
| Tie rule | `analyzer\linked._ties`, `_tie_words` |
| UI: "Analyze non notes" button, step 3 title per mode, the passage box only in business mode | `templates\index.html`, `static\app.js`, `static\style.css` |
| Removed | `selector_passages.txt`, `analysis_system_sections.txt`, `analysis_business_context.txt` |
| Tests | `tests\test_analyze_sections.py` (both modes, the tie rule); 448 passed |

Real run (TYPE-1, "Analyze company's Commercial Vehicles business", Analyze non notes): 8 s to pick (MD&A p.76,
p.78, p.77-78, Board's report p.88; notes C49 Segment information, C24 Revenue from operations), 31 s to
analyze, 965 words (History #18). Domestic CV ₹1,036 cr +8 %, export CV ₹1,324 cr −34 %, combined ~−20 %, CV
~14 % of revenue; CV sits inside the Forgings segment so nothing ties directly. One exact coincidence
remains - PV ₹3,974 mn equals the OCI hedge line 3,973.98 - and Claude rejected it itself.
