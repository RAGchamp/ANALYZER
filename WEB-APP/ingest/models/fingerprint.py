"""A report's fingerprint: a small, deterministic description of its layout and
wording (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §4.1), used to find the model
documents it most resembles.

    A physical     text-layer share, page shape, length, producer
    B typography   monospaced / serif / sans shares, bold share, large titles
    C document     cover form (10-K, 20-F, 40-F), accounting framework, currency, units
    D era          the decade on the cover
    E structure    notes running header, note-heading styles, statement titles,
                   schedules, "Item 8" / "Item 18"

Plain text is read from every page; fonts from at most SAMPLE_PAGES pages.
Bump FINGERPRINT_VERSION whenever a feature changes, then refresh the registry
(`python -m ingest.models refresh`).
"""

import re
from collections import Counter

import pymupdf

from ingest.pdf_utils import open_pdf

TEXT_ONLY = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

FINGERPRINT_VERSION = 1
SAMPLE_PAGES = 60
COVER_PAGES = 5

PRODUCERS = [
    ("edgar-agent", r"EDGR|EDGAR|Workiva|Toppan|Merrill|Donnelley|RR Donnelley|Broadridge|GlobalOne"),
    ("thomson-reuters", r"iText|Thomson"),
    ("browser", r"Skia|Chrome|Chromium"),
    ("acrobat-scan", r"Image Conversion|Paper Capture|Scan"),
    ("distiller", r"Distiller|PDFMaker|Acrobat"),
    ("office", r"Microsoft|Word|Excel|PowerPoint|LibreOffice|OpenOffice"),
    ("online-converter", r"Online2PDF|ilovepdf|smallpdf|Nitro|pdf24"),
    ("design", r"InDesign|Quark|Illustrator|PDFlib"),
]
FRAMEWORKS = {
    "ind-as": r"\bInd\s?AS\b",
    "ifrs": r"\bIFRS\b|International Financial Reporting Standards",
    "us-gaap": r"\bU\.?\s?S\.?\s?GAAP\b|generally accepted accounting principles in the United States",
    "companies-act-1956": r"Companies Act,?\s+1956",
    "schedule-vi": r"Schedule\s+VI\b",
    "companies-act-2013": r"Companies Act,?\s+2013",
}
CURRENCIES = {
    "inr": r"₹|\bRs\.|\bINR\b|\bcrores?\b|\blakhs?\b",
    "usd": r"\bUS\$|\bU\.S\. dollars|\$\s?\d",
    "aud": r"\bA\$|Australian dollars?",
    "cad": r"\bC\$|Canadian dollars?",
    "eur": r"€|\bEUR\b",
    "gbp": r"£|\bGBP\b",
}
UNITS = {
    "thousands": r"\bin thousands\b|\b\(?in\s+(?:\$|₹|US\$)?\s*thousands\b|'000",
    "millions": r"\bin millions\b|\bmillions of\b|₹\s*in\s*million|in\s*₹\s*million|US\$\s*M\b|\$\s*million",
    "crores": r"\bcrores?\b",
    "lakhs": r"\blakhs?\b",
}
NOTES_HEADER_RE = re.compile(
    r"notes?\s+(?:to|forming part of|on)\s+(?:the\s+)?(?:consolidated\s+|standalone\s+|group\s+|parent\s+company\s+)?"
    r"(?:financial\s+statements|accounts)", re.I)
NOTE_HEADINGS = {
    "number-dot": re.compile(r"^\d{1,3}\.\s+[A-Z]"),
    "number-caps": re.compile(r"^\d{1,3}\s+[A-Z][A-Z ,&'()-]{6,}$"),
    "paren-caps": re.compile(r"^\(\d{1,3}\)\s+[A-Z][A-Z ,&'.()-]{5,}$"),
    "note-word": re.compile(r"^Note\s+\d{1,3}\b", re.I),
    "section-numbered": re.compile(r"^\d{1,2}\.\d{1,2}\s+[A-Z]"),
}
STATEMENT_TITLE_RE = re.compile(
    r"^(?:\d{1,2}\.\d{1,2}\s+)?(?:consolidated\s+|standalone\s+)?(?:statements?\s+of\s+(?:profit|income|operations|"
    r"earnings|comprehensive|cash\s+flows?|changes\s+in|financial\s+position|shareholders|stockholders)|balance\s+sheets?|"
    r"income\s+statement|cash\s+flow\s+statement|profit\s+(?:&|and)\s+loss)", re.I)
SCHEDULE_RE = re.compile(r"^\s*schedule\s*['‘’]?\s*(?:[A-Z]{1,2}|\d{1,2}|[IVXL]{1,5})\b", re.I)
ITEM8_RE = re.compile(r"\bITEM\s+8\.?\s", re.I)
ITEM18_RE = re.compile(r"\bITEM\s+18\.?\s", re.I)
FORM_RE = re.compile(r"\bFORM\s+(10-K|20-F|40-F|10-KSB)\b")
YEAR_RE = re.compile(r"\b(19[4-9]\d|20[0-4]\d)\b")


