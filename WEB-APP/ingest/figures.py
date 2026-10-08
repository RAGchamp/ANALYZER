"""Statement lines and their figures (INFO/SPLIT-FUNCTIONALITY-PLAN.md §6.2, Phase 2).

The statement tables (ingest/tables.py) already know each row's kind (header,
data, total, ...), its columns and the Notes / Schedule column. This turns
every data and total row into a *line* with one *value* per column:

    C-PL-L023  "Current tax"  note 21  | FY2026 5,606.07 | FY2025 5,848.54

Figures keep the text as printed next to the parsed number. A figure whose
digit grouping is impossible (a retyping error such as "11,57,05.424") gets
no number, and the checks report it.
"""

import re
from collections import Counter

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}
MONTH_RE = "(" + "|".join(MONTHS) + r"|sept?|jan|feb|mar|apr|jun|jul|aug|oct|nov|dec)\.?"
DATE_MDY_RE = re.compile(MONTH_RE + r"\s+(\d{1,2}),?\s+(\d{4})", re.I)
DATE_DMY_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+" + MONTH_RE + r",?\s+(\d{4})", re.I)
YEAR_RE = re.compile(r"^(?:FY\s*)?((?:19|20)\d{2})$", re.I)
FY_RANGE_RE = re.compile(r"\bFY\s*'?((?:19|20)?\d{2})\s*[-–/]\s*'?((?:19|20)?\d{2})\b", re.I)
PREVIOUS_RE = re.compile(r"\bprevious\s+(?:year|period)\b", re.I)
YEAR_CELL_RE = re.compile(r"^(?:19|20)\d{2}$")

CURRENCY_RE = re.compile(r"^(?:Rs\.?|INR|US\$|\$|₹|`)\s*|\s*(?:Rs\.?|₹)$")
NIL_RE = re.compile(r"^(?:[-–—−]{1,2}|nil)$", re.I)
NUMBER_RE = re.compile(r"^(\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+|)(\.\d+)?$")

UNIT_PATTERNS = [
    re.compile(r"\(?\s*(?:in\s+)?(?:millions|thousands|billions) of (?:U\.?S\.?\s+)?dollars[^)\n]*\)?", re.I),
    # \b: "Rs" must not match the end of "dollars" ("[U.S. dollars in millions]")
    re.compile(r"(?:₹|\bRs\.?|\bINR)\s*in\s*(?:million|crore|lakh|lac)s?", re.I),
    re.compile(r"in\s*(?:₹|\bRs\.?|\bINR)\s*(?:million|crore|lakh|lac)s?", re.I),
    re.compile(r"(?:₹|\bRs\.?)\s*(?:million|crore|lakh)s?", re.I),
    # last, so it only names a unit nothing above found: "[U.S. dollars in millions, except …]".
    # The currency is required: BRK-1994's bare "dollars in thousands" stays as it was (no units line).
    re.compile(r"(?:U\.S\.|Canadian)\s+dollars\s+in\s+(?:millions|thousands|billions)", re.I),
    re.compile(r"\bUS\$M\b"),                                   # BHP's column unit
]
# A heading cell that only names the unit: "US$M", "$M", "₹ Million"
UNIT_CELL_RE = re.compile(r"^(?:US\$|A\$|\$|₹)\s*(?:M|m|mn|million|Million)$")


def _month(name):
    name = name.lower().rstrip(".")
    for full, number in MONTHS.items():
        if full.startswith(name[:3]):
            return number
    return None


def find_date(text):
    """(year, month, day) of the first date in text, e.g. "As at March 31, 2026"."""
    match = DATE_MDY_RE.search(text)
    if match:
        return int(match.group(3)), _month(match.group(1)), int(match.group(2))
    match = DATE_DMY_RE.search(text)
    if match:
        return int(match.group(3)), _month(match.group(2)), int(match.group(1))
    return None


def fiscal_month_day(headings, index):
    """The report's year-end (month, day): the index's, or the one most column
    headings use ("Year ended March 31, 2026" -> (3, 31))."""
    if index.get("fiscal_year_end"):
        _, month, day = (int(x) for x in index["fiscal_year_end"].split("-"))
        return month, day
    dates = Counter()
    for heading in headings:
        date = find_date(heading)
        if date and re.search(r"year ended|as at|as of|december 31|march 31", heading, re.I):
            dates[date[1:]] += 1
    return dates.most_common(1)[0][0] if dates else None


def parse_period(heading, fiscal_md=None):
    """(period end as ISO date or None, period label or None) for a column heading."""
    heading = (heading or "").strip()
    if not heading:
        return None, None
    if PREVIOUS_RE.search(heading):
        return None, "previous year"
    date = find_date(heading)
    if date:
        year, month, day = date
        iso = f"{year:04d}-{month:02d}-{day:02d}"
        return iso, (f"FY{year}" if fiscal_md == (month, day) else iso)
    match = YEAR_RE.match(heading) or re.search(r"\b((?:19|20)\d{2})$", heading)
    if match:
        year = int(match.group(1))
        iso = f"{year:04d}-{fiscal_md[0]:02d}-{fiscal_md[1]:02d}" if fiscal_md else None
        return iso, f"FY{year}"
    match = FY_RANGE_RE.search(heading)
    if match:
        end = match.group(2)
        year = int(end) if len(end) == 4 else 2000 + int(end)
        iso = f"{year:04d}-{fiscal_md[0]:02d}-{fiscal_md[1]:02d}" if fiscal_md else None
        return iso, f"FY{year}"
    return None, None


