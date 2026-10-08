"""The section map: which pages form "Board's Report", "Item 7. MD&A" ...
(INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §3).

Sources, in order of trust, each switched on per profile (`narrative_sections`):
  bookmarks        the PDF outline
  contents         the printed contents page ("Contents", "INDEX TO FORM 10-K"), over one or more pages
  running_headers  the section title printed at the top of every page
  form_items       10-K / 20-F "Item 7." headings
  headings         last resort: large bold headings at the top of the pages
The first source that gives sections makes the map; the others confirm it,
and disagreements become quality warnings. Pages before the first section are
"Front matter".
"""

import re
from collections import Counter

from ingest.narrative import kinds

CONTENTS_TITLE_RE = re.compile(r"^(?:.*\bindex to form\b.*|(?:table of )?contents|index)$", re.I)
PAGE_NO_RE = re.compile(r"^(?:[A-Z]-)?\d{1,3}(?:\s*[-–]\s*\d{1,3})?$")
ITEM_RE = re.compile(r"^ITEM\s+(\d{1,2}[A-C]?)\.\s*(.*)$", re.I)
CONTENTS_SEARCH_PAGES = 15


def _norm(text):
    text = text.lower().replace("&", " and ").replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def same_title(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    # "Governance" alone is not "Report on Corporate Governance"
    if len(shorter.split()) >= 2 and f" {shorter} " in f" {longer} ":
        return True
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / len(ta | tb) >= 0.6


def _despace(text):
    """"S T R E N G T H E N I N G" -> "STRENGTHENING"."""
    return re.sub(r"(?<=\b\w) (?=\w\b)", "", text) if re.fullmatch(r"(?:\w ){3,}\w.*", text) else text


# ---------------------------------------------------------------- printed page -> PDF page

def printed_to_pdf(printed, footers=None):
    """A function: printed page label -> PDF page (exact match, else the usual offset).

    printed: the labels the ingester stored; footers: page numbers read at the
    foot of the narrative pages. The footers decide the usual offset; a footer
    number counts as an exact match only when it agrees with that offset (a
    figure at the bottom of a designed page is not a page number)."""
    footers = {p: n for p, n in (footers or {}).items() if n and str(n).isdigit()}
    exact, offsets = {}, Counter()
    for pno, label in printed.items():
        if not label:
            continue
        key = str(label).lstrip("0") or "0"
        exact.setdefault(key, pno)
        if key.isdigit():
            offsets[pno - int(key)] += 1
    footer_offsets = Counter(p - int(n) for p, n in footers.items())
    offset = (footer_offsets or offsets).most_common(1)[0][0] if (footer_offsets or offsets) else 0
    for pno, n in footers.items():
        if pno - int(n) == offset:
            exact[str(int(n))] = pno

    def lookup(label):
        key = str(label).split("-")[0].split("–")[0].strip()
        if re.fullmatch(r"[A-Z]-\d+", str(label).strip()):
            key = str(label).strip()
        key = key.lstrip("0") or "0"
        if key in exact:
            return exact[key]
        return int(key) + offset if key.isdigit() else None
    return lookup


# ---------------------------------------------------------------- contents page

LEADING_NO_RE = re.compile(r"^(\d{1,3})\s+(\D.*)$")
LEADER_NO_RE = re.compile(r"^(.*?\S)\s*(?:\.\s*){3,}\s*((?:[A-Z]-)?\d{1,3}(?:\s*[-–]\s*\d{1,3})?)$")


def _split_numbers(segments):
    """"140 Business Responsibility and": a page number printed close to its title is one segment;
    so is "Board of Directors ........ 1" (dot leaders, Reliance)."""
    out = []
    for s in segments:
        lead = LEADER_NO_RE.match(s["t"])
        if lead:
            x_no = s["x1"] - 0.55 * s["size"] * len(lead.group(2))
            title = {**s, "t": lead.group(1), "x1": s["x0"] + 0.5 * s["size"] * len(lead.group(1))}
            out.append(title)
            out.append({**s, "t": lead.group(2), "x0": x_no, "leader_title": title})
            continue
        m = LEADING_NO_RE.match(s["t"])
        if not m:
            out.append(s)
            continue
        x_mid = s["x0"] + 0.55 * s["size"] * len(m.group(1))
        out.append({**s, "t": m.group(1), "x1": x_mid})
        out.append({**s, "t": m.group(2), "x0": x_mid + 4})
    return out


def _same_line(a, b):
    return abs((a["y0"] + a["y1"]) / 2 - (b["y0"] + b["y1"]) / 2) <= 0.6 * max(a["size"], b["size"])


NUMBERED_TITLE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})?\s")


