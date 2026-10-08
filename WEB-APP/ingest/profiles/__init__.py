"""Format profiles: the extraction rules for each kind of annual report
(INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §8).

A profile says which indexer reads the report, which optional rules are
switched on, and what a correct extraction must produce (its acceptance
checks). Every model document in MODEL-DOCS is tied to one profile
(ingest/models/registry.json); several models may share one.

Rules that only some formats need are *switches*, read with `active()` while
a report is being ingested (`using(profile)`), so a new format never adds an
"if" to another format's code path. Bump a profile's `version` whenever its
rules change: only the reports of that profile are rebuilt.

To add a profile: define it below (or extend one with `derive`), give it a
model document, a golden test and a baseline (see MODEL-DOCS/README.md).
"""

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Switches:
    """Optional rules. All off unless a profile turns them on."""
    # Plain-text (typewriter) filings: split one-span table rows into columns,
    # drop stray page numbers, merge misaligned columns, join wrapped captions.
    monospace_columns: bool = False
    # Statement titles that are a plain line in capitals ("CONSOLIDATED BALANCE SHEETS").
    plain_caps_titles: bool = False
    # Note headings "(1)   SIGNIFICANT ACCOUNTING POLICIES" in capitals, not bold.
    plain_note_headings: bool = False
    # PDF pages without the notes' running header inside a notes section.
    continuation_pages: bool = False
    # The notes' heading is printed once, on their first page (20-F filings printed from
    # EDGAR): the pages after it stay in the section until a statement, another section,
    # or a page that starts the next part ("Item 19.", "SIGNATURES", the auditor's report).
    notes_run_on: bool = False
    # Notes numbered one level down, "2.1 Cash and cash equivalents", and cited that way in the
    # statements' Note column (Infosys): the "N.M" headings are the notes, "N" only groups them.
    decimal_notes: bool = False
    # Statement titles numbered like the notes: "1.1  Consolidated Income Statement for …" (BHP).
    numbered_statement_titles: bool = False
    # More wordings of the notes heading, as (section, text): "Notes to the Financial Statements".
    extra_notes_headers: tuple = ()
    # A statement continued on a page with no title at all, only its figure rows (Infosys p.172).
    untitled_statement_pages: bool = False
    # Pages outside notes and statements are read into sections and blocks
    # (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md): the section-map sources to use,
    # in order of trust - "bookmarks", "contents", "running_headers", "form_items". Empty: off.
    narrative_sections: tuple = ()
    # Designed pages: panels, KPI figures, diagrams, charts and figures (plan §4.2-4.4).
    narrative_designed_pages: bool = False
    # Note tables with empty cells (loss development triangles): a row's figures are written
    # under the columns they sit in, "2017 |  | 1,088 | 1,894", not shifted to the left.
    aligned_note_columns: bool = False


@dataclass(frozen=True)
class Acceptance:
    """What a trial extraction with this profile must produce (plan §4.3)."""
    min_notes: int = 5                  # notes (or units) in the largest section
    max_first_note: int = 3             # notes numbered from about 1
    min_statement_types: int = 3        # of P&L, BS, CF, Equity in that section
    min_readable_share: float = 0.9     # statement figures that parse as numbers
    balance_must_hold: bool = True      # every balance-sheet check that could be run is ok
    must_contain: tuple = ()            # (what, regex) the report's text must mention (format-specific)


@dataclass(frozen=True)
class Profile:
    id: str
    version: int
    indexer: str                        # notes | legacy | transcribed
    format: str                         # the index's "format": modern | us-10k | legacy | transcribed
    description: str
    switches: Switches = field(default_factory=Switches)
    acceptance: Acceptance = field(default_factory=Acceptance)


def derive(base, **changes):
    """A profile that extends another: `derive(US_10K_EDGAR, id=..., switches=...)`."""
    return replace(base, **changes)


IN_INDAS = Profile(
    id="in-indas-2020s", version=2, indexer="notes", format="modern",
    description="Indian annual report under Ind AS: 'Notes to … Financial Statements' running headers, "
                "bold numbered note headings, large statement titles, a Notes column",
    switches=Switches(narrative_sections=("bookmarks", "contents", "running_headers"),
                      narrative_designed_pages=True),
    acceptance=Acceptance(must_contain=(("Ind AS or ₹", r"\bInd\s?AS\b|₹"),)),
)

US_10K_EDGAR = Profile(
    id="us-10k-edgar", version=4, indexer="notes", format="us-10k",
    description="US Form 10-K typeset by EDGAR filing agents: bold-capital statement titles, "
                "F-page numbers, no Notes column",
    switches=Switches(narrative_sections=("bookmarks", "contents", "form_items"), aligned_note_columns=True),
    acceptance=Acceptance(must_contain=(("Form 10-K", r"\bFORM\s+10-K\b"), ("Item 8", r"\bITEM\s+8\b"))),
)

