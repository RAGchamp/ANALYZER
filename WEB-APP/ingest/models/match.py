"""Match a report to a model document, or say it is new
(INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §3-4).

1. The PDF *is* a model document (same SHA-256): that model's profile.
2. Otherwise its fingerprint is compared with every model's: hard constraints
   first (scanned vs text, typewriter vs typeset, different cover forms), then
   a weighted similarity. The best SHORTLIST models above FLOOR are kept.
3. Each shortlisted profile gets a trial extraction, judged by its acceptance
   checks. A profile that passes wins - unless two different profiles pass
   with close scores: when in doubt, the report is a new model document (Q8).

Nothing matches: NewModelDocument, with the nearest models, what their rules
found, and a gap report for the developer. Such a report can't be analyzed
until its model is added (Q1).
"""

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import config
from ingest import notes_index, profiles
from ingest.models import acceptance, registry
from ingest.models.fingerprint import FINGERPRINT_VERSION, fingerprint
from ingest.pdf_utils import PdfError, open_pdf
from rptpkg import store

log = logging.getLogger(__name__)

# Bump when the matching or the acceptance checks change: remembered verdicts
# are then recomputed (packages are not rebuilt; that is INDEX_VERSION).
MATCHER_VERSION = 2
SHORTLIST = 3
FLOOR = 0.55              # below this similarity a model is not worth a trial extraction
AMBIGUITY_GAP = 0.05      # two different profiles pass within this: doubtful, so "new" (Q8)


class NewModelDocument(PdfError):
    """No model document matches this report (or it is a model whose rules aren't written yet)."""

    def __init__(self, pdf_path, message, nearest, gap_report=None, pending_model=None):
        self.pdf_path = Path(pdf_path)
        self.nearest = nearest                  # [{"model", "short", "score", "profile", "trial"}]
        self.gap_report = gap_report
        self.pending_model = pending_model
        super().__init__(message)

    def to_json(self):
        return {"message": str(self), "nearest": self.nearest, "pending_model": self.pending_model,
                "gap_report": self.gap_report and Path(self.gap_report).name}


@dataclass
class MatchResult:
    model: registry.Model
    profile: profiles.Profile
    score: float
    how: str                                   # "model document" | "fingerprint"
    checks: dict                               # acceptance.evaluate() result
    index: dict                                # the trial extraction, reused for the package

    def meta(self, registry_version):
        return {"model": self.model.id, "model_short": self.model.short, "profile": self.profile.id,
                "profile_version": self.profile.version, "model_score": round(self.score, 2),
                "model_match": self.how, "model_checks": self.checks, "registry_version": registry_version}


# ---------------------------------------------------------------- fingerprints

def _cache_dir(name):
    folder = Path(config.CACHE_DIR) / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cached_fingerprint(pdf_path, sha=None):
    sha = sha or store.pdf_sha256(pdf_path)
    path = _cache_dir("fingerprints") / f"{sha[:16]}-v{FINGERPRINT_VERSION}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    fp = fingerprint(pdf_path)
    path.write_text(json.dumps(fp), encoding="utf-8")
    return fp


# ---------------------------------------------------------------- similarity

def _close(a, b, scale=1.0):
    return max(0.0, 1.0 - abs((a or 0) - (b or 0)) / scale)


def _jaccard(a, b):
    a, b = set(a or ()), set(b or ())
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def _cosine(a, b):
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return 1.0 if na == nb == 0 else (dot / (na * nb) if na and nb else 0.0)


def conflict(a, b):
    """Why two fingerprints can't be the same format (a hard constraint), or None."""
    if (a["physical"]["text_share"] < 0.1) != (b["physical"]["text_share"] < 0.1):
        return "one is scanned, the other has text"
    if (a["typography"]["mono"] >= 0.5) != (b["typography"]["mono"] >= 0.5):
        return "one is in a typewriter font, the other typeset"
    fa, fb = a["document"]["form"], b["document"]["form"]
    if fa and fb and fa != fb:
        return f"cover says Form {fa}, the model Form {fb}"
    return None


