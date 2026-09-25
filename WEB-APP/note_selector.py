"""STEP 3 - decide which notes answer the user's question.

1. Scope (rule-based): Consolidated by default; Standalone only when the
   question explicitly asks for it; both when it asks to compare them.
2. A small Claude call that sees only the note titles and sub-headings of
   that scope (never page content) and returns JSON note ids.
3. Keyword/synonym matching against titles if the Claude call fails.
Notes named explicitly in the question ("Note 41") are always included.
"""

import json
import logging
import re

import config
from claude_client import ClaudeError, fill_prompt, run_claude, system_prompt

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

SECTION_PREFIX = {"consolidated": "C", "standalone": "S"}
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


def _scope_sections(index, scope):
    wanted = ["consolidated", "standalone"] if scope == "both" else [scope]
    return [s for s in wanted if s in index["sections"]]


def _note_id(section, number):
    return f"{SECTION_PREFIX[section]}{number}"


def _note_choice(index, section, note):
    return {
        "id": _note_id(section, note["no"]),
        "section": section,
        "section_label": config.SECTION_LABELS[section],
        "no": note["no"],
        "title": note["title"],
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
    found = []
    for match in NOTE_REF_RE.finditer(question):
        for section in _scope_sections(index, scope):
            note_id = _note_id(section, int(match.group(1)))
            if note_id in choices and note_id not in found:
                found.append(note_id)
    return found


def _selector_prompt(index, question, scope):
    lines = []
    for section in _scope_sections(index, scope):
        for note in index["sections"][section]["notes"]:
            subs = "; ".join(note["subheadings"][:8])
            line = f"{_note_id(section, note['no'])}: {note['title']}"
            if subs:
                line += f"  [sub-headings: {subs}]"
            lines.append(line)
    return fill_prompt(
        "selector_template.txt",
        SCOPE=scope,
        MAX_NOTES=config.MAX_NOTES,
        NOTES_LIST="\n".join(lines),
        QUESTION=question,
    )


def _parse_selector_reply(reply, valid_ids):
    match = re.search(r"\{.*\}", reply, re.S)
    if not match:
        raise ValueError("no JSON object in selector reply")
    data = json.loads(match.group(0))
    ids = [str(i).strip().upper() for i in data.get("notes", [])]
    ids = [i for i in ids if i in valid_ids]
    if not ids:
        raise ValueError("selector returned no valid note ids")
    return ids[:config.MAX_NOTES], str(data.get("reason", "")).strip()


def keyword_select(index, question, scope):
    """Fallback: score notes by synonym and word overlap with the question."""
    q = question.lower()
    phrases = [p for key, ps in SYNONYMS.items() if key in q for p in ps]
    words = {w for w in re.findall(r"[a-z][a-z\-]{3,}", q) if w not in STOPWORDS}
    scored = []
    for section in _scope_sections(index, scope):
        for note in index["sections"][section]["notes"]:
            title = note["title"].lower()
            subs = " ".join(note["subheadings"]).lower()
            score = sum(3 for p in phrases if p in title)
            score += sum(1 for p in phrases if p in subs)
            score += sum(2 for w in words if w.rstrip("s") in title)
            if score:
                scored.append((score, _note_id(section, note["no"])))
    if not scored:
        return []
    scored.sort(reverse=True)
    best = scored[0][0]
    return [nid for score, nid in scored if score >= best * 0.5][:config.MAX_NOTES]


def select_notes(index, question):
    scope, scope_reason = detect_scope(question)
    if not _scope_sections(index, scope):
        available = list(index["sections"])
        if not available:
            raise ValueError("No notes sections were found in this report.")
        scope = available[0]
        scope_reason = f"Requested notes section not found; using {scope}."

    choices = _by_id(index, scope)
    explicit = _explicit_refs(question, index, scope)
    method, reason = "claude", ""
    try:
        reply = run_claude(
            _selector_prompt(index, question, scope),
            system_prompt("selector_system.txt"),
            config.SELECTOR_TIMEOUT,
        )
        ids, reason = _parse_selector_reply(reply, set(choices))
    except (ClaudeError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Selector call failed (%s); using keyword fallback", exc)
        method = "keywords"
        ids = keyword_select(index, question, scope)
        reason = ("Matched by keywords against note titles "
                  f"(Claude selector unavailable: {str(exc)[:200]}).")

    ids = explicit + [i for i in ids if i not in explicit]
    return {
        "scope": scope,
        "scope_reason": scope_reason,
        "method": method,
        "reason": reason,
        "notes": [choices[i] for i in ids[:config.MAX_NOTES]],
    }
