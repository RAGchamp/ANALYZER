"""Index for scanned reports transcribed by Claude (INFO/OCR-CAPABILITY-PLAN.md §5).

Input: the cached page transcriptions (ocr_transcribe): per page a JSON
description (page_type, entity, statement_title, schedule, headings, period,
currency) and the page as Markdown. There is no PDF geometry, so units have
(page, line) extents in the page Markdown instead of (page, y).

- Entities become sections: a 10-K can hold the registrant's consolidated
  statements plus subsidiaries' own statements (Berkshire Hathaway 1968:
  the registrant, National Indemnity Company, National Fire & Marine).
  The registrant (the entity of the first statement) comes first.
- Statements: statement pages grouped by title.
- Notes: "### (1) Basis of Consolidation" / "### 1. ..." / "### Note 1 ..."
  headings on notes pages, in number order.
- Schedules: schedule pages grouped by their label (Schedule V, Schedule 'E').
- Narratives: the accountants'/auditors' report(s) and the filing text
  (cover, items, index) per entity.
"""

import re
from collections import Counter, OrderedDict

from ingest import ocr_transcribe

NOTE_HEAD_RE = re.compile(
    r"^#{1,4}\s*(?:\(\s*(\d{1,2})\s*\)|(\d{1,2})\s*[.)]|note\s+(\d{1,2})\b[.:\-–—]?)\s*(.*)$", re.I)
SCHEDULE_LABEL_RE = re.compile(r"schedule\s*[‘’'`\"]?\s*([A-Z]{1,2}|\d{1,2}|[IVXL]{1,6})\b", re.I)
GENERIC_WORDS = {"inc", "inc.", "company", "co", "co.", "corporation", "corp", "corp.", "ltd", "ltd.",
                 "limited", "the", "of", "and", "&", "insurance", "incorporated"}
MAX_NOTE_STEP = 3

STATEMENT_KINDS = [
    ("balance_sheet", re.compile(r"balance sheet|assets and liabilities|financial (position|condition)", re.I)),
    ("cash_flow", re.compile(r"source and application|sources and uses|changes in financial position|cash flow|funds",
                             re.I)),
    ("profit_loss", re.compile(r"earnings|income|operations|profit and loss|profit & loss", re.I)),
    ("equity", re.compile(r"surplus|stockholders|shareholders|retained|equity|capital", re.I)),
]
TYPE_LABELS = {"profit_loss": "Income statement", "balance_sheet": "Balance sheet",
               "equity": "Statement of surplus / equity", "cash_flow": "Funds statement"}
STATEMENT_ORDER = ["profit_loss", "balance_sheet", "cash_flow", "equity"]
ALL_STATEMENTS = ["Income statement", "Balance sheet", "Funds statement", "Statement of surplus / equity"]


# ------------------------------------------------------------------ helpers

def _norm_entity(name):
    return " ".join(re.sub(r"[^a-z0-9& ]", " ", (name or "").lower()).split())


def _key(name):
    words = [w for w in _norm_entity(name).split() if w not in GENERIC_WORDS]
    return "-".join(words) or "report"


def entity_code(name, taken):
    """"Berkshire Hathaway Inc." -> "BH"; "National Fire & Marine Insurance Company" -> "NFMI"."""
    words = [w for w in re.split(r"[\s&]+", name or "") if w and w.lower().strip(".") not in
             {"inc", "company", "co", "corporation", "corp", "ltd", "limited", "the", "of", "and"}]
    code = "".join(w[0].upper() for w in words)[:5] or "E"
    base, n = code, 2
    while code in taken:
        code, n = f"{base}{n}", n + 1
    return code


def statement_kind(title):
    for kind, pattern in STATEMENT_KINDS:
        if pattern.search(title or ""):
            return kind
    return "profit_loss" if title else None


def _schedule_ref(header):
    for text in [header.get("schedule")] + list(header.get("headings") or []):
        match = SCHEDULE_LABEL_RE.search(text or "")
        if match:
            return match.group(1).upper()
    return None


