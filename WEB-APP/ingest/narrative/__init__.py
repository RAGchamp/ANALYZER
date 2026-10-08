"""Everything in an annual report that is not a primary statement or a note
(INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md).

build(index, doc, pages) reads every page that no note / statement unit covers:
the section map (sections.py), then each page's blocks in reading order
(layout.py). Pages inside units keep today's extraction untouched. Runs only
for profiles that switch it on (`narrative_sections`); returns None otherwise.
"""

import re
import time
from collections import Counter

from ingest import profiles
from ingest.narrative import geometry, kinds, layout, markdown, passages, render, sections

TOP_BAND = 0.12          # share of the page height where running headers sit
BOTTOM_BAND = 0.90       # below this share, footers and page numbers
PAGE_NO_RE = re.compile(r"^(?:page\s+)?(?:[A-Z]-)?\d{1,3}$", re.I)


def covered_pages(index):
    """PDF pages inside a note, schedule, narrative unit or statement."""
    covered = set()
    for section in index["sections"].values():
        for unit in section.get("notes", []):
            if unit.get("start_page") and unit.get("end_page"):
                covered.update(range(unit["start_page"], unit["end_page"] + 1))
        for st in section.get("statements", []):
            covered.update(p["pdf_page"] for p in st.get("pages", []))
    return covered


def narrative_units(index):
    """The "narrative" units of old and scanned reports (Directors' Report, a 10-K's filing text,
    the auditors' report) with their pages outside the statements: prose kept on the notes side
    (so notes prompts do not change) that questions about the business still need as passages."""
    statement_pages = {p["pdf_page"] for section in index["sections"].values()
                       for st in section.get("statements", []) for p in st.get("pages", [])}
    out = []
    for section in index["sections"].values():
        for unit in section.get("notes", []):
            if unit.get("kind") == "narrative" and unit.get("start_page") and unit.get("end_page"):
                pages = [p for p in range(unit["start_page"], unit["end_page"] + 1) if p not in statement_pages]
                if pages:
                    out.append((section, unit, pages))
    return out


def _unit_section(unit, pages, parent_id=None):
    # "Filing text (cover, items, index, letters)": the words in brackets list contents, not a kind
    title = unit.get("title") or unit["id"]
    return {"id": f"U-{unit['id']}", "title": title, "kind": kinds.kind_of(re.sub(r"\(.*?\)", "", title)),
            "parent_id": parent_id, "level": 1, "start_page": pages[0], "end_page": pages[-1]}


def _norm(text):
    return re.sub(r"\d+", "#", text.strip().lower())


def split_bands(geos):
    """{page: (body segments, header lines, footer lines)}: a running header is a
    top-of-page line repeated on other pages, or small text well above the body;
    a footer is a repeated bottom line or a page number."""
    top_counts, bottom_counts = Counter(), Counter()
    for geo in geos.values():
        h = geo["height"]
        top_counts.update({_norm(s["t"]) for s in geo["segments"] if s["y1"] <= TOP_BAND * h})
        bottom_counts.update({_norm(s["t"]) for s in geo["segments"] if s["y0"] >= BOTTOM_BAND * h})
    out = {}
    for pno, geo in geos.items():
        h = geo["height"]
        segs = geo["segments"]
        body_size = layout.body_size(segs)
        header, footer, body = [], [], []
        lower = [s for s in segs if s["y1"] > TOP_BAND * h]
        first_body_y = min((s["y0"] for s in lower), default=h)
        # repeated bottom lines (a browser's print footer: the URL, "20/363") are set aside first,
        # so that the printed page number just above them is the lowest line
        chrome = [s for s in segs if s["y0"] >= BOTTOM_BAND * h and bottom_counts[_norm(s["t"])] >= 3
                  and not PAGE_NO_RE.match(s["t"])]
        rest = [s for s in segs if s not in chrome]
        lowest = max((s["y0"] for s in rest), default=0)
        for s in sorted(segs, key=lambda s: (s["y0"], s["x0"])):
            key = _norm(s["t"])
            # a bullet or a stray mark at the top of the page is text, not a running header; nor is a
            # large title that happens to open two pages ("REPORT OF INDEPENDENT ... FIRM", Magna)
            small = s["size"] <= 1.05 * body_size and not (s["bold"] and s["size"] > body_size)
            # a line at the top of a third of the pages (a browser's print date) is chrome whatever its size
            chrome_top = top_counts[key] >= max(3, 0.3 * len(geos))
            repeated_or_apart = top_counts[key] >= 2 or (
                not s["mono"] and first_body_y - s["y1"] >= 30 and len(s["t"]) <= 80)
            if s["y1"] <= TOP_BAND * h and len(s["t"]) >= 3 and (chrome_top or (small and repeated_or_apart)):
                header.append(s["t"])
            elif (s["y0"] >= BOTTOM_BAND * h and (bottom_counts[key] >= 3 or PAGE_NO_RE.match(s["t"]))) or (
                    PAGE_NO_RE.match(s["t"]) and s["y0"] >= lowest - 1
                    and not any(o is not s and o["y1"] > s["y0"] - 8 for o in rest)):
                footer.append(s["t"])          # the page number is the lowest line, wherever the page ends
            else:
                body.append(s)
        out[pno] = (body, header, footer)
    return out


