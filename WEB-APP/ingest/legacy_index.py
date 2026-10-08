"""Index for OLD annual reports (Indian GAAP, Companies Act 1956, old Schedule VI).

See INFO/ANALYZE-OLD-REPORTS-FUNC-PLAN.md. Old reports have no "Notes to
... Financial Statements" running headers. They are organised as:

  Financial Summary, Directors' Report, Auditors' Report   (narrative sections)
  Balance Sheet, Profit and Loss Account                    (primary statements)
  SCHEDULE 'A' ... 'N' forming part of the Balance Sheet /  (schedules; the
      Profit and Loss Account                                statements cite them
  SCHEDULE 'O' Notes ... forming part of the accounts        in a Schedule column)
      1. ...  2. ...  (numbered notes, mostly untitled)

The whole document is scanned for "markers": statement titles, SCHEDULE
headings, narrative section titles, signature blocks and divider pages. Each
marker's content runs until the next marker. The result is a section in the
same shape as the modern index, whose "notes" list holds "units":

  {"id": "SCH-E", "kind": "schedule", "ref": "E", ...}
  {"id": "N25", "kind": "note", "no": "N25", "number": 25, "parent": "SCH-O", ...}
  {"id": "DIR", "kind": "narrative", ...}

Every unit has an exact extent (start page + y, end page + y) on the
(de-rotated, if needed) page, like a modern note, so the extractor can cut
pages that several schedules share.

Figures are never "corrected": retyped old reports contain transcription
errors that the analysis should flag, not hide.
"""

import re
from collections import Counter

from ingest import statements
from ingest.pdf_utils import PAGE_NUMBER_RE, page_rows

SECTION = "standalone"

# Quote marks around schedule letters come out in many forms: ‘A’ 'A' `L'
# (the backtick is turned into ₹ by pdf_utils.clean_text) and �C� (a lost
# Windows-1252 character). "��" is a lost dash, i.e. a nil figure.
QUOTE_CHARS = "'‘’`´₹�\""
_Q = "[" + re.escape(QUOTE_CHARS) + "]"
REF_IN_QUOTES_RE = re.compile(_Q + r"\s*([A-Z]{1,2}|\d{1,2})\s*" + _Q)
LOST_DASH_RE = re.compile(r"�{2,}")
STRAY_QUOTE_RE = re.compile(r"[‘’`´₹�]")

SCHEDULE_RE = re.compile(
    r"^(?i:schedule)\s*'?\s*([A-Z]{1,2}|\d{1,2}|[IVXL]{1,5})\s*'?"
    r"(?:\s*[:.\-–]?\s+(\S.*))?$")
RUNNING_PART_RE = re.compile(
    r"schedules?\s+(?:annexed to and\s+)?forming\s+part\s+of\s+the\s+"
    r"(balance sheet|profit\s*(?:and|&)\s*loss account|accounts)", re.I)
NOTES_TITLE_RE = re.compile(r"\bnotes?\b", re.I)
NOTES_SECTION_RE = re.compile(r"^notes\s+(?:on|to|forming part of)\s+(?:the\s+)?accounts\b", re.I)
SIGNATURE_RE = re.compile(r"^as per our (?:attached )?report", re.I)
NUMBER_RE = re.compile(r"\d[\d,]*[\d.]\d|\d{2,}")
NOTE_ITEM_RE = re.compile(r"^(\d{1,2})\s*\.(?:\s+|$)(.*)$")
CAPTION_RE = re.compile(r"^([A-Z][A-Z0-9&,'()/\-. ]{3,80}?)\s*:\s*(.*)$")
TITLE_SKIP_RE = re.compile(r"^(previous year|as at|as on|rs\.?|year ended|\d{4}\b|\(?rs\. in)", re.I)
TRAILING_RS_RE = re.compile(r"(\s+Rs\.?)+\s*$")
TITLE_CUT_RE = re.compile(r"\s+(forming part of|for the (year|period) ended|as at)\b.*$", re.I)
DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})", re.I)
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]

