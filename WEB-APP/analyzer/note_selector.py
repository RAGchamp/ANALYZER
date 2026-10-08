"""STEP 3 - decide which notes answer the user's question.

1. Scope (rule-based): Consolidated by default; Standalone only when the
   question explicitly asks for it; both when it asks to compare them.
2. A small Claude call that sees only the note titles and sub-headings of
   that scope (never page content) and returns JSON note ids.
3. Keyword/synonym matching against titles if the Claude call fails.
Notes named explicitly in the question ("Note 41") are always included.

Old-format reports (index "format" == "legacy") have standalone accounts
only, and their choices are schedules (SCH-E), numbered notes (N25) and
report sections (DIR, AUD, SUM, CHR) - see ingest/legacy_index. They get their own
selector prompt and extra keyword synonyms for the old terminology.

Scanned reports (format "transcribed") have one section per entity (e.g. a
registrant plus subsidiaries with their own statements). The scope is the
entities the question names (default: the registrant); unit ids carry an
entity code (BH-N5, NI-SCH-V, NFMI-AUD) when there is more than one entity.
"""

import json
import logging
import re

import config
from claude_client import ClaudeError, fill_prompt, run_claude, system_prompt
from rptpkg.model import LEGACY_SECTION, SECTION_PREFIX, note_unit_id

log = logging.getLogger(__name__)

STANDALONE_RE = re.compile(
    r"\bstand[\s-]?alone\b|\bparent(?:\s+company)?\s+only\b|\bcompany[\s-]only\b"
    r"|\b(?:not|don'?t use|do not use|exclud\w*|without)\s+(?:the\s+)?consolidated\b",
    re.I,
)
NEGATED_CONSOLIDATED_RE = re.compile(
    r"\b(?:not|don'?t use|do not use|exclud\w*|without)\s+(?:the\s+)?consolidated\b", re.I)
CONSOLIDATED_RE = re.compile(r"\bconsolidated\b", re.I)
BOTH_RE = re.compile(r"\bboth\b", re.I)
NOTE_REF_RE = re.compile(r"\bnote\s*(?:no\.?|number|#)?\s*(\d{1,3})\b", re.I)
SCHEDULE_REF_RE = re.compile(
    r"\bsch(?:edule)?\.?\s*[‘’'`\"]?\s*([A-Za-z]{1,2}|\d{1,2})\b[‘’'`\"]?", re.I)
NARRATIVE_REF_RES = {
    "DIR": re.compile(r"\b(?:directors'?|directors’) report\b|\breport of the directors\b", re.I),
    "AUD": re.compile(r"\bauditors?'?’? report\b|\breport of the auditors?\b", re.I),
    "SUM": re.compile(r"\b(?:financial summary|ten[- ]year|10[- ]year)\b", re.I),
    "CHR": re.compile(r"\bchairman'?’?s (?:statement|speech|address)\b", re.I),
}

PREFIX_SECTION = {v: k for k, v in SECTION_PREFIX.items()}

