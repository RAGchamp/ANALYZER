"""The formatted document of a model document (INFO/MODEL-FORMATTED-REPORTS-PLAN.md):
the whole report as the app extracted it, as one readable HTML page.

Everything comes from the report package - the data the Analyzer sends to
Claude - in PDF page order: the statements from their stored tables, every
note / schedule / narrative unit from its clipped text, and the pages no unit
covers from their page text. The text is only *displayed* differently
(tables, headings, paragraphs); no word or figure is changed.

A model with no rules has no package: its raw PDF text is shown, labelled.
"""

import re
import threading
from collections import OrderedDict, defaultdict
from urllib.parse import urlencode

from ingest import tables

FORMATTER_VERSION = 3     # 2: report sections and page blocks (narrative pages); 3: passage ids

# ---------------------------------------------------------------- text -> blocks

TITLE_RE = re.compile(r"^=== .* ===$")
MARKER_RE = re.compile(r"^--- PDF page (\d+)(?: \(printed page ([^)]*)\))?.*? ---$")
MD_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# a printed figure: "5,701.41", "(95.34)", "$ 1,705,000", "1,051,687(d)", "25.168%", "Rs. 35,00,000", "-"
FIGURE_RE = re.compile(
    r"^[(\-–−]?\s*(?:₹|US\$|\$|Rs\.?|€|£|C\$|A\$)?\s*[(\-–−]?\s*\d[\d,]*(?:\.\d+)?\s*\)?\s*%?(?:\([a-z]\))?$"
    r"|^[-–—]+$", re.I)
YEAR_RE = re.compile(r"^(19|20)\d\d$")
UNIT_LINE_RE = re.compile(
    r"^\(?\s*(?:(?:all\s+)?amounts?\s+)?(?:in\s+)?(?:₹|rs\.?|us\$|\$|u\.s\.\s*dollars?|(?:canadian\s+)?dollars?)?\s*"
    r"(?:in\s+)?(?:million|millions|crores?|lakhs?|thousands?|billions?|m)\b[^|]{0,40}\)?$", re.I)
NUMBERED_HEADING_RE = re.compile(r"^(?:\(?\d{1,2}(?:\.\d{1,2})*[.)]?|[A-Z]\.)\s+[A-Z(]")
LIST_ITEM_RE = re.compile(r"^(?:\(?[a-z]{1,4}\)|\(?\d{1,2}\)|[•●▪\-–]\s)")
SENTENCE_END = (".", ":", ";", "?", "!")


def _is_figure(text):
    return bool(FIGURE_RE.match(text.strip()))


def _is_pipe_line(line):
    return " | " in line or (line.startswith("|") and line.endswith("|") and len(line) > 2)


def _cells(line):
    """Cells of a table row: "a | b | c", or a Markdown row "| a | b |"."""
    if line.startswith("|") and line.endswith("|"):
        line = line[1:-1]
        return [c.strip() for c in line.split("|")]
    return [c.strip() for c in line.split(" | ")]


def _is_heading(line, next_line):
    if len(line) > 90 or line.endswith((",", ";")) or _is_figure(line):
        return False
    if next_line and next_line[:1].islower():
        return False                      # a sentence wrapped onto the next line
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.8 and not line.endswith("."):
        return True
    return bool(NUMBERED_HEADING_RE.match(line)) and len(line) <= 80 and not line.endswith(".")


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _own_heading(line, title):
    """The unit's own heading line ("21. INCOME AND DEFERRED TAXES"): the card header says it."""
    t = _norm(title or "")
    line = MD_HEADING_RE.sub(r"\2", line)
    return bool(t) and _norm(line).endswith(t) and len(_norm(line)) <= len(t) + 12


def _row(cells):
    """A table row's cells, without empty cells at the end."""
    cells = list(cells)
    while len(cells) > 1 and cells[-1] == "":
        cells.pop()
    return cells


