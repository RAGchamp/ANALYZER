"""What the package's links add to a question (INFO/SPLIT-FUNCTIONALITY-PLAN.md §8, Phase 4).

  statement_hints()  the notes behind the statement lines a question is about
                     ("tax" -> Current tax, Deferred tax liabilities -> Note 21),
                     offered to the note selector and shown in step 3 as
                     "suggested by the financial statements". Claude still
                     chooses, and the user still confirms.
  linked_block()     LINKED FIGURES and CHECKS for the selected notes: the
                     statement lines linked to them, with their figures and how
                     each link was found. It helps Claude find its way; the
                     verbatim notes and statements are always sent too.
"""

import re

from analyzer.note_selector import STOPWORDS, SYNONYMS

# Question words that match too many statement lines to say anything.
GENERIC = set("liability liabilities asset assets income expense expenses total net other related "
              "position performance impact change changes year years current noncurrent non amount "
              "amounts statement statements balance sheet profit loss cash flow flows equity "
              "significant key main overall trend trends analyse analyze".split())
KIND_WORDS = {"references": "cited in the Notes column", "ties_to": "same figure in the note",
              "mentions": "title match"}
LABEL_KIND = {"label": "cited in the line"}
STATEMENT_SHORT = {"PL": "P&L", "BS": "Balance Sheet", "CF": "Cash Flow", "EQ": "Changes in Equity"}
MAX_HINT_NOTES = 4
MAX_LINKED_LINES = 30
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def _singular(word):
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"                    # liabilities -> liability
    return word[:-1] if word.endswith("s") and not word.endswith(("ss", "us", "is")) else word


def question_words(question):
    """Content words of a question, plus the words its synonyms point to."""
    q = question.lower()
    words = {_singular(w) for w in re.findall(r"[a-z][a-z\-]{2,}", q)
             if w not in STOPWORDS and w not in GENERIC}
    for key, phrases in SYNONYMS.items():
        if key in q:
            for phrase in phrases:
                words |= {_singular(w) for w in phrase.split() if w not in GENERIC}
    return {w for w in words if len(w) > 2 and w not in GENERIC}


# "Profit before tax" is about profit: "before tax" doesn't make a line about tax.
INCIDENTAL_RE = re.compile(r"\b(?:before|after|net of|excluding|including|less)\s+(?:income\s+)?tax(?:es|ation)?\b")


def _label_words(label):
    label = INCIDENTAL_RE.sub(" ", label.lower())
    return {_singular(w) for w in re.findall(r"[a-z][a-z\-]{2,}", label)}


def _statement_short(unit_id):
    code = unit_id.split("-")[1] if "-" in unit_id else unit_id
    return STATEMENT_SHORT.get(code, code)


def _owners(index, section_keys, note_ids):
    """{unit id: the selected unit it belongs to}: the selected units, plus the
    notes inside a selected notes schedule (N25 in SCH-O)."""
    owners = {uid: uid for uid in note_ids}
    for key in section_keys:
        for unit in index["sections"].get(key, {}).get("notes", []):
            if unit.get("parent") in note_ids and unit.get("id"):
                owners.setdefault(unit["id"], unit["parent"])
    return owners


def statement_hints(package, question, sections=None):
    """[{"id": note id, "lines": ["Current tax (P&L)", ...]}], best first."""
    words = question_words(question)
    if not words:
        return []
    lines = [l for l in package.lines() if not sections or l["section_id"] in sections]
    by_note = {}
    for line in lines:
        if not words & _label_words(line["label"]):
            continue
        for edge in package.edges(src=line["id"]):
            if edge["kind"] not in ("references", "ties_to", "mentions") or edge["confidence"] == "low":
                continue
            entry = by_note.setdefault(edge["dst"], {"lines": [], "strength": 0})
            text = f"{line['label']} ({_statement_short(line['unit_id'])})"
            if text not in entry["lines"]:
                entry["lines"].append(text)
                entry["strength"] += 2 if edge["confidence"] == "high" else 1
    ranked = sorted(by_note.items(), key=lambda kv: (-kv[1]["strength"], kv[0]))
    return [{"id": uid, "lines": entry["lines"][:6]} for uid, entry in ranked[:MAX_HINT_NOTES]]


def _values_text(package, line_id):
    parts = []
    for v in package.values(line_id):
        period = v["period_label"] or v["heading"] or f"column {v['col'] + 1}"
        parts.append(f"{period} {v['raw']}")
    return " | ".join(parts)