# question word -> phrases to look for in note titles / sub-headings
SYNONYMS = {
    "tax": ["tax"], "deferred": ["deferred tax"], "mat": ["tax"],
    "debt": ["borrowings"], "borrow": ["borrowings"], "loan": ["borrowings", "loans"],
    "debenture": ["borrowings"], "leverage": ["borrowings", "capital management"],
    "lease": ["leases"], "related part": ["related party"],
    "contingen": ["contingent liabilities"], "litigation": ["contingent liabilities"],
    "dispute": ["contingent liabilities"], "commitment": ["commitments"],
    "employee": ["employee benefits", "gratuity"], "pension": ["post-employment"],
    "gratuity": ["gratuity"], "esop": ["share-based"], "share-based": ["share-based"],
    "revenue": ["revenue"], "sales": ["revenue"], "income": ["other income"],
    "inventor": ["inventories"], "stock": ["inventories"],
    "receivable": ["trade receivables"], "debtor": ["trade receivables"],
    "payable": ["trade payables"], "creditor": ["trade payables"], "msme": ["micro"],
    "cash": ["cash and bank"], "liquidity": ["cash and bank", "financial risk"],
    "hedg": ["hedging", "derivative"], "derivative": ["derivative", "hedging"],
    "forex": ["hedging", "financial risk"], "currency": ["hedging", "financial risk"],
    "segment": ["segment"], "geograph": ["segment"],
    "goodwill": ["goodwill", "intangible"], "intangible": ["intangible"],
    "impairment": ["goodwill", "exceptional", "judgements"],
    "fair value": ["fair value"], "risk": ["financial risk"],
    "provision": ["provisions"], "warrant": ["provisions"],
    "equity": ["equity"], "share capital": ["share capital"], "dividend": ["distribution"],
    "capex": ["property, plant"], "fixed asset": ["property, plant"],
    "ppe": ["property, plant"], "depreciation": ["depreciation"],
    "acqui": ["business combinations"], "subsidiar": ["group information", "partly owned"],
    "joint venture": ["joint ventures"], "associate": ["associates"],
    "investment": ["investments"], "eps": ["earnings per share"],
    "earnings per share": ["earnings per share"], "exceptional": ["exceptional"],
    "csr": ["social responsibility"], "ratio": ["ratio analysis"],
    "finance cost": ["finance costs"], "interest": ["finance costs", "borrowings"],
    "accounting polic": ["accounting policies"], "judgement": ["judgements"],
    "estimate": ["judgements"], "expense": ["other expenses"],
    "raw material": ["raw materials"], "capital management": ["capital management"],
}
# Old-format reports: question word -> words in schedule / note titles,
# sub-headings or the first words of untitled notes.
LEGACY_SYNONYMS = {
    "fixed asset": ["fixed assets"], "ppe": ["fixed assets"], "property": ["fixed assets"],
    "capex": ["fixed assets", "expansion", "capital expenditure"],
    "expansion": ["expansion", "fixed assets"], "depreciation": ["depreciation", "fixed assets"],
    "reserve": ["reserves"], "surplus": ["reserves"], "equity": ["share capital", "reserves"],
    "debtor": ["current assets", "sundry debtors"], "receivable": ["current assets", "sundry debtors"],
    "creditor": ["current liabilities", "sundry creditors"], "payable": ["current liabilities"],
    "inventor": ["current assets", "stocks"], "stock": ["stocks", "current assets"],
    "borrow": ["secured loans", "unsecured loans", "loan"], "debt": ["secured loans", "unsecured loans"],
    "loan": ["secured loans", "unsecured loans", "loans & advances"],
    "interest": ["interest"], "tax": ["taxation", "tax"], "contingen": ["contingent"],
    "guarantee": ["contingent", "guarantee"], "dispute": ["contingent", "assessment"],
    "sales": ["sales", "turnover"], "revenue": ["sales", "other income"], "income": ["other income"],
    "expense": ["expenses", "expenditure"], "cost": ["expenses", "raw materials"],
    "raw material": ["raw materials"], "import": ["imports", "c.i.f"], "export": ["export"],
    "foreign": ["foreign"], "employee": ["employees", "gratuity"], "gratuity": ["gratuity"],
    "dividend": ["dividend"], "profit": ["financial results", "profit"],
    "trend": ["financial summary"], "history": ["financial summary"], "year": ["financial summary"],
    "director": ["directors' report"], "auditor": ["auditors' report"], "qualif": ["auditors' report"],
    "amalgamat": ["amalgamat"], "capacity": ["capacity"], "production": ["production"],
}
STOPWORDS = set(
    "analyze analyse analysis comment company company's companys what which how the "
    "and for with this that from their its about into over note notes report annual "
    "please explain give detail details detailed provide related".split()
)


def detect_scope(question):
    """Return (scope, reason); scope is consolidated | standalone | both."""
    wants_standalone = bool(STANDALONE_RE.search(question))
    without_negations = NEGATED_CONSOLIDATED_RE.sub("", question)
    wants_consolidated = bool(CONSOLIDATED_RE.search(without_negations))
    if wants_standalone and wants_consolidated:
        return "both", "Question mentions both standalone and consolidated statements."
    if wants_standalone and BOTH_RE.search(question):
        return "both", "Question asks about both sets of statements."
    if wants_standalone:
        return "standalone", "Question explicitly asks for standalone statements."
    return "consolidated", "Default: notes to the consolidated financial statements."