def _table_block(raw_rows, head_rows=None):
    """One table from its rows (lists of cells, or ("section", text)).

    Leading rows without figures (years count as headings) are the heading -
    unless a Markdown separator said how many heading rows there are
    (head_rows); rows shorter than the table keep their figures - and
    headings over figures - in the right-hand columns, as printed."""
    width = max((len(r) for r in raw_rows if isinstance(r, list)), default=1)
    head, body = [], []
    seen_data = False
    for n, r in enumerate(raw_rows):
        if head_rows is not None:
            if n < head_rows:
                r = [r[1]] if isinstance(r, tuple) else r
                head.append([{"text": c, "num": False} for c in r + [""] * (width - len(r))])
                continue
            seen_data = True
        if isinstance(r, tuple) or (len(r) == 1 and not _is_figure(r[0])):
            text = r[1] if isinstance(r, tuple) else r[0]
            body.append({"kind": "section", "cells": [{"text": text, "num": False}], "span": width})
            continue
        figures = [c for c in r if c and _is_figure(c)]
        headings = not figures or all(YEAR_RE.match(c) for c in figures)
        if len(r) < width:
            if headings or _is_figure(r[0]):
                r = [""] * (width - len(r)) + r
            else:
                r = [r[0]] + [""] * (width - len(r)) + r[1:]
        cells = [{"text": c, "num": bool(c) and _is_figure(c)} for c in r]
        if headings and not seen_data:
            head.append(cells)
        elif headings and head_rows is None and len([c for c in r if c]) > 1:
            body.append({"kind": "subhead", "cells": cells, "span": 1})    # a second heading mid-table
        else:
            seen_data = True
            kind = "total" if tables.TOTAL_RE.match(r[0]) else "data"
            body.append({"kind": kind, "cells": cells, "span": 1})
    return {"t": "table", "head": head, "body": body, "width": width}


def format_unit_text(text, title=None):
    """Blocks to display a unit's (or a page's) stored text. Pure function.

    Blocks: {"t": "marker", "page", "printed"}, {"t": "h", "text"}, {"t": "p", "text"},
    {"t": "caption", "text"}, {"t": "table", "head", "body", "width"}."""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    blocks = []
    para = []
    widths = sorted(len(ln) for ln in lines if ln and not _is_pipe_line(ln))
    typical = widths[int(len(widths) * 0.9)] if widths else 80
    heading_dropped = title is None

    def flush():
        if para:
            blocks.append({"t": "p", "text": " ".join(para)})
            para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        nxt = next((lines[j].strip() for j in range(i + 1, n) if lines[j].strip()), "")
        if not line:
            flush()
            i += 1
            continue
        if TITLE_RE.match(line):
            i += 1
            continue
        m = MARKER_RE.match(line)
        if m:
            flush()
            blocks.append({"t": "marker", "page": int(m.group(1)), "printed": m.group(2)})
            i += 1
            continue
        if not heading_dropped:
            heading_dropped = True
            if _own_heading(line, title):
                i += 1
                continue
        if _is_pipe_line(line) and not MD_SEPARATOR_RE.match(line):
            flush()
            rows = []
            head_rows = None
            while i < n:
                cur = lines[i].strip()
                if not cur:
                    if i + 1 < n and _is_pipe_line(lines[i + 1].strip()):
                        i += 1
                        continue
                    break
                if MD_SEPARATOR_RE.match(cur):
                    if head_rows is None:
                        head_rows = len(rows)          # Markdown: the rows above are the heading
                    i += 1
                    continue
                if _is_pipe_line(cur):
                    rows.append(_row(_cells(cur)))
                    i += 1
                    continue
                if MARKER_RE.match(cur) or TITLE_RE.match(cur):
                    break
                ahead = lines[i + 1].strip() if i + 1 < n else ""
                if all(_is_figure(tok) for tok in cur.split(" ") if tok) and not UNIT_LINE_RE.match(cur):
                    rows.append([cur])                     # a figure printed on its own line
                    i += 1
                    continue
                if len(cur) <= 80 and _is_pipe_line(ahead) and not UNIT_LINE_RE.match(cur):
                    rows.append(("section", cur))          # "Current income tax:" inside the table
                    i += 1
                    continue
                break
            blocks.append(_table_block(rows, head_rows))
            continue
        if MD_SEPARATOR_RE.match(line):
            i += 1
            continue
        md = MD_HEADING_RE.match(line)
        if md:
            flush()
            blocks.append({"t": "h", "text": md.group(2).strip()})
            i += 1
            continue
        if UNIT_LINE_RE.match(line):
            flush()
            blocks.append({"t": "caption", "text": line})
            i += 1
            continue
        if _is_heading(line, nxt):
            flush()
            blocks.append({"t": "h", "text": line})
            i += 1
            continue
        if para and (LIST_ITEM_RE.match(line) or
                     (para[-1].endswith(SENTENCE_END) and len(para[-1]) < 0.85 * typical)):
            flush()
        para.append(line)
        i += 1
    flush()
    return blocks


