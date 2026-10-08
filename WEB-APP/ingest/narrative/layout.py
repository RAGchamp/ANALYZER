"""Read one page outside the notes and statements: reading order and typed
blocks (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §4).

Input: the page geometry (geometry.capture) without its running header and
footer. Output: blocks in reading order - heading, paragraph, list, table,
metric, panel (with child blocks), diagram, chart, figure.

Order of work:
  1. tables: ruled grids (drawn lines) and aligned figure columns take their segments;
  2. list bullets (a glyph, or a small drawn mark left of a line);
  3. panels (filled or framed rectangles holding text) and graphics (curve drawings
     with short labels around them: diagrams, pie charts);
  4. text boxes: lines stacked closely, same size and weight;
  5. reading order: a recursive XY-cut - columns (full-height gutters) first, else
     bands; bands whose gutters line up are read as one set of columns;
  6. block types; list items, KPI figures with their labels, and paragraphs broken
     mid-sentence are joined.

The rules only reorder and label: `coverage_ok` checks that the blocks hold
exactly the page's characters, and a page that fails keeps its flat text.
"""

import re
from collections import Counter

from ingest import profiles

FIGURE_RE = re.compile(
    r"^[(\-–−]?\s*(?:₹|US\$|\$|Rs\.?|€|£)?\s*[(\-–−]?\s*\d[\d,]*(?:\.\d+)?\s*\)?\s*%?(?:\([a-z]\))?$|^[-–—]+$", re.I)
YEAR_RE = re.compile(r"^(19|20)\d\d$")
METRIC_RE = re.compile(
    r"^(?:₹|US\$|\$|€|£|Rs\.?)?\s?[(]?\d[\d,]*(?:\.\d+)?[)]?\s?(?:%|x|bn|mn|million|billion|crores?|cr\.?|lakhs?|"
    r"tons|tonnes|mt|mtpa|mw|gw|k|\+)?(?:\s+[A-Za-z%+]{1,12}){0,2}$", re.I)
BULLET_GLYPHS = "•▪●◦■►▶✓➢➤◆◾"
LIST_START_RE = re.compile(r"^(?:[" + BULLET_GLYPHS + r"]|\(?[a-z]{1,3}\)|\(?\d{1,2}\)|[ivx]{1,4}\.)\s*\S", re.I)
SENTENCE_END = (".", ":", ";", "?", "!", "”", "\"", ")")
# enumerators printed apart from their text: "S2", "M1", "8.", "(a)"
BADGE_RE = re.compile(r"[A-Z]?\d{1,2}[.)]?|[A-Z]\d?|[a-z][.)]|[ivx]{1,4}[.)]|\([a-z0-9]{1,4}\)")
BADGE_PREFIX_RE = re.compile(r"^[A-Z]\d{1,2}\s+\S")
RULE_RE = re.compile(r"^[-=_]{3,}$")


