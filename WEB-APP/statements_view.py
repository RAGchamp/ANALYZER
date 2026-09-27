"""Rebuild the loaded financial statements as tables for the "View the
financial statements" page.

Tables are rebuilt from the cached rows *with x-positions* (see
extractor._rows_with_positions), not from the " | " text:

- Columns are clusters of the right edges of the numbers (figures are
  right-aligned in their column).
- Every other cell goes to the column it sits over. Column headings are
  printed stacked and wrapped (the Statement of Changes in Equity has 12
  columns headed "Securities / premium", "Retained / Earnings",
  "Foreign / currency / translation / reserve / (FCTR)", ...), so all the
  heading rows between two rows of figures are merged per column, top to
  bottom.
- A heading that covers several columns ("Reserves and Surplus (Refer note
  16)", "Items of OCI (Refer note 16)") is a group heading. It is printed
  centred, so its span is widened symmetrically while it stays centred on
  the text.
"""

import re

import config

ROTATION_WORDS = {-90: "90° clockwise", 90: "90° counter-clockwise", 180: "180°"}

VALUE_RE = re.compile(r"^\(?[-–−]?\s*₹?\s*[\d,]+(\.\d+)?\)?%?$|^[-–—]$")
# Notes column: "21", "5a"; old-format reports cite schedules instead: 'A', 'E'.
NOTE_REF_RE = re.compile(r"^\d{1,2}[a-z]?$|^'[A-Z]{1,2}'$")
PAGE_RE = re.compile(r"^--- (PDF page (\d+)(?: \(printed page (\w+)\))?)(.*?) ---$")
SIGNATURE_RE = re.compile(r"^As per our report", re.I)
UNIT_RE = re.compile(r"In\s*₹\s*Million|₹\s*in\s*Million", re.I)
UNIT_WORDS = {"in", "₹", "million", "in ₹", "₹ million", "in ₹ million", "₹ in million"}
# "A. EQUITY SHARE CAPITAL:", "B. OTHER EQUITY" - all caps, so the Balance
# Sheet's "I. Non-current assets" (a Roman numeral) is not a new part.
PART_RE = re.compile(r"^[A-Z]\.\s+[A-Z][A-Z &,'()\-:]+(\(CONTD\.?\))?$")
REFER_NOTE_RE = re.compile(r"^\(Refer note [\w, ()]+\)$", re.I)
SECTION_RE = re.compile(r"^([IVX]+\.|[A-Z]\.|[A-Z][A-Z &/,'\-()]{3,}$)|:$")
TOTAL_RE = re.compile(r"^(total|net (cash|increase|decrease)|profit (for|before)|cash and cash equivalents at)",
                      re.I)
COLUMN_GAP = 6.0         # right edges closer than this are the same column
EDGE_TOLERANCE = 4.0     # a heading ending this close to a column's edge belongs to it
MIN_OVERLAP = 0.4        # share of a column's width a cell must cover to count as over it
CENTER_TOLERANCE = 12.0  # how far a widened group heading may drift from its text centre
WIDE_TABLE_COLUMNS = 5   # tables this wide get their headings spelled out for Claude


def _is_value(text):
    return bool(VALUE_RE.match(text)) or bool(NOTE_REF_RE.match(text))


def _norm(text):
    return " ".join(re.sub(r"\(\s*cont(?:d|inued)?\.?\s*\)", "", text, flags=re.I).lower().split())


def _join(parts):
    """Join stacked heading words; "Non-" + "controlling" -> "Non-controlling"."""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        out = f"{out}{part}" if out.endswith("-") else (f"{out} {part}" if out else part)
    return out


# ------------------------------------------------------------------ rows

def _prepare_rows(rows, title):
    """Drop the repeated title and the signature block, and turn the
    "In ₹ Million" unit into its own marker row."""
    out = []
    for cells in rows:
        joined = " ".join(c["t"] for c in cells)
        if SIGNATURE_RE.match(joined):
            break  # auditor / director signature block
        if _norm(joined) == _norm(title):
            continue
        if UNIT_RE.search(joined):
            kept = [c for c in cells if c["t"].lower().strip() not in UNIT_WORDS]
            out.append({"unit": True})
            if not kept:
                continue
            cells = kept
        out.append({"cells": cells})
    return out


