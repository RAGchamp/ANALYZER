"""Names shared by the ingester and the analyzer: unit ids and section codes.

Notes keep the ids the app has always used: C21 / S21 for modern notes, and
the ids the old-format and scanned indexers give their units (SCH-E, N25, DIR,
BH-N5). Primary statements get <section code>-<type code>: C-PL, C-BS, S-CF,
BH-EQ.
"""

SECTION_PREFIX = {"consolidated": "C", "standalone": "S"}
# Old-format (Companies Act 1956) reports have standalone accounts only.
LEGACY_SECTION = "standalone"

STATEMENT_CODES = {"profit_loss": "PL", "balance_sheet": "BS", "cash_flow": "CF", "equity": "EQ"}


def note_unit_id(section, note):
    """C21 / S21 for modern notes; other units carry their own id."""
    if isinstance(note, dict):
        return note.get("id") or f"{SECTION_PREFIX[section]}{note['no']}"
    return f"{SECTION_PREFIX[section]}{note}"


def section_code(key, section):
    return section.get("code") or SECTION_PREFIX.get(key) or key.upper()


def statement_unit_ids(key, section):
    """Ids of a section's statements, in order. A scanned report can have two
    statements of one type (income and adjusted income): C-PL, C-PL-2."""
    code = section_code(key, section)
    ids, seen = [], {}
    for statement in section.get("statements", []):
        base = f"{code}-{STATEMENT_CODES.get(statement['type'], statement['type'].upper())}"
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return ids


def model_line(meta):
    """"TYPE-3 USA 10-K 2025 Chubb (model document; checks passed)"."""
    if not meta.get("model"):
        return None
    how = ("model document" if meta.get("model_match") == "model document"
           else f"similarity {meta.get('model_score', 0):.2f}")
    checks = (meta.get("model_checks") or {}).get("summary", "")
    return f"{meta.get('model_short') or meta['model']} ({how}; {checks})"
