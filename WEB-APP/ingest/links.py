"""Links between statement lines and notes, and automatic checks
(INFO/SPLIT-FUNCTIONALITY-PLAN.md §7, Phase 3). Everything here is
deterministic, and every link says how it was found and on what evidence.

Links (edges: src = a statement line, dst = a note / schedule unit or a line):
  references  the statement's Notes / Schedule column ("21", "'E'"), or
              "(note 3)" / "(Refer note 16)" in the line's label       high
  ties_to     a distinctive figure of the line is printed in exactly one
              note of the section (two or three notes: medium)           high
  mentions    the line's words match a note title ("Income tax expense"
              -> "Taxation"); only for lines without a reference         medium
  carries     the same line in another statement with the same figures
              (net income in the P&L and the cash flow statement)        high

Checks:
  subtotal    a total equals the lines above it (since the heading or the
              previous total); totals that aren't simple sums (profit =
              revenue - expenses) are "unverified", not failures
  balance     total assets = total equity and liabilities, every period
  carries     see above
  note_tie    a figure of a line that references a note is printed in
              that note ("warn" when it isn't - notes often show parts only)
"""

import re
from collections import defaultdict

from rptpkg.model import note_unit_id, section_code

# "(note 3)", "(Refer note 16)", "(notes 5 and 6)", "(Schedule XVII)", "Note No. 15"
NOTE_IN_LABEL_RE = re.compile(r"\bnotes?\s*(?:no\.?\s*)?(\d{1,3}(?:\s*(?:,|and|&)\s*\d{1,3})*)\b", re.I)
SCHEDULE_IN_LABEL_RE = re.compile(r"\b(?i:schedule)(?:\s+['‘’]?|\s*['‘’])([IVXL]{1,6}|[A-Z]{1,2}|\d{1,2})\b")
PAGE_MARK_RE = re.compile(r"^--- PDF page (\d+)")
FIGURE_RE = re.compile(r"\(?-?\$?\s?\d[\d,]*\.?\d*\)?")
YEAR_VALUE_RE = re.compile(r"^(19|20)\d{2}$")
TOTAL_ASSETS_RE = re.compile(r"^total\s+assets$", re.I)
TOTAL_EQ_LIAB_RE = re.compile(
    r"^total\s+(?:equity\s+and\s+liabilities|liabilities\s+and\s+(?:shareholders|stockholders)?['’]?\s*equity)$",
    re.I)
CARRY_LABELS = re.compile(
    r"^(?:net\s+income|net\s+earnings|net\s+profit|profit\s+for\s+the\s+(?:year|period)"
    r"|profit\s+before\s+tax(?:ation)?|total\s+comprehensive\s+income(?:\s+for\s+the\s+(?:year|period))?)$",
    re.I)

STOPWORDS = set("a an and the of on in for from to at by with less net other total its their "
                "including includes include balances balance vie refer note notes year end beginning "
                "period current non noncurrent".split())
GENERIC = set("asset liability income expense revenue cost amount item provision reserve "
              "charge fund payable receivable account".split())
# Words that never link a line to a note on their own: "Interest income" is
# not about "Interest in Joint Ventures", nor "Cash generated from operations"
# about "Revenue from operations".
WEAK = set("interest operation equity decrease increase change sale gain proceed payment paid "
           "financial instrument share item value fair issue issuance allotment movement transfer "
           "adjustment receipt received purchase dividend generated attributable owner parent".split())
STEMS = {"taxation": "tax", "taxes": "tax", "liabilities": "liability", "assets": "asset", "expenses": "expense", "losses": "loss",
         "costs": "cost", "benefits": "benefit", "investments": "investment", "securities": "security",
         "revenues": "revenue", "leases": "lease", "earnings": "earning", "provisions": "provision",
         "inventories": "inventory", "receivables": "receivable", "payables": "payable",
         "intangibles": "intangible", "measurements": "measurement", "reserves": "reserve"}


# ---------------------------------------------------------------- helpers

def _words(text):
    """Content words of a label or title, singular and stemmed:
    "Deferred tax assets (net)" -> {"deferred", "tax", "asset"}."""
    text = re.sub(r"\([^)]*\)", " ", text.lower().replace("’", "'"))
    words = set()
    for word in re.findall(r"[a-z][a-z'\-]+", text):
        for part in word.strip("'-").split("-"):
            if part.endswith("'s"):
                part = part[:-2]
            part = STEMS.get(part.strip("'"), part.strip("'"))
            if part.endswith("s") and not part.endswith(("ss", "us", "is")):
                part = part[:-1]
            if len(part) > 2 and part not in STOPWORDS:
                words.add(part)
    return words


def _key(value):
    return f"{abs(value):.2f}"


