"""Model testing (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md §4): one page of a
model document, and what the app extracted from it.

The extraction is read from the report package, the same one the Analyzer
reads, so the page shows exactly what Claude would be sent - not a separate
test-only extraction. A model whose rules are not written yet (pending, or not
in the registry) has no package: its page's raw PDF text is shown instead,
clearly labelled, so feedback on it can still be given.
"""

import json
import re
import threading
from pathlib import Path

import pymupdf

import config
from ingest import pipeline
from ingest.models import registry
from ingest.narrative import render as narrative_render
from ingest.pdf_utils import PdfError, clean_text, open_pdf
from rptpkg import store
from webcommon import UserError

_page_counts = {}
_page_counts_lock = threading.Lock()


# ---------------------------------------------------------------- the documents

def _natural_key(path):
    """TYPE-2 before TYPE-10."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def list_model_docs():
    """Every PDF directly in MODEL-DOCS (not _candidates), in TYPE order."""
    folder = Path(config.MODEL_DOCS_DIR)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                  key=_natural_key)


def resolve_model_doc(name):
    """Only a PDF listed in MODEL-DOCS: no other paths."""
    for path in list_model_docs():
        if path.name == name:
            return path
    raise UserError(f"Model document not found in {config.MODEL_DOCS_DIR}: {name}")


def page_count(path):
    """Number of pages, remembered while the file is unchanged."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime)
    with _page_counts_lock:
        if key in _page_counts:
            return _page_counts[key]
    doc = open_pdf(path)
    try:
        count = doc.page_count
    finally:
        doc.close()
    with _page_counts_lock:
        _page_counts[key] = count
    return count


def registry_model(path):
    """The registry entry of this model document (by file name), or None."""
    return next((m for m in registry.load().models if m.file == path.name), None)


def doc_info(path):
    model = registry_model(path)
    try:
        pages = page_count(path)
        error = None
    except PdfError as exc:
        pages, error = None, str(exc)
    return {
        "name": path.name,
        "pages": pages,
        "error": error,
        "registered": model is not None,
        "model_id": model.id if model else None,
        "profile": model.profile if model else None,
        "pending": bool(model and model.pending),
        "description": model.description if model else "",
    }


def validate_page(value, pages):
    """The page as an int in 1..pages, or UserError with the message the page shows."""
    text = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    if not re.fullmatch(r"\d+", text):
        raise UserError(f"Enter a PDF page number (1–{pages}).")
    page = int(text)
    if not 1 <= page <= pages:
        raise UserError(f"Page {page} is out of range: this PDF has {pages} pages (1–{pages}).")
    return page


def parse_clip(value):
    """"x0,y0,x1,y1" in PDF points -> a tuple, or None (whole page)."""
    if not value:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in str(value).split(","))
    except ValueError:
        raise UserError("clip must be x0,y0,x1,y1 in PDF points")
    if x1 <= x0 or y1 <= y0:
        raise UserError("clip must be x0,y0,x1,y1 with x1 > x0 and y1 > y0")
    return x0, y0, x1, y1


def page_png(path, page, dpi=None, clip=None):
    """The page exactly as it is in the PDF (sideways pages stay sideways); clip: a part of it
    (a diagram or chart of the formatted document), with a small margin."""
    doc = open_pdf(path)
    try:
        if not 1 <= page <= doc.page_count:
            raise UserError(f"Page {page} is out of range: this PDF has {doc.page_count} pages (1–{doc.page_count}).")
        pdf_page = doc[page - 1]
        area = None
        if clip:
            area = pymupdf.Rect(clip[0] - 6, clip[1] - 6, clip[2] + 6, clip[3] + 6) & pdf_page.rect
        return pdf_page.get_pixmap(dpi=dpi or config.MODEL_TEST_DPI, clip=area).tobytes("png")
    finally:
        doc.close()


# ---------------------------------------------------------------- extraction

def extract_page(path, page):
    """{"extracted": the data (stored in feedback), "context": how it was made,
    "notices": what the user should know}. Raises UserError for a bad page."""
    path = Path(path)
    page = validate_page(page, page_count(path))
    model = registry_model(path)
    if model is None:
        return _raw(path, page, "This PDF is not in the model registry, so the app has no extraction rules for it "
                                "(see MODEL-DOCS\\README.md). Showing the page's raw PDF text.")
    if model.pending:
        return _raw(path, page, f"No extraction rules yet for {model.id} (pending). "
                                "Showing the page's raw PDF text - not extracted by the app's rules.", model)
    try:
        package = pipeline.open_report(path)
    except pipeline.NeedsTranscription:
        return _not_transcribed(path, page, model)
    except pipeline.NewModelDocument as exc:
        return _raw(path, page, f"The app's rules did not accept this model document: {exc} "
                                "Showing the page's raw PDF text.", model)
    except PdfError as exc:
        raise UserError(str(exc))
    return _from_package(package, page, model)