def _layout_kind(blocks, fallback, has_text):
    if fallback:
        return "fallback"
    if not has_text:
        return "image"
    kinds = Counter(b["kind"] for b in blocks)
    if kinds["panel"] or kinds["diagram"] or kinds["chart"] or kinds["metric"] or kinds["figure"]:
        return "designed"
    chars = sum(len(t) for b in blocks for t in layout.block_texts(b)) or 1
    table_chars = sum(len(t) for b in blocks if b["kind"] == "table" for t in layout.block_texts(b))
    return "table" if table_chars / chars >= 0.5 else "prose"


def _unit_pages(units, doc, pages, transcribed):
    """([section per narrative unit], {page: {"section_id", "blocks"}}) for passages.build."""
    unit_sections, out = [], {}
    all_pages = [p for _, _, unit_pages in units for p in unit_pages]
    # a report of several companies (BRK-1968 and its two insurers) names the company in the path
    entities = {id(section) for section, _, _ in units}
    parents = {}
    bands = {}
    if not transcribed and all_pages:
        geos = {p: geometry.capture(doc[p - 1]) for p in all_pages}
        bands = split_bands(geos)
    for report_section, unit, unit_pages in units:
        parent_id = None
        if len(entities) > 1:
            parent_id = f"U-{report_section.get('code') or report_section.get('label')}"
            if parent_id not in parents:
                parents[parent_id] = {"id": parent_id, "title": report_section.get("label") or "",
                                      "kind": "other", "parent_id": None, "level": 0}
                unit_sections.append(parents[parent_id])
        section = _unit_section(unit, unit_pages, parent_id)
        unit_sections.append(section)
        for p in unit_pages:
            text = (pages.get(p) or {}).get("text") or ""
            if transcribed:
                blocks = markdown.blocks(text)
                fallback = not markdown.coverage_ok(text, blocks)
            else:
                geo = geos[p]
                blocks = layout.read_page(geo, bands[p][0])
                fallback = not layout.coverage_ok(bands[p][0], blocks, geo["rotated"])
            if fallback:
                blocks = [{"kind": "flat", "text": text, "bbox": [0, 0, 0, 0]}]
            out[p] = {"section_id": section["id"], "blocks": blocks}
    return unit_sections, out