def is_legacy(index):
    return index.get("format") == "legacy"


def is_transcribed(index):
    return index.get("format") == "transcribed"


def max_units(index):
    return config.MAX_UNITS_LEGACY if (is_legacy(index) or is_transcribed(index)) else config.MAX_NOTES


def entity_scope(index, question):
    """(section keys, reason) for a transcribed report: the entities the
    question names, "subsidiaries" = all but the registrant, else the registrant."""
    keys = list(index["sections"])
    q = question.lower()
    words = {k: {w for w in k.split("-") if len(w) > 2} for k in keys}
    counts = {}
    for ws in words.values():
        for w in ws:
            counts[w] = counts.get(w, 0) + 1
    named = [k for k in keys
             if words[k] and (all(w in q for w in words[k])
                              or any(counts[w] == 1 and re.search(rf"\b{re.escape(w)}", q) for w in words[k]))]
    if len(keys) > 1 and re.search(r"\bsubsidiar(y|ies)\b", q):
        named += [k for k in keys[1:] if k not in named]
    if named:
        labels = ", ".join(index["sections"][k]["label"] for k in named)
        return named, f"Question names: {labels}."
    return keys[:1], (f"Default: {index['sections'][keys[0]]['label']} (the registrant)."
                      if keys else "No statements found.")


def _scope_sections(index, scope):
    if isinstance(scope, (list, tuple)):
        return [s for s in scope if s in index["sections"]]
    if scope == "both" and is_transcribed(index):
        return list(index["sections"])
    wanted = ["consolidated", "standalone"] if scope == "both" else [scope]
    return [s for s in wanted if s in index["sections"]]


def _note_id(section, note):
    """C21 / S21 for modern notes; old-format units carry their own id (SCH-E, N25, DIR)."""
    return note_unit_id(section, note)


def _note_choice(index, section, note):
    section_label = (index["sections"].get(section, {}).get("label")
                     or config.SECTION_LABELS.get(section, section))
    return {
        "id": _note_id(section, note),
        "section": section,
        "section_label": section_label,
        "no": note["no"],
        "title": note["title"],
        "kind": note.get("kind", "note"),
        "label": note.get("label") or f"{section_label} Note {note['no']} — {note['title']}",
        "title_is_excerpt": note.get("title_is_excerpt", False),
        "parent": note.get("parent"),
        "start_page": note["start_page"],
        "end_page": note["end_page"],
        "printed_pages": note.get("printed_pages"),
    }


def all_note_choices(index, scope):
    return [
        _note_choice(index, section, note)
        for section in _scope_sections(index, scope)
        for note in index["sections"][section]["notes"]
    ]


def _by_id(index, scope):
    return {c["id"]: c for c in all_note_choices(index, scope)}


def _explicit_refs(question, index, scope):
    choices = _by_id(index, scope)
    wanted = []
    if is_transcribed(index):
        multi = len(index["sections"]) > 1
        for section in _scope_sections(index, scope):
            prefix = f"{index['sections'][section]['code']}-" if multi else ""
            wanted += [f"{prefix}N{int(m.group(1))}" for m in NOTE_REF_RE.finditer(question)]
            wanted += [f"{prefix}SCH-{m.group(1).upper()}" for m in SCHEDULE_REF_RE.finditer(question)]
            if NARRATIVE_REF_RES["AUD"].search(question) or re.search(r"accountants'?’? report", question, re.I):
                wanted.append(f"{prefix}AUD")
    elif is_legacy(index):
        wanted += [f"N{int(m.group(1))}" for m in NOTE_REF_RE.finditer(question)]
        wanted += [f"SCH-{m.group(1).upper()}" for m in SCHEDULE_REF_RE.finditer(question)]
        wanted += [key for key, pattern in NARRATIVE_REF_RES.items() if pattern.search(question)]
    else:
        for match in NOTE_REF_RE.finditer(question):
            wanted += [_note_id(section, int(match.group(1))) for section in _scope_sections(index, scope)]
    found = []
    for unit_id in wanted:
        if unit_id in choices and unit_id not in found:
            found.append(unit_id)
    return found


