"""Passages: the pieces of the report's sections that a question can use
(INFO/ANALYZE-NON-FIN-DATA-PLAN.md §3).

Sections are far too big to send to Claude whole (Chubb's MD&A alone is 182 K
characters), so each run of narrative pages is cut at its headings into
passages of about 1.5 - 8 K characters, each with its heading path
("Statutory Reports › Management Discussion & Analysis › COMPANY REVIEW OF THE
EXPORTS AUTO MARKET Commercial Vehicles (CV)"), pages, text and money figures.

  - a heading starts a passage (not a pull quote typed as a heading by its size);
  - a passage over 8 K characters is split at a block ("… (part 2)");
  - a passage under 1.5 K is merged into the next one (the last into the one before);
  - every block of every narrative page is in exactly one passage.
"""

import re
from statistics import median

from rptpkg.blocks import block_text, page_label

MIN_CHARS = 1500
MAX_CHARS = 8000
LEAD_WORDS = 25

# ---------------------------------------------------------------- money figures (plan §8)

MONEY_RE = re.compile(
    r"(?P<cur>US\$|A\$|C\$|₹|Rs\.?|INR|\$|€|£)\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>lakh\s+crores?|crores?|cr\b\.?|lakhs?|lacs?|trillion|billion|bn\b|million|mn\b|thousand|[MBK]\b)?",
    re.I)
CURRENCIES = {"us$": "USD", "$": "USD", "a$": "AUD", "c$": "CAD", "₹": "INR", "rs": "INR", "rs.": "INR",
              "inr": "INR", "€": "EUR", "£": "GBP"}
SCALES = {"crore": 1e7, "crores": 1e7, "cr": 1e7, "cr.": 1e7, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
          "trillion": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
          "thousand": 1e3, "k": 1e3}
MIN_AMOUNT = 1e6            # "₹2 each", "$5 par value" are not figures to tie
MAX_FIGURES = 40


def money_figures(text):
    """Money amounts in a passage's text, with the page they are on and a little context."""
    out, page = [], None
    markers = [(m.start(), int(m.group(1))) for m in re.finditer(r"^--- PDF page (\d+)", text, re.M)]
    for m in MONEY_RE.finditer(text):
        page = next((p for pos, p in reversed(markers) if pos <= m.start()), None)
        number = float(m.group("num").replace(",", ""))
        word = " ".join((m.group("scale") or "").lower().rstrip(".").split())
        scale = 1e12 if word.startswith("lakh crore") else SCALES.get(word, 1)
        amount = number * scale
        if amount < MIN_AMOUNT:
            continue
        context = " ".join(text[max(0, m.start() - 90):m.end() + 60].split())
        out.append({"raw": m.group(0).strip(), "currency": CURRENCIES.get(m.group("cur").lower(), "?"),
                    "amount": amount, "page": page, "context": context})
        if len(out) >= MAX_FIGURES:
            break
    return out


# ---------------------------------------------------------------- passages

def _slug(section_id):
    body = re.sub(r"^N-", "", section_id)
    return body[:28].rstrip("-") or "SECTION"


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


class _Piece:
    def __init__(self, heads):
        self.heads = list(heads)
        self.items = []          # (page, seq, text, is_heading)
        self.content = 0         # characters of non-heading text
        self.part = 1

    def add(self, page, seq, text, is_heading):
        self.items.append((page, seq, text, is_heading))
        if not is_heading:
            self.content += len(text)


def _real_heading(block, section_title):
    """A heading that names what follows. Large pull quotes are typed as headings by size
    ("tensions, shifting trade dynamics, and relatively softer"): they read as prose, so
    they neither start a passage nor name one; nor does the section's own title."""
    text = block["text"].strip()
    words = text.split()
    if not words or text[0].islower() or text.endswith((",", ";")) or len(words) > 14 or len(text) > 110:
        return False
    return not (_norm(section_title) and _norm(section_title) in _norm(text))