# ---------------------------------------------------------------- the document

def _anchor(prefix, ident):
    return f"{prefix}-" + re.sub(r"[^A-Za-z0-9_-]", "_", str(ident))


def _page_label(first, last):
    return f"PDF {first}" if first == last else f"PDF {first}–{last}"


def test_url(doc_name, page):
    return "/model-testing?" + urlencode({"doc": doc_name, "page": page})


class _Pages:
    """Hands out each page's anchor exactly once, in document order."""

    def __init__(self, printed, ocr_pages):
        self.printed = printed
        self.ocr = ocr_pages
        self.done = set()

    def claim(self, page):
        if page in self.done or page not in self.printed:
            return False
        self.done.add(page)
        return True


def _mark_pages(blocks, pages, doc_name):
    for b in blocks:
        if b["t"] == "marker":
            b["anchor"] = pages.claim(b["page"])
            b["printed"] = b.get("printed") or pages.printed.get(b["page"])
            b["ocr"] = b["page"] in pages.ocr
            b["test_url"] = test_url(doc_name, b["page"])
    return blocks


def _unit_kind_label(kind):
    return {"note": "Note", "schedule": "Schedule", "narrative": "Narrative", "statement": "Statement"}.get(kind, kind)


def _statement_part(package, section, unit, pages, doc_name, line_rows, note_units):
    """A statement from its stored tables (as on the statements page), with row ids and note links."""
    view = package.statement_view(section, unit["id"])
    part = {"type": "statement", "id": unit["id"], "anchor": _anchor("unit", unit["id"]),
            "section": section, "title": unit["title"], "kind": "statement",
            "pages": _page_label(unit["start_page"], unit["end_page"]),
            "printed": unit["printed_pages"], "rotated": bool(view and view.get("rotated")),
            "view_pages": [], "blocks": None}
    if not view:
        part["blocks"] = _mark_pages(format_unit_text(unit["text"], unit["title"]), pages, doc_name)
        return part
    queue = defaultdict(list)
    for line in line_rows.get(unit["id"], []):
        queue[(line["pdf_page"], line["label"])].append(line)
    for vp in view["pages"]:
        page = {"pdf_page": vp["pdf_page"], "printed": vp.get("printed"), "note": vp.get("note"),
                "anchor": pages.claim(vp["pdf_page"]), "ocr": vp["pdf_page"] in pages.ocr,
                "test_url": test_url(doc_name, vp["pdf_page"]), "tables": []}
        for t in vp["tables"]:
            rows = []
            for r in t["rows"]:
                r = dict(r)
                if r["kind"] in ("data", "total"):
                    matches = queue.get((vp["pdf_page"], r["label"]))
                    line = matches.pop(0) if matches else None
                    r["line_id"] = line["id"] if line else None
                    r["anchor"] = _anchor("line", line["id"]) if line else None
                    r["note_link"] = None
                    if t.get("notes_col") is not None and line:
                        target = line.get("note_target") or note_units.get((section, str(line["note_ref"] or "")))
                        if target:
                            r["note_link"] = "#" + _anchor("unit", target)
                rows.append(r)
            page["tables"].append({"width": t["width"], "notes_col": t.get("notes_col"), "rows": rows})
        part["view_pages"].append(page)
    return part