def _clean_title(title):
    """"4.2 Iron Ore." / "4.1 Copper," / "Risk Factors . . . ." -> without the trailing marks."""
    return re.sub(r"[\s.,;:·…]+$", "", title).strip()


def _number_groups(entries):
    """Contents numbered "4. Our assets" / "4.1 Copper" / "4.2 Iron Ore" (BHP): the N.M entries
    belong to the nearest N. entry above them, which becomes their group. The numbering starts
    again in each part of the report ("Sustainability Report", "Corporate Governance Statement"),
    so an unnumbered entry forgets the heads seen so far."""
    heads, with_children, grouped = {}, set(), []
    for i, e in enumerate(entries):
        m = NUMBERED_TITLE_RE.match(e["title"])
        if not m:
            heads = {}
            grouped.append(e)
        elif not m.group(2):
            heads[m.group(1)] = i
            grouped.append(e)
        elif m.group(1) in heads:
            with_children.add(heads[m.group(1)])
            grouped.append({**e, "group": entries[heads[m.group(1)]]["title"]})
        else:
            grouped.append(e)
    # a head with children is their group, not an entry of its own
    return [e for i, e in enumerate(grouped) if i not in with_children]


def parse_contents(geo, continued=False):
    """Entries [{title, printed, group}] of a contents page, or [] if it isn't one.
    continued: the page after a contents page, which may carry on without a title.

    Each page number is paired with the title on its line: the text just right
    of it ("06  About the Report") or, when the number is at the right-hand end,
    the text left of it ("ITEM 1.  Business  ....  2"). A line with no number
    continues the title above it; a larger or bold capital line with no number
    starts a group ("Statutory Reports", "PART II")."""
    segs = _split_numbers(geo["segments"])
    # the title is near the top, or anywhere when dot-leader entries follow it (Reliance's contents
    # sit under the list of directors)
    leaders = sum(1 for s in geo["segments"] if LEADER_NO_RE.match(s["t"]))
    title = next((s for s in segs if CONTENTS_TITLE_RE.match(s["t"].strip())
                  and (s["y0"] < 0.35 * geo["height"] or leaders >= 5)), None)
    if title is not None and leaders >= 5:
        segs = [s for s in segs if s["y0"] >= title["y0"]]       # only what is under the title
    if title is None and not continued:
        return []
    segs = [s for s in segs if s is not title]
    sizes = sorted(s["size"] for s in segs)
    median = sizes[len(sizes) // 2] if sizes else 10
    nums = [s for s in segs if PAGE_NO_RE.match(s["t"])]
    texts = [s for s in segs if s not in nums]
    paired, entries = set(), []
    # one style per page: numbers before the titles ("06  About the Report") or after them ("Business ... 2")
    numbers_first = sum(1 for n in nums if any(_same_line(t, n) and 0 <= t["x0"] - n["x1"] <= 60 for t in texts)) \
        * 2 >= len(nums)
    for n in sorted(nums, key=lambda s: (s["y0"], s["x0"])):
        line = [t for t in texts if id(t) not in paired and _same_line(t, n)]
        right = sorted((t for t in line if 0 <= t["x0"] - n["x1"] <= 60), key=lambda t: t["x0"])
        if n.get("leader_title") is not None:
            chosen = [n["leader_title"]]           # "Title ...... 5": exactly the text before the leaders
        elif numbers_first:
            if not right:
                continue                  # a divider's page number, not an entry's
            chosen = [right[0]]
            for t in sorted(line, key=lambda t: t["x0"]):
                if t["x0"] > chosen[-1]["x1"] and t["x0"] - chosen[-1]["x1"] < 40:
                    chosen.append(t)
        else:
            chosen = sorted((t for t in line if t["x1"] < n["x0"]), key=lambda t: t["x0"])
        if not chosen:
            continue
        paired.update(id(t) for t in chosen)
        entries.append({"title": _despace(" ".join(t["t"] for t in chosen)), "printed": n["t"],
                        "x0": chosen[0]["x0"], "y0": min(t["y0"] for t in chosen),
                        "y1": max(t["y1"] for t in chosen), "size": chosen[0]["size"]})
    groups = []
    for t in sorted(texts, key=lambda s: (s["y0"], s["x0"])):
        if id(t) in paired or leaders >= 5:     # dot-leader contents: nothing but its entries
            continue
        above = [e for e in entries if abs(t["x0"] - e["x0"]) <= 3 and 0 < t["y0"] - e["y1"] <= 1.2 * e["size"]
                 and abs(t["size"] - e["size"]) < 0.5]
        if above:
            e = max(above, key=lambda e: e["y1"])
            e["title"] += " " + t["t"]
            e["y1"] = t["y1"]
        elif t["size"] >= 1.25 * median or (t["bold"] and (t["t"].isupper() or t["t"].startswith("PART "))):
            groups.append({"title": _despace(t["t"]), "y0": t["y0"], "x0": t["x0"]})
    if len(entries) < 5:
        return []
    # an entry belongs to the nearest group heading above it in the same column
    for e in entries:
        owners = [g for g in groups if g["y0"] < e["y0"] and abs(g["x0"] - e["x0"]) <= 40]
        e["group"] = max(owners, key=lambda g: g["y0"])["title"] if owners else None
    entries.sort(key=lambda e: (e["y0"], e["x0"]))
    entries = [{**e, "title": _clean_title(e["title"])} for e in entries]
    return [{k: e[k] for k in ("title", "printed", "group")} for e in entries]


def _page_value(printed):
    m = re.match(r"(?:[A-Z]-)?(\d+)", printed)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------- running headers

def header_runs(headers, groups=()):
    """(title, first, last) runs of the section title printed at the top of the pages.

    headers: {pdf page: [header lines]}. Lines on over 30 % of the pages (the
    report's name) and the part names ("STATUTORY REPORTS") are not titles; a
    page without a title (a left-hand page) continues the run."""
    counts = Counter(line for lines in headers.values() for line in set(lines))
    common = {line for line, n in counts.items() if n > 0.3 * max(len(headers), 1)}
    group_norms = {_norm(g) for g in groups}
    runs = []
    for pno in sorted(headers):
        lines = [ln for ln in headers[pno] if ln not in common and _norm(ln) not in group_norms
                 and not PAGE_NO_RE.match(ln) and len(ln) >= 4]
        mixed = [ln for ln in lines if not ln.isupper()]
        title = (mixed or lines or [None])[0]
        if title is None:
            continue
        if runs and runs[-1]["title"] == title and pno - runs[-1]["last"] <= 2:
            runs[-1]["last"] = pno
        else:
            runs.append({"title": title, "first": pno, "last": pno})
    return [r for r in runs if r["last"] > r["first"] or len(runs) < 3]


# ---------------------------------------------------------------- form items

def item_headings(geos, skip_pages):
    """[(pdf page, "7", title)] of bold "ITEM 7." headings at the left margin, in order."""
    found = []
    for pno in sorted(geos):
        if pno in skip_pages:
            continue
        geo = geos[pno]
        for s in geo["segments"]:
            m = ITEM_RE.match(s["t"])
            if m and s["bold"] and s["x0"] < 0.25 * geo["width"] and len(s["t"]) <= 160:
                found.append((pno, m.group(1).upper(), s["t"]))
    return found


# ---------------------------------------------------------------- bookmarks

def bookmark_entries(doc_toc):
    """Entries from the PDF outline. The first level holding three or more bookmarks gives the
    groups (BRK-1994: "Part I", "Part II"), the level under it the sections ("Business")."""
    levels = Counter(lvl for lvl, _, page in doc_toc if page >= 1)
    top = next((lvl for lvl in sorted(levels) if levels[lvl] >= 3), None)
    if top is None:
        return []
    entries, group = [], None
    for i, (lvl, title, page) in enumerate(doc_toc):
        if page < 1 or lvl not in (top, top + 1):
            continue
        title = " ".join(title.split())
        if lvl == top:
            has_children = any(l2 == top + 1 for l2, _, p2 in doc_toc[i + 1:i + 2] if p2 >= 1)
            group = title if has_children else None
            if not has_children:
                entries.append({"title": title, "start": page, "level": 2, "group": None})
        else:
            entries.append({"title": title, "start": page, "level": 2, "group": group})
    return entries


# ---------------------------------------------------------------- headings (last resort)

def heading_entries(geos, covered):
    """Sections from the pages' own headings, when the report has no contents page, running
    headers or bookmarks (Magna's auditor's report, Reliance's cover pages): a bold line near the
    top of a page, clearly larger than the report's usual text. Stacked title lines are joined."""
    sizes = Counter()
    for geo in geos.values():
        for s in geo["segments"]:
            sizes[round(s["size"])] += len(s["t"])
    body = float(sizes.most_common(1)[0][0]) if sizes else 10.0
    entries = []
    for pno in sorted(geos):
        if pno in covered:
            continue
        geo = geos[pno]
        segs = sorted(geo["segments"], key=lambda s: (s["y0"], s["x0"]))
        for i, s in enumerate(segs):
            if s["y0"] > 0.4 * geo["height"]:
                break
            if not (s["bold"] and s["size"] >= 1.15 * body and len(s["t"]) <= 90 and re.search(r"[A-Za-z]{3}", s["t"])):
                continue
            parts, last = [s["t"]], s
            for t in segs[i + 1:]:
                if abs(t["size"] - s["size"]) < 0.3 and t["bold"] and 0 <= t["y0"] - last["y1"] <= 0.8 * s["size"]:
                    parts.append(t["t"])
                    last = t
                elif t["y0"] > last["y1"] + 0.8 * s["size"]:
                    break
            title = _clean_title(" ".join(parts))
            if not entries or not same_title(entries[-1]["title"], title) or pno - entries[-1]["start"] > 3:
                entries.append({"title": title, "start": pno, "level": 2, "group": None})
            break
    return entries


# ---------------------------------------------------------------- the map

def _slug(title):
    words = re.sub(r"[^A-Za-z0-9]+", " ", title).upper().split()
    return "N-" + "-".join(words[:5]) if words else "N-SECTION"


def build(page_count, sources, geos, full_geos, headers, printed, doc_toc, covered, footers=None,
          markdown_entries=None):
    """The sections [{id, parent_id, seq, title, kind, level, start_page, end_page, source}], warnings
    and the contents page. geos: body segments only; full_geos: with the running header (a contents
    page's title can sit where running headers do)."""
    to_pdf = printed_to_pdf(printed, footers)
    warnings = []
    entries, source, contents_page = [], None, None

    if "bookmarks" in sources and doc_toc:
        entries = bookmark_entries(doc_toc)
        source = "bookmarks" if entries else None
    groups = []
    if not entries and "contents" in sources:
        for pno in range(1, min(page_count, CONTENTS_SEARCH_PAGES) + 1):
            if pno in covered or pno not in full_geos:
                continue
            parsed = parse_contents(full_geos[pno])
            if parsed:
                contents_page = pno
                # the contents may carry on over the next pages, without a title
                nxt = pno + 1
                while nxt in full_geos and nxt not in covered:
                    more = parse_contents(full_geos[nxt], continued=True)
                    if not more or _page_value(more[0]["printed"]) < _page_value(parsed[-1]["printed"]):
                        break
                    parsed += more
                    nxt += 1
                parsed = _number_groups(parsed)
                for e in parsed:
                    start = to_pdf(e["printed"])
                    if start and contents_page < start <= page_count:
                        entries.append({"title": e["title"], "start": start, "level": 2, "group": e["group"],
                                        "printed": e["printed"]})
                groups = [g for g in dict.fromkeys(e["group"] for e in parsed) if g]
                source = "contents"
                break
    runs = header_runs(headers, groups) if "running_headers" in sources else []
    items = item_headings(geos, covered | ({contents_page} if contents_page else set())) \
        if "form_items" in sources else []
    if not entries and runs:
        entries = [{"title": r["title"], "start": r["first"], "level": 2, "group": None} for r in runs]
        source = "running_header"
    if not entries and items:
        entries = [{"title": t, "start": p, "level": 2, "group": None} for p, _, t in items]
        source = "item_heading"
    if not entries and "headings" in sources:
        # a scanned report has no geometry: its transcription's headings
        entries = heading_entries(geos, covered) if markdown_entries is None else markdown_entries
        source = "heading"

    entries.sort(key=lambda e: e["start"])
    sections, used = [], Counter()

    def add(title, start, end, level, parent, src, printed_label=None):
        sid = _slug(title)
        used[sid] += 1
        if used[sid] > 1:
            sid = f"{sid}-{used[sid]}"
        sections.append({"id": sid, "parent_id": parent, "seq": len(sections), "title": title,
                         "kind": kinds.kind_of(title), "level": level, "start_page": start, "end_page": end,
                         "printed": printed_label, "source": src, "confirmed_by": []})
        return sid

    if entries and entries[0]["start"] > 1:
        add("Front matter", 1, entries[0]["start"] - 1, 1, None, "fallback")
    group_ids = {}
    for i, e in enumerate(entries):
        end = entries[i + 1]["start"] - 1 if i + 1 < len(entries) else page_count
        end = max(end, e["start"])
        parent = None
        if e.get("group"):
            if e["group"] not in group_ids:
                last_in_group = max(j for j, x in enumerate(entries) if x.get("group") == e["group"])
                g_end = entries[last_in_group + 1]["start"] - 1 if last_in_group + 1 < len(entries) else page_count
                group_ids[e["group"]] = add(e["group"], e["start"], max(g_end, e["start"]), 1, None, source)
            parent = group_ids[e["group"]]
        add(e["title"], e["start"], end, 2 if parent else e["level"], parent, source, e.get("printed"))

    # confirmations and disagreements
    leaves = [s for s in sections if s["source"] != "fallback" and not any(c["parent_id"] == s["id"] for c in sections)]
    for s in leaves:
        # a source never confirms itself (Infosys: the sections are its Item headings)
        for r in (runs if source != "running_header" else []):
            if same_title(r["title"], s["title"]):
                s["confirmed_by"].append("running_header")
                if abs(r["first"] - s["start_page"]) > 2 and source != "running_header":
                    warnings.append(f"'{s['title']}': {source} says PDF {s['start_page']}, "
                                    f"the running header starts at PDF {r['first']}")
                break
        for pno, _, title in (items if source != "item_heading" else []):
            if same_title(title, s["title"]):
                s["confirmed_by"].append("item_heading")
                if abs(pno - s["start_page"]) > 2 and source != "item_heading":
                    warnings.append(f"'{s['title']}': {source} says PDF {s['start_page']}, "
                                    f"its Item heading is on PDF {pno}")
                break
    return sections, warnings, contents_page