def _schedule_title(header, markdown, ref):
    """The first heading that isn't the company, the schedule label or a date line."""
    entity = _norm_entity(header.get("entity"))
    for text in list(header.get("headings") or []) + [ln.lstrip("# ").strip() for ln in markdown.splitlines()
                                                         if ln.startswith("#")]:
        plain = _norm_entity(text)
        if (not plain or plain == entity or SCHEDULE_LABEL_RE.fullmatch(text.strip(" .:")) or
                re.match(r"^(year|years|period|as of|as at|december|january|june|september|march)", plain)):
            continue
        if SCHEDULE_LABEL_RE.match(text.strip()):
            rest = SCHEDULE_LABEL_RE.sub("", text, count=1).strip(" .:-–—")
            if rest:
                return rest
            continue
        return text.strip(" :")
    return f"Schedule {ref}"


def _printed(pages, start, end):
    first, last = pages[start].get("printed_page"), pages[end].get("printed_page")
    if first and last:
        return first if first == last else f"{first}-{last}"
    return None


def unit_label(unit):
    if unit["kind"] == "note":
        return f"Note {unit['number']} — {unit['title']}" if unit["title"] else f"Note {unit['number']}"
    if unit["kind"] == "schedule":
        return f"Schedule {unit['ref']} — {unit['title']}"
    return unit["title"]


# ------------------------------------------------------------------ entities

def _assign_entities(pages):
    """Page -> entity name. Pages without one inherit the one before; an
    exhibit cover starts a new entity."""
    current, out = None, {}
    for pno, header, _ in pages:
        name = header.get("entity")
        if name and _norm_entity(name):
            current = name
        out[pno] = current
    # Pages before the first named page belong to the first entity.
    first = next((e for e in out.values() if e), None)
    return {p: e or first for p, e in out.items()}


# ------------------------------------------------------------------ units per entity

def _notes_units(entity_pages, lines):
    heads, last = [], 0
    for pno, header, _ in entity_pages:
        if header.get("page_type") != "notes":
            continue
        for i, line in enumerate(lines[pno]):
            match = NOTE_HEAD_RE.match(line.strip())
            if not match:
                continue
            number = int(next(g for g in match.groups()[:3] if g))
            if not last < number <= last + MAX_NOTE_STEP:
                continue
            heads.append((pno, i, number, match.group(4).strip(" .:—–-")))
            last = number
    units = []
    for k, (pno, line, number, title) in enumerate(heads):
        if k + 1 < len(heads):
            end_page, end_line = heads[k + 1][0], heads[k + 1][1]
        else:
            notes_pages = [p for p, h, _ in entity_pages if h.get("page_type") == "notes" and p >= pno]
            # the last note runs to the end of the last consecutive notes page
            end_page = pno
            for p in notes_pages:
                if p - end_page <= 1:
                    end_page = p
            end_line = None
        units.append({"kind": "note", "number": number, "title": title,
                      "start_page": pno, "start_line": line, "end_page": end_page, "end_line": end_line})
    return units


def _grouped(entity_pages, pick):
    """Consecutive pages with the same key (None continues the previous group)."""
    groups = []
    for pno, header, markdown in entity_pages:
        key = pick(header, markdown)
        if key is False:
            continue
        if key is None and groups and groups[-1]["pages"][-1] == pno - 1:
            groups[-1]["pages"].append(pno)
            continue
        if key is None:
            continue
        if groups and groups[-1]["key"] == key and groups[-1]["pages"][-1] >= pno - 2:
            groups[-1]["pages"].append(pno)
        else:
            groups.append({"key": key, "pages": [pno], "header": header, "markdown": markdown})
    return groups


def _digest(unit, lines):
    parts = []
    for pno in range(unit["start_page"], unit["end_page"] + 1):
        if pno not in lines:
            continue
        chunk = lines[pno]
        start = unit["start_line"] + 1 if pno == unit["start_page"] else 0
        end = unit["end_line"] if pno == unit["end_page"] and unit["end_line"] is not None else len(chunk)
        parts.extend(chunk[start:end])
        if sum(len(p) for p in parts) > 400:
            break
    text = " ".join(" ".join(parts).replace("|", " ").split())
    return text[:220]


