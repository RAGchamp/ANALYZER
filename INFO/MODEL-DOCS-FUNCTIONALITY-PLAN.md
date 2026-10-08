# Model Documents — Plan (match every report to a known format, or say it is new)

**Date:** 2026-09-27
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `INFO\SPLIT-FUNCTIONALITY-PLAN.md` (Ingester / Analyzer / report packages)
**Model documents:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\MODEL-DOCS\` (8 today, expected to reach ~100)
**Status:** **Phases 1, 2, 3 and 5 implemented** 2026-09-27 (263 tests pass). Phase 4 (rules for TYPE-6/7/8) not started. Decisions in §14; what was built in §16.

---

## 0. Goal and summary

Today every new kind of annual report means going back into the extraction code. Worse, the
app does not *know* when a report is of a kind it has never seen: it guesses a format and
extracts whatever that format's rules happen to find (§1.2).

The goal:

1. Every **format the app can read** is represented by a **model document** in
   `MODEL-DOCS\` (one document = one model, Q2), each tied to an explicit **format
   profile** (the extraction rules for it; several models may share or extend a profile).
2. Every report the user selects is first **matched** against the model documents.
   - **Match:** it is extracted with that model's profile, and the package records which model it matched and how well.
   - **No match:** the app says **"New model document: the extraction logic needs an update"**, names the nearest model and what differed, and writes a **gap report** for the developer. It does not silently guess, and the report **cannot be analyzed** until its model is added (Q1).
3. Adding format number 9, 10 … 100 becomes a **routine**: add the model PDF, write or configure its profile, add its golden test. The existing formats cannot change, because every model document is part of the regression baseline.

The matching is **deterministic** (fingerprints and test extractions, no LLM), in line with
the app's design principle. It is built in two stages (§4): a cheap **fingerprint** shortlists
the nearest models, and a **trial extraction** with each shortlisted model's profile must pass
that model's **acceptance checks**. Looking alike is not enough; the rules must also work.

---

## 1. Where things stand today (verified 2026-09-27)

### 1.1 The eight model documents

Probed with PyMuPDF and the current format detector (`ingest\report_format.detect`):

| Model | Pages | Text layer | Producer | Fonts (share of text) | Bold | Cover form | Current detector says | Supported today |
|---|---|---|---|---|---|---|---|---|
| TYPE-1 INDIA Ann-rpt 2026 Bharat Forge | 508 | yes | Online2PDF.com | Lato-Light 71%, Lato-Bold 11% | 16% | — | modern (277 notes-header pages) | **yes** (modern, Ind AS) |
| TYPE-2 INDIA Ann-rpt 1977 Reliance | 33 | yes | Acrobat Distiller 2.1 | Helvetica 85% | 15% | — | legacy (18 schedule headings) | **yes** (legacy, Schedule VI) |
| TYPE-3 USA 10-K 2025 Chubb | 460 | yes | EDGRpdf Service | Arial 54%, Times 42% | 4% | FORM 10-K | us-10k | **yes** (us-10k) |
| TYPE-4 USA Ann-rpt 1994 Berkshire | 56 | yes | iTextSharp (Thomson Reuters) | Courier 99% | 0% | FORM 10-K | us-10k | **yes** (us-10k, typewriter rules) |
| TYPE-5 USA Ann-rpt 1968 Berkshire | 39 | **no** (scanned) | Acrobat Image Conversion | — | — | — | (scanned) | **yes** (OCR, transcribed) |
| TYPE-6 CANADA 40-F 2025 Magna | 132 | yes | Toppan Merrill | Helvetica Neue 93% | 5% | — | **legacy** (8 "schedule" hits) | **no** |
| TYPE-7 INDIA 20-F 2025 Infosys | 256 | yes | Skia/PDF (Chrome) | Times New Roman 89% | 6% | FORM 20-F | **modern** (only 4 notes-header pages) | **no** |
| TYPE-8 AUSTRALIA 20-F 2025 BHP | 363 | yes | Skia/PDF (Chrome) | Times New Roman 86% | 9% | — | **modern** (0 notes-header pages) | **no** |

What types 6–8 look like (first lines of each page):
- **Magna 40-F:** no notes or statement headers at the top of any page. A 40-F often files
  the financial statements as separate exhibits, so they may not be in this PDF at all.
- **Infosys 20-F:** statements titled "Consolidated Balance Sheet as of March 31,"; the notes
  have **no running header** (one "Overview and Notes to the Consolidated Financial Statements" page).
- **BHP 20-F:** statements **section-numbered** ("1.3 Consolidated Balance Sheet as at 30 June 2026"),
  notes cited as "Financial Statements note 24"; IFRS, Australian year-end (30 June).

### 1.2 The problem in the code

- `report_format.detect()` **never says "unknown"**: "modern wins ties", and a report with no
  signals at all is called modern. BHP (0 notes pages) is "modern"; Magna is "legacy" because
  its text mentions "schedule" 8 times.
- The format rules are **implicit**: spread over `notes_index`, `statements`, `tables`, `clip`,
  `figures` as conditions ("if bold", "if ≥14pt", "if monospaced", "if the section has no bold
  headings"). Each new format adds more conditions, and only the prompt baseline stops them
  from breaking the others (as it did for BRK-1994, when a general column rule changed Bharat
  and Reliance).
- The quality report (`ingest\quality.py`) already measures whether extraction worked
  (notes, statements, figures, links, checks) — but only warns; it never refuses.

### 1.3 What we can build on

| Existing piece | Use in this plan |
|---|---|
| Report packages (`rptpkg`), `pipeline.ensure_package` | Record the matched model; rebuild when the model registry changes |
| Quality report and checks (`quality.py`, `links.py`) | The **acceptance checks** of a trial extraction |
| Prompt baseline (`tests/prompt_baseline.py`, 5 samples) | Becomes **one baseline per model document** |
| Golden tests per sample (`test_notes_index`, `test_ten_k`, `test_plain_text_filing`, …) | Become **model tests** |
| `report_format.detect` signals | Seed features for the fingerprint |

---

## 2. Concepts

| Term | Meaning |
|---|---|
| **Model document / model** | A real annual report PDF in `MODEL-DOCS\`. **Each document is its own model** (Q2), identified by its file name `TYPE-<n>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf` (Q5). |
| **Format profile** (below) | Two models with the same layout may share a profile, or one may extend the other's; they stay separate models. |
| **Format profile** | The extraction rules for a format: which indexer, and its parameters (running-header wording, heading style, title style, column rule, …). Explicit and versioned. |
| **Fingerprint** | A small, cheap, deterministic description of a PDF's layout and wording (§4.1). Stored for every model document. |
| **Acceptance checks** | What a correct extraction of this format must produce (notes in sequence, statements found, balance sheet balances…) (§4.3). |
| **Match** | A report is matched to a format when its fingerprint is close to that format's **and** a trial extraction with its profile passes the acceptance checks. |
| **New model document** | A report no format matches. The app says so and writes a **gap report**. |

---

## 3. The flow

```
PDF selected (Analyze step 1, Ingest screen, or python -m ingest)
   │
   ▼