# ---------------------------------------------------------------- narrative figures
# (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §8): money amounts in the chosen passages, converted to the
# statements' unit and looked for among the statement lines and the chosen notes.

UNIT_SCALES = [(r"crore", 1e7), (r"lakh|lac", 1e5), (r"billion|\bbn\b|\$b\b", 1e9),
               (r"million|\bmn\b|\$m\b|\bm\b", 1e6), (r"thousand|\b000s?\b|\$k\b", 1e3)]
UNIT_CURRENCIES = [(r"₹|\brs\b|inr|rupee", "INR"), (r"u\.?s\.?\s*dollar|us\$|usd|\$", "USD"),
                   (r"canadian dollar|c\$|cad", "CAD"), (r"a\$|aud|australian dollar", "AUD"),
                   (r"€|euro", "EUR"), (r"£|sterling|gbp", "GBP")]
MAX_NARRATIVE_FIGURES = 12
FIGURE_DIGITS_RE = re.compile(r"\d[\d,]*(?:\.(\d+))?")
NUMBER_RE = re.compile(r"(?<![\d.])\(?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\)?|(?<![\d.,])\d{4,}(?:\.\d+)?")


def statement_unit(package):
    """(currency, scale) the statements are printed in: "In ₹Million" -> ("INR", 1e6)."""
    units = str(package.meta.get("units") or package.index.get("units") or "").lower()
    scale = next((s for pattern, s in UNIT_SCALES if re.search(pattern, units)), None)
    currency = next((c for pattern, c in UNIT_CURRENCIES if re.search(pattern, units)), None)
    return currency, scale


def _number(raw):
    try:
        return float(raw.strip("()").replace(",", ""))
    except ValueError:
        return None