def _split_parts(rows):
    """Lettered parts ("A. EQUITY SHARE CAPITAL:", "B. OTHER EQUITY") are
    separate tables with their own columns."""
    blocks = [[]]
    for row in rows:
        text = " ".join(c["t"] for c in row.get("cells", []))
        if PART_RE.match(text) and any("cells" in r for r in blocks[-1]):
            blocks.append([])
        blocks[-1].append(row)
    return blocks


# ------------------------------------------------------------------ columns

def _numeric_cells(row_cells):
    """Figures in a row. A leading "(1)" before text is a label, not a figure."""
    nums = []
    for i, cell in enumerate(row_cells):
        if not _is_value(cell["t"]):
            continue
        if i == 0 and len(row_cells) > 1 and not _is_value(row_cells[1]["t"]):
            continue
        nums.append(cell)
    return nums


def _find_columns(block):
    edges = sorted(
        (c["x1"], c["x0"]) for row in block if "cells" in row for c in _numeric_cells(row["cells"])
    )
    clusters = []
    for x1, x0 in edges:
        if clusters and x1 - clusters[-1]["x1s"][-1] <= COLUMN_GAP:
            clusters[-1]["x1s"].append(x1)
            clusters[-1]["x0"] = min(clusters[-1]["x0"], x0)
        else:
            clusters.append({"x1s": [x1], "x0": x0})
    columns = [{"x1": max(c["x1s"]), "x0": c["x0"]} for c in clusters]
    if not columns:
        return [], None
    label_limit = columns[0]["x0"] - 2
    left = label_limit
    for col in columns:
        col["left"] = left      # a column's range is (left, x1]
        left = col["x1"]
    return columns, label_limit


def _overlapped(cell, columns):
    hits = []
    for i, col in enumerate(columns):
        width = col["x1"] - col["left"]
        overlap = min(cell["x1"], col["x1"]) - max(cell["x0"], col["left"])
        if width > 0 and overlap / width >= MIN_OVERLAP:
            hits.append(i)
    return hits


def _nearest(x1, columns):
    return min(range(len(columns)), key=lambda i: abs(columns[i]["x1"] - x1))


def _center(first, last, columns):
    return (columns[first]["left"] + columns[last]["x1"]) / 2


def _widen_group(first, last, text_center, columns, taken):
    """Widen a centred group heading's column span symmetrically (or on one
    side) while its centre stays within CENTER_TOLERANCE of the text."""
    while True:
        options = []
        for new_first, new_last in ((first - 1, last + 1), (first - 1, last), (first, last + 1)):
            if new_first < 0 or new_last >= len(columns):
                continue
            if any(i in taken for i in range(new_first, new_last + 1) if not first <= i <= last):
                continue
            drift = abs(_center(new_first, new_last, columns) - text_center)
            if drift <= CENTER_TOLERANCE:
                options.append((new_last - new_first, -drift, new_first, new_last))
        if not options:
            return first, last
        _, _, first, last = max(options)


def _classify(cells, columns, label_limit):
    """Split one row into label text, per-column texts and group headings."""
    numbers = {id(c) for c in _numeric_cells(cells)}
    label, per_col, groups = [], {}, []
    has_numbers = bool(numbers)
    # Only a heading row (other cells out in the columns) can have a
    # right-aligned heading that starts in the label area.
    heading_row = not has_numbers and any(c["x0"] >= label_limit for c in cells)
    for cell in cells:
        if id(cell) in numbers:
            per_col.setdefault(_nearest(cell["x1"], columns), []).append(cell["t"])
            continue
        if cell["x0"] < label_limit:
            # In a heading row, a heading may start in the label area yet
            # belong to a column: centred over it ("Notes" over the narrow
            # Notes column) or right-aligned to its edge ("Balance as on
            # April 1, 2025").
            center = (cell["x0"] + cell["x1"]) / 2
            edge = _nearest(cell["x1"], columns)
            over = [i for i, col in enumerate(columns) if col["left"] < center <= col["x1"]]
            if heading_row and over:
                per_col.setdefault(over[0], []).append(cell["t"])
            elif heading_row and abs(columns[edge]["x1"] - cell["x1"]) <= EDGE_TOLERANCE:
                per_col.setdefault(edge, []).append(cell["t"])
            else:
                label.append(cell["t"])
            continue
        hits = _overlapped(cell, columns)
        if len(hits) >= 2 and not has_numbers:
            groups.append({"first": hits[0], "last": hits[-1], "text": cell["t"],
                           "center": (cell["x0"] + cell["x1"]) / 2})
        else:
            col = hits[0] if len(hits) == 1 else _nearest(cell["x1"], columns)
            per_col.setdefault(col, []).append(cell["t"])
    return _join(label), per_col, groups, has_numbers