def _distinctive(raw, value):
    digits = re.sub(r"\D", "", raw)
    return (value is not None and len(digits.lstrip("0")) >= 4
            and not YEAR_VALUE_RE.match(digits))


def _note_figures(text):
    """{figure key: [(pdf page, row label)]} of the numbers in a note's text."""
    found = defaultdict(list)
    page = None
    for line in text.splitlines():
        mark = PAGE_MARK_RE.match(line)
        if mark:
            page = int(mark.group(1))
            continue
        cells = [c.strip() for c in line.split(" | ")]
        label = cells[0] if cells and not FIGURE_RE.fullmatch(cells[0]) else ""
        for match in FIGURE_RE.finditer(line):
            raw = match.group(0).strip("() $")
            digits = re.sub(r"\D", "", raw)
            if len(digits.lstrip("0")) < 4 or YEAR_VALUE_RE.match(digits):
                continue
            if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d+\.\d+|\d{4,}",
                                raw):
                continue
            value = float(raw.replace(",", ""))
            found[_key(value)].append((page, label[:80]))
    return found


def _unit_lookup(index, key):
    """Ways a statement can cite a unit of this section -> unit id."""
    section = index["sections"][key]
    code = section_code(key, section)
    ids = {note_unit_id(key, n) for n in section.get("notes", [])}
    lookup = {}
    for note in section.get("notes", []):
        uid = note_unit_id(key, note)
        if isinstance(note["no"], int) or re.fullmatch(r"\d+\.\d+", str(note["no"])):   # 21, or "2.1" (Infosys)
            lookup[str(note["no"])] = uid
    for uid in ids:
        for prefix in (f"{code}-", ""):
            if uid.startswith(prefix + "SCH-"):
                lookup.setdefault(f"'{uid[len(prefix) + 4:]}'", uid)
                lookup.setdefault(uid[len(prefix) + 4:], uid)
            if re.fullmatch(re.escape(prefix) + r"N\d+", uid):
                lookup.setdefault(uid[len(prefix) + 1:], uid)
    return lookup


def _resolve(ref, lookup):
    ref = ref.strip()
    if ref in lookup:
        return lookup[ref]
    match = re.match(r"^(\d{1,3})", ref)            # "5a", "21(a)", "21.1" -> 5 / 21
    return lookup.get(match.group(1)) if match else None


# ---------------------------------------------------------------- links

def build_links(index, unit_texts, lines, values):
    edges = []
    by_line = defaultdict(list)
    for v in values:
        by_line[v["line_id"]].append(v)
    lookups = {key: _unit_lookup(index, key) for key in index["sections"]}
    note_figures = {(key, uid): _note_figures(t["text"]) for (key, uid), t in unit_texts.items()}
    titles = {}
    for key, section in index["sections"].items():
        titles[key] = [(note_unit_id(key, n), n["title"], _words(n["title"]))
                       for n in section.get("notes", []) if n.get("kind", "note") in ("note", "schedule")]

    def add(src, dst, kind, method, confidence, evidence):
        edges.append({"src": src, "dst": dst, "kind": kind, "method": method,
                      "confidence": confidence, "evidence": evidence})

    for line in lines:
        key = line["section_id"]
        referenced = set()
        if line["note_ref"]:
            uid = _resolve(line["note_ref"], lookups[key])
            if uid:
                referenced.add(uid)
                add(line["id"], uid, "references", "notes column", "high",
                    f"'{line['label']}' cites {line['note_ref']} on PDF p.{line['pdf_page']}")
        cited = [n for m in NOTE_IN_LABEL_RE.finditer(line["label"]) for n in re.findall(r"\d+", m.group(1))]
        cited += [m.group(1).upper() for m in SCHEDULE_IN_LABEL_RE.finditer(line["label"])]
        for ref in cited:
            uid = _resolve(ref, lookups[key])
            if uid and uid not in referenced:
                referenced.add(uid)
                add(line["id"], uid, "references", "label", "high",
                    f"'{line['label']}' on PDF p.{line['pdf_page']}")

        # the line's distinctive figures, printed in a note of the same section
        hits = defaultdict(list)
        for v in by_line[line["id"]]:
            if v["nil"] or not _distinctive(v["raw"], v["value"]):
                continue
            for (sec, uid), figures in note_figures.items():
                if sec == key and _key(v["value"]) in figures:
                    hits[uid].append((v, figures[_key(v["value"])][0]))
        # a four-digit figure can be a coincidence in a long note: it needs a
        # second figure of the line in the same note
        hits = {uid: found for uid, found in hits.items()
                if len({v["value"] for v, _ in found}) >= 2
                or any(len(re.sub(r"\D", "", v["raw"]).lstrip("0")) >= 5 for v, _ in found)}
        if 0 < len(hits) <= 3:
            confidence = "high" if len(hits) == 1 else "medium"
            for uid, found in hits.items():
                v, (page, label) = found[0]
                where = f"row '{label}'" if label else "its text"
                add(line["id"], uid, "ties_to", "figure match", confidence,
                    f"{v['raw']} ({v['period_label'] or v['heading'] or 'figure'}) on PDF p.{line['pdf_page']} "
                    f"is in {uid} {where}" + (f" on PDF p.{page}" if page else ""))

        # title match, only for lines that cite no note
        if not referenced and line["kind"] == "data":
            words = _words(line["label"])
            specific = words - GENERIC
            matches = []
            for uid, title, title_words in titles[key]:
                title_specific = title_words - GENERIC
                if not specific or not title_specific:
                    continue
                if not (words & title_words) - GENERIC - WEAK:
                    continue
                if title_words <= words or (specific <= title_words and len(words) <= 4):
                    # how well the whole title fits the label, to keep only the best
                    matches.append((len(words & title_words) / len(words | title_words), uid, title))
            best = max((m[0] for m in matches), default=0)
            matches = [(uid, title) for score, uid, title in matches if score >= best - 1e-9]
            if 0 < len(matches) <= 2:
                for uid, title in matches:
                    add(line["id"], uid, "mentions", "title match",
                        "medium" if len(matches) == 1 else "low",
                        f"'{line['label']}' matches the title '{title}'")
    return edges


