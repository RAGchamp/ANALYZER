"""Low-level PDF helpers shared by the notes index and the extractor.

PyMuPDF is imported as `pymupdf`, not `fitz`: a conflicting `fitz` package
is installed in this environment and breaks `import fitz`.
"""

import re
import unicodedata

import pymupdf

BOLD_FONT_HINTS = ("Bold", "Black", "Heavy", "Semibold", "SemiBold", "Demi")
YEAR_ENDED_RE = re.compile(r"^(for the (year|period) ended|as at)\b", re.I)
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")


class PdfError(Exception):
    """A PDF that can't be analyzed (missing, encrypted, no text layer)."""


def open_pdf(path):
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # pymupdf raises several exception types
        raise PdfError(f"Could not open PDF: {exc}") from exc
    if doc.needs_pass:
        raise PdfError("This PDF is password-protected and can't be read.")
    return doc


def clean_text(text):
    """Normalize ligatures (ﬁ → fi) and the rupee glyph.

    This report's Rupee font draws ₹ as a backtick character."""
    text = unicodedata.normalize("NFKC", text)
    return text.replace("`", "₹")


def is_bold(span):
    return bool(span["flags"] & 16) or any(h in span["font"] for h in BOLD_FONT_HINTS)


def page_rows(page, y_tolerance=2.0, horizontal_only=False):
    """Visual text rows on a page, built from spans.

    Spans that share a baseline are merged even when PyMuPDF put them in
    different blocks - headings like "1. " + "CORPORATE INFORMATION" are
    split that way in this report.

    horizontal_only drops sideways text, e.g. the running header of a page
    that was de-rotated to read a landscape table.

    Returns dicts: {x0, y0, x1, y1, text, font, size, bold}, where the font
    info comes from the row's first non-blank span."""
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if horizontal_only and abs(line["dir"][0]) < 0.99:
                continue
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append(span)
    spans.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))

    rows = []
    for span in spans:
        x0, y0, x1, y1 = span["bbox"]
        for row in rows:
            if abs(row["y0"] - y0) <= y_tolerance:
                row["spans"].append(span)
                row["x0"] = min(row["x0"], x0)
                row["x1"] = max(row["x1"], x1)
                row["y1"] = max(row["y1"], y1)
                break
        else:
            rows.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "spans": [span]})

    out = []
    for row in rows:
        row["spans"].sort(key=lambda s: s["bbox"][0])
        first = row["spans"][0]
        text = " ".join(s["text"].strip() for s in row["spans"])
        out.append({
            "x0": row["x0"], "y0": row["y0"], "x1": row["x1"], "y1": row["y1"],
            "text": clean_text(re.sub(r"\s+", " ", text).strip()),
            "font": first["font"],
            "size": round(first["size"], 1),
            "bold": is_bold(first),
        })
    out.sort(key=lambda r: (r["y0"], r["x0"]))
    return out


def find_header_row(rows, header_text, page_height):
    """The running-header row in the top 20% of the page that contains
    header_text, or None. A mention of the same words in body text (e.g.
    in the auditor's report) doesn't count."""
    if not header_text:
        return None
    wanted = header_text.lower()
    for row in rows:
        if row["y0"] > page_height * 0.2:
            break
        if wanted in row["text"].lower():
            return row
    return None


def page_layout(page, rows, section_header_text=None):
    """Where the body of a notes page starts/ends, and its printed number.

    - top: bottom edge of the running header block ("Notes to ... /
      for the year ended ..."), so extraction skips it.
    - bottom: top edge of the footer (printed page number) area.
    - body_top: y of the first body row, used to decide whether a note
      heading sits at the very top of a page.
    - printed: the page number printed on the page, e.g. "402"."""
    height = page.rect.height
    top = 0.0
    header_row = find_header_row(rows, section_header_text, height)
    if header_row:
        top = header_row["y1"]
        # The "for the year ended ..." sub-line sits just below the header.
        for row in rows:
            if 0 <= row["y0"] - top < 30 and YEAR_ENDED_RE.match(row["text"]):
                top = row["y1"]
                break

    printed = None
    bottom = height
    for row in rows:
        near_edge = row["y0"] > height * 0.9 or row["y1"] < height * 0.12
        if near_edge and PAGE_NUMBER_RE.match(row["text"]):
            printed = row["text"]
        if row["y0"] > height * 0.9:
            bottom = min(bottom, row["y0"])

    body = [r for r in rows if r["y0"] > top + 0.5 and r["y1"] < bottom]
    body_top = min((r["y0"] for r in body), default=top)
    return {
        "top": round(top, 1),
        "bottom": round(bottom, 1),
        "body_top": round(body_top, 1),
        "printed": printed,
    }
