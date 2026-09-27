"""STEP 4 - extract the content of the selected notes from the PDF.

Each note is clipped to its exact extent from the Notes Index: on its first
page only text below its heading is kept, and on its last page only text
above the next note's heading. Running headers and footers are dropped.

The notes' tables have no ruling lines, so PyMuPDF's table finder can't
read them. Instead, each visual row is rebuilt from text spans, and a wide
horizontal gap between spans becomes a column break written as " | ":

    Current income tax charge | 5,701.41 | 5,994.80

Scanned reports (format "transcribed") have no PDF text: their units are
line ranges of the cached Claude page transcriptions (transcribed_index).

Old-format reports (legacy_index) use the same extents. Their "notes" are
schedules, numbered notes and report sections; a page printed sideways (a
landscape Fixed Assets schedule) is turned first, exactly as when indexing.
"""

import re
from pathlib import Path

import pymupdf

import config
import legacy_index
import ocr_transcribe
import statements
import transcribed_index
from notes_index import CONTD_RE, find_note
from pdf_utils import clean_text, open_pdf, page_layout, page_rows

COLUMN_GAP = 12.0      # points of empty space that separate table columns
ROW_TOLERANCE = 3.0    # spans whose vertical centers are this close share a row
NUMBER_CELL_RE = re.compile(r"^\(?[-–]?[\d,]+(\.\d+)?\)?%?$|^[-–—]{1,2}$")
CONTD_HEADING_RE = re.compile(r"^\d{1,3}(\.\d{1,2})?\s*\.?\s+.*\(\s*CONT", re.I)


def _spans_in(page, y_from, y_to):
    """Horizontal text spans whose vertical center is within [y_from, y_to).
    Rotated text (landscape tables) is returned separately."""
    horizontal, rotated = [], []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            is_horizontal = abs(line["dir"][0]) > 0.99
            for span in line["spans"]:
                if not span["text"].strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                if not (y_from <= (y0 + y1) / 2 < y_to):
                    continue
                (horizontal if is_horizontal else rotated).append(span)
    return horizontal, rotated


def _group_rows(spans):
    """Spans grouped into visual rows (vertical centres within
    ROW_TOLERANCE), top to bottom."""
    spans = sorted(spans, key=lambda s: ((s["bbox"][1] + s["bbox"][3]) / 2, s["bbox"][0]))
    rows = []
    for span in spans:
        center = (span["bbox"][1] + span["bbox"][3]) / 2
        if rows and abs(rows[-1]["center"] - center) <= ROW_TOLERANCE:
            rows[-1]["spans"].append(span)
        else:
            rows.append({"center": center, "spans": [span]})
    return rows


def _rows_with_positions(spans):
    """Rows as lists of {"t", "x0", "x1"}, one entry per span, kept separate
    (no gap merging). Used to rebuild statement tables by position: in the
    wide Statement of Changes in Equity, stacked column headings such as
    "Employee" and "General" sit only 11pt apart but belong to different
    columns."""
    out = []
    for row in _group_rows(spans):
        cells = [
            {"t": clean_text(re.sub(r"\s+", " ", s["text"].strip())),
             "x0": round(s["bbox"][0], 1), "x1": round(s["bbox"][2], 1)}
            for s in sorted(row["spans"], key=lambda s: s["bbox"][0])
        ]
        line = " ".join(c["t"] for c in cells)
        if CONTD_HEADING_RE.match(line):
            continue
        out.append(cells)
    return out


def _rows_to_text(spans):
    lines = []
    for row in _group_rows(spans):
        cells = []
        last_x1 = None
        for span in sorted(row["spans"], key=lambda s: s["bbox"][0]):
            text = span["text"].strip()
            # Two numbers side by side are always separate columns, even when
            # a wide table (e.g. Statement of Changes in Equity) packs them
            # closer than COLUMN_GAP.
            both_numbers = bool(cells) and NUMBER_CELL_RE.match(cells[-1]) and NUMBER_CELL_RE.match(text)
            if (last_x1 is not None and span["bbox"][0] - last_x1 <= COLUMN_GAP
                    and not both_numbers):
                cells[-1] += ("" if cells[-1].endswith(("(", "₹", "`")) else " ") + text
            else:
                cells.append(text)
            last_x1 = span["bbox"][2]
        line = clean_text(" | ".join(re.sub(r"\s+", " ", c) for c in cells))
        if CONTD_HEADING_RE.match(line):
            continue  # "21. INCOME AND DEFERRED TAXES (CONTD.)" repeats
        lines.append(line)
    return "\n".join(lines)


def _page_bounds(index, doc, pno):
    info = index["pages"].get(str(pno))
    if info:
        return info["top"], info["bottom"], info["printed"]
    page = doc[pno - 1]
    layout = page_layout(page, page_rows(page))
    return layout["top"], layout["bottom"], layout["printed"]


def _work_page(index, doc, pno):
    """(page to read, temp doc or None). Old-format pages printed sideways
    are turned so their text reads left to right, as when they were indexed."""
    info = index["pages"].get(str(pno)) or {}
    if index.get("format") == "legacy" and info.get("rotation"):
        temp, page = statements.derotated_page(doc, pno, info["rotation"])
        return page, temp
    return doc[pno - 1], None


def _finish_text(index, text):
    return legacy_index.legacy_clean(text) if index.get("format") == "legacy" else text


