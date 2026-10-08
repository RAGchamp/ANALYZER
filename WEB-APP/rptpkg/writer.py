"""Write a report package (used by the ingester only).

The ingester hands over the index it built plus everything the analyzer will
need without the PDF: every unit's clipped text, every page's text, the
statement tables, and (from Phase 2 on) lines, values, links and checks.
The index is split into the sections / units / pages tables; the reader puts
it back together exactly.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

from rptpkg import SCHEMA_VERSION
from rptpkg.model import note_unit_id, section_code, statement_unit_ids

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _rows(index, content):
    """Table rows for the index: (sections, units, layouts)."""
    sections, units = [], []
    for seq, (key, section) in enumerate(index["sections"].items()):
        data = {k: ([] if k in ("notes", "statements") else v) for k, v in section.items()}
        sections.append((key, seq, section_code(key, section), section.get("label"),
                         section.get("first_page"), section.get("last_page"), _json(data)))
        order = 0
        for note in section.get("notes", []):
            uid = note_unit_id(key, note)
            text = content["unit_texts"].get((key, uid))
            units.append((key, uid, order, note.get("kind", "note"), _json(note.get("no")), note.get("title"),
                          note.get("parent"), note.get("start_page"), note.get("end_page"),
                          note.get("printed_pages"), _json(note),
                          text["text"] if text else None, _json(text["pages"]) if text else None, None))
            order += 1
        for uid, statement in zip(statement_unit_ids(key, section), section.get("statements", [])):
            data = {k: (None if k == "text" else v) for k, v in statement.items()}
            pages = statement.get("pages", [])
            view = content["statement_views"].get((key, uid))
            units.append((key, uid, order, "statement", _json(statement["type"]), statement.get("title"),
                          None, pages[0]["pdf_page"] if pages else None,
                          pages[-1]["pdf_page"] if pages else None, None, _json(data),
                          statement.get("text"),
                          _json([{"pdf_page": p["pdf_page"], "printed": p.get("printed")} for p in pages]),
                          _json(view) if view is not None else None))
            order += 1
    return sections, units


def _write_narrative(conn, narrative):
    """Sections and page blocks outside the notes and statements (ingest/narrative), if read."""
    if not narrative:
        return
    conn.executemany(
        "INSERT INTO doc_sections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(s["id"], s["parent_id"], s["seq"], s["title"], s["kind"], s["level"], s["start_page"], s["end_page"],
          s.get("printed"), s["source"], _json(s.get("confirmed_by", [])), s.get("text"))
         for s in narrative["sections"]])
    pages, blocks = [], []
    for pno, info in sorted(narrative["pages"].items()):
        pages.append((pno, info["section_id"], info["layout_kind"], _json(info["header"]), _json(info["footer"])))
        for seq, block in enumerate(info["blocks"]):
            data = {k: v for k, v in block.items() if k not in ("kind", "bbox")}
            blocks.append((pno, seq, block["kind"], _json(block.get("bbox", [0, 0, 0, 0])), _json(data)))
    conn.executemany("INSERT INTO narrative_pages VALUES (?, ?, ?, ?, ?)", pages)
    conn.executemany("INSERT INTO page_blocks VALUES (?, ?, ?, ?, ?)", blocks)
    conn.executemany(
        "INSERT INTO doc_passages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(p["id"], p["section_id"], p["seq"], p["path"], p["kind"], p["start_page"], p["end_page"],
          p["start_block"], p["text"], p["chars"], p["lead"], _json(p["blocks"]), _json(p["figures"]))
         for p in narrative.get("passages", [])])


def write_package(path, index, content, meta):
    """Write the package to `path` (atomically: a temp file, then a rename).

    content: unit_texts {(section, unit id): {"text", "pages"}},
             statement_views {(section, unit id): view},
             pages {pdf page: {"printed", "text", "text_source", "ocr"}},
             lines, values, edges, checks (lists of dicts; may be empty).
    meta:    extra meta keys (pdf_sha256, format_label, summary, ...)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    report_facts = {k: ({} if k in ("sections", "pages") else v) for k, v in index.items()}
    all_meta = {
        "schema_version": SCHEMA_VERSION,
        "pdf_name": index.get("pdf_name"),
        "page_count": index.get("page_count"),
        "built_at": index.get("built_at"),
        "company": index.get("company"),
        "format": index.get("format", "modern"),
        "format_source": index.get("format_source", "detected"),
        **meta,
        "index": report_facts,
    }
    sections, units = _rows(index, content)
    layouts = {int(p): (seq, info) for seq, (p, info) in enumerate(index.get("pages", {}).items())}
    page_rows = []
    for pno in range(1, (index.get("page_count") or 0) + 1):
        page = content["pages"].get(pno, {})
        seq, layout = layouts.get(pno, (None, None))
        page_rows.append((pno, page.get("printed"), page.get("text"), page.get("text_source", "pdf"),
                          seq, _json(layout) if layout is not None else None,
                          _json(page["ocr"]) if page.get("ocr") is not None else None))

    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [(k, _json(v)) for k, v in all_meta.items()])
        conn.executemany("INSERT INTO sections VALUES (?, ?, ?, ?, ?, ?, ?)", sections)
        conn.executemany("INSERT INTO units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", units)
        conn.executemany("INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)", page_rows)
        conn.executemany(
            "INSERT INTO lines VALUES (:id, :section_id, :unit_id, :seq, :pdf_page, :table_no, :label, :kind,"
            " :note_ref)", content.get("lines", []))
        conn.executemany(
            'INSERT INTO "values" VALUES (:line_id, :col, :heading, :period_end, :period_label, :raw, :value,'
            " :nil)", content.get("values", []))
        conn.executemany(
            "INSERT INTO edges (src, dst, kind, method, confidence, evidence)"
            " VALUES (:src, :dst, :kind, :method, :confidence, :evidence)", content.get("edges", []))
        conn.executemany(
            "INSERT INTO checks (kind, subject, status, expected, actual, message)"
            " VALUES (:kind, :subject, :status, :expected, :actual, :message)", content.get("checks", []))
        _write_narrative(conn, content.get("narrative"))
        conn.commit()
    finally:
        conn.close()
    # On Windows a reader may hold the old file open for a moment.
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


def update_meta(path, **values):
    """Change meta values in place (e.g. the PDF's current name after a rename)."""
    conn = sqlite3.connect(path)
    try:
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, _json(value)))
        conn.commit()
    finally:
        conn.close()
