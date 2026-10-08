"""A section's text for Claude and for tools. The block rendering itself is
shared with the analyzer (rptpkg/blocks.py)."""

from rptpkg.blocks import block_text, page_label, page_text  # noqa: F401  (re-exported)


def section_text(section, blocks_by_page, printed, covered):
    """The section as one text: its pages in order; pages of notes and statements are pointed to, not repeated."""
    parts = [f"=== Report section — {section['title']} ==="]
    skipped = []

    def flush_skipped():
        if skipped:
            span = f"{skipped[0]}" if len(skipped) == 1 else f"{skipped[0]}–{skipped[-1]}"
            parts.append(f"(PDF pages {span}: financial statements and notes, extracted as notes)")
            skipped.clear()

    for pno in range(section["start_page"], section["end_page"] + 1):
        if pno in covered:
            skipped.append(pno)
            continue
        flush_skipped()
        parts.append(f"--- {page_label(pno, printed.get(pno))} ---\n{page_text(blocks_by_page.get(pno, []))}")
    flush_skipped()
    return "\n\n".join(parts)