# ------------------------------------------------------------------ tables

def _build_table(block, columns, label_limit):
    width = len(columns)
    out = []
    band = None   # heading rows being merged: {"label", "cols", "groups"}

    def flush_band():
        nonlocal band
        if not band:
            return
        # group headings from the same source row share one output row
        for group_row in band["groups"]:
            taken = set()
            segments = []
            for g in sorted(group_row, key=lambda g: g["first"]):
                first, last = _widen_group(g["first"], g["last"], g["center"], columns, taken)
                taken.update(range(first, last + 1))
                segments.append((first, last, g["text"]))
            cells, i = [], 0
            for first, last, text in sorted(segments):
                if first > i:
                    cells.append({"span": first - i, "text": ""})
                cells.append({"span": last - first + 1, "text": text})
                i = last + 1
            if i < width:
                cells.append({"span": width - i, "text": ""})
            out.append({"kind": "group", "label": "", "segments": cells})
        out.append({"kind": "header", "label": band["label"],
                    "cells": [_join(band["cols"].get(i, [])) for i in range(width)]})
        band = None

    for row in block:
        if row.get("unit"):
            flush_band()
            out.append({"kind": "unit", "label": "₹ in Million"})
            continue
        label, per_col, groups, has_numbers = _classify(row["cells"], columns, label_limit)

        if not has_numbers and (per_col or groups):
            band = band or {"label": "", "cols": {}, "groups": []}
            if label:
                band["label"] = _join([band["label"], label])
            for i, texts in per_col.items():
                band["cols"].setdefault(i, []).extend(texts)
            if groups:
                band["groups"].append(groups)
            continue
        flush_band()

        if has_numbers:
            kind = "total" if (not label or TOTAL_RE.match(label)) else "data"
            out.append({"kind": kind, "label": label,
                        "cells": [_join(per_col.get(i, [])) for i in range(width)]})
            continue

        # label-only row: a section heading, a line of text, or the wrapped
        # continuation of the row above ("year", "(Loss)", "and scrap")
        prev = out[-1] if out else None
        prev_has_figures = bool(prev and any(prev.get("cells", [])) and prev["kind"] in ("data", "total"))
        continues_paren = label.startswith("(") and (
            not prev_has_figures or not re.search(r"\d", label) or bool(REFER_NOTE_RE.match(label)))
        # "- Other Comprehensive" + "Income/(Loss)": a short line under a
        # "- item" row is the rest of that item's label.
        continues_item = bool(prev and prev["kind"] == "data" and prev["label"].startswith("- ")
                              and not label.startswith("- ") and len(label.split()) <= 3
                              and not re.search(r"\d", label))
        if (prev and prev["kind"] in ("data", "total", "text") and label
                and (label[0].islower() or continues_paren or continues_item)):
            prev["label"] = _join([prev["label"], label])
            continue
        out.append({"kind": "section" if SECTION_RE.search(label) else "text", "label": label})
    flush_band()

    # the Notes column: headed "Notes", or holding only note numbers
    notes_col = None
    header_rows = [r for r in out if r["kind"] == "header"]
    for i in range(width):
        heads = [r["cells"][i].lower() for r in header_rows]
        values = [r["cells"][i] for r in out if r["kind"] in ("data", "total") and r["cells"][i]]
        if "notes" in heads or "schedule" in heads or (values and all(NOTE_REF_RE.match(v) for v in values)):
            notes_col = i
            break
    return {"rows": out, "width": width, "notes_col": notes_col}


