"""Build (and cache) the Notes Index for an annual report PDF.

The index lists every note in the Consolidated and Standalone "Notes to
Financial Statements" sections with its number, title and exact extent
(start page + y, end page + y), so the extractor can pull just that note.

Detection is purely from the page text/fonts - the sample report has no
PDF bookmarks:
  1. A page belongs to a section if it carries that section's running
     header ("Notes to Consolidated Financial Statements", ...).
  2. Note headings are bold rows at the left margin starting with a number
     ("21. INCOME AND DEFERRED TAXES", "5 INTANGIBLE ASSETS...",
     "54.1 DETAILS OF FUNDS..."). "(CONTD.)" repeats are skipped.
  3. Note numbers must increase in small steps, which rejects bold
     numbers that aren't headings.

All page numbers are PDF page numbers (1-based). The number printed on the
page is kept separately for display.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime

import config
import statements
from pdf_utils import PdfError, find_header_row, open_pdf, page_layout, page_rows

log = logging.getLogger(__name__)

INDEX_VERSION = 7

HEADING_RE = re.compile(r"^(\d{1,3})(?:\.(\d{1,2}))?\s*\.?\s+(\S.*)$")
CONTD_RE = re.compile(r"\(\s*CONT(?:D|INUED)?\.?\s*\)", re.I)
MAX_NOTE_STEP = 4
MAX_TITLE_CHARS = 90
MAX_SUBHEADINGS = 15
SUBHEADING_STOP = (
    "particulars", "in ₹", "in rs", "as at", "year ended", "march ", "total",
    "notes to", "for the year", "balance sheet", "profit and loss",
)


def cache_path(pdf_path):
    return config.CACHE_DIR / f"{pdf_path.stem}.notes-index.json"


def load_or_build(pdf_path):
    """Return the cached index if it still matches the PDF, else rebuild."""
    stat = pdf_path.stat()
    path = cache_path(pdf_path)
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (cached.get("version") == INDEX_VERSION
                    and cached.get("size") == stat.st_size
                    and cached.get("mtime") == stat.st_mtime):
                return cached
        except (json.JSONDecodeError, OSError):
            pass
    index = build_index(pdf_path)
    path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def _section_of(page_text):
    lowered = " ".join(page_text.split()).lower()
    for section, headers in config.NOTES_HEADERS.items():
        for header in headers:
            if header.lower() in lowered:
                return section, header
    return None, None


COMPANY_SUFFIX = r"(?:Limited|Ltd\.?|Inc\.?|Corporation|PLC|Plc)"
AUDITOR_ADDRESSEE_RE = re.compile(
    r"To the Members of\s+((?:[A-Z][A-Za-z&'.\-]*\s+){1,5}" + COMPANY_SUFFIX + ")")
COMPANY_NAME_RE = re.compile(
    r"((?:[A-Z][A-Za-z&'.\-]*[ 	]+){1,5}" + COMPANY_SUFFIX + r")")


def _company_from_filename(pdf_path):
    """Fallback: "Bharat-Forge-IR-2026-conv" -> "Bharat Forge"."""
    words = []
    for word in re.split(r"[-_\s]+", pdf_path.stem):
        if re.fullmatch(r"\d+|IR|AR|FY\d*|annual|report|integrated|conv|single|page", word, re.I):
            break
        words.append(word)
    return " ".join(words) or pdf_path.stem


def _pick_company(auditor_names, all_names, pdf_path):
    """The company the auditor's report is addressed to ("To the Members of
    X Limited") is the most reliable. Otherwise the most-mentioned name."""
    if auditor_names:
        return auditor_names.most_common(1)[0][0]
    for name, _ in all_names.most_common():
        if "private" not in name.lower():
            return name
    return _company_from_filename(pdf_path)


def _truncate(title):
    title = CONTD_RE.sub("", title).strip(" -:")
    if len(title) <= MAX_TITLE_CHARS:
        return title
    return title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0] + "…"