def _statement_anchor_lines(package):
    """Lines per statement, each with the note it references (from the links)."""
    by_unit = defaultdict(list)
    references = {}
    for e in package.edges():
        if e["kind"] == "references" and e["src"] not in references:
            references[e["src"]] = e["dst"]
    for line in package.lines():
        line = dict(line)
        line["note_target"] = references.get(line["id"])
        by_unit[line["unit_id"]].append(line)
    return by_unit


SECTION_KIND_LABELS = {
    "mdna": "MD&A", "board_report": "Board's report", "governance": "Governance", "risk": "Risk",
    "sustainability": "Sustainability", "letter": "Letter", "strategy": "Strategy", "business": "Business",
    "highlights": "Highlights", "auditor_report": "Auditor's report", "financial_statements": "Financial statements",
    "remuneration": "Remuneration", "legal": "Legal", "controls": "Controls", "shareholder_info": "Shareholders",
    "exhibits": "Exhibits", "signatures": "Signatures", "front_matter": "Front matter", "contents": "Contents",
}


def image_url(doc_name, page, clip=None, thumb=False):
    args = {"doc": doc_name, "page": page}
    if clip:
        args["clip"] = ",".join(f"{v:.0f}" for v in clip)
    if thumb:
        args["size"] = "thumb"
    return "/api/model-testing/page-image?" + urlencode(args)


def _with_images(blocks, doc_name, page):
    """Graphics get a crop of the page image: labels alone don't show a diagram."""
    for b in blocks:
        if b["kind"] in ("diagram", "chart", "figure") and b.get("bbox") and b["bbox"][2] > b["bbox"][0]:
            b["image_url"] = image_url(doc_name, page, b["bbox"])
        if b["kind"] == "panel":
            _with_images(b["children"], doc_name, page)
    return blocks


def _narrative_parts(run, npages, nsections, nblocks, pages, doc_name, passage_list=()):
    """A run of pages outside the units, split by report section and rendered from blocks."""
    groups = []
    for r in run:
        sid = npages[r["pdf_page"]]["section_id"]
        if groups and groups[-1]["sid"] == sid:
            groups[-1]["rows"].append(r)
        else:
            groups.append({"sid": sid, "rows": [r]})
    parts = []
    starts = {}                    # (page, block) -> the passage that starts there, shown as a small badge
    for p in passage_list:
        starts[(p["start_page"], p["start_block"])] = p
    for g in groups:
        section = nsections.get(g["sid"]) or {"title": "Other pages", "kind": "other", "parent_id": None}
        parent = nsections.get(section.get("parent_id")) if section.get("parent_id") else None
        first, last = g["rows"][0]["pdf_page"], g["rows"][-1]["pdf_page"]
        run_pages = []
        for r in g["rows"]:
            pno = r["pdf_page"]
            info = npages[pno]
            blocks = _with_images([dict(b) for b in nblocks.get(pno, [])], doc_name, pno)
            for seq, b in enumerate(blocks):
                if (pno, seq) in starts:
                    b["passage"] = {"id": starts[(pno, seq)]["id"], "path": starts[(pno, seq)]["path"]}
            run_pages.append({"pdf_page": pno, "printed": r["printed"] or next(
                (f for f in info["footer"] if f.isdigit()), None),
                "anchor": pages.claim(pno), "ocr": pno in pages.ocr, "test_url": test_url(doc_name, pno),
                "layout_kind": info["layout_kind"], "nblocks": blocks,
                "thumb_url": image_url(doc_name, pno, thumb=True) if info["layout_kind"] == "image" else None})
        parts.append({"type": "narrative", "anchor": _anchor("nsec", f"{g['sid'] or 'none'}-{first}"),
                      "section_id": g["sid"], "title": section["title"], "parent": parent["title"] if parent else None,
                      "kind": section["kind"], "kind_label": SECTION_KIND_LABELS.get(section["kind"], "Section"),
                      "pages_label": _page_label(first, last), "pages": run_pages,
                      "first": first, "last": last})
    return parts