def parse_page(rows, title):
    """Positioned rows of one statement page -> list of tables."""
    tables = []
    for block in _split_parts(_prepare_rows(rows, title)):
        columns, label_limit = _find_columns(block)
        if not columns:
            # no figures at all: plain text lines
            text_rows = []
            for row in block:
                if row.get("unit"):
                    continue
                text = _join(c["t"] for c in row["cells"])
                text_rows.append({"kind": "section" if SECTION_RE.search(text) else "text", "label": text})
            tables.append({"rows": text_rows, "width": 1, "notes_col": None})
            continue
        tables.append(_build_table(block, columns, label_limit))
    return tables


def column_headings_lines(rows, title):
    """For wide tables, the column headings rebuilt by position, as text for
    Claude (the " | " text rows have them jumbled)."""
    lines = []
    for table in parse_page(rows, title):
        if table["width"] < WIDE_TABLE_COLUMNS:
            continue
        for row in table["rows"]:
            if row["kind"] == "group":
                groups = [f"{s['text']} (columns spanned: {s['span']})" for s in row["segments"] if s["text"]]
                if groups:
                    lines.append("[Column groups, left to right: " + "; ".join(groups) + "]")
            elif row["kind"] == "header" and sum(1 for c in row["cells"] if c) >= WIDE_TABLE_COLUMNS:
                names = [f"{i + 1}) {c or '?'}" for i, c in enumerate(row["cells"])]
                lines.append("[Column headings, left to right: " + " | ".join(names) + "]")
    return lines


# ------------------------------------------------------------------ page model

def statement_view(statement):
    pages = []
    for page in statement["pages"]:
        pages.append({
            "pdf_page": page["pdf_page"],
            "printed": page["printed"],
            "note": (f"printed sideways; rotated {ROTATION_WORDS[page['rotation']]} before extraction"
                     if page["rotation"] else ""),
            "tables": parse_page(page.get("rows", []), statement["title"]),
        })
    return {
        "id": statement["type"],
        "title": statement["title"],
        "label": statement["label"],
        "pages": pages,
        "rotated": any(p["rotation"] for p in statement["pages"]),
    }


# ------------------------------------------------------------------ scanned reports
# Transcribed statements are Markdown (ocr_transcribe). Their tables are turned
# into the same rows as parse_page() builds - header / section / data / total /
# text - so the page styles them exactly like the text-based reports.

MD_ROW_RE = re.compile(r"^\s*\|(.*)\|\s*$")
MD_SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")
MD_TOTAL_RE = re.compile(r"^total\b", re.I)
PERIOD_LINE_RE = re.compile(
    r"^(year|years|period|six months|as of|as at|at|with comparative|for the|"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)\b)", re.I)
PAGE_NO_RE = re.compile(r"^[-–\s]*\d{1,3}[-–\s]*$")


def _md_cells(line):
    return [c.strip() for c in MD_ROW_RE.match(line).group(1).split("|")]


def _plain_label(text):
    return text.replace("**", "").strip()


def _is_bold(text):
    text = text.strip()
    return text.startswith("**") and text.endswith("**") and len(text) > 4