def build(index, doc, pages):
    """{"sections", "pages": {pno: {...}}, "warnings", "stats"} or None when the profile has it off.
    doc: the open PDF, or None for a scanned report (read from its transcription in `pages`)."""
    switches = profiles.active().switches
    transcribed = index.get("format") == "transcribed"
    if not switches.narrative_sections or (doc is None and not transcribed):
        return None
    started = time.monotonic()
    covered = covered_pages(index)
    todo = [p for p in range(1, index["page_count"] + 1) if p not in covered]
    printed = {p: (pages.get(p) or {}).get("printed") for p in range(1, index["page_count"] + 1)}
    texts = {p: (pages.get(p) or {}).get("text") or "" for p in todo}

    if transcribed:
        geos, bands = {}, {p: ([], [], []) for p in todo}
        section_list, warnings, contents_page = sections.build(
            index["page_count"], switches.narrative_sections, {}, {}, {}, printed, [], covered,
            markdown_entries=markdown.heading_entries(texts, covered))
    else:
        geos = {p: geometry.capture(doc[p - 1]) for p in todo}
        bands = split_bands(geos)
        # the page numbers printed in the footers are the surest printed -> PDF mapping for the contents page
        footer_numbers = {p: next((f for f in bands[p][2] if f.isdigit()), None) for p in todo}
        body_geos = {p: {**geos[p], "segments": bands[p][0]} for p in todo}
        section_list, warnings, contents_page = sections.build(
            index["page_count"], switches.narrative_sections, body_geos, geos,
            {p: bands[p][1] for p in todo}, printed, doc.get_toc(simple=True), covered, footer_numbers)

    page_info = {}
    for p in todo:
        body, header, footer = bands[p]
        if transcribed:
            blocks = markdown.blocks(texts[p])
            fallback = not markdown.coverage_ok(texts[p], blocks)
            has_text = bool(texts[p].strip())
        else:
            geo = geos[p]
            blocks = layout.read_page(geo, body)
            fallback = not layout.coverage_ok(body, blocks, geo["rotated"])
            has_text = bool(body or geo["rotated"])
        if fallback:
            blocks = [{"kind": "flat", "text": texts[p], "bbox": [0, 0, 0, 0]}]
        kind = "contents" if p == contents_page else _layout_kind(blocks, fallback, has_text)
        leaf = None
        for s in section_list:
            if s["start_page"] <= p <= s["end_page"] and (leaf is None or s["level"] >= leaf["level"]):
                leaf = s
        page_info[p] = {"section_id": leaf["id"] if leaf else None, "layout_kind": kind, "blocks": blocks,
                        "header": header, "footer": footer, "fallback": fallback}

    # a paragraph broken at the bottom of a page continues on the next one
    for p in todo:
        nxt = page_info.get(p + 1)
        blocks = page_info[p]["blocks"]
        if nxt and blocks and nxt["blocks"] and nxt["section_id"] == page_info[p]["section_id"]:
            last, first = blocks[-1], nxt["blocks"][0]
            if (last["kind"] == "paragraph" and first["kind"] == "paragraph"
                    and not layout._ends_sentence(last["text"]) and first["text"][:1].islower()):
                last["continues"] = True

    blocks_by_page = {p: info["blocks"] for p, info in page_info.items()}
    has_children = {s["parent_id"] for s in section_list}
    for s in section_list:
        s["text"] = None if s["id"] in has_children else render.section_text(s, blocks_by_page, printed, covered)
    # the pieces a question can use (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §3); printed page labels fall
    # back to the page numbers read in the footers
    shown = {p: printed.get(p) or next((f for f in bands[p][2] if f.isdigit()), None) for p in todo}
    # the narrative units' pages are cut into passages too, but stay out of the section map and
    # page blocks (their pages keep today's extraction as units)
    unit_sections, unit_pages = _unit_pages(narrative_units(index), doc, pages, transcribed)
    shown.update({p: printed.get(p) for p in unit_pages})
    passage_list = passages.build(section_list + unit_sections, {**page_info, **unit_pages}, shown)

    kinds = Counter(info["layout_kind"] for info in page_info.values())
    block_kinds = Counter()
    for info in page_info.values():
        for b in info["blocks"]:
            block_kinds[b["kind"]] += 1
            if b["kind"] == "panel":
                block_kinds.update(c["kind"] for c in b["children"])
    stats = {
        "pages": len(todo), "blocks": sum(len(i["blocks"]) for i in page_info.values()),
        "layout_kinds": dict(kinds), "block_kinds": dict(block_kinds),
        "fallback_pages": [p for p, i in page_info.items() if i["fallback"]],
        "sources": {src: sum(1 for s in section_list if s["source"] == src) for src in
                    {s["source"] for s in section_list}},
        "confirmed": sum(1 for s in section_list if s["confirmed_by"]),
        "contents_page": contents_page,
        "passages": passages.stats(passage_list),
        "seconds": round(time.monotonic() - started, 1),
    }
    return {"sections": section_list, "pages": page_info, "passages": passage_list, "warnings": warnings,
            "stats": stats}