def build_document(package, doc_name):
    """The view model of the formatted document (see templates/formatted_report.html)."""
    meta = package.meta
    index = package.index
    sections = {s["id"]: s for s in package.query("SELECT id, seq, code, label FROM sections ORDER BY seq")}
    page_rows = package.query("SELECT pdf_page, printed, text, text_source FROM pages ORDER BY pdf_page")
    printed = {r["pdf_page"]: r["printed"] for r in page_rows}
    ocr_pages = {r["pdf_page"] for r in page_rows if r["text_source"] == "ocr"}
    pages = _Pages(printed, ocr_pages)

    units = package.query(
        "SELECT section_id, id, seq, kind, number, title, start_page, end_page, printed_pages, text "
        "FROM units ORDER BY section_id, seq")
    section_seq = {k: s["seq"] for k, s in sections.items()}
    units.sort(key=lambda u: (u["start_page"] if u["start_page"] is not None else 10 ** 6,
                              section_seq.get(u["section_id"], 99), u["seq"]))

    covered = set()
    for u in units:
        if u["start_page"] is not None and u["end_page"] is not None:
            covered.update(range(u["start_page"], u["end_page"] + 1))
    loose = [r for r in page_rows if r["pdf_page"] not in covered]
    runs = []
    for r in loose:
        if runs and runs[-1][-1]["pdf_page"] == r["pdf_page"] - 1:
            runs[-1].append(r)
        else:
            runs.append([r])

    # links: statement line -> note, and "linked from" per note
    line_rows = _statement_anchor_lines(package)
    lines_by_id = {ln["id"]: ln for rows in line_rows.values() for ln in rows}
    unit_ids = {u["id"] for u in units}
    note_units = {}
    for u in units:
        if u["kind"] != "statement" and u["number"]:
            number = str(u["number"]).strip('"')
            note_units.setdefault((u["section_id"], number), u["id"])
    linked_from = defaultdict(list)
    for e in package.edges():
        line = lines_by_id.get(e["src"])
        if line is None or e["dst"] not in unit_ids:
            continue
        item = {"line_id": line["id"], "label": line["label"], "kind": e["kind"],
                "confidence": e["confidence"], "anchor": _anchor("line", line["id"])}
        if not any(x["line_id"] == item["line_id"] and x["kind"] == item["kind"] for x in linked_from[e["dst"]]):
            linked_from[e["dst"]].append(item)

    items = [(u["start_page"] if u["start_page"] is not None else 10 ** 6, 1, u) for u in units]
    items += [(run[0]["pdf_page"], 0, run) for run in runs]
    items.sort(key=lambda it: (it[0], it[1]))

    # the report's own sections outside the notes and statements (profiles that read them)
    nsections = {s["id"]: s for s in package.doc_sections()}
    npages = package.narrative_pages()
    nblocks = package.page_blocks() if npages else {}
    passage_list = package.passages() if npages else []

    parts, toc = [], []
    # a run of other pages inside a section does not end it: its next unit needs no new heading
    current_section, seen_sections = None, set()
    section_entry = None
    for _, is_unit, item in items:
        if not is_unit and npages and all(r["pdf_page"] in npages for r in item):
            for part in _narrative_parts(item, npages, nsections, nblocks, pages, doc_name, passage_list):
                parts.append(part)
                label = f"{part['parent']} › {part['title']}" if part["parent"] else part["title"]
                toc.append({"label": part["title"], "anchor": part["anchor"], "children": [],
                            "title_attr": label, "pages": part["pages_label"], "narrative": True})
            continue
        if not is_unit:
            run = item
            first, last = run[0]["pdf_page"], run[-1]["pdf_page"]
            anchor = _anchor("pages", f"{first}-{last}")
            run_pages = []
            for r in run:
                blocks = _mark_pages(format_unit_text(r["text"]), pages, doc_name)
                run_pages.append({"pdf_page": r["pdf_page"], "printed": r["printed"],
                                  "anchor": pages.claim(r["pdf_page"]), "ocr": r["pdf_page"] in ocr_pages,
                                  "test_url": test_url(doc_name, r["pdf_page"]), "blocks": blocks,
                                  "empty": not (r["text"] or "").strip()})
            label = f"Other pages · {_page_label(first, last)}"
            parts.append({"type": "pages", "anchor": anchor, "label": label, "first": first, "last": last,
                          "count": len(run), "pages": run_pages, "open": False})
            toc.append({"label": label, "anchor": anchor, "children": []})
            continue
        u = item
        key = u["section_id"]
        if key != current_section:
            label = (sections.get(key) or {}).get("label") or key
            continued = key in seen_sections
            anchor = _anchor("sec", key) + (f"-cont{sum(p.get('section') == key for p in parts if p['type'] == 'section')}"
                                            if continued else "")
            title = f"{label} — financial statements" + (" (continued)" if continued else "")
            parts.append({"type": "section", "anchor": anchor, "title": title, "section": key})
            section_entry = {"label": title, "anchor": anchor, "children": []}
            toc.append(section_entry)
            seen_sections.add(key)
            current_section = key
        if u["kind"] == "statement":
            part = _statement_part(package, key, u, pages, doc_name, line_rows, note_units)
            toc_label = u["title"]
        else:
            number = str(u["number"]).strip('"') if u["number"] else ""
            part = {"type": "unit", "id": u["id"], "anchor": _anchor("unit", u["id"]), "section": key,
                    "kind": u["kind"], "kind_label": _unit_kind_label(u["kind"]), "number": number,
                    "title": u["title"], "pages": _page_label(u["start_page"], u["end_page"])
                    if u["start_page"] is not None else "", "printed": u["printed_pages"],
                    "linked_from": linked_from.get(u["id"], []),
                    "blocks": _mark_pages(format_unit_text(u["text"], u["title"]), pages, doc_name)}
            shown_no = number if number and not number.startswith(("SCH-", "BH-", "NI-", "NF")) else ""
            toc_label = f"{shown_no} {u['title'] or ''}".strip() if u["kind"] == "note" else (u["title"] or u["id"])
        part["covers"] = (list(range(u["start_page"], u["end_page"] + 1))
                          if u["start_page"] is not None and u["end_page"] is not None else [])
        parts.append(part)
        section_entry["children"].append({"label": toc_label, "anchor": part["anchor"],
                                          "kind": u["kind"], "pages": part.get("pages", "")})

    # every page gets exactly one anchor: pages inside a unit's range but not in its text
    for part in parts:
        if part["type"] in ("unit", "statement"):
            part["extra_anchors"] = [p for p in part.pop("covers") if pages.claim(p)]
    for p in sorted(set(printed) - pages.done):       # none expected; keeps #page-N working anyway
        pages.claim(p)
        parts.append({"type": "anchor-only", "page": p})

    checks = package.checks()
    order = {"fail": 0, "warn": 1, "unverified": 2, "ok": 3}
    checks.sort(key=lambda c: (order.get(c["status"], 9), c["id"]))
    for c in checks:
        subject = c["subject"]
        c["link"] = ("#" + _anchor("line", subject) if subject in lines_by_id else
                     "#" + _anchor("unit", subject) if subject in unit_ids else None)
    check_counts = OrderedDict()
    for status in ("ok", "unverified", "warn", "fail"):
        n = sum(1 for c in checks if c["status"] == status)
        if n:
            check_counts[status] = n

    kinds = defaultdict(int)
    for u in units:
        kinds[u["kind"]] += 1
    notices = []
    return {
        "raw": False,
        "company": meta.get("company") or index.get("company") or meta.get("pdf_name"),
        "doc_name": doc_name,
        "pdf_name": meta.get("pdf_name"),
        "facts": [
            ("Model", meta.get("model") or "-"),
            ("Profile", f"{meta.get('profile')} v{meta.get('profile_version')}" if meta.get("profile") else "-"),
            ("Format", meta.get("format_label") or meta.get("format") or "-"),
            ("Pages", str(meta.get("page_count") or len(page_rows))),
            ("Package built", str(meta.get("built_at") or meta.get("ingested_at") or "-").replace("T", " ")[:19]),
            ("Ingester version", str(meta.get("ingester_version"))),
            ("Registry version", str(meta.get("registry_version"))),
            ("Package", package.path.name),
        ],
        "counts": [(f"{n} {k}{'s' if n != 1 else ''}", k) for k, n in sorted(kinds.items())]
                  + [(f"{len(covered & set(printed))}/{len(page_rows)} pages indexed", "pages")]
                  + ([(f"{len(ocr_pages)} OCR pages", "ocr")] if ocr_pages else []),
        "quality": [ln for ln in (meta.get("quality") or {}).get("lines_text", []) if ln.strip()],
        "notices": notices,
        "parts": parts,
        "toc": toc,
        "checks": checks,
        "check_counts": list(check_counts.items()),
        "empty_sections": [s["label"] or k for k, s in sections.items()
                           if not any(u["section_id"] == k for u in units)],
        "narrative_quality": [ln for ln in (meta.get("quality") or {}).get("lines_text", [])
                              if ln.startswith("Narrative")],
    }


