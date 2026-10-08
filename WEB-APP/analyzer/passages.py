"""Finding the report passages a question needs (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §4).

A report has hundreds of passages (its MD&A, Board's report, risk factors …
cut into pieces at the ingester), so Python ranks them before Claude sees any:

  - BM25 over each passage's heading path (counted three times) and its text;
  - the question's words, plus the words that mean the same (passage_terms.GROUPS:
    "CV" also searches "commercial vehicle");
  - a small boost for the section kinds the question points at ("outlook",
    "segment" -> MD&A, letters, business; "risk" -> risk factors);
  - at most MAX_PER_SECTION candidates from one section, so one long section
    does not crowd out the rest.

The top candidates go to the selector (analyzer/note_selector.py), which picks
the passages together with the notes in its one Claude call. Pure Python and
deterministic; the index is built once per package and kept in memory.
"""

import math
import re
import threading
from collections import Counter

from analyzer.passage_terms import GROUPS, KIND_BOOSTS, SECTION_NAMES

K1, B = 1.5, 0.75
PATH_WEIGHT = 3
SYNONYM_WEIGHT = 0.6
KIND_BOOST = 1.25
NAMED_SECTION_BOOST = 1.6
MAX_PER_SECTION = 4
CANDIDATES = 30

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "did", "do", "does", "for", "from", "has",
    "have", "how", "in", "into", "is", "it", "its", "of", "on", "or", "our", "over", "the", "their", "this",
    "that", "these", "those", "to", "was", "were", "what", "which", "who", "will", "with", "we", "you", "your",
    "about", "any", "all", "also", "company", "company's", "companys", "year", "years", "tell", "me", "please",
    "explain", "describe", "analyze", "analyse", "comment", "give", "show", "say", "says", "said", "report",
    "annual", "there", "they", "them", "than", "then", "during", "fy", "s",
}

_cache = {}
_cache_lock = threading.Lock()


def _stem(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text):
    text = (text or "").lower().replace("’", "'").replace("'s", "")
    return [_stem(w) for w in TOKEN_RE.findall(text) if w not in STOPWORDS]


class _Index:
    def __init__(self, package):
        self.meta = {p["id"]: p for p in package.passages()}
        self.docs, self.lengths, self.texts = {}, {}, {}
        df = Counter()
        for pid, (path, text) in package.passage_texts().items():
            self.texts[pid] = text
            counts = Counter(tokens(text))
            for t in tokens(path):
                counts[t] += PATH_WEIGHT
            self.docs[pid] = counts
            self.lengths[pid] = sum(counts.values())
            df.update(counts.keys())
        n = max(len(self.docs), 1)
        self.avgdl = sum(self.lengths.values()) / n if self.docs else 1
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, terms):
        out = {}
        for pid, counts in self.docs.items():
            dl = self.lengths[pid] or 1
            s = 0.0
            for term, weight in terms.items():
                tf = counts.get(term)
                if tf:
                    s += weight * self.idf.get(term, 0) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * dl / self.avgdl))
            if s:
                out[pid] = s
        return out


def _index(package):
    key = (str(package.path), package.meta.get("built_at"), package.meta.get("ingested_at"))
    with _cache_lock:
        if key not in _cache:
            if len(_cache) >= 4:
                _cache.pop(next(iter(_cache)))
            _cache[key] = _Index(package)
        return _cache[key]


