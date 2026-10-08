# Model Feedback — Plan (test any model document page by page, report wrong extractions, track the fixes)

**Date:** 2026-09-28
**App:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer)
**Builds on:** `INFO\MODEL-DOCS-FUNCTIONALITY-PLAN.md` (model documents, profiles, registry) and
`INFO\SPLIT-FUNCTIONALITY-PLAN.md` (ingester / analyzer / report packages)
**New folders (already created):** `WEB-APP\Model-Feedback\`, `WEB-APP\Model-Testing\`
**Status:** **All phases implemented 2026-09-28.** Decisions in §13; what was built in §15.

---

## 0. Goal and summary

The app reads eight kinds of PDF (the model documents in `MODEL-DOCS\`). Today the only way to see
whether it extracted a page correctly is to run an analysis, or to read the package with Python.
There is no way for the user to say "this page came out wrong" and no record of what was reported
and what was fixed.

The goal:

1. A new web page, **Model testing** (`/model-testing`, template `model_testing.html`), with the
   same top nav bar as the home page.
2. The user picks **one model document** from `MODEL-DOCS\` (a dropdown of every PDF there),
   enters a **PDF page number** (validated against that PDF's page count), and clicks
   **Test extraction**.
3. The page shows an **exact image of the PDF page** and, below it, **the data the app extracted
   from that page** — taken from the same report package the Analyzer uses, so what the user sees is
   exactly what Claude would be sent.
4. A **Need correction** check box opens a comment box (**max 400 characters**) and a
   **Submit feedback** button.
5. Submitting writes `Model-Feedback\<pdf-file-name>-<timestamp>.json` (PDF file name, page, the
   extracted data, the user's comment) and adds a line to **`Model-Feedback\feedback-status.json`**
   with status **Reported**.
6. The user later gives that file name to Claude Code. Claude Code fixes the extraction rules
   (routine in §8) and records **Fixed by Claude code** with a timestamp in `feedback-status.json`.
   The status file is the **audit trail** of every issue reported and fixed.

The fixing step stays **manual** (the user starts a Claude Code session); the app never calls
Claude to fix itself.

---

## 1. Where things stand today (verified 2026-09-28)

| What | Where | Use for this plan |
|---|---|---|
| 8 model PDFs | `MODEL-DOCS\TYPE-1 … TYPE-8-*.pdf` (+ `README.md`, `_candidates\`) | The dropdown lists `*.pdf` directly in this folder (not `_candidates\`) |
| Folder setting | `config.MODEL_DOCS_DIR` (env `MODEL_DOCS_DIR`) | Reused |
| Registry | `ingest\models\registry.json`: TYPE-1…5 have a profile; **TYPE-6/7/8 are pending** (no rules) | Tells us which extraction to expect (§4.3) |
| Report packages | `cache\packages\<stem>-<sha16>.rptpkg.db`, keyed by the PDF's SHA-256 | TYPE-1…5 are byte-identical to the samples in `Annual-reports\`, so their packages already exist |
| Page text | `Package.page(n)` → `{"printed", "text"}` (the cleaned page text, running header/footer removed) | Core of the extracted data |
| Units on a page | `units` table (`start_page`, `end_page`, `text_pages`), `pages.layout` | "What the app thinks is on this page" |
| Statement figures | `lines` (with `pdf_page`) + `values` + `edges` | Figures and note links for statement pages |
| Page image | `/page-image?report=&page=` in `ingest\routes.py` (PyMuPDF, 110 dpi) — **only for `Annual-reports\`** (`resolve_report`) | Same code, generalised to model documents |
| Jobs | `webcommon.start_job` / `/api/jobs/<id>` | First ingestion of a model document can take 15 s – 1.5 min |
| Nav bar | `templates\index.html` `<nav class="navbar">` | Copied to the new page; a "Model testing" link added on every page |

Boundary rule: this feature **opens PDFs**, so it belongs to the **ingester side** (`ingest\`), never
to `analyzer\` (`tests\test_boundary.py` must keep passing).

---

## 2. What the user sees

```
┌ navbar: ← Analyze screen | Annual Report Foot Notes Analyzer | Ingest screen ↗ | Model testing  ┐
│                                                                                                    │
│  Model testing                                                                                     │
│  Model document  [ TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf        ▼ ]   508 pages · in-indas-2020s
│  Page#           [ 405 ]   PDF page number (1–508)                                                 │
│                  ⚠ Page 525 is out of range: this PDF has 508 pages (1–508).   ← only when invalid │
│                  [ Test extraction ]                                                               │
│                                                                                                    │
│  ┌ PDF page 405 (printed 402) ───────────────────────────────────────┐                             │
│  │                 [ exact image of the page ]                        │                             │
│  └────────────────────────────────────────────────────────────────────┘                             │
│  ┌ Extracted data — page 405 ─────────────────────────────────────────┐                             │
│  │ Section: Consolidated · Page type: notes page                       │                             │
│  │ On this page: Note 21 Income taxes (PDF 405–407)                    │                             │
│  │ ── Page text ──  (monospace, rows as  label | FY26 | FY25)          │                             │
│  │ ── Statement lines ── (statement pages only)                        │                             │
│  └────────────────────────────────────────────────────────────────────┘                             │
│  ☐ Need correction                                                                                 │
│    ┌ What is wrong? (max 400 characters)                         0/400 ┐                             │
│    └────────────────────────────────────────────────────────────────────┘                           │
│    [ Submit feedback ]   ✓ Saved as TYPE-1-…-20260928-101533.json (status: Reported)               │
│                                                                                                    │
│  ▸ Feedback so far (from feedback-status.json): file · page · reported · status · fixed           │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Top nav bar