1 fingerprint (2–6 s for 500 pages; cached by SHA-256)
   │
   ▼
2 shortlist: the 3 models with the closest fingerprints (hard constraints first, §4.2)
   │
   ▼
3 trial extraction with each shortlisted profile, best first; stop at the first that
  passes its acceptance checks (§4.3)
   │
   ├── passes ──► ingest with that profile ──► package (meta: model, score, checks) ──► Analyze
   │
   └── none passes ──► "New model document" ──► gap report (§6) ──► blocked (§5.2, Q1)
```

- A report that **is** a model document (same SHA-256) matches its own format directly.
- Matching runs once per report version; the result is stored in the package. It is re-run
  when the **model registry** changes (a new format may now match an old "new model" report).
- Scanned PDFs are fingerprinted from their page images' OCR text after transcription; before
  that they can only match a scanned format by their physical features (§4.1 A).

---

## 4. Matching

### 4.1 The fingerprint

Deterministic features, computed from the text layer and fonts (sampling at most ~60 pages for speed):

| Group | Features | Example (TYPE-4 BRK-1994) |
|---|---|---|
| **A. Physical** | text-layer share; page size; page count band; producer family (EDGAR, Toppan Merrill, Chrome/Skia, Distiller, iText…) | 100%, Letter, 50–100, iText |
| **B. Typography** | share of monospaced / serif / sans text; share of bold; heading style (large / bold / plain capitals); number of distinct sizes | mono 99%, bold 0%, plain capitals |
| **C. Document type** | cover form (10-K / 20-F / 40-F / none); accounting framework words (Ind AS, IFRS, US GAAP, Companies Act 1956, Schedule VI); currency and units (₹, Rs., lakh/crore, $, A$, C$, "in millions") | FORM 10-K; US GAAP; $, "in thousands" |
| **D. Era** | the years named on the cover and in statement headings | 1994 |
| **E. Structure** | running notes header present and its wording, pages with it; note-heading patterns ("21. X" bold, "(1) X" capitals, "Note 21 –", section-numbered "1.3"); statement-title patterns (big, bold capitals, plain capitals, numbered); Notes column in statements; schedules; "Item 8" / "Item 18" | notes header on 18 pages; "(1) X" capitals; plain-capitals titles; no Notes column |

Stored as a small JSON object per model document (a few KB), so the app ships the fingerprints
without the PDFs (the model PDFs are 77 MB today; at 100 models, several GB).

### 4.2 Shortlist: similarity

1. **Hard constraints** (a mismatch rules a format out): scanned vs text; monospaced vs
   proportional typesetting; cover form when both have one (a 20-F is not a 10-K model).
2. **Weighted similarity** over the rest: categorical features exact-or-not, shares compared
   numerically, pattern sets by overlap (Jaccard). Structure (E) and document type (C) weigh
   most; producer and page count least (the same format comes from different printers).
3. Keep the **top 3** models above a floor score.

Scale: comparing one fingerprint with 100 stored ones takes milliseconds.

### 4.3 Decide: trial extraction and acceptance checks

For each shortlisted format, best first: build the index with its profile (in memory, no
package yet) and run its acceptance checks. The first to pass wins.

Acceptance checks come from the format's profile, with defaults:

| Check | Default |
|---|---|
| Notes found | ≥ 5 notes, numbered in sequence, covering a contiguous page range |
| Statements found | ≥ 3 of the 4 types (P&L, BS, CF, Equity) — or the format's declared set (old reports: 2) |
| Statements before or near the notes | within the format's window |
| Figures readable | ≥ 90% of statement figures parse as numbers |
| Balance sheet | balances in every period, when both totals are found |
| Format-specific | e.g. US 10-K: "Item 8" present; legacy: schedules A… found; scanned: transcription complete |

These are the same measures the quality report already computes (§1.3), used as gates instead of
warnings. Cost: a trial extraction is the index step of ingestion (1–8 s); at most 3 per new report,
once.

### 4.4 Why two stages, and not just similarity

- Similarity alone cannot know whether the **rules** work: BHP and Chubb both look like
  "Times/Arial, US-style 20-F/10-K", but Chubb's rules find nothing in BHP.
- Trial extraction alone (try every profile) would cost minutes at 100 formats, and a lenient
  profile could "pass" on the wrong document.
- Together: similarity keeps it fast and explains *which* format is nearest; the checks make
  the decision safe.

### 4.5 Expected results on today's documents (the acceptance test for this plan)

| Document | Expected |
|---|---|
| TYPE-1 … TYPE-5 (and their copies in `Annual-reports\`) | match themselves; extraction identical to today (prompt baseline unchanged) |
| TYPE-6 Magna, TYPE-7 Infosys, TYPE-8 BHP | **New model document**, until their profiles are built (Phase 4) |

---

## 5. What the user sees

### 5.1 A match

- **Step 1 (Analyze) and the Ingest screen:** "Format: TYPE-3 — USA 10-K (matched, similarity 0.91;
  checks passed)", next to the quality report. The manual format override stays.
- The package's meta records `model`, `model_score`, `model_checks`, `registry_version`.

### 5.2 No match

A clear panel instead of a silently wrong extraction:

> **New model document.** This report doesn't match any of the 8 model documents.
> Nearest: TYPE-3 USA 10-K 2025 (similarity 0.62). With its rules: 0 notes found, 2 of 4
> statements found. The document data extraction logic needs an update for this format.
> This report can't be analyzed until its model is added.
> [View gap report] [Propose as a model document]

- The CLI (`python -m ingest`) exits with a distinct code and prints the same message.
- **Blocked completely (Q1):** no note selection, no "analyze specific PDF pages", no
  "try the nearest model". The Analyze screen shows only this panel for the report.
- **The manual format override** (step 1 and the Ingest screen) now chooses *which model to
  try*; that model's acceptance checks must still pass, otherwise the report stays blocked.
- **Propose as a model document (Q3):** copies the PDF to `MODEL-DOCS\_candidates\` with its
  gap report. It becomes a model only when a developer has added its profile and registered it (§7).
- **Doubtful matches (Q8):** when two models pass with close scores, or the best passes only
  narrowly, the answer is "new model document", not the nearest match.

---

## 6. The gap report (for the developer)

Written to `MODEL-DOCS\_candidates\<pdf name>\GAP-REPORT.md` (and shown in the app), so a
Claude Code session (or a developer) has everything needed to add the format:

- The fingerprint, side by side with the 3 nearest models, differences highlighted.
- For each nearest model: what its trial extraction found and which acceptance checks failed.
- **Evidence pages:** where the notes and statements probably are (pages with "Notes to…",
  statement names, "Item 8/18"), with the first lines of each, the fonts and sizes of likely
  headings, and a few sample table rows as extracted.
- A checklist to onboard the format (§7).
- Deterministic only by default. An optional "Describe this format with Claude" button could add
  a summary of the layout from a few page images (it costs usage) — **Q9**.

---

## 7. Adding a new format (the routine)

1. **Candidate:** the user copies the PDF into `MODEL-DOCS\` as `TYPE-<next>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf`
   (or the app does, from the "New model document" panel — Q3).
2. **Profile:** in a Claude Code session, read the gap report and write the profile:
   - **Configuration only**, when an existing indexer fits with different parameters (another running-header wording, a different statement window, section-numbered titles…).
   - **A new rule module**, when the layout needs new logic, plugged into the profile — never an `if` inside another format's code path.
3. **Golden test:** notes (count, a known note's pages), statements (pages), a figure with its period, and the acceptance checks.
4. **Register:** `python -m ingest.models register TYPE-9 …` recomputes the fingerprint and adds the entry.
5. **Verify:** `python -m ingest.models verify` — every model document matches itself and nothing else; `python -m pytest` — every model's baseline is unchanged.
6. **Ship:** rebuild the exe (the registry JSON ships; the model PDFs don't).

The routine is also documented in `MODEL-DOCS\README.md`, so it survives across sessions.

---

## 8. Format profiles (making the rules explicit)

Today's implicit rules become named profiles. Each is a small declarative object plus
references to rule functions:

| Profile | Model(s) | Indexer | Key parameters |
|---|---|---|---|
| `in-indas-2020s` | TYPE-1 | notes-by-running-header | headers "Notes to … Financial Statements"; headings bold "21. X"; titles ≥14pt; Notes column |
| `in-schedule-vi-1970s` | TYPE-2 | legacy (schedules) | "SCHEDULE 'X'"; "forming part of"; Rs., Indian grouping |
| `us-10k-edgar` | TYPE-3 | notes-by-running-header | titles bold capitals; `F-8` page numbers; `$` cells; no Notes column |
| `us-10k-typewriter` | TYPE-4 | notes-by-running-header | monospaced splitting; plain-capitals titles; "(1) X" headings; continuation pages |
| `scanned-ocr` | TYPE-5 | transcribed | Claude transcription; one section per company |
| *(to build)* `ca-40f-…`, `in-20f-…`, `au-20f-…` | TYPE-6/7/8 | per gap report | — |

- The monospaced, plain-capitals and continuation-page rules added for BRK-1994 become
  **profile switches**, turned on for `us-10k-typewriter` only, instead of "if every cell is
  monospaced" conditions.
- Profiles can **extend** a base profile (e.g. `in-20f` extends `us-10k-edgar` with
  "as of March 31" titles and header-less notes), so 100 formats don't mean 100 copies.
- The profile id and version go into the package, so a profile change rebuilds only the reports
  of that format (not all of them, as `INDEX_VERSION` does today).
- Refactoring into profiles must keep every existing baseline **byte-identical** (as in the split).

---

## 9. Code and files

```
MODEL-DOCS\                          the model PDFs (source of truth; not shipped in the exe)
  TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf … TYPE-8-…
  README.md                          the routine (§7)
  _candidates\<pdf name>\GAP-REPORT.md