# ---------------------------------------------------------------- checks

def _col_values(line_values):
    return {v["col"]: v for v in line_values if v["value"] is not None}


def _close(total, expected_raw_value):
    """total == printed figure, give or take 1 in its last printed digit (rounding)."""
    printed, raw = expected_raw_value
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return abs(total - printed) <= 10 ** -decimals + 1e-9


def build_checks(index, views, lines, values):
    checks, edges = [], []
    by_line = defaultdict(list)
    for v in values:
        by_line[v["line_id"]].append(v)

    # subtotals: walk each table's rows; lines know the row they came from
    line_at = {(l["unit_id"], l["pdf_page"], l["table_no"], l["row_no"]): l for l in lines}
    for (key, uid), view in views.items():
        for page in view["pages"]:
            for table_no, table in enumerate(page["tables"]):
                block, since_heading = [], []
                for row_no, row in enumerate(table["rows"]):
                    if row["kind"] in ("section", "header", "group", "unit"):
                        block, since_heading = [], []
                        continue
                    line = line_at.get((uid, page["pdf_page"], table_no, row_no))
                    if line is None:
                        continue          # a year row or a row without figures
                    if line["kind"] == "data":
                        block.append(line)
                        since_heading.append(line)
                        continue
                    total = _col_values(by_line[line["id"]])
                    verified = None
                    for candidate in (block, since_heading):
                        if len(candidate) < 2 or not total:
                            continue
                        sums = {}
                        for part in candidate:
                            for col, v in _col_values(by_line[part["id"]]).items():
                                if col in total:
                                    sums[col] = sums.get(col, 0.0) + v["value"]
                        if all(col in sums and _close(sums[col], (t["value"], t["raw"])) for col, t in total.items()):
                            verified = candidate
                            break
                    if total and len(since_heading) >= 2:
                        checks.append({"kind": "subtotal", "subject": line["id"],
                                       "status": "ok" if verified else "unverified",
                                       "expected": None, "actual": None,
                                       "message": (f"'{line['label']}' = the {len(verified)} lines above it"
                                                   if verified else
                                                   f"'{line['label']}' is not a simple sum of the lines above it")})
                    if verified:
                        # a verified subtotal stands for its lines in the totals further down
                        covered = {id(part) for part in verified}
                        since_heading = [part for part in since_heading if id(part) not in covered]
                    since_heading.append(line)
                    block = []

    # the balance sheet balances
    for key, section in index["sections"].items():
        for line in [l for l in lines if l["section_id"] == key and TOTAL_ASSETS_RE.match(l["label"].strip())]:
            other = next((l for l in lines if l["unit_id"] == line["unit_id"]
                          and TOTAL_EQ_LIAB_RE.match(l["label"].strip())), None)
            if not other:
                continue
            assets, claims = _col_values(by_line[line["id"]]), _col_values(by_line[other["id"]])
            for col, v in assets.items():
                if col not in claims:
                    continue
                ok = _close(v["value"], (claims[col]["value"], claims[col]["raw"]))
                checks.append({"kind": "balance", "subject": line["id"], "status": "ok" if ok else "fail",
                               "expected": claims[col]["value"], "actual": v["value"],
                               "message": f"Total assets {v['raw']} vs '{other['label']}' {claims[col]['raw']} "
                                          f"({v['period_label'] or v['heading'] or 'column ' + str(col)})"})

    # the same figure carried into another statement
    for key in index["sections"]:
        carry = [l for l in lines if l["section_id"] == key and CARRY_LABELS.match(l["label"].strip().rstrip(":"))]
        seen = set()
        for a in carry:
            for b in carry:
                if a["unit_id"] >= b["unit_id"] or (a["id"], b["id"]) in seen:
                    continue
                if a["label"].strip().lower().rstrip(":") != b["label"].strip().lower().rstrip(":"):
                    continue
                va = {v["period_label"]: v for v in by_line[a["id"]] if v["period_label"] and v["value"] is not None}
                vb = {v["period_label"]: v for v in by_line[b["id"]] if v["period_label"] and v["value"] is not None}
                common = sorted(set(va) & set(vb))
                if not common:
                    continue
                seen.add((a["id"], b["id"]))
                ok = all(_close(va[p]["value"], (vb[p]["value"], vb[p]["raw"])) for p in common)
                checks.append({"kind": "carries", "subject": a["id"], "status": "ok" if ok else "warn",
                               "expected": vb[common[0]]["value"], "actual": va[common[0]]["value"],
                               "message": f"'{a['label']}' in {a['unit_id']} and {b['unit_id']}: "
                                          + ", ".join(f"{p} {va[p]['raw']} / {vb[p]['raw']}" for p in common)})
                if ok:
                    edges.append({"src": a["id"], "dst": b["id"], "kind": "carries", "method": "same figures",
                                  "confidence": "high",
                                  "evidence": ", ".join(f"{p} {va[p]['raw']}" for p in common)})
    return checks, edges