LEGACY_STATEMENTS = [
    ("balance_sheet", "Balance Sheet", re.compile(r"^balance sheet\s+as\s+(?:at|on)\b")),
    ("profit_loss", "Profit and Loss Account",
     re.compile(r"^profit\s*(?:and|&)\s*loss account\b")),
    ("cash_flow", "Cash Flow Statement",
     re.compile(r"^(?:cash flow statement|statement of cash flows?|funds flow statement"
                r"|statement of (?:sources and applications|changes in financial position))")),
]
LEGACY_LABELS = {k: label for k, label, _ in LEGACY_STATEMENTS}
ALL_STATEMENTS = ["Profit and Loss Account", "Balance Sheet", "Cash Flow Statement",
                  "Statement of Changes in Equity"]

NARRATIVES = [
    ("SUM", "Financial Summary",
     re.compile(r"^(?:financial (?:summary|highlights|statistics)|ten[- ]year|10[- ]year)")),
    ("DIR", "Directors' Report",
     re.compile(r"^(?:directors'? report|report of the (?:board of )?directors)")),
    ("AUD", "Auditors' Report", re.compile(r"^(?:auditors?'? report|report of the auditors?)")),
    ("CHR", "Chairman's Statement",
     re.compile(r"^chairman'?s (?:statement|speech|address|letter|review)")),
]
NARRATIVE_TITLES = {key: title for key, title, _ in NARRATIVES}

TITLE_MIN_SIZE = 10.5   # statement / narrative titles (11-13pt in the sample)
MAX_NOTE_STEP = 3
MAX_SUBHEADINGS = 15
DIGEST_CHARS = 220


def legacy_clean(text):
    """Normalise the old-report text quirks. Figures are left untouched."""
    text = LOST_DASH_RE.sub("–", text)
    text = REF_IN_QUOTES_RE.sub(r"'\1'", text)
    text = STRAY_QUOTE_RE.sub("'", text)
    return text.replace("––", "–")


def _plain(text):
    return " ".join(text.replace("’", "'").lower().split())


SMALL_WORDS = {"and", "or", "of", "the", "to", "in", "for", "on", "at", "by", "as", "with", "from", "&"}


def _title_case(text):
    """Title-case the ALL-CAPS words ("INVESTMENTS (at Cost)" -> "Investments (at Cost)")."""
    out = []
    for i, word in enumerate(text.split()):
        letters = re.sub(r"[^A-Za-z]", "", word)
        if len(letters) > 1 and letters.isupper() and "." not in word:
            lower = word.lower()
            word = lower if (i and lower in SMALL_WORDS) else lower[:1].upper() + lower[1:]
        out.append(word)
    return " ".join(out)


def _clean_title(text):
    text = TRAILING_RS_RE.sub("", text)
    text = TITLE_CUT_RE.sub("", text)
    return _title_case(text.strip(" :.-–"))


# ------------------------------------------------------------------ pages

def _footer(rows, height):
    """(printed page number, its y). Only the page number is cut off: in old
    reports, signature blocks and table rows run close to the bottom edge."""
    for row in rows:
        if row["y0"] > height * 0.9 and PAGE_NUMBER_RE.match(row["text"]):
            return row["text"], row["y0"]
    return None, height