def _md_table(lines, tied):
    """One Markdown table -> {"rows", "width", "notes_col"} (the parse_page shape)."""
    rows = [_md_cells(line) for line in lines]
    rows = [r for r in rows if not (any(r) and all(MD_SEPARATOR_RE.match(c) for c in r if c))]
    width = max(len(r) for r in rows) - 1
    out = []
    header, body = rows[0], rows[1:]
    if any(c for c in header):
        out.append({"kind": "header", "label": _plain_label(header[0]),
                    "cells": [_plain_label(c) for c in (header[1:] + [""] * width)[:width]]})
    for row in body:
        label, cells = row[0], (row[1:] + [""] * width)[:width]
        plain = _plain_label(label)
        if not any(c.strip() for c in cells):
            if plain:
                kind = "section" if (_is_bold(label) or plain.endswith(":")) else "text"
                out.append({"kind": kind, "label": plain})
            continue
        is_total = (not plain or bool(MD_TOTAL_RE.match(plain))
                    or any(_plain_label(c) in tied for c in cells if c.strip()))
        out.append({"kind": "total" if is_total else "data", "label": plain,
                    "cells": [_plain_label(c) for c in cells]})
    notes_col = None
    header_rows = [r for r in out if r["kind"] == "header"]
    for i in range(width):
        heads = [r["cells"][i].lower() for r in header_rows]
        values = [r["cells"][i] for r in out if r["kind"] in ("data", "total") and r["cells"][i]]
        if "notes" in heads or "note" in heads or "schedule" in heads or (
                values and all(NOTE_REF_RE.match(v) for v in values)):
            notes_col = i
            break
    return {"rows": out, "width": width, "notes_col": notes_col}


def _text_block(rows):
    return {"rows": rows, "width": 1, "notes_col": None}


def markdown_tables(markdown, statement_title="", entity=""):
    """A transcribed page -> list of tables for the template.

    The page's own heading lines (company name, statement title, period) are
    dropped - the view already shows them - and so is the printed page number.
    Other lines become text rows; "### ..." headings between tables become
    section bands."""
    tied = {item["figure"].replace("**", "").strip() for item in _tie_outs(markdown)}
    lines = markdown.splitlines()
    tables, pending, block = [], [], []
    seen_table = False
    title_plain = _norm(statement_title)
    entity_plain = _norm(entity)

    def flush_text():
        if pending:
            tables.append(_text_block(list(pending)))
            pending.clear()

    for line in lines + [""]:
        if MD_ROW_RE.match(line):
            block.append(line)
            continue
        if block:
            flush_text()
            tables.append(_md_table(block, tied))
            block = []
            seen_table = True
        text = line.strip()
        if not text:
            continue
        heading = text.startswith("#")
        plain = _plain_label(text.lstrip("#").strip())
        norm = _norm(plain)
        if not seen_table and (heading or norm in (title_plain, entity_plain)
                               or (plain.isupper() and len(plain.split()) <= 8)
                               or PERIOD_LINE_RE.match(plain)):
            continue            # the page's own title block
        if PAGE_NO_RE.match(plain) or norm == title_plain:
            continue
        pending.append({"kind": "section" if heading or plain.endswith(":") else "text", "label": plain})
    flush_text()
    return tables


def _tie_outs(markdown):
    import ocr_quality  # local import: only needed for scanned reports
    return ocr_quality.tie_outs(markdown)["tied"]


def transcribed_statement_view(statement, entity=""):
    body = statement["text"].split("\n", 1)[1] if "\n" in statement["text"] else ""
    chunks = re.split(r"^--- PDF page \d+.*? ---$", body, flags=re.M)[1:]
    pages = []
    for page, chunk in zip(statement["pages"], chunks):
        pages.append({"pdf_page": page["pdf_page"], "printed": page["printed"],
                      "note": "transcribed from the page image",
                      "tables": markdown_tables(chunk, statement["title"], entity)})
    return {"id": statement["type"], "title": statement["title"], "label": statement["label"],
            "pages": pages, "rotated": False}


def report_view(index):
    """In index order: consolidated first, then standalone (scanned reports:
    the registrant first, then the other companies)."""
    sections = []
    transcribed = index.get("format") == "transcribed"
    for key, section in index["sections"].items():
        views = [transcribed_statement_view(s, section.get("entity", "")) if transcribed else statement_view(s)
                 for s in section.get("statements", [])]
        for n, v in enumerate(views):
            # scanned reports can have two statements of one type (e.g. income
            # and adjusted income), so their anchors are numbered
            v["anchor"] = f"{key}-{v['id']}-{n}" if transcribed else f"{key}-{v['id']}"
        label = section.get("label") or config.SECTION_LABELS.get(key, key)
        sections.append({"key": key, "label": label, "statements": views})
    return sections
