"""Page blocks as text (shared by the ingester and the analyzer).

The ingester reads the pages outside the notes and statements into typed
blocks (ingest/narrative); the analyzer sends them to Claude. Both need the
same text form, so it lives here, in the package library they share: the
blocks as Markdown-like text, tables as "a | b | c" rows (the same shape as
the notes), graphics as their labels, page markers as in the notes.
"""


def block_text(block):
    kind = block["kind"]
    if kind == "heading":
        return "#" * (block.get("level", 2) + 1) + " " + block["text"]
    if kind in ("paragraph", "rotated", "flat"):
        return block["text"]
    if kind == "list":
        return "\n".join(f"- {item}" for item in block["items"])
    if kind == "table":
        return "\n".join(" | ".join(c for c in row) for row in block["head"] + block["rows"])
    if kind == "metric":
        return f"{block['value']} — {block['label']}" if block["label"] else block["value"]
    if kind == "panel":
        return "\n\n".join(block_text(c) for c in block["children"])
    if kind in ("diagram", "chart"):
        return f"[{kind.capitalize()}: " + " · ".join(block["labels"]) + "]"
    if kind == "figure":
        return "[Figure]"
    return ""


def page_text(blocks):
    return "\n\n".join(t for t in (block_text(b) for b in blocks) if t)


def page_label(pno, printed):
    return f"PDF page {pno}" + (f" (printed page {printed})" if printed else "")