def query_terms(question):
    """{term: weight}: the question's own words, and at a lower weight the words that mean the same."""
    q = " " + re.sub(r"[^a-z0-9&' -]+", " ", question.lower().replace("’", "'")) + " "
    terms = Counter({t: 1.0 for t in tokens(question)})
    for group in GROUPS:
        if any(re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", q) for p in group):
            for phrase in group:
                for t in tokens(phrase):
                    terms[t] = max(terms[t], SYNONYM_WEIGHT)
    return dict(terms)


def named_kinds(question):
    """Section kinds the question names ("what does the MD&A say", "risk factors")."""
    q = question.lower().replace("’", "'")
    return {kind for pattern, kind in SECTION_NAMES if re.search(pattern, q)}


def _boosted_kinds(question):
    words = set(re.findall(r"[a-z]+", question.lower()))
    return {kind for triggers, kinds in KIND_BOOSTS if words & triggers for kind in kinds}


SNIPPET_CHARS = 260


def snippet(text, terms):
    """The sentence of a passage that holds most of the question's words (the question's own words
    before their synonyms), and the words as printed there, so step 3 can show why it was found.
    ("", []) when none is there."""
    best, best_score = None, 0
    for m in re.finditer(r"[^\n]+", text or ""):
        line = m.group(0)
        if line.startswith("--- PDF page"):
            continue
        # a sentence ends at ". " but not in "69.99%" or "Ltd., a"
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", line):
            found = {_stem(w.lower()) for w in TOKEN_RE.findall(sentence.lower().replace("'s", ""))} & terms.keys()
            score = sum(terms[t] for t in found)
            if score > best_score:
                best, best_score = sentence.strip(), score
    if not best:
        return "", []
    words = sorted({w for w in re.findall(r"[A-Za-z0-9]+", best)
                    if w.lower() not in STOPWORDS and _stem(w.lower()) in terms}, key=str.lower)
    if len(best) > SNIPPET_CHARS:
        # keep the part around the first matched word
        first = min((best.lower().find(w.lower()) for w in words), default=0)
        start = max(0, first - SNIPPET_CHARS // 3)
        best = ("…" if start else "") + best[start:start + SNIPPET_CHARS].strip() + "…"
    return best, words


def _label(p):
    pages = f"{p['start_page']}" if p["start_page"] == p["end_page"] else f"{p['start_page']}–{p['end_page']}"
    return {"id": p["id"], "section_id": p["section_id"], "path": p["path"], "kind": p["kind"],
            "start_page": p["start_page"], "end_page": p["end_page"], "pages": pages,
            "chars": p["chars"], "lead": p["lead"]}


def candidates(package, question, limit=CANDIDATES):
    """The passages most likely to help answer the question, best first ([] if the report has none)."""
    index = _index(package)
    if not index.docs:
        return []
    terms = query_terms(question)
    scores = index.score(terms)
    boosted, named = _boosted_kinds(question), named_kinds(question)
    for pid in list(scores):
        kind = index.meta[pid]["kind"]
        if kind in named:
            scores[pid] *= NAMED_SECTION_BOOST
        elif kind in boosted:
            scores[pid] *= KIND_BOOST
    out, per_section = [], Counter()
    for pid, score in sorted(scores.items(), key=lambda kv: (-kv[1], index.meta[kv[0]]["seq"])):
        meta = index.meta[pid]
        if per_section[meta["section_id"]] >= MAX_PER_SECTION:
            continue
        per_section[meta["section_id"]] += 1
        text, words = snippet(index.texts.get(pid), terms)
        out.append({**_label(meta), "score": round(score, 2), "snippet": text, "matched": words})
        if len(out) >= limit:
            break
    return out


def by_ids(package, ids):
    """Labels of these passages, in the order given (unknown ids are skipped)."""
    meta = _index(package).meta
    return [_label(meta[i]) for i in ids if i in meta]


def explicit(package, question, have=()):
    """The best passages of the sections a question names ("what does the Board's report say about exports?"),
    at most two, not already chosen."""
    named = named_kinds(question)
    if not named:
        return []
    best = [c for c in candidates(package, question, limit=CANDIDATES) if c["kind"] in named and c["id"] not in have]
    return best[:2]


def keyword_pick(found, limit=2):
    """The keyword fallback's passages: the best ones, when clearly ahead of the rest."""
    if not found:
        return []
    top = found[0]["score"]
    return [c["id"] for c in found if c["score"] >= 0.6 * top][:limit]