def _pieces(pages, page_blocks, section_title):
    """Cut one run of pages (one section) at its headings and at MAX_CHARS."""
    pieces, stack = [], []
    cur = _Piece([])
    for pno in pages:
        for seq, block in enumerate(page_blocks.get(pno, [])):
            text = block_text(block)
            if block["kind"] == "heading" and _real_heading(block, section_title):
                # heading levels come from font sizes page by page, not a real outline: a passage
                # is named by its own heading(s), after the section
                if cur.content:
                    pieces.append(cur)
                    cur = _Piece([])
                    stack = []
                stack = stack + [block["text"]]
                cur.heads = [" · ".join(stack)]
                cur.add(pno, seq, text, True)
                continue
            if cur.content and cur.content + len(text) > MAX_CHARS:
                pieces.append(cur)
                part = cur.part + 1
                cur = _Piece(stack)
                cur.part = part
            cur.add(pno, seq, text, False)
    if cur.items:
        pieces.append(cur)
    return pieces


def _merge_small(pieces):
    """A piece under MIN_CHARS joins the next one (the last joins the one before)."""
    out = []
    pending = None
    for piece in pieces:
        if pending is not None:
            piece.items = pending.items + piece.items
            piece.content += pending.content
            if pending.heads and pending.heads != piece.heads:
                # both headings name the merged passage: "Indian Economy · Outlook"
                last = [h for h in piece.heads[-1:] if h not in pending.heads]
                piece.heads = pending.heads[:-1] + [" · ".join(pending.heads[-1:] + last)]
            pending = None
        if piece.content < MIN_CHARS:
            pending = piece
            continue
        out.append(piece)
    if pending is not None:
        if out:
            out[-1].items += pending.items
            out[-1].content += pending.content
        else:
            out.append(pending)
    return out


def build(sections, page_info, printed):
    """Passages of every run of narrative pages, in page order.

    sections: the section map; page_info: {pdf page: {"section_id", "blocks"}};
    printed: {pdf page: printed page label or None}."""
    by_id = {s["id"]: s for s in sections}
    page_blocks = {p: info["blocks"] for p, info in page_info.items()}
    runs = []
    for pno in sorted(page_info):
        sid = page_info[pno]["section_id"]
        if sid is None:
            continue
        if runs and runs[-1]["sid"] == sid:
            runs[-1]["pages"].append(pno)
        else:
            runs.append({"sid": sid, "pages": [pno]})

    passages, per_section = [], {}
    for run in runs:
        section = by_id[run["sid"]]
        parent = by_id.get(section.get("parent_id")) if section.get("parent_id") else None
        base = ([parent["title"]] if parent else []) + [section["title"]]
        for piece in _merge_small(_pieces(run["pages"], page_blocks, section["title"])):
            heads = [h for h in piece.heads if _norm(h) != _norm(section["title"])]
            # a bullet drawn with a symbol font can come through as a control character
            path = re.sub(r"[\x00-\x1f]+", " ", " › ".join(base + heads))
            path = re.sub(r"  +", " ", path)
            if piece.part > 1:
                path += f" (part {piece.part})"
            parts, page = [], None
            for pno, _, text, _ in piece.items:
                if pno != page:
                    parts.append(f"--- {page_label(pno, printed.get(pno))} ---")
                    page = pno
                if text:
                    parts.append(text)
            text = "\n\n".join(parts)
            body = " ".join(t for _, _, t, is_heading in piece.items if not is_heading and t)
            n = per_section.get(run["sid"], 0) + 1
            per_section[run["sid"]] = n
            passages.append({
                "id": f"P-{_slug(run['sid'])}-{n:02d}",
                "section_id": run["sid"],
                "seq": len(passages),
                "path": path,
                "kind": section["kind"],
                "start_page": piece.items[0][0],
                "end_page": piece.items[-1][0],
                "start_block": piece.items[0][1],
                "text": text,
                "chars": len(text),
                "lead": " ".join(body.split()[:LEAD_WORDS]),
                "blocks": [[p, s] for p, s, _, _ in piece.items],
                "figures": money_figures(text),
            })
    return passages


def stats(passages):
    sizes = [p["chars"] for p in passages]
    return {"passages": len(passages), "median_chars": int(median(sizes)) if sizes else 0,
            "largest_chars": max(sizes, default=0),
            "split": sum(1 for p in passages if "(part " in p["path"]),
            "figures": sum(len(p["figures"]) for p in passages)}
