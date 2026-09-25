"""The four primary financial statements.

When a report is indexed, the app also finds, for each notes section
(consolidated / standalone):
  - Statement of Profit and Loss (income statement)
  - Balance Sheet
  - Cash Flow Statement
  - Statement of Changes in Equity (stockholders' equity)

They are the pages just before that section's notes whose large title row
matches a statement name - in the Bharat Forge report, consolidated pages
341-347 and standalone pages 205-209.

Some statements are printed sideways: the consolidated Statement of Changes
in Equity (PDF pages 344-345) has its text running bottom-to-top. Such a
page is first rotated (drawn onto a new landscape page turned 90°
clockwise) so its text reads left-to-right, and only then extracted.
"""

import re
from collections import Counter

import pymupdf

import config
from pdf_utils import page_layout, page_rows

# Order the statements are listed and sent to Claude in.
STATEMENT_TYPES = [
    ("profit_loss", "Statement of Profit and Loss",
     r"statement of profit and loss|income statement|statement of (comprehensive )?income"
     r"|profit and loss (account|statement)"),
    ("balance_sheet", "Balance Sheet", r"balance sheet|statement of financial position"),
    ("cash_flow", "Cash Flow Statement", r"cash flow statement|statement of cash flows?"),
    ("equity", "Statement of Changes in Equity",
     r"statement of changes in (shareholders'?|stockholders'?)?\s*equity"),
]
STATEMENT_ORDER = [t[0] for t in STATEMENT_TYPES]
STATEMENT_LABELS = {t[0]: t[1] for t in STATEMENT_TYPES}
# Matched at the start of the title row: on a de-rotated page the (now
# sideways) running header "Integrated Annual Report" can share the title's
# line, e.g. "Consolidated Statement of Changes in Equity Integrated Annual Report".
TITLE_RES = [(t[0], re.compile(rf"^(?:{t[2]})\b")) for t in STATEMENT_TYPES]
KEYWORDS = ("balance sheet", "profit and loss", "changes in equity", "cash flow",
            "income statement", "financial position")
# How far before a section's first notes page its statements may start.
STATEMENTS_WINDOW = 15
TITLE_MIN_SIZE = 14

# text direction of most lines -> degrees to turn the page so it reads normally
DERO_ANGLES = {(0, -1): -90, (0, 1): 90, (-1, 0): 180}
ROTATION_WORDS = {-90: "90° clockwise", 90: "90° counter-clockwise", 180: "180°"}


def _normalize_title(text):
    text = text.lower()
    text = re.sub(r"\(\s*cont(?:d|inued)?\.?\s*\)", "", text)
    text = re.sub(r"\b(consolidated|standalone|separate)\b", "", text)
    return " ".join(text.replace("’", "'").split())


def statement_type(title):
    normalized = _normalize_title(title)
    for key, pattern in TITLE_RES:
        if pattern.match(normalized):
            return key
    return None


def dominant_direction(page):
    """Most common text-line direction, e.g. (1, 0) for normal text or
    (0, -1) for text running bottom-to-top."""
    dirs = Counter()
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            dirs[(round(line["dir"][0]), round(line["dir"][1]))] += len(line["spans"])
    return dirs.most_common(1)[0][0] if dirs else (1, 0)


def derotated_page(doc, pno, angle):
    """Draw PDF page `pno` (1-based) onto a new page turned by `angle`, so
    sideways text becomes horizontal. Returns (temp_doc, page); keep
    temp_doc alive while using the page."""
    src = doc[pno - 1].rect
    temp = pymupdf.open()
    width, height = (src.height, src.width) if angle in (90, -90) else (src.width, src.height)
    page = temp.new_page(width=width, height=height)
    page.show_pdf_page(page.rect, doc, pno - 1, rotate=angle)
    return temp, page


def _title_row(rows, page_height):
    """The big title row near the top of the page, e.g. "Consolidated
    Balance Sheet" (22pt in the sample report)."""
    candidates = [r for r in rows if r["y0"] < page_height * 0.25 and r["size"] >= TITLE_MIN_SIZE]
    return max(candidates, key=lambda r: r["size"]) if candidates else None