def build_index(pdf_path):
    doc = open_pdf(pdf_path)
    stat = pdf_path.stat()
    started = datetime.now()

    pages = {}           # pdf page no -> layout info (notes pages only)
    rows_by_page = {}
    statement_pages = []  # pages that belong to a primary financial statement
    text_pages = 0
    auditor_names, all_names = Counter(), Counter()
    for pno in range(1, doc.page_count + 1):
        page = doc[pno - 1]
        text = page.get_text()
        if text.strip():
            text_pages += 1
            for match in AUDITOR_ADDRESSEE_RE.finditer(text):
                auditor_names[" ".join(match.group(1).split())] += 1
            for match in COMPANY_NAME_RE.finditer(text):
                all_names[match.group(1).strip()] += 1
        section, header = _section_of(text)
        rows = page_rows(page) if section else None
        if not section or not find_header_row(rows, header, page.rect.height):
            # Not a notes page (header words may just be mentioned in body
            # text) - it may be a Balance Sheet, P&L, Cash Flow or Equity page.
            info = statements.detect_statement_page(doc, pno, text)
            if info:
                statement_pages.append(info)
            continue
        layout = page_layout(page, rows, header)
        layout["section"] = section
        pages[pno] = layout
        rows_by_page[pno] = rows

    if text_pages < max(1, doc.page_count // 10):
        raise PdfError(
            "This PDF has (almost) no extractable text - it looks scanned. "
            "OCR would be required, which is out of scope for v1."
        )

    sections = {}
    for section in config.NOTES_HEADERS:
        section_pages = sorted(p for p, info in pages.items() if info["section"] == section)
        if not section_pages:
            continue
        notes = _find_notes(section_pages, pages, rows_by_page)
        _set_note_ends(notes, section_pages, pages)
        _collect_subheadings(notes, section_pages, pages, rows_by_page)
        sections[section] = {
            "label": config.SECTION_LABELS[section],
            "first_page": section_pages[0],
            "last_page": section_pages[-1],
            "page_count": len(section_pages),
            "notes": notes,
            "statements": statements.group_statements(statement_pages, section_pages[0]),
        }
        # "Load" the statements now: extract each one's text (rotating
        # sideways pages first) and cache it with the index.
        for statement in sections[section]["statements"]:
            statement["text"] = statements.extract_statement(
                doc, statement, config.SECTION_LABELS[section])

    log.info("Built notes index for %s in %.1fs: %s", pdf_path.name,
             (datetime.now() - started).total_seconds(),
             {s: (len(v["notes"]), [st["type"] for st in v["statements"]])
              for s, v in sections.items()})
    return {
        "version": INDEX_VERSION,
        "pdf": str(pdf_path),
        "pdf_name": pdf_path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "page_count": doc.page_count,
        "company": _pick_company(auditor_names, all_names, pdf_path),
        "built_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "pages": {str(p): info for p, info in pages.items()},
        "sections": sections,
    }


def _heading_candidates(section_pages, pages, rows_by_page):
    """Bold, left-margin body rows that start with a note number."""
    candidates = []
    for pno in section_pages:
        layout = pages[pno]
        body = [r for r in rows_by_page[pno]
                if layout["top"] < r["y0"] < layout["bottom"]]
        if not body:
            continue
        left = min(r["x0"] for r in body)
        for i, row in enumerate(body):
            if not row["bold"] or row["x0"] > left + 25:
                continue
            match = HEADING_RE.match(row["text"])
            if not match:
                continue
            candidates.append((pno, i, row, match, body))
    return candidates


def _find_notes(section_pages, pages, rows_by_page):
    candidates = _heading_candidates(section_pages, pages, rows_by_page)
    # Real note headings share one font; keep only the dominant one(s) so
    # bold table labels or list items in other fonts don't qualify.
    fonts = Counter(c[2]["font"] for c in candidates)
    top_count = max(fonts.values(), default=0)
    heading_fonts = {f for f, n in fonts.items() if n >= max(3, top_count * 0.2)}

    notes = []
    last_no = 0
    for pno, i, row, match, body in candidates:
        if row["font"] not in heading_fonts:
            continue
        number = int(match.group(1))
        sub = match.group(2)
        title = match.group(3).strip()
        if CONTD_RE.search(title) or title.upper().startswith("(CONTD"):
            # A "(CONTD.)" repeat of a note we never saw start means its
            # real heading wasn't readable (e.g. rotated on a landscape
            # table page). Assume it began at the top of the previous page.
            if last_no < number <= last_no + MAX_NOTE_STEP and not sub:
                prev = pno - 1
                if prev in pages and (not notes or prev > notes[-1]["start_page"]):
                    start_page = prev
                else:
                    start_page = pno
                notes.append({
                    "no": number,
                    "title": _truncate(title) or f"Note {number}",
                    "start_page": start_page,
                    "start_y": pages[start_page]["body_top"],
                    "end_page": None,
                    "end_y": None,
                    "subheadings": [],
                    "inferred_start": True,
                })
                last_no = number
            continue
        if not (last_no < number <= last_no + MAX_NOTE_STEP):
            if sub and number == last_no and notes:
                notes[-1]["subheadings"].append(f"{number}.{sub} {title}")
            continue
        if not (title[0].isalpha() or title[0] == "("):
            continue

        heading_y = row["y0"]
        # Headings can wrap onto a second bold line directly below.
        full_title = title
        for nxt in body[i + 1:i + 3]:
            gap = nxt["y0"] - row["y1"]
            if (nxt["font"] == row["font"] and 0 <= gap < 8
                    and not HEADING_RE.match(nxt["text"])
                    and not CONTD_RE.search(nxt["text"])):
                full_title += " " + nxt["text"]
                row = nxt
            else:
                break

        notes.append({
            "no": number,
            "title": _truncate(full_title),
            "start_page": pno,
            "start_y": round(heading_y, 1),
            "end_page": None,
            "end_y": None,
            "subheadings": [f"{number}.{sub} {title}"] if sub else [],
        })
        last_no = number
    return notes


def _set_note_ends(notes, section_pages, pages):
    """A note ends just before the next note's heading. If that heading is
    the first thing on its page, the note ends on the previous page."""
    for note, nxt in zip(notes, notes[1:] + [None]):
        if nxt is None:
            note["end_page"] = section_pages[-1]
            note["end_y"] = None
        elif nxt["start_y"] <= pages[nxt["start_page"]]["body_top"] + 3:
            note["end_page"] = max(note["start_page"], nxt["start_page"] - 1)
            note["end_y"] = None
        else:
            note["end_page"] = nxt["start_page"]
            note["end_y"] = nxt["start_y"]
        start = pages[note["start_page"]]["printed"]
        end = pages[note["end_page"]]["printed"]
        note["printed_pages"] = (
            f"{start}" if start == end else f"{start}-{end}"
        ) if start and end else None


def _note_at(notes, pno, y):
    for note in notes:
        after_start = (pno, y) >= (note["start_page"], note["start_y"])
        end_y = note["end_y"] if note["end_y"] is not None else float("inf")
        before_end = (pno, y) < (note["end_page"], end_y)
        if after_start and before_end:
            return note
    return None


def _collect_subheadings(notes, section_pages, pages, rows_by_page):
    """Bold sub-headings inside each note (e.g. "Reconciliation of deferred
    tax liabilities and assets"). Given to the selector as extra context."""
    for pno in section_pages:
        layout = pages[pno]
        for row in rows_by_page[pno]:
            if not row["bold"] or not (layout["top"] < row["y0"] < layout["bottom"]):
                continue
            text = row["text"].strip()
            lowered = text.lower()
            if (len(text) < 8 or len(re.findall(r"[A-Za-z]{2,}", text)) < 2
                    or lowered.startswith(SUBHEADING_STOP)
                    or CONTD_RE.search(text)
                    or (HEADING_RE.match(text) and not re.match(r"^\d+\.\d+", text))):
                continue
            note = _note_at(notes, pno, row["y0"])
            if (note and text not in note["subheadings"]
                    and len(note["subheadings"]) < MAX_SUBHEADINGS):
                note["subheadings"].append(text[:120])


def find_note(index, section, number):
    for note in index["sections"].get(section, {}).get("notes", []):
        if note["no"] == number:
            return note
    return None


def summary(index):
    """Short human-readable description for the UI."""
    parts = []
    for section in index["sections"].values():
        parts.append(
            f"{section['label']} notes: {len(section['notes'])} "
            f"(PDF p.{section['first_page']}–{section['last_page']})"
        )
    return " · ".join(parts) if parts else "No notes sections found"
