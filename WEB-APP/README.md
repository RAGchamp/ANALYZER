# Annual Report Foot Notes Analyzer

A local Flask app for in-depth analysis of the **notes to the financial
statements** in an annual report PDF. It finds the relevant note(s),
extracts exactly those pages, and asks **Claude Code** (`claude -p`) for
the analysis. The Claude interaction reuses the pattern from
`C:\Daya\RAGchamp\basic-chatbot`.

Plan: `..\INFO\ANN-RPT-NOTES-ANALYZER-PLAN.md`

## Run

```powershell
cd C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

Requirements: Python 3.12, and the Claude Code CLI logged in
(`C:\Users\dayam\.local\bin\claude.exe`, or set `CLAUDE_EXE`).

## How to use

1. **Select report**: pick a PDF from `ANN-RPT-ANALYZER\Annual-reports\`.
   To add a report, copy the PDF into that folder and click ↻. The first
   time a report is used, its notes are indexed and its **primary financial
   statements are loaded** (about 12s). Everything is cached in `cache\`.
   The statements are the Statement of Profit and Loss, Balance Sheet, Cash
   Flow Statement and Statement of Changes in Equity, for both consolidated
   and standalone. Sideways pages, such as the consolidated Statement of
   Changes in Equity on PDF pp.344–345, are rotated 90° clockwise before
   extraction. Step 1 lists them, and each one can be expanded to see the
   extracted text.
2. **Ask** a question, e.g. *Analyze the company's Tax related liabilities
   and comment on it.* The app uses the **consolidated** notes unless the
   question says standalone (or "not consolidated", "parent company only").
   It uses both if the question mentions both.
3. **Confirm the notes**: Claude picks the notes from their titles. You can
   remove or add notes, or type PDF pages to analyze instead. Then click **Analyze**.
4. **Read the analysis** (it takes 1–3 minutes). The notes are always sent
   together with the primary statements for the same section, and the
   answer includes an **Impact on the financial statements** section. That
   section ties the note figures to statement line items and explains the
   effect on the P&L, Balance Sheet, Cash Flow and Changes in Equity. Expand *Source extract* to see
   exactly what was sent. Copy it or download it as .md or .html.
5. **Follow-ups**: ask more questions on the same notes. Mention "Note 41"
   to add that note to the thread.
6. **History** (left panel): reopen any past analysis and continue its thread.

## Old annual reports (Companies Act 1956)

Old reports, e.g. `Reliance-1976-1977.pdf`, have no "Notes to … Financial
Statements". Instead they have a Balance Sheet and Profit and Loss Account,
lettered **Schedules** ('A', 'B', …) and a schedule of numbered **notes**.
The app detects this when it indexes the report. Step 1 then shows
*Format: Old (schedules, Companies Act 1956)*, which you can change with the
**Report format** dropdown if it was detected wrongly.

- **What you can pick:** schedules (`Schedule 'E' — Fixed Assets`), numbered notes
  (`Note 25 — Contingent Liabilities`; untitled notes show their first words in
  italics) and report sections (Directors' Report, Auditors' Report, Financial
  Summary). Accounts are standalone only.
- **Follow-ups:** mention "Schedule E", "note 25" or "the Directors' Report" to
  add it to the thread.
- **What the prompt tells Claude:**
  - which statements exist (and which didn't at the time)
  - to work in Rs. lakhs/crores
  - to annualise periods that aren't 12 months
  - to flag totals that don't add up as possible transcription errors
  - to derive a funds-flow when cash matters
- **Sideways pages:** pages printed sideways, such as the Fixed Assets schedule,
  are turned before reading.

## Scanned reports (OCR)

Image-only PDFs, such as `BRK-1968.pdf` (Berkshire Hathaway's 1968 Form 10-K, a
microfilm scan), are **transcribed by Claude once**, page by page. Then they are
analyzed like any other report. See `..\INFO\OCR-CAPABILITY-PLAN.md`.

1. **Select the report.** Step 1 shows *"This report is scanned…"* with an estimate
   (BRK-1968: 39 pages, about 6 minutes, about $5 at API rates, on your Claude
   Code plan). Click **Transcribe**, or type a page range and click **Transcribe these
   pages**.
   - Progress shows page by page, and **Cancel** stops the job; pages already done
     are kept.
   - Everything is cached in `cache\<report>.ocr\`: the page images, Markdown and
     checks.
2. **Checks on the transcription:**
   - `[?]` marks characters Claude couldn't read.
   - Totals are checked against their figures automatically.
   - If Tesseract is installed (`C:\Program Files\Tesseract-OCR`, or set
     `TESSERACT_EXE`), each figure is cross-checked with it. Tesseract is optional
     and never changes the text.
3. **Review transcription ↗** opens the page image next to its transcription.
   - Unreadable characters are shown in red, and unconfirmed figures in yellow.
   - **Re-transcribe this page** (optionally at 200 dpi) redoes one page.
4. **Each company is a section.** A 10-K with subsidiaries' own statements gets
   one section per company, with ids such as `BH-N5`, `NI-SCH-V`, `NFMI-AUD`.
   Questions use the registrant unless they name a company or say "subsidiaries".
5. **What the prompt includes:** a REPORT PROFILE, the TRANSCRIPTION CHECKS for the
   pages used, and rules for treating `[?]` and totals that don't add up.

Settings (environment variables):

| Variable | Effect |
|---|---|
| `ANN_RPT_OCR_WORKERS` | Pages transcribed in parallel (default 4) |
| `ANN_RPT_OCR_EFFORT` | Effort for transcription (default `low`) |
| `ANN_RPT_OCR_MODEL` | Model for transcription (default: Claude Code's; `sonnet` was faster but broke more table structure in a trial) |

Page images are sent to Anthropic, just as the extracted text is.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask routes and background jobs |
| `config.py` | Paths, CLI settings, limits, notes header strings |
| `pdf_utils.py` | PyMuPDF helpers (rows, running header/footer detection) |
| `notes_index.py` | Builds and caches the Notes Index (note number → pages), including the loaded statements |
| `report_format.py` | Detects modern vs old (Companies Act 1956 schedules) reports |
| `legacy_index.py` | Index for old reports: statements, schedules, numbered notes, Directors'/Auditors' Report, Financial Summary |
| `ocr_transcribe.py` | Scanned reports: page images → `claude -p --tools Read` → cached Markdown + page description; parallel, resumable |
| `ocr_quality.py` | Checks on transcriptions: totals that add up or don't, optional Tesseract figure cross-check |
| `transcribed_index.py` | Index for transcribed reports: one section per company; statements, notes, schedules, reports |
| `templates\ocr_review.html` | The **Review transcription** page (`/ocr-review?report=…&page=…`) |
| `statements.py` | Finds the 4 primary statements per section, rotates sideways pages, and extracts their text |
| `statements_view.py` + `templates\statements_view.html` | The **View the financial statements** page (`/statements?report=…`, opens in a new tab). Shows the extracted statements as tables, consolidated first, then standalone |
| `note_selector.py` | Scope rules, Claude note picker, keyword fallback |
| `extractor.py` | Clips each note and rebuilds table rows as `a \| b \| c` |
| `claude_client.py` | `claude -p` via stdin, with tools disabled |
| `history.py` | `Prompt-History\` numbering and read-back |
| `prompts\` | System prompts and prompt templates, editable without code changes (`*_legacy.txt` for old reports) |
| `claude-prompt-input.txt` / `Claude-prompt-output.txt` | Last prompt and last reply |
| `Prompt-History\` | `-question.txt` (with metadata), `-answer.txt`, `-prompt.txt` (full input) |
| `Analysis-history\` | One styled HTML report per prompt: `<pdf name>-<prompt no>-<YYYYMMDD-HHMMSS>.html` (1 = first question, 2 = first follow-up, …; the time is when the prompt was sent). Built by `report_html.py` + `templates\analysis_report.html` |
| `cache\` | Notes indexes and follow-up threads |
| `logs\app.log` | Timings, selected notes, prompt sizes |

## Tests

```powershell
python -m pytest -q
```

The tests run against the Bharat Forge FY2026 report. The main test checks
that consolidated Note 21 is PDF pages 405–407 (printed 402–404).
`tests\test_legacy.py` checks the old-report path against
`tests\fixtures\Reliance-1976-1977.pdf`: 15 schedules, notes 1–34 and the
report sections. `tests\test_ocr.py` checks the scanned-report path using the
recorded transcriptions of BRK-1968 (`tests\fixtures\brk1968.ocr\`). Claude is
mocked in the tests, so running them makes no Claude calls.

## Notes

- Page numbers are **PDF** page numbers. The printed page number is shown
  alongside, because in the sample report the printed number is 3 less.
- PyMuPDF must be imported as `pymupdf`: a conflicting `fitz` package is
  installed on this machine.
- Claude runs with `--tools ""` (no tools), so it only analyzes the text
  it's sent. The model is whatever Claude Code is set to; set `CLAUDE_MODEL`
  to override it.
- Scanned (image-only) PDFs aren't supported, because there is no OCR.