def build_transcribed_index(pdf_path):
    """Returns (sections, facts, pages info). Sections are keyed by entity."""
    pages = ocr_transcribe.transcribed_pages(pdf_path)
    headers = {pno: h for pno, h, _ in pages}
    lines = {pno: md.splitlines() for pno, _, md in pages}
    content = [(p, h, md) for p, h, md in pages if h.get("page_type") != "blank"]
    entity_of = _assign_entities(content)

    # the registrant: entity of the first statement page (else the most common)
    first_statement = next((p for p, h, _ in content if h.get("page_type") == "statement"), None)
    counts = Counter(entity_of.values())
    registrant = entity_of.get(first_statement) or (counts.most_common(1)[0][0] if counts else "Report")

    by_entity = OrderedDict()
    by_entity[_key(registrant)] = {"name": registrant, "pages": []}
    for item in content:
        name = entity_of[item[0]] or registrant
        by_entity.setdefault(_key(name), {"name": name, "pages": []})["pages"].append(item)
    by_entity = OrderedDict((k, v) for k, v in by_entity.items() if v["pages"])
    # One spelling per entity: the most common, preferring "Mixed Case" over
    # an exhibit cover's "ALL CAPITALS".
    for entity in by_entity.values():
        names = Counter(h.get("entity") for _, h, _ in entity["pages"] if h.get("entity"))
        if names:
            entity["name"] = max(names, key=lambda n: (not n.isupper(), names[n]))

    codes, sections = [], OrderedDict()
    multi = len(by_entity) > 1
    for key, entity in by_entity.items():
        name, entity_pages = entity["name"], entity["pages"]
        code = entity_code(name, codes)
        codes.append(code)
        prefix = f"{code}-" if multi else ""

        statements = []
        for group in _grouped(entity_pages, lambda h, md: (h.get("statement_title") or None)
                              if h.get("page_type") == "statement" else False):
            title = group["key"]
            kind = statement_kind(title)
            text_parts = [f"=== {title} ({name}) ==="]
            page_infos = []
            for pno in group["pages"]:
                printed = headers[pno].get("printed_page")
                label = f"PDF page {pno}" + (f" (printed page {printed})" if printed else "")
                text_parts.append(f"--- {label} ---\n" + "\n".join(lines[pno]).strip())
                page_infos.append({"pdf_page": pno, "printed": printed, "rotation": 0})
            statements.append({"type": kind, "label": TYPE_LABELS.get(kind, "Statement"), "title": title,
                               "pages": page_infos, "text": "\n".join(text_parts)})

        units = []
        for note in _notes_units(entity_pages, lines):
            note.update(id=f"{prefix}N{note['number']}", title_is_excerpt=False)
            units.append(note)
        for group in _grouped(entity_pages, lambda h, md: _schedule_ref(h) or None
                              if h.get("page_type") == "schedule" else False):
            ref = group["key"]
            units.append({"kind": "schedule", "ref": ref, "id": f"{prefix}SCH-{ref}",
                          "title": _schedule_title(group["header"], group["markdown"], ref),
                          "start_page": group["pages"][0], "start_line": 0,
                          "end_page": group["pages"][-1], "end_line": None, "title_is_excerpt": False})
        narrative_kinds = [("AUD", "Accountants' / auditors' report", ("auditors_report",)),
                           ("TXT", "Filing text (cover, items, index, letters)", ("cover", "index", "narrative"))]
        for code_id, title, types in narrative_kinds:
            for n, group in enumerate(_grouped(entity_pages, lambda h, md, t=types: "x"
                                               if h.get("page_type") in t else False), 1):
                suffix = "" if n == 1 else str(n)
                units.append({"kind": "narrative", "id": f"{prefix}{code_id}{suffix}", "title": title,
                              "start_page": group["pages"][0], "start_line": 0,
                              "end_page": group["pages"][-1], "end_line": None, "title_is_excerpt": False})
        for unit in units:
            unit["no"] = unit["id"]
            unit["entity"] = name
            unit["label"] = unit_label(unit)
            unit["printed_pages"] = _printed(headers, unit["start_page"], unit["end_page"])
            unit["subheadings"] = []
            unit["digest"] = _digest(unit, lines)
            unit["parent"] = None

        consolidated = any("consolidated" in (s["title"] or "").lower() for s in statements)
        present = {s["label"] for s in statements}
        page_numbers = [p for p, _, _ in entity_pages]
        sections[key] = {
            "label": name + (" (consolidated)" if consolidated else ""),
            "entity": name,
            "code": code,
            "is_registrant": _key(name) == _key(registrant),
            "first_page": min(page_numbers),
            "last_page": max(page_numbers),
            "page_count": len(page_numbers),
            "notes": sorted(units, key=lambda u: (u["start_page"], u.get("start_line") or 0)),
            "statements": sorted(statements, key=lambda s: STATEMENT_ORDER.index(s["type"])),
            "statements_present": [s["title"] for s in statements],
            "statements_missing": [t for t in ALL_STATEMENTS if t not in present],
            "period": next((h.get("period") for p, h, _ in entity_pages
                            if h.get("page_type") == "statement" and h.get("period")), None),
        }

    currency = Counter(h.get("currency") for _, h, _ in content if h.get("currency")).most_common(1)
    registrant_section = next(iter(sections.values()), {})
    facts = {
        "entities": [s["label"] for s in sections.values()],
        "currency": currency[0][0] if currency else None,
        "fiscal_year_end_label": _period_end(registrant_section.get("period")),
        "statements_present": registrant_section.get("statements_present", []),
        "statements_missing": registrant_section.get("statements_missing", []),
        "document_type": _document_type(pages),
    }
    page_info = {str(p): {"printed": h.get("printed_page"), "page_type": h.get("page_type"),
                          "entity": entity_of.get(p)} for p, h, _ in pages}
    return sections, facts, page_info