def _page_text(index, doc, pno, y_from, y_to):
    page, temp = _work_page(index, doc, pno)
    try:
        horizontal, rotated = _spans_in(page, y_from, y_to)
        text = _rows_to_text(horizontal)
        # On a turned page, the sideways text is just the running header.
        if rotated and temp is None:
            text += "\n[Rotated text on this page:]\n" + clean_text(
                " ".join(s["text"].strip() for s in rotated))
    finally:
        if temp is not None:
            temp.close()
    return _finish_text(index, text)


def _page_label(pno, printed):
    return f"PDF page {pno}" + (f" (printed page {printed})" if printed else "")


def section_label(index, section):
    return index["sections"].get(section, {}).get("label") or config.SECTION_LABELS.get(section, section)


def extract_note(index, section, number, doc=None):
    note = find_note(index, section, number)
    if not note:
        raise ValueError(f"Note {number} not found in {section} notes")
    if index.get("format") == "transcribed":
        text = transcribed_index.unit_text(Path(index["pdf"]), index, note)
        pages = [{"pdf_page": p, "printed": (index["pages"].get(str(p)) or {}).get("printed")}
                 for p in range(note["start_page"], note["end_page"] + 1)
                 if (index["pages"].get(str(p)) or {}).get("page_type") != "blank"]
        return {
            "section": section, "no": number, "title": note["title"], "pages": pages,
            "text": f"=== {note['entity']} — {note['label']} ===\n{text}",
        }
    own_doc = doc is None
    doc = doc or open_pdf(Path(index["pdf"]))
    try:
        parts = []
        pages = []
        for pno in range(note["start_page"], note["end_page"] + 1):
            top, bottom, printed = _page_bounds(index, doc, pno)
            y_from = note["start_y"] - 1 if pno == note["start_page"] else top + 0.5
            y_to = (note["end_y"] - 1
                    if pno == note["end_page"] and note["end_y"] is not None
                    else bottom)
            text = _page_text(index, doc, pno, y_from, y_to)
            pages.append({"pdf_page": pno, "printed": printed})
            parts.append(f"--- {_page_label(pno, printed)} ---\n{text.strip()}")
    finally:
        if own_doc:
            doc.close()

    label = section_label(index, section)
    if note.get("kind", "note") == "note" and isinstance(note["no"], int):
        header = f"=== Note {number} — {note['title']} ({label}) ==="
    else:
        header = f"=== {legacy_index.unit_heading(note)} ==="
    return {
        "section": section,
        "no": number,
        "title": note["title"],
        "pages": pages,
        "text": header + "\n" + "\n\n".join(parts),
    }


def extract_pages(index, page_numbers, doc=None):
    """Manual override: whole pages (minus running header/footer)."""
    if index.get("format") == "transcribed":
        parts, pages = [], []
        for pno in page_numbers:
            if not 1 <= pno <= index["page_count"]:
                raise ValueError(f"PDF page {pno} is out of range (1–{index['page_count']})")
            printed = (index["pages"].get(str(pno)) or {}).get("printed")
            text = ocr_transcribe.page_markdown(Path(index["pdf"]), pno).strip() or "(not transcribed)"
            pages.append({"pdf_page": pno, "printed": printed})
            parts.append(f"--- {_page_label(pno, printed)} ---\n{text}")
        return {"section": None, "no": None, "title": "Manually selected pages", "pages": pages,
                "text": "=== Manually selected pages ===\n" + "\n\n".join(parts)}
    own_doc = doc is None
    doc = doc or open_pdf(Path(index["pdf"]))
    try:
        parts, pages = [], []
        for pno in page_numbers:
            if not 1 <= pno <= doc.page_count:
                raise ValueError(f"PDF page {pno} is out of range (1–{doc.page_count})")
            top, bottom, printed = _page_bounds(index, doc, pno)
            text = _page_text(index, doc, pno, top + 0.5, bottom)
            pages.append({"pdf_page": pno, "printed": printed})
            parts.append(f"--- {_page_label(pno, printed)} ---\n{text.strip()}")
    finally:
        if own_doc:
            doc.close()
    return {
        "section": None,
        "no": None,
        "title": "Manually selected pages",
        "pages": pages,
        "text": "=== Manually selected pages ===\n" + "\n\n".join(parts),
    }


def extract_selection(index, notes=None, page_numbers=None):
    """Extract a list of {"section", "no"} notes, or explicit pages.
    Returns (extracts, combined_text)."""
    doc = open_pdf(Path(index["pdf"]))
    try:
        if page_numbers:
            extracts = [extract_pages(index, page_numbers, doc)]
        else:
            extracts = [extract_note(index, n["section"], n["no"], doc) for n in notes]
    finally:
        doc.close()
    combined = "\n\n".join(e["text"] for e in extracts)
    return extracts, combined


def parse_page_spec(spec):
    """"405-407, 410" -> [405, 406, 407, 410]"""
    pages = []
    spec = re.sub(r"\s*[-–—]\s*", "-", spec.strip())
    for part in re.split(r"[,\s]+", spec):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Can't read page range '{part}'. Use e.g. 405-407, 410")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start or end - start > 100:
            raise ValueError(f"Invalid page range '{part}'")
        pages.extend(range(start, end + 1))
    return sorted(dict.fromkeys(pages))