def build_raw_document(doc_name, page_texts, notice, company=None):
    """A model with no rules: every page's raw PDF text, labelled as such."""
    pages = _Pages({p: None for p, _ in page_texts}, set())
    run_pages = []
    for pno, text in page_texts:
        run_pages.append({"pdf_page": pno, "printed": None, "anchor": pages.claim(pno), "ocr": False,
                          "test_url": test_url(doc_name, pno), "raw_text": text,
                          "blocks": [], "empty": not text.strip()})
    first, last = (page_texts[0][0], page_texts[-1][0]) if page_texts else (1, 0)
    label = f"Raw PDF text · {_page_label(first, last)}"
    anchor = _anchor("pages", f"{first}-{last}")
    return {
        "raw": True, "company": company or doc_name, "doc_name": doc_name, "pdf_name": doc_name,
        "facts": [("Pages", str(len(page_texts))), ("Source", "raw PDF text (no extraction rules)")],
        "counts": [], "quality": [], "notices": [notice],
        "parts": [{"type": "pages", "anchor": anchor, "label": label, "first": first, "last": last,
                   "count": len(page_texts), "pages": run_pages, "open": True}],
        "toc": [{"label": label, "anchor": anchor, "children": []}],
        "checks": [], "check_counts": [], "empty_sections": [],
    }


def build_notice_document(doc_name, notice):
    """Nothing to show but a notice (a scanned report that is not transcribed)."""
    return {"raw": True, "company": doc_name, "doc_name": doc_name, "pdf_name": doc_name,
            "facts": [], "counts": [], "quality": [], "notices": [notice], "parts": [], "toc": [],
            "checks": [], "check_counts": [], "empty_sections": []}


# ---------------------------------------------------------------- rendered HTML cache

class HtmlCache:
    """The last few rendered documents (a 508-page report takes a second or two)."""

    def __init__(self, size=4):
        self.size = size
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        return None

    def put(self, key, html):
        with self.lock:
            self.items[key] = html
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


CACHE = HtmlCache()