def explicit_unit_ids(question, index, sections):
    """Ids of the notes / schedules / report sections a follow-up names explicitly."""
    if is_transcribed(index):
        return _explicit_refs(question, index, list(sections) or list(index["sections"])[:1])
    scope = "both" if len(sections) > 1 else (sections[0] if sections else "consolidated")
    return _explicit_refs(question, index, scope)


def drop_covered(choices):
    """A whole notes schedule already contains its numbered notes: when both
    are confirmed, send the schedule once."""
    ids = {c["id"] for c in choices}
    return [c for c in choices if not (c.get("parent") and c["parent"] in ids)]


def prefer_specific(choices):
    """When the selector picks a whole notes schedule and some of its notes,
    keep the specific notes - they are what the question is about."""
    parents = {c["parent"] for c in choices if c.get("parent")}
    return [c for c in choices if c["id"] not in parents]


def hints_text(hints, valid_ids):
    """The selector prompt's block of notes suggested by the financial
    statements (analyzer/linked.py); "" when there is nothing to suggest."""
    shown = [h for h in hints or [] if h["id"] in valid_ids]
    if not shown:
        return ""
    return ("\nSUGGESTED BY THE FINANCIAL STATEMENTS - the statement lines your question is about are "
            "linked to these notes (a hint only; choose on the notes' content):\n"
            + "\n".join(f"- {h['id']}: " + "; ".join(h["lines"]) for h in shown) + "\n")


def _legacy_selector_prompt(index, question, hints=""):
    section = index["sections"][LEGACY_SECTION]
    groups = {"schedule": [], "note": [], "narrative": []}
    for unit in section["notes"]:
        subs = "; ".join(unit.get("subheadings", [])[:8])
        if unit["kind"] == "schedule":
            line = f"{unit['id']}: {unit['title']}"
            if unit.get("part_of"):
                line += f" (part of the {unit['part_of']})"
        elif unit["kind"] == "note":
            line = f"{unit['id']}: " + (f"— {unit['digest']}" if unit.get("title_is_excerpt")
                                        else f"{unit['title']} — {unit.get('digest', '')}")
        else:
            line = f"{unit['id']}: {unit['title']}"
        if subs:
            line += f"  [sub-headings: {subs}]"
        groups[unit["kind"]].append(line)
    notes_list = "\n\n".join(
        f"{heading}\n" + "\n".join(lines)
        for heading, lines in (("SCHEDULES:", groups["schedule"]),
                               ("NUMBERED NOTES:", groups["note"]),
                               ("REPORT SECTIONS:", groups["narrative"]))
        if lines)
    period = (f"year ended {index['fiscal_year_end_label']}"
              if index.get("fiscal_year_end_label") else "an old report")
    return fill_prompt(
        "selector_template_legacy.txt",
        PERIOD=period,
        STATEMENTS=", ".join(index.get("statements_present") or []) or "none found",
        MAX_NOTES=config.MAX_UNITS_LEGACY,
        NOTES_LIST=notes_list,
        HINTS=hints,
        QUESTION=question,
    )


def _transcribed_selector_prompt(index, question, scope, hints=""):
    blocks = []
    for key in _scope_sections(index, scope):
        section = index["sections"][key]
        statements = "; ".join(s["title"] for s in section["statements"]) or "none"
        lines = [f"== {section['label']} (statements always provided: {statements})"]
        for unit in section["notes"]:
            line = f"{unit['id']}: {unit['label']}"
            if unit.get("digest"):
                line += f" — {unit['digest'][:140]}"
            lines.append(line)
        blocks.append("\n".join(lines))
    return fill_prompt(
        "selector_template_transcribed.txt",
        DOCUMENT=index.get("document_type") or "report",
        PERIOD=index.get("fiscal_year_end_label") or "unknown period",
        MAX_NOTES=max_units(index),
        NOTES_LIST="\n\n".join(blocks),
        HINTS=hints,
        QUESTION=question,
    )


