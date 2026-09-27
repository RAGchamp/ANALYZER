"""Which kind of annual report a PDF is (see INFO/ANALYZE-OLD-REPORTS-FUNC-PLAN.md §3-4).

  modern  - "Notes to ... Financial Statements" running headers (Ind AS /
            revised Schedule III-VI reports). The original indexer.
  legacy  - old Schedule VI (Companies Act 1956): statements, lettered or
            numbered SCHEDULES "forming part of the Balance Sheet / Profit
            and Loss Account", and a notes schedule. legacy_index.py.

(Scanned, image-only PDFs are rejected before this point.)

Detection only counts cheap text signals on every page. "modern" wins ties,
so reports that indexed before keep indexing exactly as before.
"""

import re

import config

FORMATS = ("modern", "legacy")
FORMAT_LABELS = {
    "modern": "Modern (notes to financial statements)",
    "legacy": "Old (schedules, Companies Act 1956)",
}

SCHEDULE_LINE_RE = re.compile(
    r"^\s*schedule\s*[‘’'`´\"�]?\s*(?:[A-Z]{1,2}|\d{1,2}|[IVXL]{1,5})\s*[‘’'`´\"�]?\s*(?:[:.\-–]|$)",
    re.I | re.M)
FORMING_PART_RE = re.compile(
    r"forming\s+part\s+of\s+the\s+(?:balance\s+sheet|profit\s*(?:and|&)\s*loss\s+account|accounts)",
    re.I)
MIN_SIGNALS = 3


def _has_notes_header(text):
    lowered = " ".join(text.split()).lower()
    return any(h.lower() in lowered for headers in config.NOTES_HEADERS.values() for h in headers)


def detect(page_texts):
    """Returns (format, signals) for a list of page texts."""
    signals = {
        "modern_notes_pages": sum(1 for t in page_texts if _has_notes_header(t)),
        "schedule_headings": sum(len(SCHEDULE_LINE_RE.findall(t)) for t in page_texts),
        "forming_part_pages": sum(1 for t in page_texts if FORMING_PART_RE.search(t)),
    }
    if signals["modern_notes_pages"] >= MIN_SIGNALS:
        return "modern", signals
    if signals["schedule_headings"] >= MIN_SIGNALS or signals["forming_part_pages"] >= MIN_SIGNALS:
        return "legacy", signals
    return "modern", signals
