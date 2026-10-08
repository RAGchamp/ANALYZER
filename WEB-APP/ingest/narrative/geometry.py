"""Page geometry for the narrative reader (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §4.1).

One page becomes plain data - text segments, drawings, images - so the layout
rules are pure functions that tests can run on recorded pages without the PDF.

A *segment* is a run of spans on one line with no wide gap in it: a line of
prose is one segment, a table row "Current tax   5,606.07   5,848.54" is
three. Side-by-side columns never share a segment.
"""

import re

from ingest.pdf_utils import clean_text

SEGMENT_GAP = 10.0        # points of empty space that start a new segment on a line
BOLD_SHARE = 0.8          # a segment is bold when this share of its characters is bold


def _is_bold(span):
    return bool(span["flags"] & 16) or "bold" in span["font"].lower() or "black" in span["font"].lower()


def _segment(spans):
    raw, last = "", None
    for s in spans:
        # the space between two words can be a gap between spans, not a character
        if (last is not None and s["bbox"][0] - last["bbox"][2] > 0.2 * s["size"]
                and not raw.endswith(" ") and not s["text"].startswith(" ")):
            raw += " "
        raw += s["text"]
        last = s
    text = clean_text(raw.replace("\xa0", " "))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    chars = sum(len(s["text"].strip()) for s in spans) or 1
    bold = sum(len(s["text"].strip()) for s in spans if _is_bold(s)) / chars >= BOLD_SHARE
    sizes = [s["size"] for s in spans if s["text"].strip()]
    return {
        "t": text,
        "x0": round(min(s["bbox"][0] for s in spans), 1), "y0": round(min(s["bbox"][1] for s in spans), 1),
        "x1": round(max(s["bbox"][2] for s in spans), 1), "y1": round(max(s["bbox"][3] for s in spans), 1),
        "size": round(max(sizes), 1) if sizes else 0.0,
        "bold": bold,
        "mono": all(_is_mono(s) for s in spans if s["text"].strip()),
        "color": spans[0].get("color", 0),
    }


def _is_mono(span):
    return "courier" in span["font"].lower() or "mono" in span["font"].lower()


def _split_mono(span):
    """A typewriter row is one span padded with spaces ("Sales . . .    $ 2,351,918    $ 1,962,862"):
    its pieces between runs of two or more spaces become spans of their own, placed by character."""
    text = span["text"]
    if not _is_mono(span) or "  " not in text.strip():
        return [span]
    x0, x1 = span["bbox"][0], span["bbox"][2]
    width = (x1 - x0) / max(len(text), 1)
    out = []
    for m in re.finditer(r"\S+(?: \S+)*", text):
        out.append({**span, "text": m.group(0),
                    "bbox": (x0 + width * m.start(), span["bbox"][1], x0 + width * m.end(), span["bbox"][3])})
    return out or [span]


def _line_segments(line):
    out, run, last_x1 = [], [], None
    for span in (piece for s in line["spans"] for piece in _split_mono(s)):
        if not span["text"].strip():
            if run:
                last_x1 = max(last_x1 or 0, span["bbox"][0])
            continue
        if run and last_x1 is not None and span["bbox"][0] - last_x1 > SEGMENT_GAP:
            seg = _segment(run)
            if seg:
                out.append(seg)
            run = []
        run.append(span)
        last_x1 = span["bbox"][2]
    if run:
        seg = _segment(run)
        if seg:
            out.append(seg)
    return out


def _drawings(page):
    """Drawings on the page (a two-page spread also draws the facing page, off this one)."""
    getter = getattr(page, "get_cdrawings", None) or page.get_drawings
    width, height = page.rect.width, page.rect.height
    out = []
    for d in getter():
        x0, y0, x1, y1 = d["rect"]
        if x1 < 0 or y1 < 0 or x0 > width or y0 > height:
            continue
        kinds = {item[0] for item in d.get("items", [])}
        out.append({
            "x0": round(x0, 1), "y0": round(y0, 1), "x1": round(x1, 1), "y1": round(y1, 1),
            "fill": d.get("fill") is not None and d.get("type") in ("f", "fs"),
            "stroke": d.get("type") in ("s", "fs"),
            "width": round(d.get("width") or 0, 1),
            "curves": sum(1 for item in d.get("items", []) if item[0] == "c"),
            "items": len(d.get("items", [])),
            "kinds": "".join(sorted(kinds)),
        })
    return out


def capture(page):
    """{"width", "height", "segments", "rotated", "drawings", "images"} of one page."""
    segments, rotated = [], []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            if abs(dx - 1) > 0.01 or abs(dy) > 0.01:
                text = clean_text(" ".join(s["text"] for s in line["spans"])).strip()
                if text:
                    rotated.append(text)
                continue
            segments.extend(_line_segments(line))
    images = []
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        if x1 > x0 and y1 > y0:
            images.append({"x0": round(x0, 1), "y0": round(y0, 1), "x1": round(x1, 1), "y1": round(y1, 1)})
    return {"width": round(page.rect.width, 1), "height": round(page.rect.height, 1),
            "segments": segments, "rotated": rotated, "drawings": _drawings(page), "images": images}