# Each profile below sets its switches in full (a derived profile does not inherit the narrative
# section sources: which ones fit is a property of the format).
US_10K_TYPEWRITER = derive(
    US_10K_EDGAR, id="us-10k-typewriter", version=2,
    description="US Form 10-K from the 1990s EDGAR text, rendered in a typewriter font: "
                "one font, no bold, tables of spaces and dot leaders",
    switches=Switches(monospace_columns=True, plain_caps_titles=True,
                      plain_note_headings=True, continuation_pages=True,
                      narrative_sections=("bookmarks", "contents", "form_items")),
)

IN_SCHEDULE_VI = Profile(
    id="in-schedule-vi-1970s", version=2, indexer="legacy", format="legacy",
    description="Indian annual report under the Companies Act 1956 (old Schedule VI): "
                "statements, lettered schedules, a notes schedule; Rs. with Indian grouping",
    switches=Switches(narrative_sections=("bookmarks", "contents", "headings")),
    acceptance=Acceptance(min_statement_types=2, must_contain=(("schedules", r"(?i)\bSCHEDULE\b"),)),
)

SCANNED_OCR = Profile(
    id="scanned-ocr", version=2, indexer="transcribed", format="transcribed",
    description="Scanned report (no text layer), transcribed page by page by Claude; "
                "one section per company",
    switches=Switches(narrative_sections=("headings",)),        # from the transcription's headings
    acceptance=Acceptance(min_notes=1, min_statement_types=2, max_first_note=99, balance_must_hold=False),
)

CA_40F = derive(
    IN_INDAS, id="ca-40f-usgaap", version=2,
    description="Canadian Form 40-F financial statements exhibit (US GAAP), printed from EDGAR: "
                "'Notes to Consolidated Financial Statements' running headers, bold numbered note "
                "headings, capital statement titles, a Note column",
    switches=Switches(narrative_sections=("bookmarks", "contents", "headings")),
    # the layout rules are the Ind AS ones; only the wording differs. "Chartered Professional
    # Accountants" is how a Canadian auditor signs - no other model document has it.
    acceptance=Acceptance(must_contain=(
        ("a Canadian auditor (Chartered Professional Accountants)", r"Chartered Professional Accountants"),
        ("U.S. or Canadian dollars", r"U\.S\.\s+dollars|Canadian\s+dollars"),
    )),
)

IN_20F = derive(
    IN_INDAS, id="in-20f-ifrs", version=2,
    description="Indian company's Form 20-F (IFRS, US dollars), printed from EDGAR: the notes heading once "
                "('Overview and Notes to the Consolidated Financial Statements'), notes numbered 2.1, 2.2 … "
                "and cited that way in the statements' Note column",
    switches=Switches(notes_run_on=True, decimal_notes=True, untitled_statement_pages=True,
                      narrative_sections=("bookmarks", "contents", "form_items")),
    acceptance=Acceptance(must_contain=(
        ("Form 20-F", r"\bForm\s+20-F\b"),
        ("an Indian company (Ind AS or ₹)", r"\bInd\s?AS\b|₹"),
    )),
)

AU_20F = derive(
    IN_INDAS, id="au-20f-ifrs", version=2,
    description="Australian company's Form 20-F (IFRS, US$M), printed from EDGAR: numbered statement titles "
                "('1.1 Consolidated Income Statement'), '1.6 Notes to the Financial Statements' printed once, "
                "notes 1, 2, 3 … with sub-sections 24.1, 24.2",
    switches=Switches(notes_run_on=True, numbered_statement_titles=True,
                      extra_notes_headers=(("consolidated", "Notes to the Financial Statements"),),
                      # its running headers are the browser's print lines: the contents page only
                      narrative_sections=("bookmarks", "contents"), narrative_designed_pages=True),
    acceptance=Acceptance(must_contain=(
        ("Form 20-F", r"\bForm\s+20-F\b"),
        ("an Australian company (Corporations Act 2001)", r"Corporations Act 2001"),
    )),
)

PROFILES = {p.id: p for p in (IN_INDAS, US_10K_EDGAR, US_10K_TYPEWRITER, IN_SCHEDULE_VI, SCANNED_OCR, CA_40F,
                              IN_20F, AU_20F)}

# Outside an ingestion (e.g. a test calling a rule function directly): no switches.
DEFAULT = Profile(id="default", version=0, indexer="notes", format="modern", description="no profile")

_active = contextvars.ContextVar("ingest_profile", default=DEFAULT)


def active():
    """The profile of the report being ingested now."""
    return _active.get()


@contextmanager
def using(profile):
    token = _active.set(profile)
    try:
        yield profile
    finally:
        _active.reset(token)


def get(profile_id):
    return PROFILES[profile_id]