def _read_page(doc, pno):
    """Rows of one page, de-rotated if it is printed sideways, plus layout."""
    page = doc[pno - 1]
    rotation = statements.DERO_ANGLES.get(statements.dominant_direction(page), 0)
    temp = None
    try:
        if rotation:
            temp, work = statements.derotated_page(doc, pno, rotation)
        else:
            work = page
        rows = page_rows(work, horizontal_only=True)
        height = work.rect.height
        for row in rows:
            row["text"] = legacy_clean(row["text"])
        if rotation:
            # The page number is upright on the original page; on the turned
            # page it is sideways and was dropped with the running header.
            printed, _ = _footer(page_rows(page, horizontal_only=True), page.rect.height)
            bottom = height
        else:
            printed, bottom = _footer(rows, height)
    finally:
        if temp is not None:
            temp.close()
    header = [r for r in rows if r["y1"] < height * 0.12]
    top = max((r["y1"] for r in header), default=0.0)
    body = [r for r in rows if top < r["y0"] and r["y1"] <= bottom + 0.5]
    # "SCHEDULE FORMING PART OF THE BALANCE SHEET" repeats on every schedule
    # page: it is part of the page header, and says what the schedules belong to.
    part_of = None
    for row in body[:2]:
        match = RUNNING_PART_RE.search(row["text"])
        if match and not SCHEDULE_RE.match(row["text"]):
            part = match.group(1).lower()
            part_of = ("Balance Sheet" if part.startswith("balance")
                       else "Profit and Loss Account" if part.startswith("profit") else "Accounts")
            top = row["y1"]
            body = [r for r in body if r["y0"] > top]
            break
    return {
        "rows": body,
        "layout": {
            "part_of": part_of,
            "top": round(top, 1),
            "bottom": round(bottom, 1),
            "body_top": round(min((r["y0"] for r in body), default=top), 1),
            "printed": printed,
            "rotation": rotation,
        },
    }


# ------------------------------------------------------------------ markers

def _statement_type(text):
    plain = _plain(text)
    for key, _, pattern in LEGACY_STATEMENTS:
        if pattern.match(plain):
            return key
    return None


def _narrative(text):
    plain = _plain(text)
    for key, _, pattern in NARRATIVES:
        if pattern.match(plain):
            return key
    return None


def _find_markers(pages):
    """Every place where a new unit starts (or content ends), in page order."""
    markers = []
    for pno, page in pages.items():
        rows, layout = page["rows"], page["layout"]
        numbers = sum(len(NUMBER_RE.findall(r["text"])) for r in rows)
        if len(rows) <= 6 and numbers < 3:
            # divider page ("BALANCE SHEET AND PROFIT AND LOSS ACCOUNT"), cover
            markers.append({"kind": "end", "page": pno, "y": layout["body_top"]})
            continue
        height_limit = layout["body_top"] + 0.35 * (layout["bottom"] - layout["top"])
        for i, row in enumerate(rows):
            text = row["text"].strip()
            if SIGNATURE_RE.match(text):
                markers.append({"kind": "end", "page": pno, "y": row["y0"]})
                continue
            if not row["bold"]:
                continue
            kind = _statement_type(text)
            if kind and row["size"] >= TITLE_MIN_SIZE and row["y0"] <= height_limit and numbers >= 6:
                markers.append({"kind": "statement", "type": kind, "title": text,
                                "page": pno, "y": row["y0"], "row": i})
                continue
            match = SCHEDULE_RE.match(text)
            if match:
                markers.append({"kind": "schedule", "ref": match.group(1).upper(),
                                "inline_title": match.group(2) or "", "page": pno,
                                "y": row["y0"], "row": i})
                continue
            if NOTES_SECTION_RE.match(_plain(text)):
                markers.append({"kind": "notes_section", "title": text, "page": pno,
                                "y": row["y0"], "row": i})
                continue
            key = _narrative(text)
            if key and row["size"] >= TITLE_MIN_SIZE and row["y0"] - layout["body_top"] < 60:
                markers.append({"kind": "narrative", "key": key, "page": pno,
                                "y": row["y0"], "row": i})
    markers.sort(key=lambda m: (m["page"], m["y"]))

    # Keep the first occurrence of each schedule / narrative / statement; a
    # repeated statement title ("(Contd.)" pages) continues the same unit.
    seen, kept = set(), []
    for marker in markers:
        ident = (marker["kind"], marker.get("ref") or marker.get("key") or marker.get("type"))
        if marker["kind"] != "end" and ident in seen:
            continue
        seen.add(ident)
        if (kept and kept[-1]["kind"] == "notes_section" and marker["kind"] == "schedule"
                and marker["page"] == kept[-1]["page"] and 0 <= marker["y"] - kept[-1]["y"] < 40):
            continue
        if (kept and marker["kind"] == "notes_section" and kept[-1]["kind"] == "schedule"
                and marker["page"] == kept[-1]["page"] and 0 <= marker["y"] - kept[-1]["y"] < 60):
            kept[-1]["inline_title"] = kept[-1]["inline_title"] or marker["title"]
            continue
        kept.append(marker)
    return kept