def _period_end(period):
    if not period:
        return None
    match = re.search(r"([A-Z][a-z]+\.?\s+\d{1,2},\s*\d{4})", period)
    return match.group(1) if match else period


def _document_type(pages):
    text = " ".join(" ".join(h.get("headings") or []) for _, h, _ in pages[:6]).lower()
    if "10-k" in text:
        return "SEC Form 10-K"
    if "annual report" in text:
        return "Annual report"
    return "Report"


def summary(index):
    parts = [f"Scanned report, transcribed ({index.get('ocr', {}).get('pages', 0)} pages)"]
    for section in index["sections"].values():
        kinds = Counter(u["kind"] for u in section["notes"])
        bits = [f"{len(section['statements'])} statements", f"{kinds.get('note', 0)} notes"]
        if kinds.get("schedule"):
            bits.append(f"{kinds['schedule']} schedules")
        parts.append(f"{section['label']}: " + ", ".join(bits))
    return " · ".join(parts)


# ------------------------------------------------------------------ extraction

def unit_text(pdf_path, index, unit):
    """The unit's lines from the cached page Markdown, with page labels."""
    parts = []
    for pno in range(unit["start_page"], unit["end_page"] + 1):
        info = index["pages"].get(str(pno))
        if not info or info.get("page_type") == "blank":
            continue
        chunk = ocr_transcribe.page_markdown(pdf_path, pno).splitlines()
        start = (unit.get("start_line") or 0) if pno == unit["start_page"] else 0
        end = (unit["end_line"] if pno == unit["end_page"] and unit.get("end_line") is not None
               else len(chunk))
        body = "\n".join(chunk[start:end]).strip()
        if not body:
            continue
        printed = info.get("printed")
        parts.append(f"--- PDF page {pno}" + (f" (printed page {printed})" if printed else "")
                     + " ---\n" + body)
    return "\n\n".join(parts)
