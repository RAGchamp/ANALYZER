# MODEL-DOCS — the report formats the analyzer can read

Every annual report is first **matched** to one of the model documents in this folder
(`INFO\MODEL-DOCS-FUNCTIONALITY-PLAN.md`). A report that matches none is a
**new model document**: the app refuses it (it can't be analyzed at all) and writes a
**gap report** explaining what differs. This file is the routine for adding a format.

## What is here

| File | Model | Extraction rules (profile) |
|---|---|---|
| `TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf` | Indian annual report, Ind AS | `in-indas-2020s` |
| `TYPE-2-INDIA-Ann-rpt-1977-Reliance.pdf` | Indian annual report, Companies Act 1956 (old Schedule VI) | `in-schedule-vi-1970s` |
| `TYPE-3-USA-10-K-2025-Chubb.pdf` | US Form 10-K, EDGAR typesetting | `us-10k-edgar` |
| `TYPE-4-USA-Ann-rpt-1994-Berkshire.pdf` | US Form 10-K, 1990s EDGAR text in a typewriter font | `us-10k-typewriter` |
| `TYPE-5-USA-Ann-rpt-1968-Berkshire.pdf` | Scanned report (transcribed by Claude) | `scanned-ocr` |
| `TYPE-6-CANADA-40-F-2025-Magna.pdf` | Canadian Form 40-F, financial statements exhibit (US GAAP, U.S. dollars) | `ca-40f-usgaap` |
| `TYPE-7-INDIA-20-F-2025-Infosys.pdf` | Indian company's Form 20-F (IFRS), notes numbered 2.1, 2.2 … | `in-20f-ifrs` |
| `TYPE-8-AUSTRALIA-20-F-2025-BHP.pdf` | Australian company's Form 20-F (IFRS, US$M), numbered statement titles | `au-20f-ifrs` |
| `_candidates\<name>\` | Reports proposed from the app ("Propose as a model document"), each with its `GAP-REPORT.md` | — |

- **Each document is its own model.** Its id is the file name:
  `TYPE-<n>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf`.
- The registry of models is `WEB-APP\ingest\models\registry.json` (checksum, profile,
  description, fingerprint). It ships with the app; **these PDFs don't** (and are not in git).
- The profiles (extraction rules) are in `WEB-APP\ingest\profiles\__init__.py`.

## Adding a format (the routine)

Step-by-step guide for users, with the exact Claude Code prompt and a worked example:
`INFO/HOW-TO-TRAIN-NEW-MODEL-FORMAT.html`.

1. **Model document.** Copy the PDF here as `TYPE-<next number>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf`.
   If the app flagged it, its gap report is in `_candidates\<name>\GAP-REPORT.md` (or open
   *View gap report* on the Ingest screen; *Describe with Claude* adds a layout description).
2. **Profile.** In a Claude Code session, read the gap report and write the profile in
   `WEB-APP\ingest\profiles\__init__.py`:
   - **Settings only**, when an existing indexer fits (other running-header wording, other
     acceptance checks): `derive(<existing profile>, id=…, …)`.
   - **New rules**, when the layout needs new logic: add a new *switch* to `Switches`, write the
     rule so it only runs when that switch is on, and turn it on in the new profile only.
     Never change another format's code path.
3. **Register it.**
   `cd WEB-APP` then `python -m ingest.models register "..\MODEL-DOCS\TYPE-9-….pdf" --profile <id> --description "…"`.
   (A model whose rules aren't written yet: `--pending` instead of `--profile`.)
4. **Golden test.** Add a test like `tests\test_plain_text_filing.py`: notes (count, a known
   note's pages), statements (pages), a figure with its period. Add a copy to
   `Annual-reports\` and to `tests\prompt_baseline.py` to freeze its prompts.
   **Narrative pages** (everything outside notes and statements,
   `INFO\ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md`): turn on `narrative_sections` (the
   section sources that fit - the gap report lists the clues it found: `bookmarks`, `contents`,
   `running_headers`, `form_items`, or `headings` as a last resort) and, for designed
   pages, `narrative_designed_pages` in the profile; check the `Narrative:` lines of the quality report
   (sections found, coverage check: every page OK), and add two or three hand-checked pages to
   `tests\fixtures\narrative_gold\gold.json`.
5. **Verify.**
   - `python -m ingest.models verify`: every model matches itself, passes its checks, and is not
     accepted by any other format's rules.
   - `python -m pytest`: every model's prompts are unchanged (`tests\test_prompt_identity.py`).
6. **Ship.** Rebuild the exe.

## Fixing a page reported on the Model testing screen

`/model-testing` lets the user test any page of these documents and report a wrong extraction as
`WEB-APP\Model-Feedback\<pdf>-<timestamp>.json` (INFO\MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md). A fix
follows step 2 above (profile, switch, version bump), adds a test to
`tests\test_feedback_regressions.py`, runs step 5, and is recorded with
`python -m ingest.feedback fixed <file> --summary "…"` — routine in `.claude\CLAUDE.md` §7a.

Other commands: `python -m ingest.models list`, `… fingerprint <pdf>`, `… match <pdf>`,
`… refresh` (re-fingerprint every model after the fingerprint code changes).
