"""Read a report package (used by the analyzer and the ingester).

open_package() rebuilds the index exactly as the ingester built it (the
analyzer's note picking and prompts work on it unchanged) and serves the
texts, pages, statement tables, lines, links and checks on demand. Each call
opens its own short SQLite connection, so a Package can be shared between
the app's worker threads.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from rptpkg import SCHEMA_VERSION


class PackageError(Exception):
    """The package is missing, damaged or from an incompatible version."""


def _major(version):
    return str(version).split(".")[0]


class Package:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise PackageError(f"Report package not found: {self.path.name}")
        try:
            with closing(self._connect()) as conn:
                self.meta = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM meta")}
                self.index = self._rebuild_index(conn)
        except sqlite3.DatabaseError as exc:
            raise PackageError(f"Report package {self.path.name} is damaged ({exc}). Re-ingest the report.")
        if _major(self.meta.get("schema_version")) != _major(SCHEMA_VERSION):
            raise PackageError(
                f"Report package {self.path.name} has schema version {self.meta.get('schema_version')}; "
                f"this app reads version {SCHEMA_VERSION}. Re-ingest the report.")

    def _connect(self):
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    def _rebuild_index(self, conn):
        index = dict(self.meta["index"])
        index["pages"] = {
            str(pno): json.loads(layout)
            for pno, layout in conn.execute(
                "SELECT pdf_page, layout FROM pages WHERE layout IS NOT NULL ORDER BY layout_seq")}
        sections = {}
        for key, data in conn.execute("SELECT id, data FROM sections ORDER BY seq"):
            sections[key] = json.loads(data)
        for key, kind, data, text in conn.execute(
                "SELECT section_id, kind, data, text FROM units ORDER BY section_id, seq"):
            unit = json.loads(data)
            if kind == "statement":
                unit["text"] = text
                sections[key]["statements"].append(unit)
            else:
                sections[key]["notes"].append(unit)
        index["sections"] = sections
        return index

    # ---------------------------------------------------------------- texts
    def unit_text(self, section, unit_id):
        """{"text", "pages"} of a unit, exactly as the ingester clipped it."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT text, text_pages FROM units WHERE section_id = ? AND id = ?",
                               (section, unit_id)).fetchone()
        if not row or row[0] is None:
            raise PackageError(f"No text for {unit_id} in this report package")
        return {"text": row[0], "pages": json.loads(row[1])}

    def page(self, pdf_page):
        """{"printed", "text"} of one PDF page, or None if out of range."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT printed, text FROM pages WHERE pdf_page = ?", (pdf_page,)).fetchone()
        return {"printed": row[0], "text": row[1]} if row else None

    def ocr_page(self, pdf_page):
        """A scanned page's transcription checks ({} when there are none)."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT ocr FROM pages WHERE pdf_page = ?", (pdf_page,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}

    # ---------------------------------------------------------------- statements
    def statement_units(self):
        """[(section, unit id, statement)] in index order."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT section_id, id FROM units WHERE kind = 'statement' "
                                "ORDER BY section_id, seq").fetchall()
        by_section = {}
        for key, uid in rows:
            by_section.setdefault(key, []).append(uid)
        out = []
        for key, section in self.index["sections"].items():
            out += [(key, uid, st) for uid, st in zip(by_section.get(key, []), section.get("statements", []))]
        return out

    def statement_view(self, section, unit_id):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT view FROM units WHERE section_id = ? AND id = ?",
                               (section, unit_id)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    # ---------------------------------------------------------------- lines, links, checks
    def _dicts(self, sql, args=()):
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args)]

    def lines(self, unit_id=None):
        if unit_id:
            return self._dicts("SELECT * FROM lines WHERE unit_id = ? ORDER BY seq", (unit_id,))
        return self._dicts("SELECT * FROM lines ORDER BY section_id, unit_id, seq")

    def values(self, line_id):
        return self._dicts('SELECT * FROM "values" WHERE line_id = ? ORDER BY col', (line_id,))

    def edges(self, src=None, dst=None):
        if src:
            return self._dicts("SELECT * FROM edges WHERE src = ? ORDER BY id", (src,))
        if dst:
            return self._dicts("SELECT * FROM edges WHERE dst = ? ORDER BY id", (dst,))
        return self._dicts("SELECT * FROM edges ORDER BY id")

    def checks(self, status=None):
        if status:
            return self._dicts("SELECT * FROM checks WHERE status = ? ORDER BY id", (status,))
        return self._dicts("SELECT * FROM checks ORDER BY id")

    def query(self, sql, args=()):
        """Read-only SQL for tools and tests."""
        return self._dicts(sql, args)

    # ---------------------------------------------------------------- report sections (schema 1.1)
    def _optional(self, sql, args=()):
        """Rows of a table added in a minor schema version ([] in older packages)."""
        try:
            return self._dicts(sql, args)
        except sqlite3.OperationalError:
            return []

    def doc_sections(self):
        """The report's own sections outside the notes and statements, in order."""
        rows = self._optional("SELECT id, parent_id, seq, title, kind, level, start_page, end_page, printed, source,"
                              " confirmed_by FROM doc_sections ORDER BY seq")
        for r in rows:
            r["confirmed_by"] = json.loads(r["confirmed_by"])
        return rows

    def section_text(self, section_id):
        rows = self._optional("SELECT text FROM doc_sections WHERE id = ?", (section_id,))
        return rows[0]["text"] if rows else None

    def narrative_pages(self):
        """{pdf page: {section_id, layout_kind, header, footer}} of the pages read into blocks."""
        out = {}
        for r in self._optional("SELECT * FROM narrative_pages ORDER BY pdf_page"):
            out[r["pdf_page"]] = {"section_id": r["section_id"], "layout_kind": r["layout_kind"],
                                  "header": json.loads(r["header"]), "footer": json.loads(r["footer"])}
        return out

    def passages(self):
        """The report's passages in order, without their text: {id, section_id, path, kind, pages, chars, lead}.
        [] for packages from before schema 1.2."""
        rows = self._optional("SELECT id, section_id, seq, path, kind, start_page, end_page, start_block, chars, lead"
                              " FROM doc_passages ORDER BY seq")
        return rows

    def passage(self, passage_id):
        """One passage with its text, blocks and money figures (None if unknown)."""
        rows = self._optional("SELECT * FROM doc_passages WHERE id = ?", (passage_id,))
        if not rows:
            return None
        row = rows[0]
        row["blocks"] = json.loads(row["blocks"])
        row["figures"] = json.loads(row["figures"])
        return row

    def passage_texts(self):
        """{passage id: (path, text)} of every passage: the search index is built from these."""
        return {r["id"]: (r["path"], r["text"]) for r in self._optional("SELECT id, path, text FROM doc_passages")}

    def page_blocks(self, pdf_page=None):
        """Blocks in reading order: of one page, or {pdf page: [blocks]} of all."""
        sql = "SELECT pdf_page, seq, kind, bbox, data FROM page_blocks"
        rows = self._optional(sql + (" WHERE pdf_page = ? ORDER BY seq" if pdf_page else " ORDER BY pdf_page, seq"),
                              (pdf_page,) if pdf_page else ())
        blocks = {}
        for r in rows:
            blocks.setdefault(r["pdf_page"], []).append(
                {"kind": r["kind"], "bbox": json.loads(r["bbox"]), **json.loads(r["data"])})
        return blocks.get(pdf_page, []) if pdf_page else blocks


def open_package(path):
    return Package(path)