def similarity(a, b):
    """0..1: how alike two fingerprints are (after the hard constraints)."""
    pa, pb, ta, tb = a["physical"], b["physical"], a["typography"], b["typography"]
    da, db, sa, sb = a["document"], b["document"], a["structure"], b["structure"]
    decade_a, decade_b = a["era"]["decade"], b["era"]["decade"]
    parts = [
        # (weight, similarity)
        (0.5, float(pa["producer"] == pb["producer"])),
        (0.5, float(pa["page_shape"] == pb["page_shape"])),
        (0.25, float(pa["pages_band"] == pb["pages_band"])),
        (1.0, _close(ta["serif"], tb["serif"])),
        (1.0, _close(ta["bold"], tb["bold"], 0.3)),
        (1.5, _close(ta["big_title_pages"], tb["big_title_pages"])),
        (2.0, float(da["form"] == db["form"])),
        (2.0, _jaccard(da["frameworks"], db["frameworks"])),
        (0.5, _jaccard(da["currencies"], db["currencies"])),
        (1.0, _jaccard(da["units"], db["units"])),
        (1.0, 1.0 if decade_a == decade_b else _close(decade_a, decade_b, 40) if decade_a and decade_b else 0.5),
        (2.5, _close(min(sa["notes_header_pages"], 50), min(sb["notes_header_pages"], 50), 50)),
        (2.5, _cosine(sa["note_headings"], sb["note_headings"])),
        (1.0, float((sa["numbered_statement_titles"] > 0) == (sb["numbered_statement_titles"] > 0))),
        (1.0, float((sa["schedules"] >= 10) == (sb["schedules"] >= 10))),
        (1.0, float(sa["item_8"] == sb["item_8"])),
        (1.0, float(sa["item_18"] == sb["item_18"])),
    ]
    return sum(w * s for w, s in parts) / sum(w for w, _ in parts)


def rank(fp, reg, format_choice=None):
    """[(model, score, conflict)] best first. A forced format keeps only its models."""
    out = []
    for model in reg.models:
        if format_choice not in (None, "auto") and (model.pending or profiles.get(model.profile).format != format_choice):
            continue
        why = conflict(fp, model.fingerprint)
        out.append((model, 0.0 if why else similarity(fp, model.fingerprint), why))
    out.sort(key=lambda item: -item[1])
    return out


# ---------------------------------------------------------------- trial extraction

def _page_texts(pdf_path):
    doc = open_pdf(pdf_path)
    try:
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def trial(pdf_path, profile, format_choice=None):
    """(index, checks) of the report read with this profile. NeedsTranscription
    propagates: a scanned report must be transcribed before it can be judged."""
    forced = format_choice if format_choice not in (None, "auto") else None
    index = notes_index.build_index(Path(pdf_path), forced, profile=profile)
    return index, acceptance.evaluate(profile, index, _page_texts(pdf_path))


def _nearest_json(model, score, why, trial_result=None):
    return {"model": model.id, "short": model.short, "score": round(score, 2),
            "profile": model.profile, "pending": model.pending, "conflict": why,
            "trial": trial_result and {"passed": trial_result["passed"], "summary": trial_result["summary"],
                                       "checks": trial_result["checks"]}}


# ---------------------------------------------------------------- the decision

