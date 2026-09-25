"""Save each analysis / follow-up as a styled, self-contained HTML report
in Analysis-history/, named

    <annual report file name>-<prompt number>-<YYYYMMDD-HHMMSS>.html

Prompt number 1 is the first question of a thread, 2 its first follow-up,
and so on. The timestamp is when the prompt was sent to Claude.

The look follows OLD-STUFF/ANALYSIS-REPORTS/BHARATFORGE-ANN-REPORT-
ANALYSIS-2025-26.html: navy top bar with the RAGChamp logo on the left and
"ANNUAL REPORT ANALYZER" on the right, then a light-blue box with the
company, report and question, then the analysis.
"""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config

_env = Environment(
    loader=FileSystemLoader(str(config.BASE_DIR / "templates")),
    autoescape=select_autoescape(["html"]),
)

NUMERIC_CELL_RE = re.compile(
    r"^[(\-–−+]?\s*[₹$€£]?\s*[\d.,]+\s*(%|pp|x|bn|m|cr)?\s*\)?\s*(%|pp|x|bn|m|cr)?$"
    r"|^[-–—]$|^n\.?[ma]\.?$", re.I)
TD_RE = re.compile(r"<td>(.*?)</td>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def report_filename(pdf_name, prompt_no, sent_at):
    stem = Path(pdf_name).stem
    return f"{stem}-{prompt_no}-{sent_at.strftime('%Y%m%d-%H%M%S')}.html"


def _style_tables(answer_html):
    """Bootstrap table classes (same look as the reference report) and
    right-aligned numeric cells."""
    def cell(match):
        text = TAG_RE.sub("", match.group(1)).strip()
        if NUMERIC_CELL_RE.match(text):
            return f'<td class="num">{match.group(1)}</td>'
        return match.group(0)

    html = TD_RE.sub(cell, answer_html)
    html = html.replace(
        "<table>",
        '<div class="table-responsive"><table class="table table-sm table-bordered align-middle">')
    return html.replace("</table>", "</table></div>")


def save_report(*, pdf_name, company, question, answer_html, prompt_no, sent_at,
                scope, notes_text, pages_text, statements_text="", original_question=None):
    """Write the report and return its file name."""
    logo_svg = config.LOGO_FILE.read_text(encoding="utf-8") if config.LOGO_FILE.exists() else ""
    html = _env.get_template("analysis_report.html").render(
        pdf_name=pdf_name,
        company=company,
        question=question,
        original_question=original_question,
        answer_html=_style_tables(answer_html),
        prompt_no=prompt_no,
        prompt_label=("Initial question" if prompt_no == 1
                      else f"Follow-up question {prompt_no - 1}"),
        sent_at=sent_at.strftime("%d %b %Y, %H:%M:%S"),
        scope=scope,
        notes_text=notes_text,
        pages_text=pages_text,
        statements_text=statements_text,
        logo_svg=logo_svg,
    )
    filename = report_filename(pdf_name, prompt_no, sent_at)
    (config.ANALYSIS_HISTORY_DIR / filename).write_text(html, encoding="utf-8")
    return filename
