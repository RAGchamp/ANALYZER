"""The "View the financial statements" page, from the report package.

The ingester parsed every statement page into tables (ingest/tables.py) and
stored them with the statement; this only puts them in page order:
consolidated first, then standalone (scanned reports: the registrant first,
then the other companies).
"""

import config


def report_view(package):
    index = package.index
    transcribed = index.get("format") == "transcribed"
    views = {}
    for key, uid, _ in package.statement_units():
        views.setdefault(key, []).append(package.statement_view(key, uid))
    sections = []
    for key, section in index["sections"].items():
        section_views = [dict(v) for v in views.get(key, []) if v is not None]
        for n, v in enumerate(section_views):
            # scanned reports can have two statements of one type (e.g. income
            # and adjusted income), so their anchors are numbered
            v["anchor"] = f"{key}-{v['id']}-{n}" if transcribed else f"{key}-{v['id']}"
        label = section.get("label") or config.SECTION_LABELS.get(key, key)
        sections.append({"key": key, "label": label, "statements": section_views})
    return sections
