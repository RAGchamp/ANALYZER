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

Old reports (Companies Act 1956 schedules, no notes running headers) are
detected by report_format and indexed by legacy_index instead; see
INFO/ANALYZE-OLD-REPORTS-FUNC-PLAN.md.

Scanned (image-only) reports are indexed from their Claude page
transcriptions by transcribed_index, once they have been transcribed
(ocr_transcribe); until then NeedsTranscription is raised. See
INFO/OCR-CAPABILITY-PLAN.md.
"""

import logging
import re
from collections import Counter
from datetime import datetime

import config
from ingest import legacy_index, ocr_transcribe, profiles, report_format, statements, transcribed_index
from ingest.pdf_utils import PdfError, find_header_row, open_pdf, page_layout, page_rows

log = logging.getLogger(__name__)

INDEX_VERSION = 34

HEADING_RE = re.compile(r"^(\d{1,3})(?:\.(\d{1,2}))?\s*\.?\s+(\S.*)$")
# "(1)   SIGNIFICANT ACCOUNTING POLICIES" in plain-text filings (group 2: no sub-number)
PLAIN_HEADING_RE = re.compile(r"^\((\d{1,3})\)()\s+(\S.*)$")
CONTD_RE = re.compile(r"\(\s*CONT(?:D|INUED)?\.?\s*\)", re.I)
MAX_NOTE_STEP = 4
WRAP_MARGIN_SHARE = 0.1  # a wrapping heading line ends within 10% of the right margin
MAX_TITLE_CHARS = 90
MAX_SUBHEADINGS = 15
SUBHEADING_STOP = (
    "particulars", "in ₹", "in rs", "as at", "year ended", "march ", "total",
    "notes to", "for the year", "balance sheet", "profit and loss",
)


class NeedsTranscription(PdfError):
    """A scanned report that hasn't been (fully) transcribed yet."""

    def __init__(self, pdf_path, page_count):
        self.pdf_path = pdf_path
        self.page_count = page_count
        self.status = ocr_transcribe.status(pdf_path)
        super().__init__(
            f"This report is scanned ({page_count} pages without text). "
            "Transcribe it first (step 1).")


def _section_of(page_text):
    lowered = " ".join(page_text.split()).lower()
    for section, headers in config.NOTES_HEADERS.items():
        for header in headers:
            if header.lower() in lowered:
                return section, header
    for section, header in profiles.active().switches.extra_notes_headers:
        if header.lower() in lowered:
            return section, header
    return None, None


COMPANY_SUFFIX = r"(?:Limited|Ltd\.?|Inc\.?|Corporation|PLC|Plc)"
AUDITOR_ADDRESSEE_RE = re.compile(
    r"To the Members of\s+((?:[A-Z][A-Za-z&'.\-]*\s+){1,5}" + COMPANY_SUFFIX + ")")
COMPANY_NAME_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z&'.\-]*[ \t]+){1,5}" + COMPANY_SUFFIX + r")\b")


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


def build_index(pdf_path, force_format=None, profile=None):
    """The report's index.

    profile: the format profile to read it with (ingest/profiles; the model
    matcher chooses it). Its indexer and its switches apply, and the index's
    "format" is the profile's. Without a profile the format is detected from
    the text (report_format), as before model documents existed."""
    if profile is None:
        return _build(pdf_path, force_format, None)
    with profiles.using(profile):
        return _build(pdf_path, force_format, profile)


def _build(pdf_path, force_format, profile):
    doc = open_pdf(pdf_path)
    stat = pdf_path.stat()
    started = datetime.now()
    page_texts = [page.get_text() for page in doc]
    text_pages = sum(1 for t in page_texts if t.strip())
    scanned = text_pages < max(1, int(doc.page_count * ocr_transcribe.SCANNED_TEXT_SHARE))
    if profile is None:
        indexer = "transcribed" if scanned else None
    else:
        indexer = profile.indexer
        if scanned != (indexer == "transcribed"):
            raise PdfError(f"The {profile.id} rules read {'scanned' if indexer == 'transcribed' else 'text'} "
                           f"PDFs; this one is {'scanned' if scanned else 'a text PDF'}.")
    if indexer == "transcribed":
        return _build_transcribed(doc, pdf_path, stat, started)
    detected, signals = report_format.detect(page_texts)
    if indexer is None:
        indexer = "legacy" if (force_format or detected) == "legacy" else "notes"
    if indexer == "legacy":
        return _build_legacy_index(doc, pdf_path, stat, started, page_texts, signals, force_format)

    pages = {}           # pdf page no -> layout info (notes pages only)
    rows_by_page = {}
    statement_pages = []  # pages that belong to a primary financial statement
    text_pages = 0
    auditor_names, all_names = Counter(), Counter()
    for pno in range(1, doc.page_count + 1):
        page = doc[pno - 1]
        text = page_texts[pno - 1]
        if text.strip():
            text_pages += 1
            _count_names(text, auditor_names, all_names)
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
        if profiles.active().switches.notes_run_on:
            layout = _chrome_free_layout(page, rows, layout)
        layout["section"] = section
        pages[pno] = layout
        rows_by_page[pno] = rows

    _check_text_layer(text_pages, doc.page_count)
    if profiles.active().switches.continuation_pages:
        _fill_plain_text_gaps(doc, pages, rows_by_page, statement_pages)
    if profiles.active().switches.notes_run_on:
        _fill_run_on_pages(doc, pages, rows_by_page)
    if profiles.active().switches.untitled_statement_pages:
        _add_untitled_statement_pages(doc, pages, statement_pages)

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
        "format": profile.format if profile else (force_format if force_format in ("modern", "us-10k") else (
            detected if detected in ("modern", "us-10k") else "modern")),
        "format_source": "manual" if force_format else "detected",
        "format_signals": signals,
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