def _fmt(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _tolerance(fig, scale):
    """How far a figure can be from its match, in the statements' unit: half of its last printed
    digit ("₹1,324 crore" is ±0.5 crore = ±5 million), never less than half a statement unit
    (the statements round too). A looser percentage would tie unrelated lines by coincidence."""
    m = FIGURE_DIGITS_RE.search(fig["raw"])
    decimals = len(m.group(1)) if m and m.group(1) else 0
    number = float(m.group(0).replace(",", "")) if m else 0
    figure_scale = fig["amount"] / number if number else 1
    return max(0.5 * 10 ** -decimals * figure_scale / scale, 0.5)


# Words too common in a figure's context or a line's label to show they are about the same thing.
TIE_FILLER = set("total net other year years current non amount amounts crore crores lakh lakhs million "
                 "billion thousand rupee rupees inr usd during compared previous financial fiscal".split())
EXACT = 0.5     # statement units: a figure this close ties on its own


def _tie_words(text):
    return {_singular(w) for w in re.findall(r"[a-z][a-z\-]{2,}", (text or "").lower())
            if w not in STOPWORDS and w not in TIE_FILLER and len(w) > 3} - TIE_FILLER


def _ties(value, tolerance, candidate, context_words):
    """An exact match (within half a statement unit) ties on its own; a looser one only when the
    line's label shares a meaningful word with the figure's context - otherwise "₹397 crore" of
    revenue would tie to an unrelated 3,973.98 by coincidence."""
    diff = abs(abs(candidate["value"]) - value)
    if diff <= EXACT:
        return True
    return diff <= tolerance and bool(_tie_words(candidate["label"]) & context_words)


def narrative_figures_block(package, passages, sections, notes_text=""):
    """NARRATIVE FIGURES for the passages sent: each money amount, converted to the statements'
    unit, with the statement line or note figure it ties to - or that nothing does ("" if none)."""
    currency, scale = statement_unit(package)
    if not passages or not currency or not scale:
        return ""
    lines = package.query(
        'SELECT l.id, l.label, l.unit_id, l.pdf_page, v.value, v.raw, v.period_label FROM lines l '
        'JOIN "values" v ON v.line_id = l.id WHERE v.value IS NOT NULL AND l.section_id IN (%s)'
        % ",".join("?" * len(sections)), tuple(sections)) if sections else []
    titles = {uid: st["title"] for _, uid, st in package.statement_units()}
    note_numbers = [(n, raw) for raw in NUMBER_RE.findall(notes_text or "") if (n := _number(raw))]
    unit_word = {1e6: "million", 1e7: "crore", 1e5: "lakh", 1e9: "billion", 1e3: "thousand"}.get(scale, "")
    out, seen = [], set()
    for p in passages:
        stored = package.passage(p["id"])
        for fig in (stored or {}).get("figures", []):
            if fig["currency"] != currency or fig["raw"] in seen:
                continue
            seen.add(fig["raw"])
            value = fig["amount"] / scale
            tolerance = _tolerance(fig, scale)
            where = f"{p['path'].split(' › ')[-2] if ' › ' in p['path'] else p['path']}, p.{fig['page']}"
            head = f"- {fig['raw']} ({where}) = {_fmt(value)} {unit_word}"
            context_words = _tie_words(fig.get("context"))
            ties = [ln for ln in lines if ln["value"] and _ties(value, tolerance, ln, context_words)]
            if ties:
                ln = min(ties, key=lambda t: abs(abs(t["value"]) - value))
                out.append(f"{head} -> ties to {titles.get(ln['unit_id'], ln['unit_id'])} \"{ln['label']}\" "
                           f"{ln['raw']} {ln['period_label'] or ''} (p.{ln['pdf_page']})".rstrip())
            elif any(abs(abs(n) - value) <= EXACT for n, _ in note_numbers):
                raw = next(r for n, r in note_numbers if abs(abs(n) - value) <= EXACT)
                out.append(f"{head} -> appears in the notes provided ({raw})")
            else:
                out.append(f"{head} -> no statement line or figure in the notes provided matches")
            if len(out) >= MAX_NARRATIVE_FIGURES:
                break
        if len(out) >= MAX_NARRATIVE_FIGURES:
            break
    if not out:
        return ""
    return ("----- NARRATIVE FIGURES (money amounts in the report sections provided, converted to the "
            f"statements' unit, {currency} {unit_word}; matched automatically - verify) -----\n"
            + "\n".join(out) + "\n----- END OF NARRATIVE FIGURES -----\n\n")


def linked_block(package, notes, sections):
    """LINKED FIGURES and CHECKS for these notes, as prompt text ("" if none)."""
    index = package.index
    owners = _owners(index, sections, {n["id"] for n in notes})
    labels = {n["id"]: (f"Note {n['no']}" if n.get("kind", "note") == "note" and isinstance(n["no"], int)
                        else n.get("label") or n["id"]) for n in notes}
    statement_titles = {uid: st["title"] for key, uid, st in package.statement_units()}

    found = {}
    for uid in sorted(owners):
        for edge in package.edges(dst=uid):
            if edge["kind"] not in ("references", "ties_to", "mentions"):
                continue
            line = package.query("SELECT * FROM lines WHERE id = ?", (edge["src"],))
            if not line or line[0]["section_id"] not in sections:
                continue
            entry = found.setdefault(edge["src"], {"line": line[0], "links": []})
            entry["links"].append((labels[owners[uid]], edge))
    if not found:
        return ""

    def rank(item):
        line, links = item[1]["line"], item[1]["links"]
        best = min(CONFIDENCE_ORDER.get(e["confidence"], 3) for _, e in links)
        return (best, line["section_id"], line["unit_id"], line["seq"])

    chosen = sorted(found.items(), key=rank)[:MAX_LINKED_LINES]
    chosen.sort(key=lambda item: (item[1]["line"]["section_id"], item[1]["line"]["unit_id"], item[1]["line"]["seq"]))
    out = ["----- LINKED FIGURES (statement lines the app linked to the notes provided; "
           "found automatically - check them against the statements and notes) -----"]
    for line_id, entry in chosen:
        line = entry["line"]
        how = {}
        for note_label, edge in entry["links"]:
            word = LABEL_KIND.get(edge["method"]) or KIND_WORDS.get(edge["kind"], edge["kind"])
            if word not in how.setdefault(note_label, []):
                how[note_label].append(word)
        where = f"{statement_titles.get(line['unit_id'], line['unit_id'])}, PDF p.{line['pdf_page']}"
        links = "; ".join(f"{label} ({', '.join(words)})" for label, words in how.items())
        out.append(f"- {line['label']} ({where}) | {_values_text(package, line_id)} -> {links}")

    # does each balance sheet involved balance, and anything wrong with these lines
    line_ids = {line_id for line_id, _ in chosen}
    codes = {line_id.split("-")[0] for line_id in line_ids}
    checks = [c for c in package.checks()
              if (c["kind"] == "balance" and c["subject"].split("-")[0] in codes)
              or (c["subject"] in line_ids and c["status"] in ("warn", "fail"))]
    if checks:
        out.append("CHECKS (automatic)")
        out += [f"- {c['status']}: {c['message']}" for c in checks[:12]]
    out.append("----- END OF LINKED FIGURES -----")
    return "\n".join(out) + "\n\n"