def _set_extents(units, markers, pages, last_page):
    """Each unit ends where the next marker starts (the same rule as modern
    notes: a marker at the very top of a page ends the unit on the page before)."""
    for unit, marker in units:
        idx = markers.index(marker)
        nxt = markers[idx + 1] if idx + 1 < len(markers) else None
        if nxt is None:
            unit["end_page"], unit["end_y"] = last_page, None
        elif nxt["y"] <= pages[nxt["page"]]["layout"]["body_top"] + 3:
            unit["end_page"] = max(unit["start_page"], nxt["page"] - 1)
            unit["end_y"] = None
        else:
            unit["end_page"], unit["end_y"] = nxt["page"], round(nxt["y"], 1)
        start = pages[unit["start_page"]]["layout"]["printed"]
        end = pages[unit["end_page"]]["layout"]["printed"]
        unit["printed_pages"] = (f"{start}" if start == end else f"{start}-{end}") if start and end else None


def _rows_in(unit, pages):
    """(page, row) pairs inside a unit's extent, in reading order."""
    for pno in range(unit["start_page"], unit["end_page"] + 1):
        for row in pages[pno]["rows"]:
            if pno == unit["start_page"] and row["y0"] < unit["start_y"] - 0.5:
                continue
            if pno == unit["end_page"] and unit["end_y"] is not None and row["y0"] >= unit["end_y"] - 0.5:
                continue
            yield pno, row


# ------------------------------------------------------------------ units

def _schedule_refs(statement_units, pages):
    """Schedule letter -> the statement line that cites it, e.g. 'E' -> "Fixed Assets"."""
    refs = {}
    for unit in statement_units:
        for _, row in _rows_in(unit, pages):
            match = re.search(r"^(?:\d+\.?\s*|[a-z]\)\s*)?(.*?)\s*'([A-Z]{1,2}|\d{1,2})'", row["text"])
            if match and match.group(1) and match.group(2) not in refs:
                label = _clean_title(match.group(1))
                if len(label) > 2:
                    refs[match.group(2)] = {"label": label, "statement": unit["label"]}
    return refs


def _schedule_title(marker, pages, refs):
    if marker["inline_title"]:
        return _clean_title(marker["inline_title"])
    rows = pages[marker["page"]]["rows"]
    for row in rows[marker["row"] + 1:]:
        if row["y0"] - marker["y"] > 90:
            break
        if SCHEDULE_RE.match(row["text"]) or not row["bold"] or TITLE_SKIP_RE.match(row["text"]):
            continue
        title = _clean_title(row["text"])
        if len(title) > 2:
            return title
    ref = refs.get(marker["ref"])
    return ref["label"] if ref else f"Schedule '{marker['ref']}'"


def _subheadings(unit, pages, skip):
    subs = []
    for _, row in _rows_in(unit, pages):
        text = row["text"].strip()
        if _clean_title(text) == unit.get("title"):
            continue
        if (row["bold"] and len(text) >= 5 and text not in skip and _clean_title(text)[:100] not in subs
                and not SCHEDULE_RE.match(text) and not RUNNING_PART_RE.search(text)
                and not TITLE_SKIP_RE.match(text) and len(re.findall(r"[A-Za-z]{3,}", text)) >= 1):
            subs.append(_clean_title(text)[:100])
        if len(subs) >= MAX_SUBHEADINGS:
            break
    return subs