def _count_names(text, auditor_names, all_names):
    for match in AUDITOR_ADDRESSEE_RE.finditer(text):
        auditor_names[" ".join(match.group(1).split())] += 1
    for match in COMPANY_NAME_RE.finditer(text):
        all_names[match.group(1).strip()] += 1


def _check_text_layer(text_pages, page_count):
    if text_pages < max(1, page_count // 10):
        raise PdfError(
            "This PDF has (almost) no extractable text - it looks scanned. "
            "OCR would be required, which is out of scope for v1."
        )


def _build_transcribed(doc, pdf_path, stat, started):
    status = ocr_transcribe.status(pdf_path)
    if not status["complete"]:
        raise NeedsTranscription(pdf_path, doc.page_count)
    sections, facts, page_info = transcribed_index.build_transcribed_index(pdf_path)
    registrant = next(iter(sections.values()), {})
    log.info("Built transcribed index for %s in %.1fs: %s", pdf_path.name,
             (datetime.now() - started).total_seconds(),
             {k: (len(s["notes"]), len(s["statements"])) for k, s in sections.items()})
    return {
        "version": INDEX_VERSION,
        "format": "transcribed",
        "format_source": "detected",
        "format_signals": {},
        **facts,
        "ocr": {k: status[k] for k in ("done", "failed", "unreadable", "to_check", "untied",
                                       "seconds", "cost_usd")} | {"pages": status["done"]},
        "ocr_revision": status["revision"],
        "pdf": str(pdf_path),
        "pdf_name": pdf_path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "page_count": doc.page_count,
        "company": registrant.get("entity") or pdf_path.stem,
        "built_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "pages": page_info,
        "sections": sections,
    }


def _build_legacy_index(doc, pdf_path, stat, started, page_texts, signals, force_format):
    auditor_names, all_names = Counter(), Counter()
    text_pages = 0
    for text in page_texts:
        if text.strip():
            text_pages += 1
            _count_names(text, auditor_names, all_names)
    _check_text_layer(text_pages, doc.page_count)
    section, layouts, facts = legacy_index.build_legacy_section(doc)
    for statement in section["statements"]:
        statement["text"] = legacy_index.legacy_clean(
            statements.extract_statement(doc, statement, section["label"]))
        for page in statement["pages"]:
            for cells in page.get("rows", []):
                for cell in cells:
                    cell["t"] = legacy_index.legacy_clean(cell["t"])
    log.info("Built legacy index for %s in %.1fs: %s statements, %s",
             pdf_path.name, (datetime.now() - started).total_seconds(),
             [s["type"] for s in section["statements"]], facts["unit_counts"])
    return {
        "version": INDEX_VERSION,
        "format": "legacy",
        "format_source": "manual" if force_format else "detected",
        "format_signals": signals,
        **facts,
        "pdf": str(pdf_path),
        "pdf_name": pdf_path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "page_count": doc.page_count,
        "company": _pick_company(auditor_names, all_names, pdf_path),
        "built_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "pages": {str(p): info for p, info in layouts.items()},
        "sections": {legacy_index.SECTION: section} if section["notes"] or section["statements"] else {},
    }


def _monospaced(rows):
    """Nearly every row in a typewriter font (a "Page 33" footer may not be)."""
    mono = sum(1 for r in rows if "courier" in r["font"].lower() or "mono" in r["font"].lower())
    return bool(rows) and mono >= 0.9 * len(rows)


def _fill_plain_text_gaps(doc, pages, rows_by_page, statement_pages):
    """Plain-text filings (EDGAR 10-Ks of the 1990s) print the notes' running
    header once per filing page, and a filing page that overflows continues on
    a PDF page without it (BRK-1994: PDF pp.30-31 inside note 5). A page between
    two pages of one notes section, set entirely in a monospaced font and not a
    statement, belongs to that section. Other reports are left as they are."""
    statement_nos = {info["pdf_page"] for info in statement_pages}
    for section in config.NOTES_HEADERS:
        known = sorted(p for p, info in pages.items() if info["section"] == section)
        for pno in range(known[0] + 1, known[-1]) if known else ():
            if pno in pages or pno in statement_nos:
                continue
            rows = page_rows(doc[pno - 1])
            if not _monospaced(rows):
                continue
            layout = page_layout(doc[pno - 1], rows)
            layout["section"] = section
            pages[pno] = layout
            rows_by_page[pno] = rows


# Browser-printed EDGAR pages: "9/27/26, 1:05 PM   20-F" at the top, and a "Table of Contents" link.
PAGE_CHROME_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*[AP]M\b|^Table of Contents$", re.I)
# A page that starts the part after the notes.
RUN_ON_STOP_RE = re.compile(
    r"^(?:Item\s+\d+[A-Z]?\.|SIGNATURES?$|(?:\d+[A-Z]?\s+)?Reports? of Independent Registered Public "
    r"Accounting Firm|Exhibit Index)", re.I)
MAX_RUN_ON_PAGES = 150


PAGE_URL_RE = re.compile(r"^https?://")


def _chrome_free_layout(page, rows, layout=None):
    """page_layout() without the browser's print chrome: the date line and a
    "Table of Contents" link above the body, the page's URL below it."""
    layout = layout or page_layout(page, rows)
    height = page.rect.height
    chrome = [r for r in rows if r["y0"] < height * 0.12 and PAGE_CHROME_RE.match(r["text"])]
    footer = [r for r in rows if r["y0"] > height * 0.85 and PAGE_URL_RE.match(r["text"])]
    if chrome:
        layout["top"] = round(max(layout["top"], max(r["y1"] for r in chrome)), 1)
    if footer:
        layout["bottom"] = round(min(layout["bottom"], min(r["y0"] for r in footer)) - 0.5, 1)
    if chrome or footer:
        body = [r for r in rows if r["y0"] > layout["top"] + 0.5 and r["y1"] < layout["bottom"]]
        layout["body_top"] = round(min((r["y0"] for r in body), default=layout["top"]), 1)
    return layout


def _fill_run_on_pages(doc, pages, rows_by_page):
    """20-F notes printed from EDGAR carry their heading ("Overview and Notes to the
    Consolidated Financial Statements", "1.6 Notes to the Financial Statements") on their
    first page only. The pages after it belong to that section until another section's
    page or a page that starts the next part (RUN_ON_STOP_RE). The statements come before
    the notes, so a statement-like page inside them (BHP p.295: a note's "Cash flow
    statement" table) does not end the section."""
    for start in sorted(pages):
        section = pages[start]["section"]
        for pno in range(start + 1, min(doc.page_count, start + MAX_RUN_ON_PAGES) + 1):
            if pno in pages:
                if pages[pno]["section"] != section:
                    break
                continue
            rows = page_rows(doc[pno - 1])
            layout = _chrome_free_layout(doc[pno - 1], rows)
            body = [r for r in rows if layout["top"] < r["y0"] < layout["bottom"]]
            if any(RUN_ON_STOP_RE.match(r["text"]) for r in body[:3]):
                break
            layout["section"] = section
            pages[pno] = layout
            rows_by_page[pno] = rows


FIGURE_ROW_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\(\d[\d,]*\)|—")


def _add_untitled_statement_pages(doc, pages, statement_pages):
    """A statement continued on the next page with no title, only figure rows
    (Infosys p.172, the rest of the Changes in Equity): that page is the same
    statement. Pages that are notes pages or statements already are left alone."""
    known = {info["pdf_page"] for info in statement_pages}
    for info in sorted(statement_pages, key=lambda i: i["pdf_page"]):
        pno = info["pdf_page"] + 1
        if pno > doc.page_count or pno in known or pno in pages or info["rotation"]:
            continue
        rows = page_rows(doc[pno - 1])
        layout = _chrome_free_layout(doc[pno - 1], rows)
        body = [r for r in rows if layout["top"] < r["y0"] < layout["bottom"]]
        if len(body) < 5 or sum(1 for r in body if FIGURE_ROW_RE.search(r["text"])) < 0.6 * len(body):
            continue
        statement_pages.append({**info, "pdf_page": pno, "printed": layout["printed"],
                                "top": layout["top"], "bottom": layout["bottom"]})
        known.add(pno)


def _is_capitals(title):
    letters = re.sub(r"[^A-Za-z]", "", CONTD_RE.sub("", title))
    return len(letters) >= 3 and letters.isupper()


def _heading_candidates(section_pages, pages, rows_by_page, plain=False):
    """Bold, left-margin body rows that start with a note number.

    plain: for reports in one font with no bold at all (plain-text filings),
    left-margin rows in capitals: "(1)   SIGNIFICANT ACCOUNTING POLICIES",
    "2. DEBT". Capitals keep numbered body paragraphs out."""
    candidates = []
    for pno in section_pages:
        layout = pages[pno]
        body = [r for r in rows_by_page[pno]
                if layout["top"] < r["y0"] < layout["bottom"]]
        if not body:
            continue
        left = min(r["x0"] for r in body)
        for i, row in enumerate(body):
            if row["x0"] > left + 25 or (not plain and not row["bold"]):
                continue
            match = HEADING_RE.match(row["text"]) or (PLAIN_HEADING_RE.match(row["text"]) if plain else None)
            if not match or (plain and not _is_capitals(match.group(3))):
                continue
            candidates.append((pno, i, row, match, body))
    return candidates


def _find_notes(section_pages, pages, rows_by_page):
    candidates = _heading_candidates(section_pages, pages, rows_by_page)
    if not candidates and profiles.active().switches.plain_note_headings:
        # no bold note headings at all: a plain-text filing
        candidates = _heading_candidates(section_pages, pages, rows_by_page, plain=True)
    # Real note headings share one font; keep only the dominant one(s) so
    # bold table labels or list items in other fonts don't qualify.
    fonts = Counter(c[2]["font"] for c in candidates)
    top_count = max(fonts.values(), default=0)
    heading_fonts = {f for f, n in fonts.items() if n >= max(3, top_count * 0.2)}

    if profiles.active().switches.decimal_notes:
        return _decimal_notes([c for c in candidates if c[2]["font"] in heading_fonts])

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

        notes.append({
            "no": number,
            "title": _truncate(_full_title(title, row, i, body)),
            "start_page": pno,
            "start_y": round(row["y0"], 1),
            "end_page": None,
            "end_y": None,
            "subheadings": [f"{number}.{sub} {title}"] if sub else [],
        })
        last_no = number
    return notes


def _full_title(title, row, i, body):
    """Headings can wrap onto a second bold line directly below - but only
    a line that runs to the right margin wraps. A short heading ("13.
    Debt" in the Chubb 10-K) is followed by bold table headings or a
    sub-heading ("December 31", "a) Common Shares"), not more title."""
    full_title = title
    right = max(r["x1"] for r in body)
    wrap_from = right - WRAP_MARGIN_SHARE * (right - min(r["x0"] for r in body))
    for nxt in body[i + 1:i + 3]:
        gap = nxt["y0"] - row["y1"]
        if (row["x1"] >= wrap_from and nxt["font"] == row["font"] and 0 <= gap < 8
                and not HEADING_RE.match(nxt["text"])
                and not CONTD_RE.search(nxt["text"])):
            full_title += " " + nxt["text"]
            row = nxt
        else:
            break
    return full_title


def _decimal_step(last, key):
    """(2, 5) may follow (2, 4) or (1, 6); numbers advance in small steps."""
    if last is None:
        return key[1] <= MAX_NOTE_STEP
    (last_n, last_m), (n, m) = last, key
    if n == last_n:
        return last_m < m <= last_m + MAX_NOTE_STEP
    return last_n < n <= last_n + MAX_NOTE_STEP and m <= MAX_NOTE_STEP


def _decimal_notes(candidates):
    """Profile switch decimal_notes: the notes are the "N.M" headings ("2.1 Cash
    and cash equivalents"), numbered "2.1" as the statements cite them. A plain "N"
    heading only groups them ("2 Notes to the consolidated financial statements")."""
    notes = []
    last = None
    for pno, i, row, match, body in candidates:
        sub = match.group(2)
        title = match.group(3).strip()
        if not sub or CONTD_RE.search(title) or not (title[0].isalpha() or title[0] == "("):
            continue
        key = (int(match.group(1)), int(sub))
        if not _decimal_step(last, key):
            continue
        notes.append({
            "no": f"{key[0]}.{key[1]}",
            "title": _truncate(_full_title(title, row, i, body)),
            "start_page": pno,
            "start_y": round(row["y0"], 1),
            "end_page": None,
            "end_y": None,
            "subheadings": [],
        })
        last = key
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
        start = pages.get(note["start_page"], {}).get("printed")
        end = pages.get(note["end_page"], {}).get("printed")
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
    if index.get("format") == "legacy":
        return legacy_index.summary(index)
    if index.get("format") == "transcribed":
        return transcribed_index.summary(index)
    parts = []
    for section in index["sections"].values():
        parts.append(
            f"{section['label']} notes: {len(section['notes'])} "
            f"(PDF p.{section['first_page']}–{section['last_page']})"
        )
    return " · ".join(parts) if parts else "No notes sections found"
