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
from ingest import legacy_index, ocr_transcribe, profiles, statements, transcribed_index
from ingest.notes_index import CONTD_RE, find_note
from ingest.pdf_utils import (MONOSPACE_FLAG, clean_text, open_pdf, page_layout, page_rows, split_monospace,
                              text_dict)

COLUMN_GAP = 12.0      # points of empty space that separate table columns
ROW_TOLERANCE = 3.0    # spans whose vertical centers are this close share a row
NUMBER_CELL_RE = re.compile(r"^\(?[-–]?[\d,]+(\.\d+)?\)?%?$|^[-–—]{1,2}$")
CONTD_HEADING_RE = re.compile(r"^\d{1,3}(\.\d{1,2})?\s*\.?\s+.*\(\s*CONT", re.I)


def _spans_in(page, y_from, y_to):
    """Horizontal text spans whose vertical center is within [y_from, y_to).
    Rotated text (landscape tables) is returned separately."""
    horizontal, rotated = [], []
    for block in text_dict(page)["blocks"]:
        for line in block.get("lines", []):
            is_horizontal = abs(line["dir"][0]) > 0.99
            for span in line["spans"]:
                if not span["text"].strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                if not (y_from <= (y0 + y1) / 2 < y_to):
                    continue
                # a plain-text filing's row is one monospaced span: split it into columns
                pieces = split_monospace(span) if profiles.active().switches.monospace_columns else [span]
                (horizontal if is_horizontal else rotated).extend(pieces)
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
    columns. Cells in a typewriter font are marked "m" (plain-text filings,
    whose columns are not exactly aligned; see tables._find_columns)."""
    out = []
    for row in _group_rows(spans):
        cells = []
        for s in sorted(row["spans"], key=lambda s: s["bbox"][0]):
            cell = {"t": clean_text(re.sub(r"\s+", " ", s["text"].strip())),
                    "x0": round(s["bbox"][0], 1), "x1": round(s["bbox"][2], 1)}
            if s.get("flags", 0) & MONOSPACE_FLAG:
                cell["m"] = 1
            cells.append(cell)
        line = " ".join(c["t"] for c in cells)
        if CONTD_HEADING_RE.match(line):
            continue
        out.append(cells)
    return out


def _rows_to_text(spans):
    rows = []
    for row in _group_rows(spans):
        cells = []
        pieces = []      # each cell's spans: (text, x0, x1)
        last_x1 = None
        for span in sorted(row["spans"], key=lambda s: s["bbox"][0]):
            text = span["text"].strip()
            piece = (text, span["bbox"][0], span["bbox"][2])
            # Two numbers side by side are always separate columns, even when
            # a wide table (e.g. Statement of Changes in Equity) packs them
            # closer than COLUMN_GAP.
            both_numbers = bool(cells) and NUMBER_CELL_RE.match(cells[-1]) and NUMBER_CELL_RE.match(text)
            # US filings print "$" as its own span, far left of its amount
            # and close after the previous column's amount: "$ 10,622 $ 9,640"
            # -> "$10,622 | $9,640", not "$ | 10,622 $ | 9,640".
            if cells and cells[-1] == "$" and NUMBER_CELL_RE.match(text):
                cells[-1] += text
                pieces[-1].append(piece)
            elif cells and cells[-1].endswith(" $") and NUMBER_CELL_RE.match(text):
                cells[-1] = cells[-1][:-2]
                cells.append("$" + text)
                pieces.append([pieces[-1].pop(), piece])
            elif text == "$" and cells and NUMBER_CELL_RE.match(cells[-1]):
                cells.append(text)
                pieces.append([piece])
            elif (last_x1 is not None and span["bbox"][0] - last_x1 <= COLUMN_GAP
                    and not both_numbers):
                cells[-1] += ("" if cells[-1].endswith(("(", "₹", "`")) else " ") + text
                pieces[-1].append(piece)
            else:
                cells.append(text)
                pieces.append([piece])
            last_x1 = span["bbox"][2]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if CONTD_HEADING_RE.match(clean_text(" | ".join(cells))):
            continue  # "21. INCOME AND DEFERRED TAXES (CONTD.)" repeats
        rows.append((cells, pieces))
    if profiles.active().switches.aligned_note_columns:
        rows = _align_columns(rows)
    return "\n".join(clean_text(" | ".join(row[0])) for row in rows)


HEADING_ROWS = 4    # rows above a table that may be its column headings


def _is_figure_cell(text):
    return bool(NUMBER_CELL_RE.match(text.removeprefix("$")))


def _align_columns(rows):
    """Put each cell under its column in a table whose rows leave cells empty.

    A loss development triangle prints accident year 2017's first figure under
    the 2017 column, not the 2016 one; written left to right it would read as
    2016's. The columns are the right edges of the table's fullest rows
    (figures are right-aligned), and a shorter row gets an empty cell for each
    column it skips: "2017 |  | 1,088 | 1,894". Empty cells after its last
    cell are left out. The heading rows just above the table are placed the
    same way when they are right-aligned to its columns too: "Unaudited" over
    the 2024 column, "Years Ended December 31" over 2025. A row whose cells
    don't each sit clearly under a different column is left as it is."""
    rows = list(rows)
    in_run = [len(cells) >= 2 and any(_is_figure_cell(c) for c in cells[1:]) for cells, _ in rows]
    i = 0
    while i < len(rows):
        if not in_run[i]:
            i += 1
            continue
        end = i
        while end < len(rows) and in_run[end]:
            end += 1
        grid = _grid(rows[i:end])
        if grid:
            for k in range(i, end):
                rows[k] = _placed(rows[k], _slots(rows[k], *grid))
            k = i - 1
            while k >= max(0, i - HEADING_ROWS) and not in_run[k]:
                placed = _heading_slots(rows[k], *grid)
                if placed is None:
                    break
                rows[k] = _placed(rows[k], placed)
                k -= 1
        i = end
    return rows