def match(pdf_path, format_choice=None, reg=None):
    """The model and profile to read this report with; NewModelDocument if none.
    reg: another registry (`verify` leaves a model out to check it matches nothing else)."""
    from ingest.models import gap_report   # the report needs match's helpers

    pdf_path = Path(pdf_path)
    remember = reg is None
    reg = reg or registry.load()
    sha = store.pdf_sha256(pdf_path)
    forced = format_choice not in (None, "auto")

    remembered = known_verdict(pdf_path, format_choice) if remember else None
    if remembered is not None:
        raise remembered

    own = reg.by_sha(sha)
    if own and own.pending:
        # For the developer: how do the other models' rules do on it?
        others = registry.Registry(reg.version, reg.fingerprint_version, [m for m in reg.models if m.id != own.id])
        nearest, hint = [_nearest_json(own, 1.0, None)], ""
        try:
            alt = match(pdf_path, format_choice, reg=others)
            nearest.append(_nearest_json(alt.model, alt.score, None, alt.checks))
            hint = (f" The rules of {alt.model.short} ({alt.profile.id}) pass its checks: it may be "
                    "registered with that profile.")
        except NewModelDocument as other:
            nearest += [n for n in other.nearest if n["model"] != own.id]
        except notes_index.NeedsTranscription:
            pass
        path = gap_report.write(pdf_path, None, nearest, pending=own)
        error = NewModelDocument(
            pdf_path, f"This is model document {own.short}, whose extraction rules have not been written yet. "
                      f"It can't be analyzed until they are added.{hint}", nearest, path, pending_model=own.id)
        if remember:
            _remember(sha, reg, format_choice, error)
        raise error
    if own and (not forced or profiles.get(own.profile).format == format_choice):
        profile = profiles.get(own.profile)
        index, checks = trial(pdf_path, profile, format_choice)
        if not checks["passed"]:
            log.warning("Model document %s fails its own checks: %s", own.id, checks["summary"])
        return MatchResult(own, profile, 1.0, "model document", checks, index)

    fp = cached_fingerprint(pdf_path, sha)
    ranked = rank(fp, reg, format_choice)
    # Trial extractions for the SHORTLIST best models that have rules (pending
    # models and conflicts don't use up a place); the message shows the
    # overall nearest ones plus every model tried.
    nearest, passed, tried = [], [], set()
    for model, score, why in ranked:
        if len(tried) >= SHORTLIST and len(nearest) >= SHORTLIST:
            break
        result = None
        if (len(tried) < SHORTLIST and not model.pending and not why and score >= FLOOR
                and model.profile not in tried):
            tried.add(model.profile)
            profile = profiles.get(model.profile)
            try:
                index, result = trial(pdf_path, profile, format_choice)
            except notes_index.NeedsTranscription:
                raise
            except PdfError as exc:
                result = {"passed": False, "summary": str(exc), "checks": []}
            if result["passed"]:
                passed.append(MatchResult(model, profile, score, "fingerprint", result, index))
        if len(nearest) < SHORTLIST or result is not None:
            nearest.append(_nearest_json(model, score, why, result))

    if passed:
        best = passed[0]
        rivals = [p for p in passed[1:] if p.profile.id != best.profile.id and best.score - p.score < AMBIGUITY_GAP]
        if not rivals:
            log.info("Matched %s to %s (%s, %.2f)", pdf_path.name, best.model.id, best.profile.id, best.score)
            return best
        message = (f"This report could be {best.model.short} or {rivals[0].model.short}: both sets of rules work "
                   "and the formats look equally close. The extraction logic needs a decision for this format.")
    else:
        head = nearest[0] if nearest else None
        message = (f"This report doesn't match any of the {len(reg.models)} model documents. "
                   + (f"Nearest: {head['short']} (similarity {head['score']:.2f})"
                      + (f"; with its rules: {head['trial']['summary']}" if head.get("trial") else
                         "; its extraction rules are not written yet" if head.get("pending") else
                         f"; {head['conflict']}" if head.get("conflict") else "")
                      + ". " if head else "")
                   + "The document data extraction logic needs an update for this format.")
    path = gap_report.write(pdf_path, fp, nearest)
    error = NewModelDocument(pdf_path, message, nearest, path)
    if remember:
        _remember(sha, reg, format_choice, error)
    raise error


# ---------------------------------------------------------------- remembered verdicts
# A "new model document" verdict costs up to SHORTLIST trial extractions; it is
# kept until the registry, the ingester or the format choice changes.

def _verdict_path(sha):
    return _cache_dir("matches") / f"{sha[:16]}.json"


def _verdict_key(reg, format_choice):
    return {"registry_version": reg.version, "ingester_version": notes_index.INDEX_VERSION,
            "matcher_version": MATCHER_VERSION,
            "format_choice": format_choice if format_choice not in (None, "auto") else None}


def _remember(sha, reg, format_choice, error):
    data = {**_verdict_key(reg, format_choice), "error": error.to_json()}
    _verdict_path(sha).write_text(json.dumps(data), encoding="utf-8")


def known_verdict(pdf_path, format_choice=None):
    """The remembered "new model document" verdict for this PDF, or None."""
    sha = store.pdf_sha256(pdf_path)
    path = _verdict_path(sha)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = {k: data.get(k) for k in ("registry_version", "ingester_version", "matcher_version", "format_choice")}
    if stored != _verdict_key(registry.load(), format_choice):
        return None
    error = data["error"]
    gap = Path(config.CACHE_DIR) / "gap-reports" / error["gap_report"] if error.get("gap_report") else None
    return NewModelDocument(pdf_path, error["message"], error["nearest"], gap, error.get("pending_model"))
