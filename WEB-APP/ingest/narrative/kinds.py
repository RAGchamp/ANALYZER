"""Section kinds: a small fixed vocabulary, so the UI and (later) the Analyzer can
treat sections alike across formats (plan §3.2). An unknown title is "other":
never dropped, just not specially labelled. First match wins."""

import re

KINDS = (
    ("contents", r"\b(table of )?contents\b|\bindex to form\b"),
    ("exhibits", r"\bexhibits?\b"),
    ("auditor_report", r"auditor'?s'? report|report of independent|independent (registered )?(public )?accounting"),
    ("financial_statements", r"financial statements|supplementary data|financial information|balance sheet|"
                             r"profit and loss|financial stmnts|schedules?\b"),
    ("mdna", r"management'?s? discussion|\bmd ?(and|&) ?a\b|operating and financial review|financial review|"
             r"directors'? report on operations"),
    ("risk", r"\brisk"),
    ("board_report", r"board'?s'? report|directors'? report|report of the directors"),
    ("remuneration", r"remuneration|compensation"),
    ("governance", r"governance|directors|board of directors|senior management|audit committee|code of ethics|"
                   r"accountant'?s? fees|accounting fees|related part|certain relationships"),
    ("sustainability", r"sustainab|responsib|\besg\b|natural capital|human capital|social and relationship|"
                       r"intellectual capital|manufactured capital|financial capital|climate|safety"),
    ("letter", r"chairman|\bchair'?s\b|\bcmd\b|\bceo\b|chief executive|managing director|letter|message|desk"),
    ("highlights", r"highlights|at a glance|key figures|key information"),
    ("strategy", r"strateg|value creation|business model|materiality|stakeholder|why \w+|outlook"),
    ("shareholder_info", r"shareholder|stockholder|market for (the )?registrant|dividend|offer and listing|"
                         r"security holders|security ownership|description of securities|shares?\b"),
    ("business", r"\bbusiness\b|about (the )?(company|us|bharat|report)|overview|footprint|properties|"
                 r"information on the company|our assets|operations|mine safety"),
    ("legal", r"legal proceedings|litigation"),
    ("controls", r"controls and procedures"),
    ("signatures", r"signatures?"),
    ("front_matter", r"front matter|corporate information"),
)
_COMPILED = [(k, re.compile(p, re.I)) for k, p in KINDS]
ITEM_PREFIX_RE = re.compile(r"^\s*item\s+\d{1,2}[A-C]?\.\s*", re.I)


def kind_of(title):
    """By the title's words, never by an Item number: "Item 3" is Legal Proceedings in a 10-K
    and Key Information in a 20-F."""
    title = ITEM_PREFIX_RE.sub("", (title or "").replace("’", "'").replace("‘", "'"))
    for kind, pattern in _COMPILED:
        if pattern.search(title):
            return kind
    return "other"