def _from_package(package, page, model):
    row = package.query("SELECT printed, text, text_source FROM pages WHERE pdf_page = ?", (page,))
    row = row[0] if row else {"printed": None, "text": "", "text_source": "pdf"}
    layout = package.index.get("pages", {}).get(str(page)) or {}
    sections = package.index.get("sections", {})

    units = []
    for unit in package.query(
            "SELECT section_id, id, kind, number, title, start_page, end_page FROM units "
            "WHERE start_page <= ? AND end_page >= ? ORDER BY section_id, seq", (page, page)):
        number = json.loads(unit["number"]) if unit["number"] else None
        units.append({
            "section": unit["section_id"], "id": unit["id"], "kind": unit["kind"], "number": number,
            "title": unit["title"],
            "pages": str(unit["start_page"]) if unit["start_page"] == unit["end_page"]
            else f"{unit['start_page']}-{unit['end_page']}",
            "starts_here": unit["start_page"] == page,
        })

    kinds = {u["kind"] for u in units}
    page_type = ("statement" if "statement" in kinds else
                 "notes" if layout.get("section") or kinds & {"note", "schedule"} else
                 "narrative" if kinds else "other")
    section = layout.get("section") or (units[0]["section"] if units else None)

    extracted = {
        "source": "package",
        "section": section,
        "section_label": (sections.get(section) or {}).get("label") if section else None,
        "page_type": page_type,
        "text_source": row["text_source"],
        "units_on_page": units,
        "page_text": row["text"] or "",
        "statement_lines": _statement_lines(package, page),
    }
    if row["text_source"] == "ocr":
        extracted["ocr_checks"] = package.ocr_page(page)
    narrative = package.narrative_pages().get(page)
    if narrative:
        # a page outside the notes and statements: its report section and its blocks in reading order
        section = next((s for s in package.doc_sections() if s["id"] == narrative["section_id"]), None)
        extracted["page_type"] = "narrative"
        extracted["narrative"] = {
            "section": {k: section[k] for k in ("id", "title", "kind", "start_page", "end_page", "source")}
            if section else None,
            "layout_kind": narrative["layout_kind"],
            "header": narrative["header"], "footer": narrative["footer"],
            "blocks": package.page_blocks(page),
            # the passages a question can use that include this page (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §3)
            "passages": [{"id": p["id"], "path": p["path"], "pages": f"{p['start_page']}–{p['end_page']}",
                          "chars": p["chars"]}
                         for p in package.passages() if p["start_page"] <= page <= p["end_page"]],
        }

    meta = package.meta
    notices = []
    if meta.get("model") and meta["model"] != model.id:
        notices.append(f"This PDF matched {meta.get('model')}, not its own model {model.id} - worth reporting.")
    context = {
        "pdf_sha256": meta.get("pdf_sha256"),
        "model_id": meta.get("model"),
        "profile": meta.get("profile"),
        "profile_version": meta.get("profile_version"),
        "index_version": meta.get("ingester_version"),
        "registry_version": meta.get("registry_version"),
        "format": meta.get("format"),
        "package": package.path.name,
    }
    return {"page": page, "printed": row["printed"], "extracted": extracted, "context": context,
            "notices": notices}


def _statement_lines(package, page):
    out = []
    for line in package.query("SELECT id, label, kind, note_ref FROM lines WHERE pdf_page = ? ORDER BY seq",
                              (page,)):
        links = []
        for edge in package.edges(src=line["id"]):
            link = {"to": edge["dst"], "kind": edge["kind"], "confidence": edge["confidence"]}
            if link not in links:
                links.append(link)
        out.append({
            "id": line["id"], "label": line["label"], "kind": line["kind"], "note_ref": line["note_ref"],
            "values": [{"period": v["period_label"], "heading": v["heading"], "raw": v["raw"]}
                       for v in package.values(line["id"])],
            "links": links,
        })
    return out


def _raw_context(path, model):
    return {"pdf_sha256": store.pdf_sha256(path), "model_id": model.id if model else None,
            "profile": None, "profile_version": None, "index_version": pipeline.INGESTER_VERSION,
            "registry_version": registry.load().version, "format": None, "package": None}