def parse_value(raw):
    """(number or None, nil) for a printed figure: "(1,828)" -> (-1828.0, False),
    "$10,622" -> (10622.0, False), "—" -> (0.0, True), "11,57,05.424" -> (None, False)."""
    text = raw.replace(" ", " ").strip()
    text = CURRENCY_RE.sub("", text).strip()
    if NIL_RE.match(text):
        return 0.0, True
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text[:1] in "-–−":
        negative, text = True, text[1:].strip()
    text = CURRENCY_RE.sub("", text).strip().rstrip("%").replace(" ", "")
    match = NUMBER_RE.match(text)
    if not match or not (match.group(1) or match.group(2)):
        return None, False
    value = float((match.group(1).replace(",", "") or "0") + (match.group(2) or ""))
    return (-value if negative else value), False


def report_units(index):
    """The unit the statements are printed in, e.g. "₹ in Million" or
    "(in millions of U.S. dollars, except per share data)"."""
    for section in index["sections"].values():
        for statement in section.get("statements", []):
            for pattern in UNIT_PATTERNS:
                match = pattern.search(statement.get("text", ""))
                if match:
                    return " ".join(match.group(0).split())
    return None


def _year_row(cells, notes_col):
    filled = [c for i, c in enumerate(cells) if i != notes_col and c.strip()]
    return bool(filled) and all(YEAR_CELL_RE.match(c.strip()) for c in filled)


def statement_lines(section_key, unit_id, view, fiscal_md, default_period=None):
    """Lines and values of one statement, from its stored view."""
    lines, values = [], []
    seq = 0
    for page in view["pages"]:
        for table_no, table in enumerate(page["tables"]):
            width, notes_col = table["width"], table["notes_col"]
            headings = [""] * width
            for row_no, row in enumerate(table["rows"]):
                cells = row.get("cells")
                if row["kind"] == "header" and cells:
                    # "Notes | US$M | US$M" under "2026 | 2025": a unit row keeps the years (BHP)
                    headings = [headings[i] if i < len(headings) and headings[i].strip()
                                and UNIT_CELL_RE.match(c.strip()) else c for i, c in enumerate(cells)]
                    continue
                if row["kind"] not in ("data", "total") or not cells:
                    continue
                if _year_row(cells, notes_col):
                    # "(in millions of U.S. dollars) | 2025 | 2024 | 2023": the column headings
                    headings = [c if c.strip() and i != notes_col else (headings[i] if i < len(headings) else "")
                                for i, c in enumerate(cells)]
                    continue
                figures = [(i, c.strip()) for i, c in enumerate(cells) if i != notes_col and c.strip()]
                if not figures:
                    continue
                seq += 1
                line_id = f"{unit_id}-L{seq:03d}"
                note_ref = cells[notes_col].strip() if notes_col is not None and notes_col < len(cells) else ""
                lines.append({"id": line_id, "section_id": section_key, "unit_id": unit_id, "seq": seq,
                              "pdf_page": page["pdf_page"], "table_no": table_no,
                              "label": row["label"] or "(total)", "kind": row["kind"],
                              "note_ref": note_ref or None, "row_no": row_no})
                for col, raw in figures:
                    heading = headings[col] if col < len(headings) else ""
                    period_end, period_label = parse_period(heading, fiscal_md)
                    if period_label is None and width == 1 and default_period:
                        period_end, period_label = default_period
                    value, nil = parse_value(raw)
                    values.append({"line_id": line_id, "col": col, "heading": heading or None,
                                   "period_end": period_end, "period_label": period_label,
                                   "raw": raw, "value": value, "nil": int(nil)})
    return lines, values


def all_lines(index, views):
    """Lines and values of every statement in the report.
    views: {(section key, unit id): view} in index order."""
    headings = [h for view in views.values() for page in view["pages"] for t in page["tables"]
                for r in t["rows"] if r["kind"] == "header" for h in r["cells"]]
    fiscal_md = fiscal_month_day(headings, index)
    lines, values = [], []
    for (key, uid), view in views.items():
        section = index["sections"][key]
        default = None
        period = section.get("period") or index.get("fiscal_year_end_label")
        date = find_date(period) if period else None
        if date:
            default = (f"{date[0]:04d}-{date[1]:02d}-{date[2]:02d}", f"FY{date[0]}")
        more_lines, more_values = statement_lines(key, uid, view, fiscal_md, default)
        lines += more_lines
        values += more_values
    return lines, values, fiscal_md