def _ends(pieces):
    return [p[-1][2] for p in pieces]


def _grid(run):
    """(column right edges, reach) of one table - consecutive rows with figures -
    or None when it has no row with every column filled."""
    width = max(len(cells) for cells, _ in run)
    full = [_ends(pieces) for cells, pieces in run
            if len(cells) == width and all(_is_figure_cell(c) for c in cells[1:])]
    if width < 3 or not full:
        return None
    edges = [sum(e[i] for e in full) / len(full) for i in range(width)]
    reach = min(b - a for a, b in zip(edges[1:], edges[2:])) / 2
    return edges, reach


def _placed(row, placed):
    """The row with its cells at the given columns: placed is [(column, text), ...]."""
    if placed is None:
        return row
    cells = [""] * (placed[-1][0] + 1)
    for col, text in placed:
        cells[col] = text
    return cells, row[1]


def _column(end, edges, reach):
    """The figure column a cell ending at `end` is right-aligned to, or None."""
    col = min(range(1, len(edges)), key=lambda c: abs(edges[c] - end))
    return col if abs(edges[col] - end) <= reach else None


def _slots(row, edges, reach):
    """[(column, text)] for a row shorter than the table, or None. The first
    cell is the label (column 0) unless it is a figure under a column."""
    cells, pieces = row
    if len(cells) >= len(edges) or not all(_is_figure_cell(c) for c in cells[1:]):
        return None
    placed = []
    for i, (cell, end) in enumerate(zip(cells, _ends(pieces))):
        col = 0 if i == 0 and not _is_figure_cell(cell) else _column(end, edges, reach)
        if col is None:
            if i > 0 or end > edges[0] + reach:
                return None
            col = 0     # a year as the row's label: "2017"
        if placed and col <= placed[-1][0]:
            return None
        placed.append((col, cell))
    return placed


def _heading_slots(row, edges, reach):
    """[(column, text)] for a heading row above the table, or None when one of
    its cells is not right-aligned to a column. A cell starting at the left
    margin is the row's label ("(in millions of U.S. dollars)", a title).
    Headings printed close together were joined into one cell ("Reinsurance
    Life Insurance"); they are split again when each sits over its own column."""
    cells, pieces = row
    placed = []
    for i, (cell, parts) in enumerate(zip(cells, pieces)):
        if i == 0 and parts[0][1] <= edges[0] + reach:
            found = [(0, cell)]
        else:
            cols = [_column(p[2], edges, reach) for p in parts]
            if len(parts) > 1 and None not in cols and cols == sorted(set(cols)):
                found = [(c, re.sub(r"\s+", " ", p[0])) for c, p in zip(cols, parts)]
            else:
                col = _column(parts[-1][2], edges, reach)
                if col is None:
                    return None
                found = [(col, cell)]
        if placed and found[0][0] <= placed[-1][0]:
            return None
        placed += found
    return placed


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
    # modern notes: 21, or "2.1" (decimal notes carry no "kind"; old-format units always do)
    if note.get("kind", "note") == "note" and (isinstance(note["no"], int) or "kind" not in note):
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


def page_record(index, doc, pno):
    """(printed page number, cleaned text) of one whole page, minus its running
    header and footer: what "Or analyze specific PDF pages" sends. The ingester
    stores it for every page. `doc` is unused for scanned reports."""
    if index.get("format") == "transcribed":
        printed = (index["pages"].get(str(pno)) or {}).get("printed")
        return printed, ocr_transcribe.page_markdown(Path(index["pdf"]), pno).strip() or "(not transcribed)"
    top, bottom, printed = _page_bounds(index, doc, pno)
    return printed, _page_text(index, doc, pno, top + 0.5, bottom).strip()


def extract_pages(index, page_numbers, doc=None):
    """Manual override: whole pages (minus running header/footer)."""
    transcribed = index.get("format") == "transcribed"
    own_doc = doc is None and not transcribed
    if own_doc:
        doc = open_pdf(Path(index["pdf"]))
    try:
        parts, pages = [], []
        page_count = index["page_count"] if transcribed else doc.page_count
        for pno in page_numbers:
            if not 1 <= pno <= page_count:
                raise ValueError(f"PDF page {pno} is out of range (1–{page_count})")
            printed, text = page_record(index, doc, pno)
            pages.append({"pdf_page": pno, "printed": printed})
            parts.append(f"--- {_page_label(pno, printed)} ---\n{text}")
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
