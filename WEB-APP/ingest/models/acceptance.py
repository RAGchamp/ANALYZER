"""Acceptance checks: did a trial extraction with a profile work?
(INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §4.3)

The same measures as the quality report, used as gates: enough notes numbered
from about 1, enough of the primary statements, figures that read as numbers,
a balance sheet that balances, and the format's own must-have wording.
"""

import re

from ingest import figures, links, profiles, tables

STATEMENT_NAMES = {"profit_loss": "P&L", "balance_sheet": "BS", "cash_flow": "CF", "equity": "Equity"}


def _check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _main_section(index):
    """The section the checks look at: the one with the most notes."""
    sections = list(index["sections"].values())
    return max(sections, key=lambda s: (len(s.get("notes", [])), len(s.get("statements", []))), default=None)


def evaluate(profile, index, page_texts):
    """{"passed": bool, "checks": [{"name", "ok", "detail"}], "summary": str}."""
    rules = profile.acceptance
    checks = []
    section = _main_section(index)
    notes = section.get("notes", []) if section else []
    statements = section.get("statements", []) if section else []

    numbered = [n["no"] for n in notes if isinstance(n.get("no"), int)]
    first = min(numbered) if numbered else None
    checks.append(_check("notes", len(notes) >= rules.min_notes,
                         f"{len(notes)} notes found (need {rules.min_notes})"))
    if numbered and profile.indexer == "notes":
        checks.append(_check("note numbering", first <= rules.max_first_note,
                             f"notes numbered from {first}"))

    types = sorted({s["type"] for s in statements})
    checks.append(_check("statements", len(types) >= rules.min_statement_types,
                         f"{len(types)} of 4 statements found"
                         + (f" ({', '.join(STATEMENT_NAMES.get(t, t) for t in types)})" if types else "")
                         + f" (need {rules.min_statement_types})"))

    if statements:
        with profiles.using(profile):
            views = tables.report_views(index)
        lines, values, _ = figures.all_lines(index, views)
        readable = [v for v in values if v["value"] is not None]
        share = len(readable) / len(values) if values else 0.0
        checks.append(_check("figures readable", values and share >= rules.min_readable_share,
                             f"{len(readable)} of {len(values)} statement figures read as numbers"))
        if rules.balance_must_hold:
            balance = [c for c in links.build_checks(index, views, lines, values)[0] if c["kind"] == "balance"]
            failed = [c for c in balance if c["status"] != "ok"]
            checks.append(_check("balance sheet", not failed,
                                 f"{len(balance) - len(failed)} of {len(balance)} balance checks ok"
                                 if balance else "no totals to compare"))

    text = "\n".join(page_texts)
    for what, pattern in rules.must_contain:
        found = re.search(pattern, text)
        checks.append(_check("wording", found, f"mentions {what}" if found else f"doesn't mention {what}"))

    passed = all(c["ok"] for c in checks)
    failed = [c["detail"] for c in checks if not c["ok"]]
    return {"passed": passed, "checks": checks,
            "summary": "checks passed" if passed else "; ".join(failed)}