def _raw(path, page, notice, model=None):
    doc = open_pdf(path)
    try:
        text = clean_text(doc[page - 1].get_text("text")).strip()
    finally:
        doc.close()
    extracted = {"source": "raw-pdf-text", "section": None, "section_label": None, "page_type": None,
                 "text_source": "pdf", "units_on_page": [], "page_text": text, "statement_lines": []}
    return {"page": page, "printed": None, "extracted": extracted, "context": _raw_context(path, model),
            "notices": [notice]}


def _not_transcribed(path, page, model):
    extracted = {"source": "not-transcribed", "section": None, "section_label": None, "page_type": None,
                 "text_source": "ocr", "units_on_page": [], "page_text": "", "statement_lines": []}
    return {"page": page, "printed": None, "extracted": extracted, "context": _raw_context(path, model),
            "notices": ["This scanned report has not been transcribed yet, so there is no extracted text. "
                        "Transcribe it on the Ingest screen (it costs Claude usage, so model testing never "
                        "starts it by itself)."]}


# ---------------------------------------------------------------- shown in the box

SOURCE_LABELS = {
    "package": "the report package (what the Analyzer sends to Claude)",
    "raw-pdf-text": "raw PDF text - NOT extracted by the app's rules",
    "not-transcribed": "nothing - the scanned report is not transcribed",
}


def _aligned_tables(text):
    """Pad the cells of each run of "a | b | c" lines to their column's width, so
    the box shows the columns under each other: "2017 |  | 1,088" would otherwise
    look shifted left. Display only - the stored text keeps single spaces."""
    out, block = [], []

    def flush():
        rows = [line.split(" | ") for line in block]
        widths = {}
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths.get(i, 0), len(cell))
        for row in rows:
            out.append(" | ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                                  for i, cell in enumerate(row)).rstrip())
        block.clear()

    for line in text.splitlines():
        if " | " in line:
            block.append(line)
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


def format_extracted(result):
    """The extracted data as readable text for the page's box."""
    ex = result["extracted"]
    page_line = f"PDF page {result['page']}"
    if result.get("printed"):
        page_line += f" (printed {result['printed']})"
    lines = [page_line, f"Source: {SOURCE_LABELS.get(ex['source'], ex['source'])}"]
    if ex["source"] == "package":
        lines.append(f"Section: {ex.get('section_label') or ex.get('section') or '-'} · "
                     f"Page type: {ex['page_type']} page · Text from: {ex['text_source']}")
        lines.append("")
        lines.append("== On this page ==")
        if ex["units_on_page"]:
            for u in ex["units_on_page"]:
                start = "starts here" if u["starts_here"] else "continued"
                lines.append(f"  {u['id']:<10} {u['kind']:<9} {u.get('title') or ''}  "
                             f"(PDF {u['pages']}, {start})")
        else:
            lines.append("  (nothing indexed on this page)")
    lines += ["", "== Page text ==", _aligned_tables(ex["page_text"]) or "(empty)"]
    if ex["statement_lines"]:
        lines += ["", "== Statement lines on this page =="]
        for ln in ex["statement_lines"]:
            values = " | ".join(f"{v['raw']} ({v['period'] or v['heading'] or '?'})" for v in ln["values"])
            ref = f"  [note {ln['note_ref']}]" if ln["note_ref"] else ""
            total = " (total)" if ln["kind"] == "total" else ""
            lines.append(f"  {ln['id']}  {ln['label']}{total} | {values}{ref}")
            targets = sorted({f"{k['to']} ({k['kind']})" for k in ln["links"]})
            if targets:
                lines.append(f"      links: {', '.join(targets)}")
    if ex.get("ocr_checks"):
        lines += ["", "== Transcription checks ==", json.dumps(ex["ocr_checks"], indent=2, ensure_ascii=False)]
    if ex.get("narrative"):
        n = ex["narrative"]
        s = n["section"]
        lines += ["", "== Report section ==",
                  f"  {s['title']}  ({s['kind']}, PDF {s['start_page']}–{s['end_page']}, from {s['source']})"
                  if s else "  (none)",
                  f"  Page layout: {n['layout_kind']} · running header cut: {' / '.join(n['header']) or '-'}"
                  f" · footer: {' / '.join(n['footer']) or '-'}",
                  "", "== Blocks in reading order (the formatted document shows these) =="]
        for i, b in enumerate(n["blocks"], 1):
            text = narrative_render.block_text(b).replace("\n", "\n        ")
            lines.append(f"  {i:>3}. [{b['kind']}] {text}")
        if n.get("passages"):
            lines += ["", "== Passages including this page (what a question can use) =="]
            lines += [f"  {p['id']}  {p['path']}  (PDF {p['pages']}, {p['chars']:,} characters)"
                      for p in n["passages"]]
    return "\n".join(lines)
