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

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask routes and background jobs |
| `config.py` | Paths, CLI settings, limits, notes header strings |
| `pdf_utils.py` | PyMuPDF helpers (rows, running header/footer detection) |
| `notes_index.py` | Builds and caches the Notes Index (note number → pages), including the loaded statements |
| `statements.py` | Finds the 4 primary statements per section, rotates sideways pages, and extracts their text |
| `statements_view.py` + `templates\statements_view.html` | The **View the financial statements** page (`/statements?report=…`, opens in a new tab). Shows the extracted statements as tables, consolidated first, then standalone |
| `note_selector.py` | Scope rules, Claude note picker, keyword fallback |
| `extractor.py` | Clips each note and rebuilds table rows as `a \| b \| c` |
| `claude_client.py` | `claude -p` via stdin, with tools disabled |
| `history.py` | `Prompt-History\` numbering and read-back |
| `prompts\` | System prompts and prompt templates, editable without code changes |
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
that consolidated Note 21 is PDF pages 405–407 (printed 402–404). Claude is
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
