"""What goes around the notes in a prompt: the REPORT PROFILE, the list of
statements Claude must cover, and the labels used in prompts, history and
saved reports.

The prompts carry a REPORT PROFILE and name only the statements the report
has. For modern reports these render exactly the text used before.
"""

import config
from claude_client import system_prompt
from webcommon import UserError

MODERN_STATEMENT_SECTIONS = ("### Statement of Profit and Loss\n### Balance Sheet\n"
                             "### Cash Flow Statement\n### Statement of Changes in Equity")
MODERN_STATEMENT_NAMES = "Profit and Loss, Balance Sheet, Cash Flow, Changes in Equity"


def is_legacy(index):
    return index.get("format") == "legacy"


def is_transcribed(index):
    return index.get("format") == "transcribed"


def is_us10k(index):
    return index.get("format") == "us-10k"


US10K_STATEMENT_SECTIONS = ("### Income Statement (Statement of Operations)\n### Balance Sheet\n"
                            "### Cash Flow Statement\n### Statement of Shareholders' Equity")
US10K_STATEMENT_NAMES = "Income Statement, Balance Sheet, Cash Flow, Shareholders' Equity"
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]


def _us10k_profile(package):
    quality = package.meta.get("quality") or {}
    periods = ", ".join(p for p in quality.get("periods", []) if p.startswith("FY")) or "see the statements"
    year_end = package.meta.get("fiscal_month_day")
    ended = f" (years ended {MONTH_NAMES[year_end[0] - 1]} {year_end[1]})" if year_end else ""
    lines = ["REPORT PROFILE", "Format: US annual report on Form 10-K (US GAAP)",
             f"Periods in the statements: {periods}{ended}"]
    if package.meta.get("units"):
        lines.append(f"Units: {package.meta['units']}")
    lines.append("The statements have no Notes column; LINKED FIGURES shows how the app linked "
                 "statement lines to the notes.")
    return "\n".join(lines) + "\n\n"


def notes_label(notes):
    def name(n):
        if n.get("kind", "note") == "note" and isinstance(n["no"], int):
            return f"{n['section_label']} Note {n['no']} – {n['title']}"
        return n["label"]  # old-format schedule / note / report section
    return ", ".join(
        f"{name(n)} (PDF p.{n['start_page']}"
        + (f"–{n['end_page']}" if n["end_page"] != n["start_page"] else "") + ")"
        for n in notes
    )


def _section_statements(index, sections):
    keys = sections or list(index["sections"])[:1]
    return [st for k in keys for st in index["sections"].get(k, {}).get("statements", [])]


def ocr_checks_text(package, sections):
    """TRANSCRIPTION CHECKS for the pages of these sections: totals that don't
    add up and figures nothing confirmed (checked when the report was transcribed)."""
    index = package.index
    pages = sorted({p for k in sections or list(index["sections"])[:1]
                    for u in index["sections"].get(k, {}).get("notes", []) + [
                        {"start_page": st["pages"][0]["pdf_page"], "end_page": st["pages"][-1]["pdf_page"]}
                        for st in index["sections"].get(k, {}).get("statements", [])]
                    for p in range(u["start_page"], u["end_page"] + 1)})
    lines = []
    for pno in pages:
        header = package.ocr_page(pno)
        bits = []
        if header.get("unreadable"):
            bits.append(f"{header['unreadable']} unreadable character(s) [?]")
        for item in header.get("untied", []):
            label = "" if item["label"] == "(total)" else f" ({item['label']})"
            bits.append(f"total {item['figure']}{label} does not equal the figures above it")
        for item in header.get("unchecked", []):
            bits.append(f"total {item['figure']} can't be checked (a figure above it is unreadable)")
        if header.get("figures_to_check"):
            bits.append("figures not confirmed by the cross-check: " + ", ".join(header["figures_to_check"][:12]))
        if bits:
            lines.append(f"- p.{pno}: " + "; ".join(bits))
    return "TRANSCRIPTION CHECKS (automatic)\n" + ("\n".join(lines) if lines else "- no issues found")