WEB-APP\ingest\models\
  registry.json                      one entry per model document: type, format/profile id, fingerprint,
                                     acceptance checks, golden facts, registry version
  fingerprint.py                     §4.1
  match.py                           shortlist + trial extraction + decision (§4.2–4.3)
  gap_report.py                      §6
  __main__.py                        python -m ingest.models  fingerprint | match <pdf> | register | verify
WEB-APP\ingest\profiles\             §8: one module per profile (+ shared rule functions)
WEB-APP\ingest\pipeline.py           ensure_package: match first; NewModelDocument when nothing matches
config.py                            MODEL_DOCS_DIR (env MODEL_DOCS_DIR)
```

- `pipeline.ensure_package` raises **`NewModelDocument`** (like `NeedsTranscription` today); the
  routes turn it into the §5.2 panel.
- Package meta: `model`, `profile`, `profile_version`, `model_score`, `model_checks`, `registry_version`.
- The analyzer is unaffected (it reads packages); it only shows the model line.

---

## 10. Scaling to ~100 formats

| Concern | Approach |
|---|---|
| Matching time | Fingerprint once (cached by SHA-256); compare with 100 fingerprints in ms; at most 3 trial extractions |
| Rules growing tangled | Profiles with explicit switches and inheritance (§8); a new format never edits another's code path |
| Regression risk | Every model document has a golden test and a prompt baseline; `verify` checks self-match and no cross-match |
| Test time | Tiers: `pytest -m fast` (fingerprints, units, a few models) for every change; the full per-model suite before a release/exe build |
| Model PDF storage | `MODEL-DOCS\` stays **outside git** (Q6); the registry keeps each PDF's SHA-256; tests skip models whose PDF is missing, but `verify` reports them |
| Similar formats confused | Hard constraints + acceptance checks; prefer "new model" over a wrong match (Q8) |
| Registry drift | `registry_version`; packages remember it; changing it re-matches reports marked "new model" |

---

## 11. Testing plan

| Test | What it proves |
|---|---|
| Fingerprint determinism | Same PDF → same fingerprint; renamed copy → same |
| Self-match | Each of TYPE-1…5 matches its own format, with checks passed |
| Copies | The 5 reports in `Annual-reports\` match TYPE-1…5 |
| New-model detection | TYPE-6, 7, 8 → `NewModelDocument`, nearest model named, gap report written |
| No cross-match | No model document matches another format |
| Hard constraints | A scanned PDF never matches a text format; a monospaced one never matches a proportional one |
| Acceptance checks | Unit tests with doctored indexes (0 notes, 2 statements, unbalanced BS) fail the gate |
| Baseline unchanged | Prompt baseline byte-identical for TYPE-1…5 after the profile refactor |
| UI / CLI | The "New model document" panel on step 1 and the Ingest screen; CLI exit code and message |
| Generalisation (later, Q4) | Extra test reports that are not model documents (e.g. another US 10-K) match the right model; added when you provide them |

---

## 12. Phases

| Phase | Deliverable | Visible to the user |
|---|---|---|
| **0** | This plan agreed; §14 answered (done 2026-09-27) | — |
| **1** | Fingerprints, registry for TYPE-1…5, matcher (shortlist + trial extraction + checks), `NewModelDocument`, the panel, CLI; types 6–8 flagged | Model line; "New model document" panel |
| **2** | Format profiles: today's rules moved into 5 explicit profiles with switches; baseline byte-identical | — |
| **3** | Gap report; "copy as candidate"; `python -m ingest.models register / verify`; `MODEL-DOCS\README.md` | Gap report |
| **4** | Onboard TYPE-6 Magna (first: are the financial statements in the PDF?), TYPE-7 Infosys (header-less notes), TYPE-8 BHP (section-numbered statements, "note 24" references, 30 June year-end) — each through the §7 routine | Three more formats supported |
| **5** | Scale: test tiers, registry versioning, exe packaging of the registry; extra test reports (Q4) as they arrive | — |

Each phase ends with tests green, README / HOW doc / CLAUDE.md updated, and the exe rebuilt.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| **False "new model"** for a report of a known format that differs slightly (another printer, another year) | Acceptance checks decide, not looks; extra test reports (Q4, later) tune the shortlist floor; the user can choose a model to try (it must still pass its checks); otherwise the report becomes a new model — the price of Q1 + Q8 |
| **False match** (wrong rules, plausible output) | Hard constraints; format-specific checks (e.g. 10-K needs "Item 8"); prefer "new" when two formats pass with close scores |
| **One document per format over-fits** the fingerprint | Treat structure features as patterns, not exact values; add documents to a format as they arrive |
| **Refactor breaks today's formats** | Profiles introduced only with the byte-identical baseline (as in the split) |
| **Model PDFs grow to GBs** | Not in the exe; outside git or LFS; fingerprints and golden facts are small and shipped |
| **Magna 40-F may have no financial statements** (exhibits) | Phase 4 checks first; if absent, the gap report says so and the format is registered as "statements in separate exhibits" |
| **Onboarding still needs code** for truly new layouts | Yes — the plan makes that work isolated, guided (gap report) and safe (verify + baselines), not automatic |

---

## 14. Decisions (confirmed 2026-09-27)

| # | Question | Decision | Where applied |
|---|---|---|---|
| Q1 | What may the user do with a report that matches no model? | **Block completely**: no analysis at all (no notes, no manual pages, no "try nearest") until its model is added | §0, §3, §5.2 |
| Q2 | Is a model one document or a format family? | **One document each**; several models may share or extend a profile | §0, §2, §8 |
| Q3 | Who registers a new model? | **The app proposes, the developer registers**: "Propose as a model document" copies it to `MODEL-DOCS\_candidates\` with its gap report | §5.2, §6, §7 |
| Q4 | Extra test reports (not model documents) for generalisation? | **Later**: build and test on the 8 model documents first | §11, §12 |
| Q5 | Model id | **The file name** `TYPE-<n>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf`; the registry adds a description and the profile | §2, §9 |
| Q6 | Where the model PDFs live | **Outside git**; the registry keeps each PDF's SHA-256 | §9, §10 |
| Q7 | Re-check ingested reports when the registry changes? | **Yes, automatically**: packages remember the registry version and are re-matched | §3, §10 |
| Q8 | When in doubt | **Say "new model document"** | §5.2, §13 |
| Q9 | Claude in the gap report | **Optional "Describe with Claude" button, off by default**; matching stays deterministic | §6 |

---

## 15. Out of scope

- Automatically *writing* extraction rules for a new format (the plan detects, explains and
  guides; a person or Claude Code session still writes the profile).
- Matching with an LLM or ML model (deterministic fingerprints and checks only).
- Changing the Analyzer, prompts or the decisions in `INFO\SPLIT-FUNCTIONALITY-PLAN.md` §14.

---

## 16. Implementation notes (2026-09-27)

### 16.1 What was built

| Plan | Built |
|---|---|
| §8 Profiles | `ingest/profiles/__init__.py`: `in-indas-2020s`, `in-schedule-vi-1970s`, `us-10k-edgar`, `us-10k-typewriter` (derived from `us-10k-edgar`), `scanned-ocr`. Each has an indexer, a format label, `Switches` and `Acceptance`, and a `version`. The BRK-1994 rules (monospaced columns, plain-capitals titles, "(1) X" headings, continuation pages) are switches, on for `us-10k-typewriter` only, read with `profiles.active()` during ingestion. `notes_index.build_index(pdf, profile=…)`. |
| §4.1 Fingerprint | `ingest/models/fingerprint.py` (`FINGERPRINT_VERSION` 1): all pages' text, fonts from ≤60 sampled pages (images skipped). 6–10 s for a 500-page report, 0.1 s for a scan; cached by SHA-256 in `cache/fingerprints/`. |
| §4.2–4.3 Matching | `ingest/models/match.py`: SHA match → otherwise hard constraints (scanned, typewriter, cover form) → weighted similarity → trial extraction of the best 3 models **that have rules** and score ≥ 0.55 → acceptance checks (`acceptance.py`) → two different profiles passing within 0.05: new model (Q8). `NewModelDocument` carries the nearest models, their trial results and the gap report. "New model" verdicts are remembered in `cache/matches/` until the registry, `INDEX_VERSION` or `MATCHER_VERSION` changes (a new report is judged once, then answered in ~1 s). |
| §9 Registry | `ingest/models/registry.json` (version 8): TYPE-1…5 with their profiles, **TYPE-6/7/8 registered as pending** (no rules yet), each with SHA-256, description and fingerprint. `python -m ingest.models list / fingerprint / match / register / refresh / verify`. |
| §5 UI | Step 1: "Model: … (model document; checks passed)" next to the format, or the **New model document** panel (nearest models and what their rules found, *View gap report*); the report stays blocked. Ingest screen: the same panel with *Propose as a model document* and *Describe with Claude* (confirm dialog), and a **Model documents** card listing the registry. Analyze / follow-up / statements for a new model document: refused ("New model document: …"). |
| §6 Gap report | `ingest/models/gap_report.py` → `cache/gap-reports/<pdf>-<sha>.md`, page `/gap-report?report=…`: verdict, fingerprint table vs the nearest models (differences marked ≠), each model's checks, evidence pages (first lines, heading fonts), the routine. *Propose* copies PDF + `GAP-REPORT.md` to `MODEL-DOCS/_candidates/<name>/`. *Describe with Claude* renders up to 4 evidence pages and appends Claude's description. |
| §7 Routine | `MODEL-DOCS/README.md`. |
| §9 Package | meta `model`, `model_short`, `profile`, `profile_version`, `model_score`, `model_match` ("model document" / "fingerprint"), `model_checks`, `registry_version`. A package is rebuilt when its profile's version changes; when the registry version changes it is re-matched (same model and profile: only the meta is updated). |
| §10 Test tiers | `pytest.ini` marker `slow` (reads the large MODEL-DOCS PDFs): full run 263 tests ~5 min, `-m "not slow"` 251 tests ~2 min. |

### 16.2 Results on the eight model documents (§4.5)

- `python -m ingest.models verify`: **OK**. TYPE-1…4 match themselves with their checks passed (TYPE-5
  needs its transcription, present for the copy in `Annual-reports`); with itself left out,
  **no model document is accepted by another format's rules**.
- TYPE-6/7/8 are refused as new model documents. With their own (pending) entries left out, they
  still match nothing: Infosys and BHP are nearest to each other (0.71); Magna is tried with the
  Reliance, Bharat Forge and Chubb rules and fails all of them (≤ 1 note, 0 statements).
- An unseen report of a known format (the Reliance PDF with other bytes) matches TYPE-2 by
  fingerprint (similarity > 0.95) with its checks passed.
- The prompt baseline stayed byte-identical; the only change is the new "model" line in the
  `/api/index` answer of each sample.

### 16.3 Differences from the plan and things found on the way

- **Pending models** (a model document without rules) were added to the registry, so the matcher
  can say "this *is* TYPE-8, whose rules aren't written yet" and the gap report compares it with
  the models that do have rules (and says so if one of them would already pass).
- A shortlist bug found while testing: pending models took shortlist places, so a model with rules
  ranked 4th was never tried. Trials now go to the best 3 models *with rules*.
- `MATCHER_VERSION` (not in the plan) invalidates remembered verdicts without rebuilding packages.
- Acceptance checks name what they need in words ("doesn't mention Form 10-K"), not regexes.
- **Magna (TYPE-6) is its Annual Information Form**: it contains no financial statements and no
  notes (a 40-F files them as separate exhibits). No rules can make it analyzable; the financial
  statements exhibit is the document to add for Magna.
  **Done 2026-09-28:** TYPE-6 replaced by Exhibit 99.3 (consolidated financial statements, 46 pages) and
  re-registered as pending (registry version 9). Its trial with `in-indas-2020s` finds 26 notes, 4 statements
  and 264 readable figures; only the wording check (Ind AS or the rupee symbol) fails.
  **Rules written 2026-09-28:** profile `ca-40f-usgaap` (registry version 10), see `.claude\CLAUDE.md` §7 item 16.
- **Phase 4 done 2026-09-28:** Infosys (`in-20f-ifrs`) and BHP (`au-20f-ifrs`), registry version 12,
  with the switches `notes_run_on`, `decimal_notes`, `untitled_statement_pages`,
  `numbered_statement_titles`, `extra_notes_headers` (`.claude\CLAUDE.md` §7 item 17).

### 16.4 Phase 4 — next

| Model | What its rules need (from the gap reports) |
|---|---|
| TYPE-6 Magna 40-F | Replace (or add) the financial-statements exhibit; this PDF has none |
| TYPE-7 Infosys 20-F | Notes without running headers (the notes section must be found from "Notes to…" / Item 18 and note headings); statement titles "… as of March 31," |
| TYPE-8 BHP 20-F | Section-numbered statements ("1.3 Consolidated Balance Sheet as at 30 June 2026"); notes cited as "Financial Statements note 24"; 30 June year-end |

Each goes through the routine (`MODEL-DOCS/README.md`): a profile (derived where possible, new rules
behind new switches), `register --profile`, a golden test, a baseline sample, `verify`.