def _selector_prompt(index, question, scope, hints=""):
    if is_transcribed(index):
        return _transcribed_selector_prompt(index, question, scope, hints)
    if is_legacy(index):
        return _legacy_selector_prompt(index, question, hints)
    lines = []
    for section in _scope_sections(index, scope):
        for note in index["sections"][section]["notes"]:
            subs = "; ".join(note["subheadings"][:8])
            line = f"{_note_id(section, note)}: {note['title']}"
            if subs:
                line += f"  [sub-headings: {subs}]"
            lines.append(line)
    return fill_prompt(
        "selector_template.txt",
        SCOPE=scope,
        MAX_NOTES=config.MAX_NOTES,
        NOTES_LIST="\n".join(lines),
        HINTS=hints,
        QUESTION=question,
    )


def _parse_selector_reply(reply, valid_ids, limit=None, valid_passages=()):
    """(note ids, passage ids, reason). Either list may be empty, not both."""
    match = re.search(r"\{.*\}", reply, re.S)
    if not match:
        raise ValueError("no JSON object in selector reply")
    data = json.loads(match.group(0))
    ids = [str(i).strip().upper() for i in data.get("notes", [])]
    ids = [i for i in ids if i in valid_ids]
    wanted = {p.upper(): p for p in valid_passages}
    passages = [wanted[str(i).strip().upper()] for i in data.get("passages") or []
                if str(i).strip().upper() in wanted]
    if not ids and not passages:
        raise ValueError("selector returned no valid note ids")
    return ids[:limit or config.MAX_NOTES], passages[:config.MAX_PASSAGES], str(data.get("reason", "")).strip()


def keyword_passages(candidates, limit=2):
    """The keyword fallback's passages: the best candidates, when clearly ahead of the rest."""
    if not candidates:
        return []
    top = candidates[0]["score"]
    return [c["id"] for c in candidates if c.get("score", 0) >= 0.6 * top][:limit]


def keyword_select(index, question, scope):
    """Fallback: score notes by synonym and word overlap with the question."""
    q = question.lower()
    synonyms = {**SYNONYMS, **LEGACY_SYNONYMS} if is_legacy(index) else SYNONYMS
    phrases = [p for key, ps in synonyms.items() if key in q for p in ps]
    words = {w for w in re.findall(r"[a-z][a-z\-]{3,}", q) if w not in STOPWORDS}
    scored = []
    for section in _scope_sections(index, scope):
        for note in index["sections"][section]["notes"]:
            title = note["title"].lower()
            subs = " ".join(note["subheadings"]).lower()
            digest = note.get("digest", "").lower()
            score = sum(3 for p in phrases if p in title)
            score += sum(1 for p in phrases if p in subs or p in digest)
            score += sum(2 for w in words if w.rstrip("s") in title)
            if score:
                scored.append((score, _note_id(section, note)))
    if not scored:
        return []
    scored.sort(reverse=True)
    best = scored[0][0]
    return [nid for score, nid in scored if score >= best * 0.5][:max_units(index)]


def resolve_scope(index, question):
    """(scope, reason): the notes sections a question is about (consolidated by default)."""
    scope, scope_reason = detect_scope(question)
    if is_legacy(index):
        scope = LEGACY_SECTION
        scope_reason = "Old-format report: it has standalone accounts only (no consolidated accounts)."
    if is_transcribed(index):
        scope, scope_reason = entity_scope(index, question)
    if not _scope_sections(index, scope):
        available = list(index["sections"])
        if not available:
            raise ValueError("No notes sections were found in this report.")
        scope = available[0]
        scope_reason = f"Requested notes section not found; using {scope}."
    return scope, scope_reason


def _scope_names(index, scope):
    """(scope, label) as shown: several scanned companies become "entities"."""
    if isinstance(scope, list):
        return "entities", " + ".join(index["sections"][k]["label"] for k in scope)
    return scope, {"consolidated": "Consolidated", "standalone": "Standalone",
                   "both": "Consolidated + Standalone"}.get(scope, scope)