def detect_statement_page(doc, pno, page_text):
    """If PDF page `pno` is (part of) a primary statement, return its info:
    {type, title, pdf_page, printed, rotation, top, bottom}. Else None."""
    lowered = " ".join(page_text.split()).lower()
    if not any(k in lowered for k in KEYWORDS):
        return None
    page = doc[pno - 1]
    rotation = 0
    temp = None
    direction = dominant_direction(page)
    if direction in DERO_ANGLES:
        rotation = DERO_ANGLES[direction]
        temp, work_page = derotated_page(doc, pno, rotation)
        if dominant_direction(work_page) != (1, 0):
            temp.close()
            return None
    else:
        work_page = page
    try:
        rows = page_rows(work_page)
        title = _title_row(rows, work_page.rect.height)
        kind = statement_type(title["text"]) if title else None
        if not kind:
            return None
        layout = page_layout(work_page, rows)
        # The printed page number is upright on the original page even when
        # the statement itself is sideways.
        printed = layout["printed"] or page_layout(page, page_rows(page))["printed"]
        return {
            "type": kind,
            "title": re.sub(r"\s*\(\s*cont(?:d|inued)?\.?\s*\)", "", title["text"], flags=re.I),
            "pdf_page": pno,
            "printed": printed,
            "rotation": rotation,
            "top": round(title["y0"] - 1, 1),
            "bottom": layout["bottom"],
        }
    finally:
        if temp is not None:
            temp.close()


def group_statements(statement_pages, notes_first_page):
    """Statements for one notes section: detected pages within
    STATEMENTS_WINDOW pages before the notes start, grouped by type."""
    window = range(notes_first_page - STATEMENTS_WINDOW, notes_first_page)
    by_type = {}
    for info in statement_pages:
        if info["pdf_page"] not in window:
            continue
        entry = by_type.setdefault(info["type"], {
            "type": info["type"],
            "label": STATEMENT_LABELS[info["type"]],
            "title": info["title"],
            "pages": [],
        })
        entry["pages"].append({k: info[k] for k in ("pdf_page", "printed", "rotation", "top", "bottom")})
    return [by_type[t] for t in STATEMENT_ORDER if t in by_type]


def pages_text(statement):
    pages = [p["pdf_page"] for p in statement["pages"]]
    return f"{pages[0]}" if len(pages) == 1 else f"{pages[0]}–{pages[-1]}"


def summary(statements):
    """"Statement of Profit and Loss (p.342–343) · ... (p.344–345, rotated)" """
    parts = []
    for s in statements:
        rotated = any(p["rotation"] for p in s["pages"])
        parts.append(f"{s['label']} (p.{pages_text(s)}{', rotated' if rotated else ''})")
    return " · ".join(parts)


def label(index, sections):
    """Human-readable list of the statements sent for these sections."""
    parts = []
    for section in sections:
        for s in index["sections"].get(section, {}).get("statements", []):
            parts.append(f"{s['title']} (PDF p.{pages_text(s)})")
    return "; ".join(parts)


def extract_statement(doc, statement, section_label):
    """Text of one statement, with each page de-rotated first if needed,
    rebuilt as rows ("label | note | FY26 | FY25")."""
    from extractor import _page_label, _rows_to_text, _rows_with_positions, _spans_in  # avoid cycle
    from statements_view import column_headings_lines

    parts = []
    for page_info in statement["pages"]:
        pno = page_info["pdf_page"]
        temp = None
        if page_info["rotation"]:
            temp, page = derotated_page(doc, pno, page_info["rotation"])
        else:
            page = doc[pno - 1]
        try:
            # Only horizontal text: on a de-rotated page, the running header
            # and page number are now sideways and are dropped.
            spans, _ = _spans_in(page, page_info["top"], page_info["bottom"])
            text = _rows_to_text(spans)
            # Rows with x-positions, cached in the index, so the "View the
            # financial statements" page can rebuild real columns and
            # multi-line column headings.
            page_info["rows"] = _rows_with_positions(spans)
        finally:
            if temp is not None:
                temp.close()
        # Wide tables print stacked, wrapped column headings that come out of
        # the text jumbled; give Claude the headings rebuilt by position.
        headings = column_headings_lines(page_info["rows"], statement["title"])
        if headings:
            text = "\n".join(headings) + "\n" + text
        note = (f" (printed sideways; rotated {ROTATION_WORDS[page_info['rotation']]} before extraction)"
                if page_info["rotation"] else "")
        parts.append(f"--- {_page_label(pno, page_info['printed'])}{note} ---\n{text.strip()}")
    header = f"=== {statement['title']} ({section_label}) ==="
    return header + "\n" + "\n\n".join(parts)


def sections_text(index, sections):
    """Combined, already-extracted text of all statements for the given
    sections (cached in the index when the report was loaded)."""
    return "\n\n".join(
        statement["text"]
        for section in sections
        for statement in index["sections"].get(section, {}).get("statements", [])
    )
