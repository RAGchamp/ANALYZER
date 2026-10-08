"""The quality report of an ingested report (INFO/SPLIT-FUNCTIONALITY-PLAN.md §6.4).

Built at ingestion and stored in the package (meta "quality"), so step 1 and
the CLI can show what was found - and what wasn't - before anyone asks a
question:

    Chubb-10-K-2025.pdf · US 10-K · 460 pages · ingested in 14s
    Consolidated: 22 notes (PDF 111–212); statements: P&L p.108 · BS p.107 · CF p.110 · Equity p.109
    Links: 118 of 164 statement lines linked to a note (72%)
    Checks: subtotals 38/38 ok · balance sheet balances
"""

from rptpkg.model import statement_unit_ids

SHORT_NAMES = {"profit_loss": "P&L", "balance_sheet": "BS", "cash_flow": "CF", "equity": "Equity"}
MODERN_TYPES = ("profit_loss", "balance_sheet", "cash_flow", "equity")
KIND_WORDS = {"note": "note", "schedule": "schedule", "narrative": "report section"}


def _pages(statement):
    pages = [p["pdf_page"] for p in statement.get("pages", [])]
    if not pages:
        return "?"
    return f"{pages[0]}" if len(pages) == 1 else f"{pages[0]}–{pages[-1]}"


def build(index, content, seconds):
    """The quality facts, as stored in the package."""
    fmt = index.get("format", "modern")
    sections, warnings = [], []
    for key, section in index["sections"].items():
        statements = [{"id": uid, "type": st["type"], "title": st["title"], "pages": _pages(st),
                       "rotated": any(p.get("rotation") for p in st.get("pages", []))}
                      for uid, st in zip(statement_unit_ids(key, section), section.get("statements", []))]
        kinds = {}
        for unit in section.get("notes", []):
            kind = unit.get("kind", "note")
            kinds[kind] = kinds.get(kind, 0) + 1
        sections.append({"id": key, "label": section.get("label") or key,
                         "notes": len(section.get("notes", [])), "units": kinds,
                         "first_page": section.get("first_page"), "last_page": section.get("last_page"),
                         "statements": statements})
        found = {s["type"] for s in statements}
        if fmt in ("modern", "us-10k"):
            missing = [SHORT_NAMES[t] for t in MODERN_TYPES if t not in found]
            if missing:
                warnings.append(f"{section.get('label') or key}: statements not found: {', '.join(missing)}")
        if not section.get("notes"):
            warnings.append(f"{section.get('label') or key}: no notes found")
    if not index["sections"]:
        warnings.append("No notes sections were found: check NOTES_HEADERS in config.py, "
                        "or use 'Or analyze specific PDF pages'")
    if index.get("statements_missing") and fmt == "legacy":
        warnings.append("Not in this report (not required at the time): "
                        + ", ".join(index["statements_missing"]))
    values = content.get("values", [])
    periods = sorted({v["period_label"] for v in values if v["period_label"]}, reverse=True)
    unreadable = [v for v in values if v["value"] is None]
    if unreadable:
        warnings.append(f"{len(unreadable)} statement figure(s) could not be read as numbers, e.g. "
                        + ", ".join(repr(v["raw"]) for v in unreadable[:3]))
    return {
        "pdf_name": index.get("pdf_name"),
        "units": content.get("units"),
        "periods": periods,
        "lines": len(content.get("lines", [])),
        "figures": len(values),
        "format": fmt,
        "page_count": index.get("page_count"),
        "ingest_seconds": round(seconds, 1),
        "sections": sections,
        "warnings": warnings,
        "model": content.get("model"),
        "links": content.get("link_summary"),
        "checks": content.get("check_summary"),
        "narrative": _narrative_facts(content.get("narrative")),
    }


def _narrative_facts(narrative):
    """What the narrative reader found (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §8)."""
    if not narrative:
        return None
    leaves = [s for s in narrative["sections"]
              if s["source"] != "fallback" and not any(c["parent_id"] == s["id"] for c in narrative["sections"])]
    return {**narrative["stats"], "sections": len(leaves),
            "confirmed": sum(1 for s in leaves if s["confirmed_by"]), "warnings": narrative["warnings"]}