def _note_items(container, pages):
    """Numbered notes (1., 2., ...) at the left margin of a notes schedule."""
    rows = list(_rows_in(container, pages))
    if not rows:
        return []
    left = min(r["x0"] for _, r in rows)
    starts, last = [], 0
    for i, (pno, row) in enumerate(rows):
        match = NOTE_ITEM_RE.match(row["text"])
        if not match or row["x0"] > left + 12:
            continue
        number = int(match.group(1))
        if not last < number <= last + MAX_NOTE_STEP:
            continue
        starts.append((i, number, match.group(2).strip()))
        last = number

    items = []
    for k, (i, number, rest) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(rows)
        text = " ".join([rest] + [r["text"] for _, r in rows[i + 1:end]]).strip()
        pno, row = rows[i]
        caption = CAPTION_RE.match(rest)
        if caption and caption.group(1).upper() == caption.group(1):
            title, excerpt = _clean_title(caption.group(1)), False
        else:
            words = text.split()
            title = " ".join(words[:12]) + ("…" if len(words) > 12 else "")
            excerpt = True
        items.append({
            "id": f"N{number}", "no": f"N{number}", "number": number, "kind": "note",
            "title": title or f"Note {number}", "title_is_excerpt": excerpt,
            "parent": container["id"], "start_page": pno, "start_y": round(row["y0"], 1),
            "digest": text[:DIGEST_CHARS], "subheadings": [],
        })
    # A note ends where the next one starts; the last ends with its schedule.
    for item, nxt in zip(items, items[1:] + [None]):
        if nxt is None:
            item["end_page"], item["end_y"] = container["end_page"], container["end_y"]
        elif nxt["start_y"] <= pages[nxt["start_page"]]["layout"]["body_top"] + 3:
            item["end_page"], item["end_y"] = max(item["start_page"], nxt["start_page"] - 1), None
        else:
            item["end_page"], item["end_y"] = nxt["start_page"], nxt["start_y"]
        start = pages[item["start_page"]]["layout"]["printed"]
        end = pages[item["end_page"]]["layout"]["printed"]
        item["printed_pages"] = (f"{start}" if start == end else f"{start}-{end}") if start and end else None
    return items


def _fiscal_year_end(statement_units):
    for unit in statement_units:
        match = DATE_RE.search(unit["title"])
        if match and match.group(2).lower() in MONTHS:
            day, month, year = int(match.group(1)), MONTHS.index(match.group(2).lower()) + 1, int(match.group(3))
            return f"{year:04d}-{month:02d}-{day:02d}", f"{day} {MONTHS[month - 1].title()} {year}"
    return None, None


def unit_label(unit):
    """Short display label: "Schedule 'E' — Fixed Assets", "Note 25 — ..."."""
    if unit["kind"] == "schedule":
        return f"Schedule '{unit['ref']}' — {unit['title']}" if unit.get("ref") else unit["title"]
    if unit["kind"] == "note":
        return f"Note {unit['number']} — {unit['title']}"
    return unit["title"]


def unit_heading(unit):
    """Header line for the extracted text sent to Claude."""
    if unit["kind"] == "schedule" and unit.get("part_of"):
        return f"{unit_label(unit)} (forming part of the {unit['part_of']})"
    if unit["kind"] == "note" and unit.get("parent_title"):
        return f"{unit_label(unit)} (in {unit['parent_title']})"
    return unit_label(unit)


# ------------------------------------------------------------------ index

