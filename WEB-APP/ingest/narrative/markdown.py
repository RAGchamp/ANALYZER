"""Scanned reports (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §4.7): there is no
page geometry, only the stored transcription in Markdown ("### EXHIBIT 1", "| a | b |", "- item").
Its blocks and headings come from the Markdown; the coverage check compares the text without
the Markdown marks."""

import re
from collections import Counter

from ingest.narrative import layout

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SEPARATOR_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
ITEM_RE = re.compile(r"^(?:[-*•]|\d{1,2}[.)])\s+(.*)$")


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def blocks(text):
    """Blocks of one transcribed page, in the order written."""
    out, para = [], []

    def flush():
        if para:
            out.append({"kind": "paragraph", "text": " ".join(para), "bbox": [0, 0, 0, 0]})
            para.clear()

    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush()
            i += 1
            continue
        m = HEADING_RE.match(line)
        if m:
            flush()
            out.append({"kind": "heading", "level": min(3, max(1, len(m.group(1)) - 1)), "text": m.group(2).strip(),
                        "bbox": [0, 0, 0, 0]})
            i += 1
            continue
        if line.startswith("|"):
            flush()
            rows, head_rows = [], None
            while i < len(lines) and lines[i].strip().startswith("|"):
                if SEPARATOR_RE.match(lines[i].strip()):
                    head_rows = head_rows if head_rows is not None else len(rows)
                else:
                    rows.append(_cells(lines[i]))
                i += 1
            head_rows = head_rows or 0
            out.append({"kind": "table", "source": "markdown", "head": rows[:head_rows], "rows": rows[head_rows:],
                        "bbox": [0, 0, 0, 0]})
            continue
        m = ITEM_RE.match(line)
        if m:
            flush()
            if out and out[-1]["kind"] == "list" and not lines[i - 1].strip() == "":
                out[-1]["items"].append(m.group(1))
            else:
                out.append({"kind": "list", "items": [m.group(1)], "markers": "", "bbox": [0, 0, 0, 0]})
            i += 1
            continue
        para.append(line)
        i += 1
    flush()
    return out


def plain(text):
    """The page's text without the Markdown marks the blocks don't show."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if SEPARATOR_RE.match(s) and s.startswith("|"):
            continue
        s = HEADING_RE.sub(r"\2", s)
        if s.startswith("|"):
            s = " ".join(_cells(s))
        else:
            s = ITEM_RE.sub(r"\1", s)
        out.append(s)
    return "\n".join(out)


def coverage_ok(text, page_blocks):
    count = lambda texts: Counter(ch for t in texts for ch in t if not ch.isspace())   # noqa: E731
    return count([plain(text)]) == count(t for b in page_blocks for t in layout.block_texts(b))


def heading_entries(texts, covered):
    """Sections from the transcription's own headings: the first heading lines of a page
    ("### EXHIBIT 1" / "### NATIONAL INDEMNITY COMPANY"), or its first line in capitals."""
    entries = []
    for pno in sorted(texts):
        if pno in covered:
            continue
        lines = [ln.strip() for ln in (texts[pno] or "").splitlines() if ln.strip()]
        if len(" ".join(lines).split()) <= 2:
            continue                                    # "BLANK PAGE"
        title = []
        for ln in lines[:4]:
            m = HEADING_RE.match(ln)
            if m:
                title.append(m.group(2).strip())
            elif len(title) < 2 and layout._caps(ln) and len(ln) <= 80:
                title.append(ln)
            else:
                break
        if title:
            entries.append({"title": " ".join(title), "start": pno, "level": 2, "group": None})
    return entries
