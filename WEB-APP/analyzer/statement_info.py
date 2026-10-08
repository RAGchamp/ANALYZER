"""The primary statements as the analyzer names and sends them: the page
ranges, the one-line summary, the list for the prompt and the saved report,
and the combined text (extracted by the ingester, see ingest/statements.py)."""


def pages_text(statement):
    pages = [p["pdf_page"] for p in statement["pages"]]
    return f"{pages[0]}" if len(pages) == 1 else f"{pages[0]}–{pages[-1]}"


def summary(statements):
    """"Statement of Profit and Loss (p.342–343) · ... (p.344–345, rotated)" """
    parts = []
    for s in statements:
        rotated = any(p["rotation"] for p in s["pages"])
        parts.append(f"{s['label']} (p.{pages_text(s)}{', rotated' if rotated else ''})")
    return " · ".join(parts)


def label(index, sections):
    """Human-readable list of the statements sent for these sections."""
    parts = []
    for section in sections:
        for s in index["sections"].get(section, {}).get("statements", []):
            parts.append(f"{s['title']} (PDF p.{pages_text(s)})")
    return "; ".join(parts)


def sections_text(index, sections):
    """Combined, already-extracted text of all statements for the given
    sections (cached in the index when the report was loaded)."""
    return "\n\n".join(
        statement["text"]
        for section in sections
        for statement in index["sections"].get(section, {}).get("statements", [])
    )