def _caps(text):
    """A line in capitals ("MARKET INFORMATION"): at least 4 letters, 90 % upper case."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) >= 0.9 * len(letters)

COLUMN_GUTTER = 7.0      # an empty vertical strip this wide separates columns
PANEL_MIN_AREA = 2500.0
GRAPHIC_MIN_SIDE = 70.0


# ---------------------------------------------------------------- small helpers

def _box(items):
    return (min(i["x0"] for i in items), min(i["y0"] for i in items),
            max(i["x1"] for i in items), max(i["y1"] for i in items))


def _center(item):
    return (item["x0"] + item["x1"]) / 2, (item["y0"] + item["y1"]) / 2


def _inside(item, rect, pad=2.0):
    cx, cy = _center(item)
    return rect[0] - pad <= cx <= rect[2] + pad and rect[1] - pad <= cy <= rect[3] + pad


def _is_figure(text):
    return bool(FIGURE_RE.match(text.strip()))


def body_size(segments):
    sizes = Counter()
    for s in segments:
        sizes[round(s["size"])] += len(s["t"])
    return float(sizes.most_common(1)[0][0]) if sizes else 10.0


def _rows(segments, tolerance=3.0):
    """Segments grouped into rows by their vertical centre, top to bottom."""
    rows = []
    for s in sorted(segments, key=lambda s: ((s["y0"] + s["y1"]) / 2, s["x0"])):
        cy = (s["y0"] + s["y1"]) / 2
        if rows and abs(rows[-1]["cy"] - cy) <= tolerance:
            rows[-1]["segs"].append(s)
        else:
            rows.append({"cy": cy, "segs": [s]})
    for r in rows:
        r["segs"].sort(key=lambda s: s["x0"])
    return rows


# ---------------------------------------------------------------- tables

def _grid_tables(segments, drawings):
    """Ruled tables: at least 3 horizontal and 3 vertical rules that cross."""
    hs = [d for d in drawings if d["y1"] - d["y0"] <= 1.5 and d["x1"] - d["x0"] >= 20]
    vs = [d for d in drawings if d["x1"] - d["x0"] <= 1.5 and d["y1"] - d["y0"] >= 8]
    if len(hs) < 3 or len(vs) < 3:
        return []
    # vertical rules that overlap in height form one grid
    vs.sort(key=lambda d: d["y0"])
    groups = []
    for v in vs:
        if groups and v["y0"] <= groups[-1]["y1"] + 2:
            groups[-1]["vs"].append(v)
            groups[-1]["y1"] = max(groups[-1]["y1"], v["y1"])
        else:
            groups.append({"vs": [v], "y0": v["y0"], "y1": v["y1"]})
    tables = []
    for g in groups:
        xs = sorted({round((v["x0"] + v["x1"]) / 2) for v in g["vs"]})
        xs = [x for i, x in enumerate(xs) if i == 0 or x - xs[i - 1] > 2]
        ys = sorted({round((h["y0"] + h["y1"]) / 2) for h in hs
                     if g["y0"] - 2 <= h["y0"] <= g["y1"] + 2 and h["x0"] <= xs[-1] and h["x1"] >= xs[0]})
        ys = [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 2]
        if len(xs) < 3 or len(ys) < 3:
            continue
        rect = (xs[0], ys[0], xs[-1], ys[-1])
        inside = [s for s in segments if _inside(s, rect, 0)]
        if len(inside) < 4:
            continue
        cells = {}
        for s in inside:
            cx, cy = _center(s)
            r = max(i for i, y in enumerate(ys[:-1]) if y <= cy) if cy >= ys[0] else 0
            c = max(i for i, x in enumerate(xs[:-1]) if x <= cx) if cx >= xs[0] else 0
            cells.setdefault((r, c), []).append(s)
        n_rows, n_cols = len(ys) - 1, len(xs) - 1
        grid = [[" ".join(s["t"] for s in sorted(cells.get((r, c), []), key=lambda s: (s["y0"], s["x0"])))
                 for c in range(n_cols)] for r in range(n_rows)]
        keep_cols = [c for c in range(n_cols) if any(grid[r][c] for r in range(n_rows))]
        grid = [[row[c] for c in keep_cols] for row in grid if any(row[c] for c in keep_cols)]
        if len(grid) < 2 or len(keep_cols) < 2:
            continue
        head = []
        while grid and len(head) < 2 and not any(_is_figure(c) for c in grid[0][1:]) and len(grid) > 2:
            head.append(grid.pop(0))
        tables.append({"type": "table", "source": "grid", "segs": inside, "head": head, "rows": grid,
                       "x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3]})
    return tables


def _band_rows(band, cols):
    """The rows between two rules. Usually one (BRSR: one row per band, its cells wrapped over
    several lines); but a table ruled only around groups of rows (BHP's EBITDA factors) has a row
    for each line that starts with a label in the first column - wrapped text of the other columns
    stays with the row above it."""
    first_col_end = cols[1] - 3 if len(cols) > 1 else float("inf")
    starts = sorted({round((s["y0"] + s["y1"]) / 2) for s in band if s["x0"] < first_col_end})
    starts = [y for i, y in enumerate(starts) if i == 0 or y - starts[i - 1] > 3]
    if len(starts) <= 1:
        return [band]
    # wrapped first-column labels (a line that continues the label above, with no figure beside it) stay one row
    figures_at = {round((s["y0"] + s["y1"]) / 2) for s in band if s["x0"] >= first_col_end and _is_figure(s["t"])}

    def has_figure(y):
        return any(abs(y - f) <= 3 for f in figures_at)

    if sum(1 for y in starts if has_figure(y)) < 2:
        return [band]                        # a heading or text row wrapped over several lines
    heads = [y for i, y in enumerate(starts) if i == 0 or has_figure(y) or not has_figure(starts[i - 1])]
    rows = {y: [] for y in heads}
    for s in band:
        cy = (s["y0"] + s["y1"]) / 2
        owner = max((y for y in heads if y <= cy + 3), default=heads[0])
        rows[owner].append(s)
    return [rows[y] for y in heads if rows[y]]


def _ruled_row_tables(segments, drawings, width):
    """Text tables ruled only between rows (BRSR, governance): four or more
    horizontal rules of the same width, with no full-width prose between them.
    Rules drawn as one piece per cell are joined, and their joints are the
    column boundaries; otherwise the columns are the left edges the cells share."""
    pieces = sorted((d for d in drawings if d["y1"] - d["y0"] <= 1.5 and d["x1"] - d["x0"] >= 8
                     and -1 <= d["x0"] and d["x1"] <= width + 1), key=lambda d: (round(d["y0"]), d["x0"]))
    hs = []
    for d in pieces:
        last = hs[-1] if hs else None
        if last and abs(last["y0"] - d["y0"]) <= 1 and d["x0"] <= last["x1"] + 1.5:
            last["joints"].append(round(d["x0"]))
            last["x1"] = max(last["x1"], d["x1"])
        else:
            hs.append({"x0": d["x0"], "x1": d["x1"], "y0": d["y0"], "y1": d["y1"], "joints": []})
    hs = [h for h in hs if h["x1"] - h["x0"] >= 0.4 * width]
    by_extent = {}
    for h in hs:
        by_extent.setdefault((round(h["x0"] / 3), round(h["x1"] / 3)), []).append(h)
    tables, taken = [], set()           # rules of slightly different widths must not read one table twice
    for rules in sorted(by_extent.values(), key=len, reverse=True):
        segments = [s for s in segments if id(s) not in taken]
        joint_counts = Counter(j for r in rules for j in set(r["joints"]))
        joints = sorted(j for j, n in joint_counts.items() if n >= 0.5 * len(rules))
        ys = sorted({round((r["y0"] + r["y1"]) / 2, 1) for r in rules})
        ys = [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 2]
        if len(ys) < 4:
            continue
        x0, x1 = min(r["x0"] for r in rules), max(r["x1"] for r in rules)
        bands = []
        for a, b in zip(ys, ys[1:]):
            inside = [s for s in segments if x0 - 2 <= s["x0"] and s["x1"] <= x1 + 2 and a <= _center(s)[1] <= b]
            if any(s["x1"] - s["x0"] > 0.55 * (x1 - x0) for s in inside):
                inside = None                     # prose between the rules: not a table row
            bands.append(inside)
        runs, cur = [], []
        for band in bands + [None]:
            if band is None:
                if len([b for b in cur if b]) >= 3:
                    runs.append(cur)
                cur = []
            else:
                cur.append(band)
        for run in runs:
            rows = [b for b in run if b]
            segs = [s for b in rows for s in b]
            if joints:
                cols = [x0 - 3] + [j - 3 for j in joints]
            else:
                starts = sorted(s["x0"] for s in segs)
                clusters = []
                for x in starts:
                    if clusters and x - clusters[-1][-1] <= 6:
                        clusters[-1].append(x)
                    else:
                        clusters.append([x])
                cols = [c[0] for c in clusters if sum(1 for b in rows if any(c[0] - 1 <= s["x0"] <= c[-1] + 1
                                                                            for s in b)) >= 0.3 * len(rows)]
            if len(cols) < 2:
                continue
            grid = []
            for b in rows:
                for sub in _band_rows(b, cols):
                    cells = [[] for _ in cols]
                    for s in sorted(sub, key=lambda s: (s["y0"], s["x0"])):
                        k = max((i for i, c in enumerate(cols) if c <= s["x0"] + 3), default=0)
                        cells[k].append(s["t"])
                    grid.append([" ".join(c) for c in cells])
            head = [grid.pop(0)] if rows[0] and all(s["bold"] for s in rows[0]) and len(grid) > 1 else []
            bx0, by0, bx1, by1 = _box(segs)
            taken.update(id(s) for s in segs)
            tables.append({"type": "table", "source": "ruled", "segs": segs, "head": head, "rows": grid,
                           "x0": bx0, "y0": by0, "x1": bx1, "y1": by1})
    return tables


def _aligned_tables(segments, body):
    """Borderless tables: rows with figures right-aligned in shared columns."""
    rows = _rows(segments)
    numeric = [len(r["segs"]) >= 2 and any(_is_figure(s["t"]) and not YEAR_RE.match(s["t"]) for s in r["segs"][1:])
               for r in rows]
    # typewriter tables underline their columns: a row of "-----" / "=====" stays in the table
    rules = [all(RULE_RE.match(s["t"]) for s in r["segs"]) for r in rows]
    tables, i, prev_end = [], 0, 0
    while i < len(rows):
        if not numeric[i]:
            i += 1
            continue
        j, last, count, gap_rows = i, i, 0, 0
        while j < len(rows):
            if j > last and rows[j]["cy"] - rows[last]["cy"] > 3.2 * body:
                break
            if numeric[j]:
                count, last, gap_rows = count + 1, j, 0
            elif rules[j]:
                last = j
            elif len(rows[j]["segs"]) == 1 and len(rows[j]["segs"][0]["t"]) <= 90 and gap_rows < 2:
                gap_rows += 1
            else:
                break
            j += 1
        end = last + 1
        if count >= 3:
            start = i
            while start > prev_end and i - start < 3:           # heading rows, never the previous table's
                prev = rows[start - 1]
                if (len(prev["segs"]) >= 2 and rows[start]["cy"] - prev["cy"] <= 2.5 * body
                        and all(not _is_figure(s["t"]) or YEAR_RE.match(s["t"]) for s in prev["segs"])):
                    start -= 1
                else:
                    break
            table = _build_aligned(rows[start:end], rows[i:end])
            if table:
                tables.append(table)
            i = prev_end = end
        else:
            i += 1
    return tables


def _build_aligned(rows, data_rows):
    figs = sorted((s for r in data_rows for s in r["segs"][1:] if _is_figure(s["t"])), key=lambda s: s["x1"])
    clusters = []
    for s in figs:
        if clusters and s["x1"] - clusters[-1]["x1s"][-1] <= 8:
            clusters[-1]["x1s"].append(s["x1"])
            clusters[-1]["x0"] = min(clusters[-1]["x0"], s["x0"])
        else:
            clusters.append({"x1s": [s["x1"]], "x0": s["x0"]})
    cols = [{"x0": c["x0"], "x1": max(c["x1s"])} for c in clusters if len(c["x1s"]) >= 2]
    if not cols:
        return None
    label_limit = cols[0]["x0"] - 2
    head, body, segs = [], [], []
    header_zone = True
    for r in rows:
        cells = [""] * (len(cols) + 1)
        is_numeric = any(_is_figure(s["t"]) and not YEAR_RE.match(s["t"]) for s in r["segs"][1:])
        for s in r["segs"]:
            segs.append(s)
            if s["x1"] <= label_limit and not (header_zone and not is_numeric and s["x0"] > label_limit - 40):
                k = 0
            else:
                cx = (s["x0"] + s["x1"]) / 2
                k = 1 + min(range(len(cols)), key=lambda c: min(abs(s["x1"] - cols[c]["x1"]),
                                                              abs(cx - (cols[c]["x0"] + cols[c]["x1"]) / 2)))
            cells[k] = f"{cells[k]} {s['t']}".strip()
        if header_zone and not is_numeric and r is not data_rows[0]:
            head.append(cells)
        else:
            header_zone = False
            body.append(cells)
    x0, y0, x1, y1 = _box(segs)
    return {"type": "table", "source": "aligned", "segs": segs, "head": head, "rows": body,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1}


# ---------------------------------------------------------------- bullets, panels, graphics

def _mark_bullets(segments, drawings, images, body):
    """Glyph-only segments are joined to the text on their right; lines with a
    glyph or a small drawn mark just left of them start list items. Drawn marks
    count only when at least two line up (a list), and never beside a heading
    (the arrow before a section title is not a bullet)."""
    glyphs = [s for s in segments if len(s["t"]) <= 2 and s["t"][0] in BULLET_GLYPHS]
    kept = [s for s in segments if s not in glyphs]
    for g in glyphs:
        gy = (g["y0"] + g["y1"]) / 2
        target = min((s for s in kept if abs((s["y0"] + s["y1"]) / 2 - gy) <= 4 and 0 <= s["x0"] - g["x1"] <= 25),
                     key=lambda s: s["x0"] - g["x1"], default=None)
        if target is None:
            kept.append(g)
            continue
        target["t"] = f"{g['t']} {target['t']}"
        target["x0"] = g["x0"]
        target["bullet"] = True
    # enumerator badges: "S2" / "M1" / "01" printed just left of the text they number
    badges = [s for s in kept if len(s["t"]) <= 3 and BADGE_RE.fullmatch(s["t"])]
    for b in badges:
        by = (b["y0"] + b["y1"]) / 2
        target = min((s for s in kept if s is not b and s not in badges
                      and abs((s["y0"] + s["y1"]) / 2 - by) <= 0.6 * s["size"] and 0 <= s["x0"] - b["x1"] <= 15),
                     key=lambda s: s["x0"] - b["x1"], default=None)
        if target is not None:
            target["t"] = f"{b['t']} {target['t']}"
            target["x0"] = b["x0"]
            target["bullet"] = True
            kept.remove(b)
    # a drawn mark that is part of an icon (touching a drawing of some size) is not a bullet
    icons = [d for d in drawings if min(d["x1"] - d["x0"], d["y1"] - d["y0"]) >= 6
             and max(d["x1"] - d["x0"], d["y1"] - d["y0"]) <= 60]
    # a bullet is an image, a filled mark or one thick stroke (not an arrow's thin lines)
    marks = [m for m in drawings + images
             if m["x1"] - m["x0"] <= 8 and 1.5 <= m["y1"] - m["y0"] <= 12
             and ("fill" not in m or m["fill"] or (m.get("width", 0) >= 1.5 and m["items"] == 1))
             and (m["x1"] - m["x0"] >= 1.5 or m.get("width", 0) >= 1.5)
             and not any(i is not m and i["x0"] - 1 <= m["x1"] and m["x0"] <= i["x1"] + 1
                         and i["y0"] - 1 <= m["y1"] and m["y0"] <= i["y1"] + 1 for i in icons)]
    hits = []
    for s in kept:
        if s["t"][0] in BULLET_GLYPHS or BADGE_PREFIX_RE.match(s["t"]):
            s["bullet"] = True
            continue
        if s["bold"] or s["size"] > 1.15 * body:
            continue
        for m in marks:
            my = (m["y0"] + m["y1"]) / 2
            if s["y0"] - 2 <= my <= s["y1"] + 2 and s["x0"] - 22 <= m["x1"] <= s["x0"] - 0.5:
                hits.append((s, m))
                break
    for s, m in hits:
        if sum(1 for _, other in hits if abs(other["x1"] - m["x1"]) <= 2) >= 2:
            s["bullet"] = True
    return kept


def _panels(drawings, segments, width, height):
    """Filled or framed rectangles holding text on two or more lines."""
    page_area = width * height
    candidates = []
    for d in drawings:
        w, h = d["x1"] - d["x0"], d["y1"] - d["y0"]
        if w * h < PANEL_MIN_AREA or w * h > 0.6 * page_area or w < 60 or h < 25:
            continue
        if not (d["fill"] or (d["stroke"] and "r" in d["kinds"])) or d["curves"] > 8:
            continue
        if d["kinds"] == "c":                   # a circle or ring: part of a graphic, not a panel
            continue
        rect = (d["x0"], d["y0"], d["x1"], d["y1"])
        inside = [s for s in segments if _inside(s, rect, 0)]
        if len({round(s["y0"] / 3) for s in inside}) < 2:
            continue
        candidates.append(rect)
    candidates.sort(key=lambda r: -(r[2] - r[0]) * (r[3] - r[1]))
    accepted = []
    for r in candidates:
        if not any(r[0] < a[2] and a[0] < r[2] and r[1] < a[3] and a[1] < r[3] for a in accepted):
            accepted.append(r)
    return accepted


def _graphics(drawings, panels, width, height):
    """Clusters of curve drawings (a cycle diagram, a pie chart) with their connector lines."""
    def near(a, b, pad):
        return a[0] - pad <= b[2] and b[0] - pad <= a[2] and a[1] - pad <= b[3] and b[1] - pad <= a[3]

    curvy = [d for d in drawings if d["curves"] and (d["x1"] - d["x0"]) < 0.9 * width
             and (d["y1"] - d["y0"]) < 0.9 * height
             and not any(abs(d["x0"] - p[0]) < 1 and abs(d["y1"] - p[3]) < 1 for p in panels)]
    clusters = []
    for d in curvy:
        rect = [d["x0"], d["y0"], d["x1"], d["y1"]]
        curves = d["curves"]
        merged = True
        while merged:
            merged = False
            for c in clusters:
                if near(rect, c["rect"], 6):
                    rect = [min(rect[0], c["rect"][0]), min(rect[1], c["rect"][1]),
                            max(rect[2], c["rect"][2]), max(rect[3], c["rect"][3])]
                    curves += c["curves"]
                    clusters.remove(c)
                    merged = True
                    break
        clusters.append({"rect": rect, "curves": curves})
    out = []
    # connector lines out to the labels: short ones only (a page frame is not a connector)
    lines = [d for d in drawings if d["kinds"] == "l" and max(d["x1"] - d["x0"], d["y1"] - d["y0"]) <= 150]
    for c in clusters:
        r = list(c["rect"])
        if r[2] - r[0] < GRAPHIC_MIN_SIDE or r[3] - r[1] < GRAPHIC_MIN_SIDE or c["curves"] < 4:
            continue
        core = tuple(r)
        for d in lines:
            if near(core, (d["x0"], d["y0"], d["x1"], d["y1"]), 3):
                r = [min(r[0], d["x0"]), min(r[1], d["y0"]), max(r[2], d["x1"]), max(r[3], d["y1"])]
        out.append(tuple(r))
    return out


# ---------------------------------------------------------------- text boxes

def _text_boxes(segments):
    """Lines stacked closely (same size and weight, overlapping horizontally) form a box."""
    segs = sorted(segments, key=lambda s: (s["y0"], s["x0"]))
    parent = {}
    child_of = {}
    for ia, a in enumerate(segs):
        best = None
        for ib in range(ia + 1, len(segs)):
            b = segs[ib]
            if b["y0"] > a["y1"] + 1.2 * max(a["size"], b["size"]) + 2:
                break
            if ib in parent or b.get("bullet"):
                continue
            if b["y0"] <= a["y0"] + 0.4 * a["size"]:
                continue
            gap = b["y0"] - a["y1"]
            if gap > 0.8 * max(a["size"], b["size"]) + 1.5 or gap < -2:
                continue
            if a["bold"] != b["bold"] or max(a["size"], b["size"]) > 1.25 * min(a["size"], b["size"]):
                continue
            if a["mono"] and b["mono"] and _caps(a["t"]) != _caps(b["t"]):
                continue            # typewriter text: a heading in capitals is not part of the paragraph
            overlap = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            if overlap < 0.2 * min(a["x1"] - a["x0"], b["x1"] - b["x0"]) and abs(a["x0"] - b["x0"]) > 3:
                continue
            key = (gap, -overlap)
            if best is None or key < best[0]:
                best = (key, ib)
        if best is not None:
            parent[best[1]] = ia
            child_of[ia] = best[1]
    boxes = []
    for i, s in enumerate(segs):
        if i in parent:
            continue
        chain = [s]
        j = i
        while j in child_of:
            j = child_of[j]
            chain.append(segs[j])
        x0, y0, x1, y1 = _box(chain)
        sizes = sorted(c["size"] for c in chain)
        boxes.append({"type": "box", "lines": chain, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                      "size": sizes[len(sizes) // 2], "bold": all(c["bold"] for c in chain),
                      "bullet": bool(chain[0].get("bullet")), "mono": all(c.get("mono") for c in chain)})
    return boxes


def _split_paragraphs(box):
    """A box of lines with no space between its paragraphs (EDGAR prints, Infosys) ends a paragraph
    where a line stops short of the column's width at the end of a sentence and the next line starts
    a new one."""
    lines = box["lines"]
    if len(lines) < 3 or box.get("bullet"):
        return [box]
    width = max(ln["x1"] for ln in lines) - min(ln["x0"] for ln in lines)
    left = min(ln["x0"] for ln in lines)
    gaps = sorted(b["y0"] - a["y1"] for a, b in zip(lines, lines[1:]))
    usual_gap = gaps[len(gaps) // 2]
    parts, cur = [], [lines[0]]
    for prev, line in zip(lines, lines[1:]):
        short = prev["x1"] - left < 0.8 * width
        wider_gap = line["y0"] - prev["y1"] > usual_gap + max(2.0, 0.25 * prev["size"])
        if (short or wider_gap) and prev["t"].rstrip().endswith(SENTENCE_END) and line["t"][:1].isupper():
            parts.append(cur)
            cur = []
        cur.append(line)
    parts.append(cur)
    if len(parts) == 1:
        return [box]
    out = []
    for part in parts:
        x0, y0, x1, y1 = _box(part)
        out.append({**box, "lines": part, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "bullet": box["bullet"] and part is parts[0]})
    return out


def _join_lines(lines):
    out = ""
    for line in lines:
        t = line["t"]
        if not out:
            out = t
        elif out.endswith("-") and t[:1].islower():
            out += t
        else:
            out += " " + t
    return out


# ---------------------------------------------------------------- reading order

def _gaps(intervals, min_gap):
    """Empty strips between the union of intervals, at least min_gap wide."""
    gaps, end = [], None
    for a, b in sorted(intervals):
        if end is not None and a - end >= min_gap:
            gaps.append((end, a))
        end = b if end is None else max(end, b)
    return gaps


def _split(nodes, gaps, lo_key, hi_key):
    groups = [[] for _ in range(len(gaps) + 1)]
    for n in nodes:
        k = sum(1 for g in gaps if n[lo_key] >= g[1] - 0.01)
        groups[k].append(n)
    return [g for g in groups if g]


def xycut(nodes, merge=True):
    if len(nodes) <= 1:
        return list(nodes)
    xg = _gaps([(n["x0"], n["x1"]) for n in nodes], COLUMN_GUTTER)
    if xg:
        return [m for col in _split(nodes, xg, "x0", "x1") for m in xycut(col)]
    yg = _gaps([(n["y0"], n["y1"]) for n in nodes], 0.5)
    if not yg:
        return sorted(nodes, key=lambda n: (round(n["y0"]), n["x0"]))
    bands = _split(nodes, yg, "y0", "y1")
    if not merge:
        return [m for band in bands for m in xycut(band, merge=True)]
    # bands whose gutters line up are one set of columns (paragraph breaks that happen to align)
    groups = []
    for band in bands:
        gut = _gaps([(n["x0"], n["x1"]) for n in band], COLUMN_GUTTER)
        if groups and gut and groups[-1]["gut"] and any(
                min(g[1], h[1]) - max(g[0], h[0]) >= 4 for g in gut for h in groups[-1]["gut"]):
            groups[-1]["bands"].append(band)
            groups[-1]["gut"] = gut
        else:
            groups.append({"bands": [band], "gut": gut})
    out = []
    for g in groups:
        members = [n for band in g["bands"] for n in band]
        cols = _gaps([(n["x0"], n["x1"]) for n in members], COLUMN_GUTTER) if len(g["bands"]) > 1 else []
        if cols:
            out += [m for col in _split(members, cols, "x0", "x1") for m in xycut(col)]
        else:
            out += [m for band in g["bands"] for m in xycut(band, merge=False)]
    return out


# ---------------------------------------------------------------- block types

def _box_block(box, body):
    text = _join_lines(box["lines"])
    bbox = [box["x0"], box["y0"], box["x1"], box["y1"]]
    words = text.split()
    if len(box["lines"]) <= 2 and len(words) <= 5 and box["size"] >= 1.7 * body and METRIC_RE.match(text):
        return {"kind": "metric", "value": text, "label": "", "bbox": bbox, "size": box["size"]}
    is_heading = (len(box["lines"]) <= 3 and len(text) <= 140 and not text.endswith((".", ",", ";"))
                  and not _is_figure(text)
                  and (box["size"] >= 1.15 * body or (box["bold"] and box["size"] >= 0.95 * body)
                       # a typewriter has no bold: its headings are short lines in capitals
                       or (box.get("mono") and len(box["lines"]) <= 2 and _caps(text) and not RULE_RE.match(text))))
    if box["bullet"] and not is_heading:        # "8. INTERNAL FINANCIAL CONTROLS" stays a heading
        item = text.lstrip(BULLET_GLYPHS).strip()
        return {"kind": "list", "items": [item], "markers": text[:len(text) - len(text.lstrip(BULLET_GLYPHS))],
                "bbox": bbox}
    if is_heading:
        ratio = box["size"] / body
        level = 1 if ratio >= 1.6 else 2 if ratio >= 1.2 else 3
        return {"kind": "heading", "level": level, "text": text, "bbox": bbox}
    return {"kind": "paragraph", "text": text, "bbox": bbox}


def _drop_empty_columns(head, rows):
    width = max((len(r) for r in head + rows), default=0)
    grid = [r + [""] * (width - len(r)) for r in head + rows]
    keep = [c for c in range(width) if any(r[c] for r in grid)]
    return [[r[c] for c in keep] for r in grid[:len(head)]], [[r[c] for c in keep] for r in grid[len(head):]]


def _node_blocks(node, body):
    """Blocks of one reading-order node (a text box may hold several paragraphs)."""
    if node["type"] == "box":
        return [_box_block(b, body) for b in _split_paragraphs(node)]
    return [_node_block(node, body)]


def _node_block(node, body):
    kind = node["type"]
    if kind == "box":
        return _box_block(node, body)
    bbox = [node["x0"], node["y0"], node["x1"], node["y1"]]
    if kind == "table":
        head, rows = _drop_empty_columns(node["head"], node["rows"])
        return {"kind": "table", "source": node["source"], "head": head, "rows": rows, "bbox": bbox}
    if kind == "graphic":
        labels = [_join_lines(b["lines"]) for b in xycut(node["labels"])]
        figures = sum(1 for t in labels if _is_figure(t) or t.endswith("%"))
        return {"kind": "chart" if labels and figures * 2 >= len(labels) else "diagram", "labels": labels,
                "bbox": bbox}
    if kind == "figure":
        return {"kind": "figure", "labels": [], "bbox": bbox}
    if kind == "panel":
        return {"kind": "panel", "children": _finish([b for c in xycut(node["children"]) for b in _node_blocks(c, body)]),
                "bbox": bbox}
    raise ValueError(kind)


def _ends_sentence(text):
    return text.rstrip().endswith(SENTENCE_END)


def _finish(blocks):
    """Join list items, KPI figures with their labels, and paragraphs broken mid-sentence."""
    out = []
    for b in blocks:
        prev = out[-1] if out else None
        # a short line right before a list, table, KPI or graphic is its heading
        if (prev and prev["kind"] == "paragraph" and len(prev["text"]) <= 60 and not _ends_sentence(prev["text"])
                and b["kind"] in ("list", "table", "metric", "diagram", "chart", "panel")):
            prev["kind"], prev["level"] = "heading", 3
        if prev and b["kind"] == "list" and prev["kind"] == "list":
            prev["items"] += b["items"]
            prev["markers"] += b["markers"]
            prev["bbox"] = _union(prev["bbox"], b["bbox"])
            continue
        if (prev and prev["kind"] == "metric" and not prev["label"] and b["kind"] in ("paragraph", "heading")
                and len(b["text"]) <= 80 and b["bbox"][1] - prev["bbox"][3] <= 25):
            prev["label"] = b["text"]
            prev["bbox"] = _union(prev["bbox"], b["bbox"])
            continue
        if (prev and prev["kind"] == "paragraph" and b["kind"] == "paragraph"
                and not _ends_sentence(prev["text"]) and b["text"][:1].islower()):
            prev["text"] += " " + b["text"]
            prev["bbox"] = _union(prev["bbox"], b["bbox"])
            continue
        out.append(b)
    for b in out:
        b.pop("size", None)
    return out


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


# ---------------------------------------------------------------- the page

def read_page(geo, segments=None):
    """Blocks of one page in reading order. `segments`: the body segments (the
    running header and footer already removed); default: all of them."""
    switches = profiles.active().switches
    segments = [dict(s) for s in (geo["segments"] if segments is None else segments)]
    width, height = geo["width"], geo["height"]
    body = body_size(segments)
    drawings, images = geo["drawings"], geo["images"]

    designed = switches.narrative_designed_pages
    tables = _grid_tables(segments, drawings)
    finders = ([lambda segs: _ruled_row_tables(segs, drawings, width)] if designed else []) + \
        [lambda segs: _aligned_tables(segs, body)]
    for finder in finders:
        used = {id(s) for t in tables for s in t["segs"]}
        tables += finder([s for s in segments if id(s) not in used])
    used = {id(s) for t in tables for s in t["segs"]}
    rest = [s for s in segments if id(s) not in used]

    rest = _mark_bullets(rest, drawings, images, body)
    panels = _panels(drawings, rest, width, height) if designed else []
    graphics = _graphics(drawings, panels, width, height) if designed else []
    boxes = _text_boxes(rest)

    nodes = list(tables)
    for g in graphics:
        labels = [b for b in boxes
                  if len(b["lines"]) <= 3 and sum(len(ln["t"]) for ln in b["lines"]) <= 80
                  and b["size"] <= 1.3 * body and not b["bullet"]
                  and b["x0"] <= g[2] + 12 and b["x1"] >= g[0] - 12 and b["y0"] <= g[3] + 12 and b["y1"] >= g[1] - 12]
        if not labels:
            continue
        boxes = [b for b in boxes if b not in labels]
        x0, y0, x1, y1 = _box(labels + [{"x0": g[0], "y0": g[1], "x1": g[2], "y1": g[3]}])
        nodes.append({"type": "graphic", "labels": labels, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    for p in panels:
        inner = [n for n in boxes + nodes if _inside(n, p, 1)]
        if not inner:
            continue
        boxes = [b for b in boxes if b not in inner]
        nodes = [n for n in nodes if n not in inner]
        x0, y0, x1, y1 = _box(inner + [{"x0": p[0], "y0": p[1], "x1": p[2], "y1": p[3]}])
        nodes.append({"type": "panel", "children": inner, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    if designed:
        page_area = width * height
        for im in images:
            area = (im["x1"] - im["x0"]) * (im["y1"] - im["y0"])
            if 0.12 * page_area <= area <= 0.75 * page_area:
                nodes.append({"type": "figure", **im})
    nodes += boxes

    blocks = _finish([b for n in xycut(nodes) for b in _node_blocks(n, body)])
    if geo.get("rotated"):
        blocks.append({"kind": "rotated", "text": " ".join(geo["rotated"]), "bbox": [0, 0, 0, 0]})
    return blocks


# ---------------------------------------------------------------- the invariant

def block_texts(block):
    """Every text a block shows (for the coverage check and the section text)."""
    kind = block["kind"]
    if kind in ("heading", "paragraph", "rotated", "flat"):
        return [block["text"]]
    if kind == "list":
        return [block.get("markers", "")] + block["items"]
    if kind == "metric":
        return [block["value"], block["label"]]
    if kind == "table":
        return [c for row in block["head"] + block["rows"] for c in row]
    if kind in ("diagram", "chart", "figure"):
        return block["labels"]
    if kind == "panel":
        return [t for child in block["children"] for t in block_texts(child)]
    return []


def _chars(texts):
    return Counter(ch for t in texts for ch in t if not ch.isspace())


def coverage_ok(segments, blocks, rotated=()):
    """The blocks hold exactly the characters of the page's segments (nothing lost or added)."""
    return _chars([s["t"] for s in segments] + list(rotated)) == _chars(t for b in blocks for t in block_texts(b))