def _font_class(name):
    lowered = name.lower()
    if "courier" in lowered or "mono" in lowered or "consol" in lowered:
        return "mono"
    if any(k in lowered for k in ("times", "serif", "garamond", "georgia", "minion", "caslon", "baskerville",
                                  "cambria", "bookman", "palatino", "century")) and "sans" not in lowered:
        return "serif"
    return "sans"


def _page_shape(page):
    w, h = page.rect.width, page.rect.height
    if w > h:
        return "landscape"
    if abs(w - 595) < 25 and abs(h - 842) < 25:
        return "a4"
    if abs(w - 612) < 25 and abs(h - 792) < 25:
        return "letter"
    return "other"


def _band(pages):
    return "<60" if pages < 60 else "60-150" if pages < 150 else "150-300" if pages < 300 else "300+"


def _present(patterns, text, minimum):
    return sorted(k for k, pat in patterns.items() if len(re.findall(pat, text)) >= minimum)


def fingerprint(pdf_path):
    doc = open_pdf(pdf_path)
    try:
        count = doc.page_count
        texts = [p.get_text() for p in doc]
        text_pages = sum(1 for t in texts if t.strip())
        producer = doc.metadata.get("producer") or ""
        family = next((name for name, pat in PRODUCERS if re.search(pat, producer, re.I)), "other")

        # B. typography, from sampled pages
        step = max(1, count // SAMPLE_PAGES)
        sample = sorted(set(range(min(COVER_PAGES, count))) | set(range(0, count, step)))[:SAMPLE_PAGES + COVER_PAGES]
        chars, bold, sizes, big_pages = Counter(), 0, Counter(), 0
        if text_pages < max(1, count // 10):
            sample = []                       # scanned: no fonts to read (and images are slow)
        for i in sample:
            page_big = False
            for block in doc[i].get_text("dict", flags=TEXT_ONLY)["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        n = len(span["text"].strip())
                        if not n:
                            continue
                        chars[_font_class(span["font"])] += n
                        sizes[round(span["size"])] += n
                        if span["flags"] & 16 or "bold" in span["font"].lower() or "black" in span["font"].lower():
                            bold += n
                        if span["size"] >= 14 and i >= COVER_PAGES:
                            page_big = True
            big_pages += page_big
        total = sum(chars.values()) or 1
        body_pages = max(1, len([i for i in sample if i >= COVER_PAGES]))
        shape = _page_shape(doc[0]) if count else "other"
    finally:
        doc.close()

    everything = "\n".join(texts)
    cover = "\n".join(texts[:COVER_PAGES])
    form = FORM_RE.search(cover)
    years = Counter(int(y) for y in YEAR_RE.findall(cover))
    first_lines = [[ln.strip() for ln in t.splitlines() if ln.strip()][:6] for t in texts]
    lines = [ln.strip() for t in texts for ln in t.splitlines() if ln.strip()]
    per_100 = 100.0 / max(1, text_pages)
    headings = {name: round(sum(1 for ln in lines if pat.match(ln)) * per_100, 1)
                for name, pat in NOTE_HEADINGS.items()}
    notes_header_pages = sum(1 for fl in first_lines if any(NOTES_HEADER_RE.search(ln) for ln in fl))
    title_pages = sum(1 for fl in first_lines if any(STATEMENT_TITLE_RE.match(ln) for ln in fl))
    numbered_titles = sum(1 for fl in first_lines for ln in fl if re.match(r"^\d{1,2}\.\d{1,2}\s", ln)
                          and STATEMENT_TITLE_RE.match(ln))

    return {
        "version": FINGERPRINT_VERSION,
        "physical": {
            "text_share": round(text_pages / max(1, count), 2),
            "page_shape": shape,
            "pages": count,
            "pages_band": _band(count),
            "producer": family,
        },
        "typography": {
            "mono": round(chars["mono"] / total, 2),
            "serif": round(chars["serif"] / total, 2),
            "sans": round(chars["sans"] / total, 2),
            "bold": round(bold / total, 2),
            "big_title_pages": round(big_pages / body_pages, 2),
            "body_size": sizes.most_common(1)[0][0] if sizes else None,
        },
        "document": {
            "form": form.group(1) if form else None,
            "frameworks": _present(FRAMEWORKS, everything, 2),
            "currencies": _present(CURRENCIES, everything, 5),
            "units": _present(UNITS, everything, 2),
        },
        "era": {
            "decade": (years.most_common(1)[0][0] // 10 * 10) if years else None,
        },
        "structure": {
            "notes_header_pages": round(notes_header_pages * per_100, 1),
            "note_headings": headings,
            "statement_title_pages": title_pages,
            "numbered_statement_titles": numbered_titles,
            "schedules": sum(1 for ln in lines if SCHEDULE_RE.match(ln)),
            "item_8": bool(ITEM8_RE.search(everything)),
            "item_18": bool(ITEM18_RE.search(everything)),
        },
    }
