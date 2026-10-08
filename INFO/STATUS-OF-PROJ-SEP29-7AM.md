# Project Status — 2026-09-29, 7 AM

**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Purpose:** the state of every feature built so far, what changed on 2026-09-29, and what is still open, so
work can resume later.
**Replaces:** `STATUS-OF-PROJECT-SEP-28-5PM.md` (kept for history). Detail on each feature: `.claude\CLAUDE.md` §7
(items 1–24) and the plans in `INFO\`.

---

## 1. Where things stand

| Plan / feature | Status |
|---|---|
| `ANN-RPT-NOTES-ANALYZER-PLAN.md`: the original app (notes index, 4 statements, note picking, analysis, follow-ups, history, HTML reports) | Done. Follow-ups changed on 2026-09-29 (§2.3) |
| `ANALYZE-OLD-REPORTS-FUNC-PLAN.md`: Companies Act 1956 reports | Done. Its "Phase 6 deferred" status line is out of date (item 6) |
| `OCR-CAPABILITY-PLAN.md`: scanned reports | Done |
| `SPLIT-FUNCTIONALITY-PLAN.md`: Ingester / Analyzer, report packages | Done |
| `MODEL-DOCS-FUNCTIONALITY-PLAN.md`: model documents TYPE-1…8 | Done, all 8 have rules. Its "Phase 4 not started" status line is out of date (item 6) |
| `MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md`: Model testing and feedback | Done. 1 feedback report, fixed |
| `MODEL-FORMATTED-REPORTS-PLAN.md`: the formatted document | Done |
| `ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md`: pages outside notes and statements (sections, blocks) | Done, Phases 0–8 for all 8 models. Phase 7 was built as the next plan |
| `ANALYZE-NON-FIN-DATA-PLAN.md`: questions use report passages; two flows, *Identify notes* / *Analyze non notes* | Done, and extended on 2026-09-29 (§2) |

Verified on 2026-09-29, after all of today's changes:
- `python -m pytest -q`: **455 passed** (8 min; 448 before today).
- `python -m ingest.models verify`: **OK** (run after change 2.1).
- `tests\test_prompt_identity.py`: green. The only baseline file re-frozen today is
  `Reliance-1976-1977.3-followup.prompt.txt`, an intended change (§2.3).
- `INDEX_VERSION` is **34** (was 33), so every package rebuilds on first use.

---

## 2. Work done on 2026-09-29

All four changes came from the user testing "Analyze non notes" on `BRK-1968.pdf` (the scanned 1968 Berkshire
10-K) with the question *"what percentage of shares was owned by Buffett partnership"*.

### 2.1 Old and scanned reports: their prose becomes passages (`.claude\CLAUDE.md` §7 item 23)
- **Symptom:** "Analyze non notes" said the report's sections "have not been read into passages (re-ingest it)",
  even after a re-ingest.
- **Cause:**
  - Old and scanned reports keep their prose as **`narrative` units** on the notes side:
    - BRK-1968: `BH-TXT` "Filing text" (PDF 1–5) and three auditors' reports;
    - Reliance: `SUM`, `DIR` (Directors' Report) and `AUD`.
  - The narrative stage skips every page covered by a unit.
  - So BRK-1968 had only 3 passages: a blank page, the exhibit covers and the "END / FILMED" page.
  - None of them matched the question, and the error message misread that as a missing ingest.
- **Fix:**
  - `ingest\narrative\__init__.py` (`narrative_units`, `_unit_pages`) also cuts the units' pages into passages:
    - section ids `U-<unit id>`;
    - the kind comes from the title (Directors' Report → `board_report`);
    - the company goes in the path when a report covers several (BRK-1968 + its two insurers).
  - Their blocks are **not stored** and the pages stay units, so notes prompts are unchanged.
- **Result:**
  - BRK-1968 has 9 passages; the question ranks `P-U-BH-TXT-01` (PDF 1–3, with "69.99%") first.
  - Reliance has 10, including its Directors' Report.
- **Error message:** `analyzer\jobs._identify_business` now asks for a re-ingest only when a report has no
  passages at all. Otherwise it says "none of this report's passages … uses the words of the question".
- **Tests:**
  - `test_narrative_units_are_passages_too` and `test_scanned_filing_text_is_a_passage` in `tests\test_passages.py`;
  - `test_no_matching_passage_is_not_called_a_missing_ingest` in `tests\test_analyze_sections.py`.

### 2.2 Step 3 shows why a passage was found
- **Symptom:** step 3 listed only the passage's path. Its tooltip and *Show* started at the passage's first
  words (the cover page for BRK-1968), so the searched words were not visible.
- **Fix:**
  - `analyzer\passages.snippet()`: every candidate carries `snippet` (the sentence holding most of the question's
    words) and `matched` (those words as printed).
  - `static\app.js` shows the snippet under each passage, marks the words there and in *Show* (it scrolls to the
    first match), in the suggestions and in the *Add passage…* search results.
  - The marks are built as DOM nodes (no `innerHTML`); CSS is in `static\style.css`.
- Prompts are unchanged: the selector only sees id / path / pages / lead.
- **Tests:** `test_snippet_is_the_sentence_with_the_question_words`, `test_candidates_carry_their_snippet`.

### 2.3 Follow-ups search the whole report again (`.claude\CLAUDE.md` §7 item 24; §6 decision changed)
- **User request:** a follow-up used only the first question's context; the whole report should be searched again.
- **Decisions (asked 2026-09-29):**
  - **add** to the thread's notes and passages (the earlier ones stay);
  - **no pause** (the answer says what was added);
  - **both flows**.
- **Built** in `analyzer\jobs.py`:
  - **`_rescan_notes`** (Identify notes):
    - runs `select_notes` on the follow-up plus the thread's first question (`_followup_question`);
    - adds up to `MAX_FOLLOWUP_NEW_NOTES` = **3** new notes;
    - adds no notes when the selector is unavailable (keyword guesses would only add noise);
    - a note from the other section brings that section's statements.
  - **`_rescan_business`** (Analyze non notes):
    - ranks passages for the follow-up and runs `select_for_business`;
    - adds up to `MAX_FOLLOWUP_NEW_PASSAGES` = **2** passages and up to `MAX_BUSINESS_NOTES` = 2 notes.
  - Additions that would pass `MAX_CONTEXT_CHARS` (150 K) are skipped and logged.
  - Unchanged: a thread on manual PDF pages keeps its pages; "Note 41" and "what does the Board's report say …"
    still add that note or section.
- **Cost:** one extra small Claude call per follow-up (about 8 s).
- **UI:**
  - under each follow-up: "Found in the report for this follow-up, added to the thread: …" or
    "Searched the report again: nothing new was needed …";
  - a new progress text, and a new placeholder in the follow-up box.
- **Tests:** `test_followup_searches_the_report_again` (`tests\test_app.py`) and
  `test_business_followup_searches_the_report_again` (`tests\test_analyze_sections.py`).
- **Real run** (BRK-1968):
  - follow-up: *"What did the accountants say about the National Indemnity Company statements?"*;
  - added: National Indemnity's accountants' report (p.22), its Exhibit 1 cover and its Note 1 (p.28);
  - the answer quoted both audit opinions and Note 1 correctly (History #22–#23).

### 2.4 Checked, not a defect
*Ask follow-up* looked broken. It is enabled only when the follow-up box has text. Confirmed with the user and
with a Playwright run in Edge.

### 2.5 Docs updated today
- `.claude\CLAUDE.md`: §6 "Follow-ups" row, §4 flow line, §7 items 23–24, `INDEX_VERSION` 34, 455 tests.
- `WEB-APP\README.md`: the follow-up paragraphs of both flows.

---

## 3. Open items

### A. Must do before relying on the exe

**1. Rebuild the exe.**
- `ANN-RPT-ANALYZER.exe` (port 5001 in the user's tests) is dated 2026-09-29 06:16, but its builder copy
  `PYTHON-EXEC-BUILDER\INPUT\PROJ-2\ANN-RPT-ANALYZER\WEB-APP` is older than the passages work: it has no
  `analyzer\passages.py`. Which source that exe came from is not certain.
- Steps:
  1. Refresh the INPUT copy (`robocopy /MIR`, excluding the runtime folders).
  2. Build with the **dashboard builder** (`PYTHON-APP-DASHBOARD`, style `dashboard`); see `.claude\CLAUDE.md` §3.
  3. Check for "Dashboard smoke test PASSED".
- Until then, test with `python app.py` (port 5000) and press Ctrl+F5 in the browser.

**2. Commit to git.**
- Nothing has been committed since `bb0892e` (the second commit).
- About 38 tracked files are changed and 130 paths are untracked.
- The uncommitted work covers: the split, model documents, Model testing, the formatted document, narrative
  extraction, passages, the two flows and today's work.
- `tests\fixtures\baseline\` is untracked too, so baseline diffs cannot be reviewed with `git diff` yet.
- Needs a branch and a few logical commits.

### B. Small fixes and checks

**3. The four page defects on TYPE-1 PDF pages 28 and 76** (from the Sep 28 status, item 2) — **still open.**
1. A false `[Figure]` block from the facing page of a two-page spread.
   - `geometry.capture` still keeps images outside the page area (x < 0).
   - Fix: filter them to the page area, as is already done for drawings.
2. Headings printed in colour or medium weight are not recognised.
   - p.76: "BUSINESS ENVIRONMENT / Automobile Business / …" became one paragraph.
   - p.28: "Moving up the value chain" is joined to the following paragraph.
3. The large pull quote on p.28 shows as an ordinary paragraph.
4. Model testing shows "Section: -" on a narrative page.

Each fix needs a regression test on its page and a re-check of the gold pages. Any change to what goes into a
package means an `INDEX_VERSION` bump.

**4. Passage quality on old and scanned reports (seen today).**
- Reliance's Financial Summary passage path is noisy: its table's year columns were typed as headings
  ("Financial Summary › 1976-77 › 1975-76 …").
- BRK-1968's tiny passages (exhibit covers of ~150 characters, "END / DATE / FILMED") are harmless but useless.
  A minimum size for passages without a heading would drop them.

**5. Follow-up rescan: watch in real use.**
- New behaviour; tuning points:
  - the caps (3 notes / 2 passages);
  - the first question added to the selector prompt;
  - whether "nothing new was needed" shows too often for short follow-ups ("explain point 2").
- The Identify-notes rescan has been tested with Claude mocked only. The real run was on the business flow;
  one real notes-flow follow-up is worth doing.

**6. Out-of-date status lines in two plans** (still open from Sep 28, item 5).
- `MODEL-DOCS-FUNCTIONALITY-PLAN.md` line 7 still says "Phase 4 … not started". It is done (CLAUDE.md §7
  items 16–17; the plan's own §16.4).
- `ANALYZE-OLD-REPORTS-FUNC-PLAN.md` line 361 still says "Phase 6 (scanned reports) is deferred". It was
  built by `OCR-CAPABILITY-PLAN.md`.

**7. The HOW document is behind.**
`INFO\HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html` still describes follow-ups as "on the same notes" (lines 211,
228, 273). It has nothing on:
- the two flows or report passages;
- unit passages (2.1) or snippets (2.2);
- the follow-up rescan (2.3).

**8. Known extraction limits (unchanged, from the narrative plan §15–§16).**
- BHP p.41: two figures in one cell.
- "Read more Pg. 28" shows as a small heading.
- Short label rows stay paragraphs.
- Map-like infographics (TYPE-1 p.20): approximate reading order.
- Reliance's contents sections are approximate.

### C. Data on this PC

**9. The TYPE-5 model document (Berkshire 1968) is not transcribed here.**
- `MODEL-DOCS\TYPE-5-USA-Ann-rpt-1968-Berkshire.pdf` has no transcription cache on this PC:
  - `ingest.models verify` skips its self-match;
  - its imported package is not rebuilt.
- The same PDF in `Annual-reports\BRK-1968.pdf` **is** transcribed (`cache\BRK-1968.ocr`). All of today's
  BRK-1968 work used it.
- Fix: copy or rename that transcription cache for the TYPE-5 name, or export/import the package on the Ingest
  screen. Re-transcribing would cost about $5.

### D. Not done from the plans

**10. A real Chubb MD&A run** of the passages flow (`ANALYZE-NON-FIN-DATA-PLAN.md` §15, "Not done"). Only TYPE-1
and today's BRK-1968 runs were made.

**11. Noise in passage paths from designed pages** ("D80,061 crore · Expertise across …", "In ₹ Million").

---

## 4. Backlog ideas (not planned in detail)

12. Ready-made question templates (Tax, Debt, Related parties, Contingencies).
13. Export of an analysis to Word or PDF.
14. More patterns for the automatic checks (profit = income − expenses); non-simple subtotals are
    "unverified" today.
15. A local copy of Bootstrap so the formatted document, statements page and reports work offline (in the
    exe too).
16. Model testing for any PDF in `Annual-reports\`, not only model documents.
17. KPI datasets from BRSR / ESG pages as structured data.
18. New from today: a *Search the whole report again* switch per follow-up, if the automatic rescan turns out
    too eager. Offered as an option on 2026-09-29; the user chose automatic.

---

## 5. Decided against (not pending)

These were considered and rejected by decision:
- browser PDF upload;
- batch ingestion (Q7);
- comparing reports across years (Q8);
- Claude tools that query the package or PDF (Q9);
- reading chart values;
- automatic writing of extraction rules;
- matching with an LLM;
- an LLM knowledge graph.

Changed by decision today, not dropped: follow-ups are no longer limited to the thread's first notes (§2.3).

---

## 6. How to resume

1. Read `.claude\CLAUDE.md` (§6 decisions, §7 items 21–24, §9 gotchas) and this file.
2. `cd WEB-APP`, then `python -m pytest -q`. Expect **455 passed** (~8 min).
3. `python app.py`, then open http://127.0.0.1:5000. `BRK-1968.pdf` with the Buffett Partnership question is
   a quick check of 2.1–2.3.
4. Suggested order:
   - items 2 and 6 (commit, status lines);
   - item 1 (rebuild the exe);
   - item 7 (HOW doc);
   - item 3 (page defects);
   - items 4–5 (passage quality, rescan tuning);
   - the backlog, as wanted.
