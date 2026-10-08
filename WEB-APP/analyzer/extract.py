"""STEP 4 - put together the text of the selected notes (or pages) for Claude.

The ingester already clipped every note, schedule and page to its exact
extent (ingest/clip.py) and stored the text in the report package; this
module only looks it up and joins it, in the same format as always:

    === Note 21 — INCOME AND DEFERRED TAXES (Consolidated) ===
    --- PDF page 405 (printed page 402) ---
    Current income tax charge | 5,701.41 | 5,994.80
"""

from rptpkg.blocks import page_text
from rptpkg.model import note_unit_id


def _page_label(pno, printed):
    return f"PDF page {pno}" + (f" (printed page {printed})" if printed else "")


def find_note(index, section, number):
    for note in index["sections"].get(section, {}).get("notes", []):
        if note["no"] == number:
            return note
    return None


def extract_note(package, section, number):
    note = find_note(package.index, section, number)
    if not note:
        raise ValueError(f"Note {number} not found in {section} notes")
    stored = package.unit_text(section, note_unit_id(section, note))
    return {"section": section, "no": number, "title": note["title"],
            "pages": stored["pages"], "text": stored["text"]}


def extract_pages(package, page_numbers):
    """Manual override: whole pages (minus running header/footer). A page outside the notes
    and statements is sent in its reading order - its blocks - so two columns are not
    interleaved (INFO/ANALYZE-NON-FIN-DATA-PLAN.md decision Q6); other pages as stored."""
    page_count = package.index["page_count"]
    narrative = package.narrative_pages()
    parts, pages = [], []
    for pno in page_numbers:
        page = package.page(pno) if 1 <= pno <= page_count else None
        if page is None:
            raise ValueError(f"PDF page {pno} is out of range (1–{page_count})")
        pages.append({"pdf_page": pno, "printed": page["printed"]})
        text = page["text"]
        if pno in narrative and narrative[pno]["layout_kind"] != "fallback":
            text = page_text(package.page_blocks(pno)) or text
        parts.append(f"--- {_page_label(pno, page['printed'])} ---\n{text}")
    return {"section": None, "no": None, "title": "Manually selected pages", "pages": pages,
            "text": "=== Manually selected pages ===\n" + "\n\n".join(parts)}


def extract_passages(package, passage_ids, budget=None):
    """The chosen report passages as the REPORT SECTIONS text (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §6.1).

    passage_ids come best first; when they don't fit in `budget` characters the last
    ones are left out. Returns (passages sent, dropped ids, text)."""
    sent, dropped, parts, used = [], [], [], 0
    for pid in passage_ids:
        p = package.passage(pid)
        if p is None:
            continue
        pages = f"{p['start_page']}" if p["start_page"] == p["end_page"] else f"{p['start_page']}–{p['end_page']}"
        block = f"=== {p['path']} (PDF {pages}) ===\n{p['text']}"
        if budget is not None and sent and used + len(block) > budget:
            dropped.append(pid)
            continue
        used += len(block)
        parts.append(block)
        sent.append({"id": p["id"], "path": p["path"], "kind": p["kind"], "section_id": p["section_id"],
                     "start_page": p["start_page"], "end_page": p["end_page"], "pages": pages, "chars": p["chars"]})
    return sent, dropped, "\n\n".join(parts)


def extract_selection(package, notes=None, page_numbers=None):
    """Extract a list of {"section", "no"} notes, or explicit pages.
    Returns (extracts, combined_text)."""
    if page_numbers:
        extracts = [extract_pages(package, page_numbers)]
    else:
        extracts = [extract_note(package, n["section"], n["no"]) for n in notes]
    return extracts, "\n\n".join(e["text"] for e in extracts)