def _narrative_lines(n):
    """Kept apart from the "Warning:" lines: those are part of the Analyze screen's summary."""
    sources = ", ".join(f"{src.replace('_', ' ')}" for src in n["sources"] if src != "fallback")
    kinds = n["layout_kinds"]
    blocks = n["block_kinds"]
    line = (f"Narrative: {n['sections']} report sections (from {sources or 'no source'}; "
            f"{n['confirmed']} confirmed by another source); {n['pages']} pages → {n['blocks']} blocks")
    detail = " · ".join(f"{label} {count}" for label, count in (
        ("prose pages", kinds.get("prose")), ("designed", kinds.get("designed")), ("table pages", kinds.get("table")),
        ("image pages", kinds.get("image")), ("tables", blocks.get("table")), ("KPI figures", blocks.get("metric")),
        ("diagrams/charts", (blocks.get("diagram") or 0) + (blocks.get("chart") or 0))) if count)
    fallback = n.get("fallback_pages") or []
    check = (f"coverage check: {len(fallback)} page(s) kept as flat text ({', '.join(map(str, fallback[:8]))})"
             if fallback else f"coverage check: {n['pages']}/{n['pages']} pages OK")
    out = [line, f"Narrative pages: {detail}; {check}"]
    p = n.get("passages")
    if p and p.get("passages"):
        out.append(f"Narrative passages: {p['passages']} (median {p['median_chars'] / 1000:.1f} K characters, "
                   f"largest {p['largest_chars'] / 1000:.1f} K; {p['split']} split); "
                   f"{p['figures']} money figures")
    out += [f"Narrative warning: {w}" for w in n.get("warnings", [])]
    return out


def report_lines(quality, format_label=""):
    head = f"{quality['pdf_name']}  ·  {format_label or quality['format']}  ·  {quality['page_count']} pages"
    if quality.get("ingest_seconds") is not None:
        head += f"  ·  ingested in {quality['ingest_seconds']:.0f}s"
    lines = [head]
    if quality.get("model"):
        lines.append(f"Model: {quality['model']}")
    for s in quality["sections"]:
        where = f" (PDF {s['first_page']}–{s['last_page']})" if s.get("first_page") else ""
        units = s.get("units") or {"note": s["notes"]}
        counts = ", ".join(f"{n} {KIND_WORDS.get(k, k)}{'' if n == 1 else 's'}" for k, n in units.items())
        line = f"{s['label']}: {counts or '0 notes'}{where}"
        if s["statements"]:
            line += "; statements: " + " · ".join(
                f"{SHORT_NAMES.get(st['type'], st['title'])} p.{st['pages']}{' (rotated)' if st['rotated'] else ''}"
                for st in s["statements"])
        lines.append(line)
    if quality.get("lines"):
        figures = f"Figures: {quality['lines']} statement lines, {quality['figures']} figures"
        if quality.get("periods"):
            figures += "; periods " + ", ".join(quality["periods"][:6])
        if quality.get("units"):
            figures += f"; units {quality['units']}"
        lines.append(figures)
    links = quality.get("links")
    if links and links.get("lines"):
        lines.append(f"Links: {links['linked']} of {links['lines']} statement lines linked to a note "
                     f"({round(100 * links['linked'] / links['lines'])}%)"
                     + (" — " + ", ".join(f"{n} by {m}" for m, n in links.get("by_method", {}).items())
                        if links.get("by_method") else ""))
    checks = quality.get("checks")
    if checks and checks.get("total"):
        parts = [f"{checks['ok']} of {checks['total']} ok"]
        if checks.get("fail"):
            parts.append(f"{checks['fail']} failed")
        if checks.get("warn"):
            parts.append(f"{checks['warn']} warnings")
        lines.append("Checks: " + " · ".join(parts))
    if quality.get("narrative"):
        lines += _narrative_lines(quality["narrative"])
    for warning in quality.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return lines


def report_text(package):
    quality = package.meta.get("quality")
    if not quality:
        return package.meta.get("summary", "")
    return "\n".join(report_lines(quality, package.meta.get("format_label", "")))