def report_profile(package, sections=None):
    index = package.index
    if is_transcribed(index):
        statements_here = _section_statements(index, sections)
        missing = sorted({m for k in (sections or list(index["sections"])[:1])
                          for m in index["sections"].get(k, {}).get("statements_missing", [])})
        ocr = index.get("ocr", {})
        lines = [
            "REPORT PROFILE",
            f"Format: Scanned report, transcribed by AI from page images ({index.get('document_type') or 'report'})",
            "Companies in this report: " + "; ".join(index.get("entities") or []),
            f"Period: {index.get('fiscal_year_end_label') or 'see the statements'}"
            " (each company's statements give its own date)",
            f"Currency: {index.get('currency') or 'see the statements'}",
            "Statements provided: " + ("; ".join(st["title"] for st in statements_here) or "none found"),
        ]
        if missing:
            lines.append(f"Statement types NOT in the report for these companies: {', '.join(missing)}")
        lines.append(f"Source quality: AI transcription of {ocr.get('pages', '?')} scanned pages; [?] marks "
                     f"unreadable characters ({ocr.get('unreadable', 0)} in the report)")
        return "\n".join(lines) + "\n\n" + ocr_checks_text(package, sections) + "\n\n"
    if is_us10k(index):
        return _us10k_profile(package)
    if not is_legacy(index):
        return ""
    present = index.get("statements_present") or []
    missing = index.get("statements_missing") or []
    lines = [
        "REPORT PROFILE",
        "Format: Old Indian GAAP (Companies Act 1956, old Schedule VI) - standalone accounts only",
        f"Year ended: {index.get('fiscal_year_end_label') or 'see the statements'}"
        " (check the notes for the length of the previous period)",
        "Currency/units: Rs. with Indian digit grouping (1,00,000 = 1 lakh; 1,00,00,000 = 1 crore)",
        f"Statements in this report: {', '.join(present) or 'none found'}",
    ]
    if missing:
        lines.append(f"Statements NOT in this report: {', '.join(missing)} (not required at the time)")
    lines.append("Source quality: may be re-typed from the printed report; figures may contain "
                 "transcription errors")
    return "\n".join(lines) + "\n\n"


def statement_sections_text(index, sections=None):
    if is_transcribed(index):
        titles = list(dict.fromkeys(st["title"] for st in _section_statements(index, sections)))
        text = "\n".join(f"### {t}" for t in titles) or "### (no statements found)"
        if not any(st["type"] == "cash_flow" for st in _section_statements(index, sections)):
            text += ("\n(This report has no funds or cash flow statement. If cash movements matter to "
                     "the question, add a short derived funds-flow statement, clearly labelled as derived "
                     "by the analyst and not published.)")
        return text
    if is_us10k(index):
        return US10K_STATEMENT_SECTIONS
    if not is_legacy(index):
        return MODERN_STATEMENT_SECTIONS
    present = index.get("statements_present") or ["Profit and Loss Account", "Balance Sheet"]
    text = "\n".join(f"### {name}" for name in present)
    if "Cash Flow Statement" not in present:
        text += ("\n(This report has no Cash Flow Statement. If cash movements matter to the "
                 "question, add a short derived funds-flow statement, clearly labelled as "
                 "derived by the analyst and not published.)")
    return text


def statement_names(index, sections=None):
    if is_transcribed(index):
        return "; ".join(dict.fromkeys(st["title"] for st in _section_statements(index, sections))) \
            or "the statements provided"
    if is_us10k(index):
        return US10K_STATEMENT_NAMES
    if not is_legacy(index):
        return MODERN_STATEMENT_NAMES
    return ", ".join(index.get("statements_present") or []) or "the statements provided"


def analysis_system(index):
    text = system_prompt("analysis_system.txt")
    if is_legacy(index):
        text += "\n\n" + system_prompt("analysis_system_legacy.txt")
    if is_transcribed(index):
        text += "\n\n" + system_prompt("analysis_system_transcribed.txt")
    if is_us10k(index):
        text += "\n\n" + system_prompt("analysis_system_us10k.txt")
    return text


def scope_label(notes, pages):
    if pages:
        return "Manually selected pages"
    labels = sorted({n["section_label"] for n in notes})
    return " and ".join(labels)


def pages_label(extracts):
    return ", ".join(str(p["pdf_page"]) for e in extracts for p in e["pages"])


def note_pages_label(notes, page_spec):
    """PDF page ranges of the notes, e.g. "405–407, 451–452"."""
    if page_spec:
        return page_spec
    return ", ".join(
        f"{n['start_page']}" if n["start_page"] == n["end_page"]
        else f"{n['start_page']}–{n['end_page']}" for n in notes)


def statement_sections(index, notes):
    """The notes sections whose primary statements accompany a question:
    the sections of the selected notes, or consolidated (the default) for
    manually entered pages."""
    sections = sorted({n["section"] for n in notes})
    if not sections:
        sections = ["consolidated"] if "consolidated" in index["sections"] else list(index["sections"])[:1]
    return sections


def check_size(extract_text):
    if len(extract_text) > config.MAX_CONTEXT_CHARS:
        raise UserError(
            f"The selected content is {len(extract_text):,} characters, above the "
            f"{config.MAX_CONTEXT_CHARS:,} limit. Remove a note or narrow the pages."
        )