Same markup and CSS class as the home page (`navbar`, title, `ghost nav-link`). Links: **Analyze**
(`/`), **Ingest screen**, **Model testing** (current page highlighted). The History toggle only makes
sense on the Analyze screen, so on this page the left slot is a "← Analyze screen" link (as the Ingest
screen does). The home page and the Ingest screen get a **Model testing** link. *(Confirmed, Q2.)*

### 2.2 Step by step

1. **Model document dropdown.** Filled from `GET /api/model-testing/docs`: every `*.pdf` directly in
   `MODEL-DOCS\`, sorted by name (TYPE-1, TYPE-2, …; natural sort so TYPE-10 comes after TYPE-9).
   Each option carries its page count, its registry model id and profile (or "pending — no extraction
   rules yet", or "not in the registry"). A PDF not in the registry is still listed.
   If `MODEL-DOCS\` is missing (e.g. the exe on another PC) the page says so and shows its path.
2. **Page#.** A number box. Validation happens **in the browser as the user types** (using the page
   count from step 1) and **again on the server** (never trust the browser):
   - empty / not a whole number → "Enter a PDF page number (1–508)."
   - `< 1` or `> page count` → "**Page 525 is out of range: this PDF has 508 pages (1–508).**"
   - valid → the message clears and **Test extraction** is enabled. Until then it is disabled.
   - Changing the document re-validates the page number already entered.
3. **Test extraction.** Starts a job (§4.1), shows a spinner ("Reading the model document…",
   "Ingesting (first time only, up to ~1.5 min)…"), then shows the page image and the extracted data.
   The check box and feedback form are reset for each new test.
4. **Page image.** `GET /api/model-testing/page-image?doc=&page=` → PNG rendered with PyMuPDF at
   **150 dpi** (sharper than the Ingest screen's 110, still < 1 MB), shown at full card width with a
   "Open full size" link. The page is shown **as it is in the PDF** (sideways statement pages stay
   sideways) — it is a replica, not the app's rotated view.
5. **Extracted data box.** Read-only, monospace, scrollable; contents in §4.2. A "Copy" button.
6. **Need correction** check box → reveals the comment box and **Submit feedback**.
   - `<textarea maxlength="400">` with a live counter `123/400`; the server also enforces 400.
   - Submit is disabled while the comment is empty (only spaces counts as empty).
   - After a successful submit: a green line "Saved as `<file>` (status: Reported)", the text area is
     locked and the button is replaced by "Report another problem on this page" (a second report on the
     same page is allowed, it gets its own file).
7. **Feedback so far** (collapsible, closed by default): the rows of `feedback-status.json`, newest
   first, with the status in colour (Reported = amber, Fixed by Claude code = green). Clicking a row
   opens the feedback JSON in a new tab (`/api/model-testing/feedback/<file>`).

---

## 3. Files written

### 3.1 One feedback file per report — `Model-Feedback\<pdf stem>-<YYYYMMDD-HHMMSS>.json`

Name: the PDF file name without `.pdf`, a dash, the local time of submission. Example:
`TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533.json`. If that name already exists (two
submits in one second) → `…-101533-2.json`.

```json
{
  "schema_version": 1,
  "feedback_file": "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533.json",
  "pdf_file": "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf",
  "page": 405,
  "printed_page": "402",
  "reported_at": "2026-09-28T10:15:33+05:30",
  "user_feedback": "Note 21 heading is missing; the deferred tax table's FY25 column is merged into the label.",
  "extracted_data": {
    "source": "package",
    "section": "consolidated",
    "page_type": "notes",
    "units_on_page": [{"id": "C21", "kind": "note", "number": 21, "title": "Income taxes",
                       "pages": "405-407", "starts_here": true}],
    "page_text": "21. INCOME TAXES\n(a) Income tax expense | FY26 | FY25\n…",
    "statement_lines": []
  },
  "context": {
    "pdf_sha256": "fbe10a171fc5ab27…",
    "model_id": "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge",
    "profile": "in-indas-2020s", "profile_version": 1,
    "index_version": 25, "ingester_version": "…", "registry_version": 8,
    "package": "cache/packages/Bharat-Forge-IR-2026-conv-single-page-fbe10a171fc5ab27.rptpkg.db",
    "page_image": "Model-Testing/TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533-p405.png"
  }
}
```

The four fields the user asked for are `pdf_file`, `page`, `extracted_data` and `user_feedback`.
`context` is added so Claude Code can **reproduce** the extraction later and **tell whether a fix
changed it** (which rules and versions produced it). The feedback file is **never edited** after it is
written — it is the evidence; status changes go only to `feedback-status.json`.

### 3.2 The audit trail — `Model-Feedback\feedback-status.json`

Created (empty) the first time it is needed. One entry per feedback file; each entry keeps its full
**history**, so nothing is ever overwritten:

```json
{
  "schema_version": 1,
  "entries": [
    {
      "feedback_file": "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533.json",
      "pdf_file": "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf",
      "page": 405,
      "date_reported": "2026-09-28T10:15:33+05:30",
      "status": "Fixed by Claude code",
      "date_fixed": "2026-09-29T16:02:10+05:30",
      "history": [
        {"status": "Reported", "at": "2026-09-28T10:15:33+05:30"},
        {"status": "Fixed by Claude code", "at": "2026-09-29T16:02:10+05:30",
         "summary": "in-indas-2020s v2: headings split across two spans are joined",
         "changed": ["ingest/notes_index.py", "ingest/profiles/__init__.py"],
         "test": "tests/test_feedback_regressions.py::test_type1_p405"}
      ]
    }
  ]
}
```

- `status` / `date_fixed` are the **current** state (easy to read); `history` is the **trail**.
- Statuses: **Reported** → **Fixed by Claude code** only (Q3); the fix is recorded on the same entry
  with a history record appended (Q4). More statuses can be added later without changing the format.
- Writes are **atomic** (write `feedback-status.json.tmp`, then `os.replace`) under a lock, so a
  crash or two browser tabs can't corrupt it. If the file is unreadable, the app refuses to write and
  says so rather than starting a new empty file (that would lose the trail).

### 3.3 `Model-Testing\` — page snapshots

At submit time the page image that the user looked at is saved as
`Model-Testing\<feedback file stem>-p<page>.png`, and referenced from the feedback JSON
(`context.page_image`). Claude Code can then **see** the page (the Read tool shows images) without
opening the PDF itself. Test runs *without* feedback write nothing. *(Confirmed, Q1.)*

---

## 4. Extraction for one page

### 4.1 Where the data comes from

`ingest\model_testing.py: extract_page(pdf_path, page)`:

1. Resolve the model document (only a `*.pdf` listed directly in `MODEL-DOCS\`; any other name is
   refused — no path traversal).
2. `pipeline.open_report(pdf_path)` → the report package, **building it if needed**, through the
   normal model matching. This is the key point: the test shows **exactly what the app itself
   extracts**, from the same package the Analyzer reads, not a separate test-only extraction.
   TYPE-1…5 are byte-identical to the sample reports, so their packages already exist (same SHA-256)
   and a test takes well under a second.
3. Read the page from the package (§4.2).

It runs as a job (`start_job("model-test", …)`) because a first ingestion can take a while; the
browser polls `/api/jobs/<id>` as the other screens do.

### 4.2 What "extracted data for the page" contains

| Part | From | Shown when |
|---|---|---|
| **Header**: PDF page, printed page, section (Consolidated / Standalone / company), page type (notes page, statement page, other page), text source (pdf / ocr) | `pages` row + `pages.layout` + `sections` | always |
| **On this page**: every unit whose pages include this one — notes, schedules, statements — with id, number, title, page range, and whether its heading **starts** on this page | `units` (`text_pages`, `start_page`, `end_page`) | always ("nothing indexed on this page" otherwise — which is itself useful feedback) |
| **Page text**: the cleaned text of the page, rows rebuilt as `label \| FY26 \| FY25` | `Package.page(n)["text"]` | always |
| **Statement lines**: for a statement page, each line on this page: id, label, total/data, note ref, figures with their period (`Current tax \| 5,606.07 (FY2026) \| 5,848.54 (FY2025)`), linked notes | `lines` (`pdf_page = n`), `values`, `edges` | statement pages |
| **Scanned pages**: the transcription checks | `Package.ocr_page(n)` | TYPE-5 only |

The same structure is shown in the box (as readable text) and stored in `extracted_data` (as JSON).

### 4.3 Documents the app can't extract (yet)

| Case | Today | What the page does |
|---|---|---|
| **Pending model** (TYPE-6/7/8: no rules) | `NewModelDocument` — blocked | Shows the page image and a notice "**No extraction rules yet for this model** (pending)". The box shows the page's **raw PDF text** (PyMuPDF `get_text()`), clearly labelled *raw text — not extracted by the app's rules*, with `extracted_data.source = "raw-pdf-text"`. Feedback is **allowed**: it is exactly the input Phase 4 of the model-docs plan (writing TYPE-6/7/8 rules) needs. |
| **Not in the registry** | New model document | Same as pending, notice "not registered — see MODEL-DOCS\README.md". |
| **Scanned, not yet transcribed** (TYPE-5 without its OCR) | `NeedsTranscription` | Image + "This page has not been transcribed; transcribe it on the Ingest screen" (transcription costs Claude usage, so model testing never starts it by itself). Page text from an existing transcription is used if there is one. |
| **Matched to a different model** than its own name | Possible if rules drift | Shown as a warning in the header ("this PDF matched TYPE-3, not itself") — that is a bug worth reporting. |

---

## 5. Routes (new blueprint `ingest\model_test_routes.py`)

| Route | Does |
|---|---|
| `GET /model-testing` (also `/Model-testing.html`) | The page (`templates\model_testing.html`, `static\model_testing.js`) |
| `GET /api/model-testing/docs` | `[{name, pages, model_id, profile, pending, registered}]`, `model_docs_dir`, error if missing |
| `POST /api/model-testing/extract` `{doc, page}` | Validates, starts the job; result = `{page, printed, image_url, extracted, extracted_text, notice}` |
| `GET /api/model-testing/page-image?doc=&page=` | PNG at 150 dpi (400 "out of range" otherwise) |
| `POST /api/model-testing/feedback` `{doc, page, comment}` | Validates (page in range, 1–400 chars after trimming), **re-extracts server-side** (the stored data is what the server produced, not what the browser sent back), writes the feedback file, the PNG and the status entry; returns `{feedback_file, status}` |
| `GET /api/model-testing/feedback` | `feedback-status.json` entries |
| `GET /api/model-testing/feedback/<file>` | One feedback JSON (name must match an entry — no path traversal) |

All errors use `UserError` → `{"error": …}` with status 400, as elsewhere. The app stays on
`127.0.0.1` only.

---

## 6. Code and files

| File | Change |
|---|---|
| `config.py` | `MODEL_FEEDBACK_DIR` (env `MODEL_FEEDBACK_DIR`, default `BASE_DIR\Model-Feedback`), `MODEL_TESTING_DIR` (default `BASE_DIR\Model-Testing`), `FEEDBACK_MAX_CHARS = 400`, `MODEL_TEST_DPI = 150`; both folders created at start-up like the others |
| `ingest\model_testing.py` **(new)** | `list_model_docs()`, `resolve_model_doc(name)`, `page_count(path)`, `validate_page(path, page)`, `extract_page(path, page)` → dict, `format_extracted(dict)` → text for the box |
| `ingest\feedback.py` **(new)** | The feedback store: `submit(...)`, `load_status()`, `mark_fixed(file, summary, changed, test)`, atomic write + lock, file naming. Also a CLI (§7) |
| `ingest\model_test_routes.py` **(new)** | The routes in §5 (a blueprint, registered in `app.py`) |
| `templates\model_testing.html`, `static\model_testing.js` **(new)** | The page; reuses `style.css` (cards, navbar, spinner, error box) |
| `templates\index.html`, `templates\ingest.html` | "Model testing" nav link |
| `static\style.css` | A few rules: page image frame, counter, status colours |
| `tests\test_model_testing.py` **(new)** | §10 |
| `README.md`, `INFO\HOW-…WORKS.html`, `.claude\CLAUDE.md`, `MODEL-DOCS\README.md` | New screen, new folders, the fix routine (§8) |
| exe build | Exclude `Model-Feedback`, `Model-Testing` from the copied folder (the exe has no `MODEL-DOCS\` anyway, so the page will say "folder not found" there) |

No change to the ingestion rules, packages, prompts or `INDEX_VERSION`: the prompt baseline stays
byte-identical (`tests\test_prompt_identity.py`).

---

## 7. Command line for the audit trail

`python -m ingest.feedback` (run from `WEB-APP\`), used mainly by Claude Code:

```
python -m ingest.feedback list [--open]                 # table of entries; --open = not fixed yet
python -m ingest.feedback show <feedback file>          # the report + re-extract that page NOW and diff it
                                                        #   against the extracted_data that was reported