def note_tie_checks(edges, lines, values, unit_texts):
    """A line that cites a note: is one of its figures printed in that note?"""
    checks = []
    by_line = defaultdict(list)
    for v in values:
        by_line[v["line_id"]].append(v)
    line_by_id = {l["id"]: l for l in lines}
    figures_cache = {}
    tied = {(e["src"], e["dst"]) for e in edges if e["kind"] == "ties_to"}
    for edge in edges:
        if edge["kind"] != "references":
            continue
        line = line_by_id[edge["src"]]
        text = unit_texts.get((line["section_id"], edge["dst"]))
        if not text:
            continue
        figures = figures_cache.setdefault(edge["dst"], _note_figures(text["text"]))
        candidates = [v for v in by_line[line["id"]] if not v["nil"] and v["value"] is not None
                      and _distinctive(v["raw"], v["value"])]
        if not candidates:
            continue
        found = [v for v in candidates if _key(v["value"]) in figures]
        ok = bool(found) or (edge["src"], edge["dst"]) in tied
        checks.append({"kind": "note_tie", "subject": line["id"], "status": "ok" if ok else "warn",
                       "expected": None, "actual": candidates[0]["value"],
                       "message": (f"'{line['label']}' {found[0]['raw']} is in {edge['dst']}" if found else
                                   f"'{line['label']}' cites {edge['dst']}, but none of its figures "
                                   f"({', '.join(v['raw'] for v in candidates[:3])}) is printed there")})
    return checks


def summaries(lines, edges, checks):
    linkable = [l for l in lines]
    linked = {e["src"] for e in edges if e["kind"] in ("references", "ties_to", "mentions")}
    by_method = defaultdict(set)
    for e in edges:
        if e["kind"] in ("references", "ties_to", "mentions"):
            by_method[e["method"]].add(e["src"])
    counts = defaultdict(int)
    for c in checks:
        counts[c["status"]] += 1
    by_kind = defaultdict(lambda: defaultdict(int))
    for c in checks:
        by_kind[c["kind"]][c["status"]] += 1
    return ({"lines": len(linkable), "linked": len(linked & {l["id"] for l in linkable}),
             "by_method": {m: len(s) for m, s in sorted(by_method.items())}},
            {"total": len(checks), "ok": counts["ok"], "warn": counts["warn"], "fail": counts["fail"],
             "unverified": counts["unverified"],
             "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())}})


def build(index, content):
    """Adds edges, checks and their summaries to the package content."""
    lines, values = content["lines"], content["values"]
    edges = build_links(index, content["unit_texts"], lines, values)
    checks, carry_edges = build_checks(index, content["statement_views"], lines, values)
    edges += carry_edges
    checks += note_tie_checks(edges, lines, values, content["unit_texts"])
    content["edges"], content["checks"] = edges, checks
    content["link_summary"], content["check_summary"] = summaries(lines, edges, checks)
