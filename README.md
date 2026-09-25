# Annual Report Foot Notes Analyzer

A local Python Flask web app for in-depth analysis of the **notes to the
financial statements** in an annual report PDF, using **Claude Code**.

Pick a report and ask a question, e.g. *"Analyze the company's Tax related
liabilities and comment on it."* The app then:

- finds the relevant note(s) deterministically (e.g. Note 21, PDF pages 405–407) and lets you confirm them;
- extracts exactly those notes with their tables;
- sends them, together with the company's four primary financial statements (P&L, Balance Sheet, Cash Flow, Changes in Equity), to Claude Code;
- shows an analysis that explains the **impact on each financial statement**, with follow-up questions, history, and a styled HTML report for every answer.

It also has a **View the financial statements** page that rebuilds all the
statements as tables. That includes sideways-printed pages and multi-line
column headings.

## Repository layout

| Path | What it is |
|---|---|
| [`WEB-APP/`](WEB-APP/) | The application. **Start with [`WEB-APP/README.md`](WEB-APP/README.md)** |
| [`INFO/HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html`](INFO/HOW-ANN-RPT-FOOT-NOTE-ANALYZER-WORKS.html) | How it works, step by step: every stage, the real bugs found and their fixes |
| [`INFO/ANN-RPT-NOTES-ANALYZER-PLAN.md`](INFO/ANN-RPT-NOTES-ANALYZER-PLAN.md) | The original development plan and the confirmed design decisions |
| [`.claude/CLAUDE.md`](.claude/CLAUDE.md) | Briefing for Claude Code (or a developer) who wants to reuse or extend the app |
| `Annual-reports/` | The sample report used for development and tests (Bharat Forge Integrated Annual Report FY2025-26) |
| `WEB-APP/Prompt-History/`, `WEB-APP/Analysis-history/` | Example analyses produced by the app |

## Quick start

```powershell
cd WEB-APP
pip install -r requirements.txt
python app.py              # open http://127.0.0.1:5000
python -m pytest -q        # 72 tests, Claude mocked
```

Requires Python 3.12 and the [Claude Code](https://claude.com/claude-code)
CLI installed and logged in. Set `CLAUDE_EXE` if `claude` is not at the
default path, and `REPORTS_DIR` to use a different folder of PDFs (see
`WEB-APP/config.py`).

The sample report is a publicly released annual report of Bharat Forge
Limited, included only so the app and its tests can be run as-is. The
analyses are research aids, not investment advice.