def build_legacy_section(doc):
    """Returns (section dict for "standalone", page layouts, report facts)."""
    pages = {pno: _read_page(doc, pno) for pno in range(1, doc.page_count + 1)}
    markers = _find_markers(pages)
    last_page = doc.page_count

    statement_units, schedule_units, narrative_units = [], [], []
    for marker in markers:
        base = {"start_page": marker["page"], "start_y": round(marker["y"], 1),
                "end_page": None, "end_y": None}
        if marker["kind"] == "statement":
            statement_units.append(({**base, "type": marker["type"], "title": marker["title"],
                                     "label": LEGACY_LABELS[marker["type"]]}, marker))
        elif marker["kind"] in ("schedule", "notes_section"):
            schedule_units.append((base, marker))
        elif marker["kind"] == "narrative":
            narrative_units.append(({**base, "id": marker["key"], "no": marker["key"],
                                     "kind": "narrative",
                                     "title": NARRATIVE_TITLES[marker["key"]]}, marker))
    _set_extents(statement_units + schedule_units + narrative_units, markers, pages, last_page)

    statement_list = [u for u, _ in statement_units]
    refs = _schedule_refs(statement_list, pages)

    units = []
    for unit, marker in schedule_units:
        if marker["kind"] == "notes_section":
            unit.update(id="NOTES", no="NOTES", kind="schedule", ref=None,
                        title=_clean_title(marker["title"]))
        else:
            ref = marker["ref"]
            unit.update(id=f"SCH-{ref}", no=f"SCH-{ref}", kind="schedule", ref=ref,
                        title=_schedule_title(marker, pages, refs))
            part = (pages[unit["start_page"]]["layout"]["part_of"]
                    or (refs.get(ref) or {}).get("statement"))
            if NOTES_TITLE_RE.search(unit["title"]):
                part = "Accounts"
            if part:
                unit["part_of"] = part
        unit["title_is_excerpt"] = False
        unit["subheadings"] = _subheadings(unit, pages, skip={marker.get("title", "")})
        units.append(unit)
        if unit["id"] == "NOTES" or NOTES_TITLE_RE.search(unit["title"]):
            for item in _note_items(unit, pages):
                item["parent_title"] = unit_label(unit)
                units.append(item)
    for unit, _ in narrative_units:
        unit["title_is_excerpt"] = False
        unit["subheadings"] = _subheadings(unit, pages, skip=set())
        units.append(unit)
    for unit in units:
        unit["label"] = unit_label(unit)

    # Statements in the modern shape, so statements.extract_statement works.
    statement_entries = []
    for unit in statement_list:
        page_infos = []
        for pno in range(unit["start_page"], unit["end_page"] + 1):
            layout = pages[pno]["layout"]
            page_infos.append({
                "pdf_page": pno, "printed": layout["printed"], "rotation": layout["rotation"],
                "top": unit["start_y"] - 1 if pno == unit["start_page"] else layout["top"],
                "bottom": (unit["end_y"] - 1 if pno == unit["end_page"] and unit["end_y"] is not None
                           else layout["bottom"]),
            })
        title = _title_case(unit["title"]) if unit["title"].isupper() else unit["title"]
        statement_entries.append({"type": unit["type"], "label": unit["label"],
                                  "title": title, "pages": page_infos})
    order = {"profit_loss": 0, "balance_sheet": 1, "cash_flow": 2}
    statement_entries.sort(key=lambda s: order[s["type"]])

    body_pages = sorted({p for u in units for p in range(u["start_page"], u["end_page"] + 1)})
    section = {
        "label": "Standalone",
        "first_page": body_pages[0] if body_pages else 1,
        "last_page": body_pages[-1] if body_pages else doc.page_count,
        "page_count": len(body_pages),
        "notes": units,
        "statements": statement_entries,
        "schedule_refs": refs,
    }
    fy_iso, fy_label = _fiscal_year_end(statement_list)
    present = {LEGACY_LABELS[s["type"]] for s in statement_entries}
    facts = {
        "fiscal_year_end": fy_iso,
        "fiscal_year_end_label": fy_label,
        "currency": "Rs.",
        "statements_present": [s["label"] for s in statement_entries],
        "statements_missing": [s for s in ALL_STATEMENTS if s not in present],
        "unit_counts": dict(Counter(u["kind"] for u in units)),
    }
    layouts = {pno: page["layout"] for pno, page in pages.items()}
    return section, layouts, facts


def summary(index):
    section = index["sections"].get(SECTION)
    if not section:
        return "No schedules or notes found"
    counts = Counter(u["kind"] for u in section["notes"])
    parts = ["Old-format report (Companies Act 1956): standalone accounts"]
    if index.get("fiscal_year_end_label"):
        parts.append(f"year ended {index['fiscal_year_end_label']}")
    parts.append(f"{counts.get('schedule', 0)} schedules")
    parts.append(f"{counts.get('note', 0)} notes")
    if counts.get("narrative"):
        parts.append(f"{counts['narrative']} report sections")
    return " · ".join(parts)