def select_notes(index, question, hints=None):
    """The "Identify notes" flow. hints: notes suggested by the financial statements
    (analyzer/linked.py), shown to Claude and used first by the keyword fallback."""
    scope, scope_reason = resolve_scope(index, question)
    choices = _by_id(index, scope)
    hints = [h for h in hints or [] if h["id"] in choices]
    explicit = _explicit_refs(question, index, scope)
    method, reason = "claude", ""
    limit = max_units(index)
    system_file = ("selector_system_transcribed.txt" if is_transcribed(index)
                   else "selector_system_legacy.txt" if is_legacy(index) else "selector_system.txt")
    try:
        reply = run_claude(
            _selector_prompt(index, question, scope, hints_text(hints, choices)),
            system_prompt(system_file),
            config.SELECTOR_TIMEOUT,
        )
        ids, _, reason = _parse_selector_reply(reply, set(choices), limit)
    except (ClaudeError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Selector call failed (%s); using keyword fallback", exc)
        method = "keywords"
        ids = keyword_select(index, question, scope)
        # the notes behind the statement lines the question is about come first
        first = [h["id"] for h in hints[:2]]
        ids = first + [i for i in ids if i not in first]
        reason = ("Matched by keywords against note titles "
                  f"(Claude selector unavailable: {str(exc)[:200]}).")

    ids = explicit + [i for i in ids if i not in explicit]
    scope, scope_label = _scope_names(index, scope)
    return {
        "scope": scope,
        "scope_label": scope_label,
        "scope_reason": scope_reason,
        "method": method,
        "reason": reason,
        "notes": prefer_specific([choices[i] for i in ids[:limit]]),
        "hints": hints,
    }


def select_for_business(index, question, passages, hints=None):
    """The "Analyze non notes" flow (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §16): the report passages
    a business question needs, and at most config.MAX_BUSINESS_NOTES notes whose figures confirm
    or contradict them. passages: the candidates ranked by analyzer/passages.py, best first."""
    scope, scope_reason = resolve_scope(index, question)
    choices = _by_id(index, scope)
    hints = [h for h in hints or [] if h["id"] in choices]
    method = "claude"
    note_lines = [f"{c['id']}: {c['title'] if c['kind'] == 'note' and isinstance(c['no'], int) else c['label']}"
                  for c in choices.values()]
    prompt = fill_prompt(
        "business_selector_template.txt",
        QUESTION=question,
        PASSAGE_LIST="\n".join(f"{p['id']} | {p['path']} | PDF {p['pages']} | \"{p['lead'][:160]}\""
                               for p in passages),
        MAX_PASSAGES=config.MAX_PASSAGES,
        MAX_NOTES=config.MAX_BUSINESS_NOTES,
        NOTES_LIST="\n".join(note_lines),
        HINTS=hints_text(hints, choices),
        EXAMPLE_ID=passages[0]["id"] if passages else "P-…",
    )
    try:
        reply = run_claude(prompt, system_prompt("business_selector_system.txt"), config.SELECTOR_TIMEOUT)
        ids, passage_ids, reason = _parse_selector_reply(reply, set(choices), config.MAX_BUSINESS_NOTES,
                                                         [p["id"] for p in passages])
        if not passage_ids:
            passage_ids = keyword_passages(passages)
    except (ClaudeError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Business selector call failed (%s); using keyword fallback", exc)
        method = "keywords"
        ids, passage_ids = [h["id"] for h in hints[:1]], keyword_passages(passages, limit=3)
        reason = ("Passages matched by keywords against the report's sections "
                  f"(Claude selector unavailable: {str(exc)[:200]}).")
    scope, scope_label = _scope_names(index, scope)
    return {
        "scope": scope,
        "scope_label": scope_label,
        "scope_reason": scope_reason,
        "method": method,
        "reason": reason,
        "notes": prefer_specific([choices[i] for i in ids[:config.MAX_BUSINESS_NOTES]]),
        "hints": hints,
        "passages": [next(p for p in passages if p["id"] == pid) for pid in passage_ids],
    }