python -m ingest.feedback fixed <feedback file> --summary "…" [--changed a.py b.py] [--test tests/…::test_x]
```

`fixed` refuses when the file isn't in `feedback-status.json` or is already fixed, and writes the
status change atomically. Using a command (instead of Claude Code editing the JSON by hand) keeps the
file format and timestamps consistent.

---

## 8. The fix routine (manual, in a Claude Code session)

The user says, for example: *"Fix the model feedback `Model-Feedback\TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533.json`."*
Claude Code then:

1. **Reads** the feedback JSON and the page snapshot PNG in `Model-Testing\`.
2. **Reproduces**: `python -m ingest.feedback show <file>` — confirms the problem still exists with
   the current code (if the extraction already changed, report that instead of fixing blindly).
3. **Fixes the rules** following `MODEL-DOCS\README.md`: the model's profile, and a **switch** for a
   rule only that format needs — never an `if` in another format's path. Bumps the right version
   (profile `version`, or `INDEX_VERSION` for shared code) so the packages rebuild.
4. **Adds a regression test** in `tests\test_feedback_regressions.py`, one test per feedback file,
   named after it, asserting the corrected output for that page.
5. **Runs** `python -m pytest -q` and `python -m ingest.models verify`. Prompt baseline changes are
   reviewed and re-frozen **only** for the documents the fix was meant to change; any other model
   changing means the fix leaked into another format.
6. **Marks it fixed**: `python -m ingest.feedback fixed <file> --summary … --changed … --test …`.
7. Tells the user what changed; the user can re-run **Test extraction** on the same page to see it.

This routine is added to `.claude\CLAUDE.md` so any future session follows it.

---

## 9. Validation and safety details

- Page number: whole number only (`"12a"`, `"1.5"`, `"-3"` rejected), 1 … page count; checked in the
  browser and on the server for every route that takes one.
- Document name: must be exactly one of the listed PDFs; anything else (`..\x.pdf`, a `_candidates`
  file) → "Model document not found".
- Comment: trimmed; 1–400 characters (Unicode characters, not bytes); stored as typed (JSON-escaped);
  shown back HTML-escaped.
- JSON files written as UTF-8 with `ensure_ascii=False`, indent 2.
- Timestamps: local time with UTC offset (ISO 8601); file names use `YYYYMMDD-HHMMSS` like
  `Analysis-history\`.

---

## 10. Testing plan

`tests\test_model_testing.py` (runs on the real model PDFs, skips missing ones; no Claude calls):

- **Listing:** 8 documents, natural order, TYPE-6/7/8 flagged pending, page counts (TYPE-1 = 508).
- **Validation:** page 0, 509, `"abc"`, `"1.5"` → 400 with the out-of-range / number message; page
  508 → ok; unknown or traversal document names refused.
- **Extraction (TYPE-1):** page 405 → consolidated notes page, `C21` starts here, printed page 402;
  page 342 → statement page, statement lines include `Current tax` with 5,606.07 (FY2026);
  page 344 (sideways) → image rendered sideways, text extracted upright.
- **Extraction (TYPE-3 Chubb):** a statement page 107–110 and a notes page.
- **Pending (TYPE-6):** raw text, `source = "raw-pdf-text"`, notice present, feedback still accepted.
- **Feedback store** (temp folder): file name format, same-second collision → `-2`, all fields
  present, 401 characters rejected, empty rejected; status file created, entry appended;
  `mark_fixed` adds history and `date_fixed`, second `mark_fixed` refused; corrupt status file →
  error, nothing overwritten; the PNG snapshot written.
- **CLI:** `list`, `show` (diff empty when nothing changed), `fixed`.
- **Boundary:** `tests\test_boundary.py` unchanged — nothing in `analyzer\` imports the new modules.
- **Prompt identity:** unchanged.
- **Visual:** headless Edge screenshots of `/model-testing` (empty, out-of-range message, a result with
  the correction form open); `node --check static/model_testing.js`.

---

## 11. Phases

| Phase | Content | Done when |
|---|---|---|
| 1 | `config`, `ingest\model_testing.py` (list, validate, extract page), tests | Extraction tests pass |
| 2 | Routes + page (dropdown, Page# validation, Test extraction, image, extracted box), nav links | Screenshots OK |
| 3 | `ingest\feedback.py`: Need correction, 400-char form, Submit, feedback JSON, PNG, `feedback-status.json`; "Feedback so far" list | Store tests pass; a real submit writes both files |
| 4 | CLI `list / show / fixed`, fix routine in CLAUDE.md and `MODEL-DOCS\README.md`, `tests\test_feedback_regressions.py` skeleton | A dry-run fix of a sample feedback end to end |
| 5 | README, HOW doc, exe build exclusions | Docs current, full test suite passes |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| The first test of a model document ingests it (slow) | Runs as a job with progress text; TYPE-1…5 already have packages |
| Extracted data in the feedback no longer matches after code changes | `context` stores the versions; `feedback show` re-extracts and diffs |
| Status file corrupted or lost | Atomic writes, refuse to overwrite an unreadable file; feedback files are self-contained, so the status file could be rebuilt from them (`feedback list --rebuild`, only if ever needed) |
| A fix for one model breaks another | The fix routine's `verify` + prompt-identity step (§8.5) |
| Large page images | 150 dpi PNG ≈ 0.3–1 MB; snapshots only on submit |

---

## 13. Decisions (confirmed 2026-09-28)

| # | Question | Decision |
|---|---|---|
| Q1 | What `WEB-APP\Model-Testing\` holds | **Page-image snapshots** saved with each submitted feedback (§3.3). Test runs without feedback write nothing. The template stays in `templates\` |
| Q2 | Nav bar | **Same style and title as home, no History toggle** ("← Analyze screen" in its place); a Model testing link on every page |
| Q3 | Statuses | **Reported** and **Fixed by Claude code** only; more can be added later |
| Q4 | How "Fixed by Claude code" is recorded | **Same entry**: status and `date_fixed` updated, a history record appended — one row per issue |
| Q5 | Pending models (TYPE-6/7/8) | **Feedback allowed**; page shows raw PDF text, clearly labelled |
| Q6 | Git | **Both folders in git** (`Model-Feedback\` JSON and `Model-Testing\` PNGs) |
| Q7 | Extra JSON fields | **Yes**: `printed_page`, `reported_at` and a `context` block (SHA-256, model, profile / index / registry versions, snapshot path) |

---

## 14. Out of scope

- Claude fixing issues automatically from the app (manual by decision; the app never calls Claude here).
- Editing or correcting the extracted data in the browser (the user describes the problem in words).
- Testing reports in `Annual-reports\` that are not model documents (can be added later: same code,
  other folder).
- Multi-page or region selection on the page image.

---

## 15. Implementation notes (2026-09-28)

### 15.1 What was built

| Piece | Where |
|---|---|
| Settings | `config.py`: `MODEL_FEEDBACK_DIR`, `MODEL_TESTING_DIR` (env-overridable), `FEEDBACK_MAX_CHARS = 400`, `MODEL_TEST_DPI = 150` |
| Documents, Page#, one page's extraction | `ingest/model_testing.py` |
| Feedback store, audit trail, CLI | `ingest/feedback.py` (`python -m ingest.feedback list / show / fixed`) |
| Routes | `ingest/model_test_routes.py` (blueprint, registered in `app.py`) |
| Screen | `templates/model_testing.html`, `static/model_testing.js`, styles at the end of `static/style.css`; "Model testing" link on the Analyze and Ingest screens |
| Tests | `tests/test_model_testing.py` (35 tests); `tests/test_feedback_regressions.py` (skeleton for the fix routine) |
| Seed files | `Model-Feedback/feedback-status.json` (empty trail), `Model-Testing/.gitkeep` |
| Docs | README, HOW doc (§15 card + file map), `.claude/CLAUDE.md` (§7a fix routine), `MODEL-DOCS/README.md` |

### 15.2 Verified

- Extraction on every kind of model: TYPE-1 p.405 (C21 starts here, printed 402), p.342 (C-PL lines with
  Current tax 5,606.07 FY2026), p.344 (sideways C-EQ; image stays sideways); TYPE-2 p.15 (SCH-A, SCH-B);
  TYPE-3 p.108 (statement lines); TYPE-5 p.9 (OCR text, BH-N1…N3); TYPE-6 p.3 (raw text + pending notice).
  With packages already built each test takes 0.1–1 s.
- Headless Edge screenshots: the out-of-range message ("Page 525 is out of range: this PDF has 508 pages
  (1–508)", Test extraction disabled), a TYPE-1 result with the form open, a pending TYPE-6 result.
- A real submit through the running server wrote the feedback JSON, the PNG and the status entry.
- No change to packages, prompts or `INDEX_VERSION`; the boundary test passes (all new code is in `ingest/`).

### 15.3 Differences from the plan and things found on the way

- The TYPE-5 (scanned 1968) package predates model matching, so it records no model: the "matched another
  model" warning only fires when a *different* model is recorded.
- Opening a model document shares the sample report's package (same SHA-256): `ensure_package` copies it
  under the model document's name, so the first test of TYPE-1…5 does not re-ingest.
- The registry lookup for the dropdown uses the file name (no hashing of 8 PDFs on every page load); the
  extraction itself goes through the normal SHA-based matching.
- The feedback JSON also stores `notices` (e.g. "pending — raw text"), so Claude Code sees what the user saw.

