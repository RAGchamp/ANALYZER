"""The model documents, from the command line (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §7).

    python -m ingest.models list
    python -m ingest.models fingerprint <pdf>
    python -m ingest.models match <pdf> [--format us-10k]
    python -m ingest.models register <MODEL-DOCS\\TYPE-n-….pdf> --profile <id> [--description "…"]
    python -m ingest.models register <MODEL-DOCS\\TYPE-n-….pdf> --pending --description "…"
    python -m ingest.models refresh          # re-fingerprint every model (after the fingerprint changes)
    python -m ingest.models verify           # every model matches itself and no other
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import config
from ingest import notes_index, pipeline, profiles
from ingest.models import match as matcher
from ingest.models import registry
from ingest.models.fingerprint import FINGERPRINT_VERSION, fingerprint
from rptpkg import store


def _model_pdf(model):
    return Path(config.MODEL_DOCS_DIR) / model.file


def cmd_list(args):
    reg = registry.load()
    print(f"Registry version {reg.version}, fingerprint version {reg.fingerprint_version}, "
          f"{len(reg.models)} models ({len(reg.supported())} with extraction rules)")
    for m in reg.models:
        present = "" if _model_pdf(m).exists() else "   [PDF missing]"
        print(f"  {m.id:<45} {m.profile or 'PENDING':<22}{present}")
    return 0


def cmd_fingerprint(args):
    print(json.dumps(fingerprint(args.pdf), indent=1, ensure_ascii=False))
    return 0


def cmd_match(args):
    try:
        result = matcher.match(args.pdf, args.format)
    except matcher.NewModelDocument as exc:
        print(f"NEW MODEL DOCUMENT: {exc}")
        print(f"Gap report: {exc.gap_report}")
        return 3
    except notes_index.NeedsTranscription as exc:
        print(f"{exc}")
        return 2
    print(f"Matched {result.model.short} ({result.how}, similarity {result.score:.2f}); profile "
          f"{result.profile.id}; {result.checks['summary']}")
    return 0


def cmd_register(args):
    pdf = args.pdf
    if not registry.NAME_RE.match(pdf.stem):
        sys.exit(f"Name it TYPE-<n>-<COUNTRY>-<FORM>-<YEAR>-<Company>.pdf (got {pdf.name})")
    if not args.pending and args.profile not in profiles.PROFILES:
        sys.exit(f"Unknown profile {args.profile!r}; known: {', '.join(profiles.PROFILES)} (or --pending)")
    reg = registry.load()
    entry = registry.Model(id=pdf.stem, file=pdf.name, sha256=store.pdf_sha256(pdf),
                           profile=None if args.pending else args.profile,
                           description=args.description or "", fingerprint=fingerprint(pdf))
    old = reg.by_id(pdf.stem)
    if old:
        entry.description = entry.description or old.description
        reg.models.remove(old)
    reg.models.append(entry)
    reg.version += 1
    reg.fingerprint_version = FINGERPRINT_VERSION
    registry.save(reg)
    print(f"Registered {entry.short} ({entry.profile or 'pending'}); registry version {reg.version}")
    return 0


def cmd_refresh(args):
    reg = registry.load()
    for m in reg.models:
        pdf = _model_pdf(m)
        if not pdf.exists():
            print(f"  missing, kept as is: {m.file}")
            continue
        m.sha256 = store.pdf_sha256(pdf)
        m.fingerprint = fingerprint(pdf)
        print(f"  fingerprinted {m.id}")
    reg.version += 1
    reg.fingerprint_version = FINGERPRINT_VERSION
    registry.save(reg)
    print(f"Registry version {reg.version}")
    return 0


def _verify_narrative(model, pdf):
    """The pages outside notes and statements (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md):
    a profile that reads them must find the report's sections, and every page must pass the coverage
    check (a page kept as flat text is reported; the analysis still works)."""
    if not profiles.get(model.profile).switches.narrative_sections:
        return []
    try:
        package = pipeline.open_report(pdf)
    except (matcher.NewModelDocument, notes_index.NeedsTranscription):
        return []
    facts = (package.meta.get("quality") or {}).get("narrative")
    if not facts:
        if package.meta.get("format") == "transcribed":
            print(f"  {model.id}: narrative not read (an imported scanned package; re-transcribe to rebuild)")
            return []
        return [f"{model.id}: its profile reads narrative pages but the package has none (rebuild it)"]
    fallback = facts.get("fallback_pages") or []
    print(f"  {model.id}: narrative {facts['sections']} sections, {facts['pages']} pages, "
          f"coverage {facts['pages'] - len(fallback)}/{facts['pages']}")
    problems = []
    if facts["pages"] >= 10 and facts["sections"] == 0:
        problems.append(f"{model.id}: no report sections found on its {facts['pages']} narrative pages")
    if fallback:
        problems.append(f"{model.id}: {len(fallback)} narrative page(s) kept as flat text: {fallback[:10]}")
    return problems


def cmd_verify(args):
    """Every model document: present, unchanged, matches itself with its
    checks passed, and - with itself left out - matches no model of another profile."""
    reg = registry.load()
    problems = []
    if reg.fingerprint_version != FINGERPRINT_VERSION:
        problems.append(f"fingerprints are version {reg.fingerprint_version}, the code {FINGERPRINT_VERSION}: "
                        "run `python -m ingest.models refresh`")
    for m in reg.models:
        pdf = _model_pdf(m)
        if not pdf.exists():
            problems.append(f"{m.id}: PDF missing in {config.MODEL_DOCS_DIR}")
            continue
        if store.pdf_sha256(pdf) != m.sha256:
            problems.append(f"{m.id}: the PDF changed since it was registered")
            continue
        try:
            own = matcher.match(pdf, reg=reg)
            if not own.checks["passed"]:
                problems.append(f"{m.id}: fails its own checks: {own.checks['summary']}")
        except matcher.NewModelDocument:
            if not m.pending:
                problems.append(f"{m.id}: does not match itself")
        except notes_index.NeedsTranscription:
            print(f"  {m.id}: not transcribed here; self-match not checked")
        others = registry.Registry(reg.version, reg.fingerprint_version, [x for x in reg.models if x.id != m.id])
        try:
            other = matcher.match(pdf, reg=others)
            if other.profile.id != m.profile:
                problems.append(f"{m.id}: also matches {other.model.id} ({other.profile.id})")
        except (matcher.NewModelDocument, notes_index.NeedsTranscription):
            pass
        if not m.pending:
            problems += _verify_narrative(m, pdf)
        print(f"  checked {m.id}")
    for p in problems:
        print(f"PROBLEM: {p}")
    print("verify: OK" if not problems else f"verify: {len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ingest.models", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("fingerprint"); p.add_argument("pdf", type=Path)
    p = sub.add_parser("match"); p.add_argument("pdf", type=Path); p.add_argument("--format")
    p = sub.add_parser("register"); p.add_argument("pdf", type=Path)
    p.add_argument("--profile"); p.add_argument("--pending", action="store_true"); p.add_argument("--description")
    sub.add_parser("refresh")
    sub.add_parser("verify")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return {"list": cmd_list, "fingerprint": cmd_fingerprint, "match": cmd_match, "register": cmd_register,
            "refresh": cmd_refresh, "verify": cmd_verify}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
