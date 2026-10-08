-- Report package (INFO/SPLIT-FUNCTIONALITY-PLAN.md §5).
-- One SQLite file per annual report, written by the ingester, read by the analyzer.
-- JSON columns hold lists / objects exactly as the ingester produced them.

-- schema_version, ingester_version, pdf_name, pdf_sha256, page_count, built_at,
-- company, format, format_source, format_label, summary, quality, and "index":
-- the report-level facts of the index (sections and page layouts are in their tables).
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                       -- JSON
);

-- consolidated / standalone (modern, us-10k, legacy) or one per company (scanned).
CREATE TABLE sections (
    id          TEXT PRIMARY KEY,             -- "consolidated", "standalone", "berkshire-hathaway"
    seq         INTEGER NOT NULL,
    code        TEXT NOT NULL,                -- C, S, BH ...: prefix of statement ids
    label       TEXT,
    first_page  INTEGER,
    last_page   INTEGER,
    data        TEXT NOT NULL                 -- JSON: the section as indexed (without its units)
);

-- Notes, schedules, report sections and the primary statements.
CREATE TABLE units (
    section_id    TEXT NOT NULL REFERENCES sections(id),
    id            TEXT NOT NULL,              -- C21, SCH-E, BH-N5 ... / statements: C-PL, C-BS, C-CF, C-EQ
    seq           INTEGER NOT NULL,           -- order within the section (notes first, then statements)
    kind          TEXT NOT NULL,              -- note, schedule, narrative, statement
    number        TEXT,                       -- JSON: 21, "SCH-E", or the statement type
    title         TEXT,
    parent_id     TEXT,
    start_page    INTEGER,
    end_page      INTEGER,
    printed_pages TEXT,
    data          TEXT NOT NULL,              -- JSON: the unit as indexed
    text          TEXT,                       -- the clipped text Claude receives
    text_pages    TEXT,                       -- JSON: [{pdf_page, printed}] of that text
    view          TEXT,                       -- JSON: statements only, the tables of the statements page
    PRIMARY KEY (section_id, id)
);

-- Every page of the PDF.
CREATE TABLE pages (
    pdf_page    INTEGER PRIMARY KEY,
    printed     TEXT,
    text        TEXT,                         -- cleaned whole-page text ("Or analyze specific PDF pages")
    text_source TEXT NOT NULL,                -- pdf | ocr
    layout_seq  INTEGER,                      -- position in the index's page layouts, if it has one
    layout      TEXT,                         -- JSON: the page's layout as indexed
    ocr         TEXT                          -- JSON: scanned reports, the page's transcription checks
);

-- Statement lines with figures (Phase 2).
CREATE TABLE lines (
    id          TEXT PRIMARY KEY,             -- C-PL-L023
    section_id  TEXT NOT NULL,
    unit_id     TEXT NOT NULL,                -- the statement
    seq         INTEGER NOT NULL,
    pdf_page    INTEGER NOT NULL,
    table_no    INTEGER NOT NULL,
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL,                -- data | total
    note_ref    TEXT                          -- "21", "5a", "'E'" from the Notes / Schedule column
);

CREATE TABLE "values" (
    line_id      TEXT NOT NULL REFERENCES lines(id),
    col          INTEGER NOT NULL,
    heading      TEXT,                        -- the column heading as printed
    period_end   TEXT,                        -- ISO date when the heading names one
    period_label TEXT,                        -- FY2026, 2025, ...
    raw          TEXT NOT NULL,               -- as printed: "(1,828)", "$10,622", "—"
    value        REAL,                        -- parsed; NULL if unreadable
    nil          INTEGER NOT NULL DEFAULT 0,  -- printed as a dash
    PRIMARY KEY (line_id, col)
);

-- Links between units, lines and note rows (Phase 3).
CREATE TABLE edges (
    id          INTEGER PRIMARY KEY,
    src         TEXT NOT NULL,                -- a line id
    dst         TEXT NOT NULL,                -- a unit id (C21, SCH-E) or a line id
    kind        TEXT NOT NULL,                -- references | ties_to | mentions | sum_of | carries
    method      TEXT NOT NULL,
    confidence  TEXT NOT NULL,                -- high | medium | low
    evidence    TEXT NOT NULL
);
CREATE INDEX edges_src ON edges(src);
CREATE INDEX edges_dst ON edges(dst);

-- The report's own sections outside the notes and statements (schema 1.1,
-- INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §5): "Board's Report", "Item 7" ...
CREATE TABLE doc_sections (
    id            TEXT PRIMARY KEY,           -- N-BOARD-S-REPORT, N-ITEM-7-MANAGEMENT-S-DISCUSSION
    parent_id     TEXT,                       -- the part: N-STATUTORY-REPORTS, N-PART-II
    seq           INTEGER NOT NULL,
    title         TEXT NOT NULL,              -- as printed
    kind          TEXT NOT NULL,              -- mdna, board_report, risk, governance ... (ingest/narrative/kinds.py)
    level         INTEGER NOT NULL,
    start_page    INTEGER NOT NULL,
    end_page      INTEGER NOT NULL,
    printed       TEXT,                       -- the printed page the contents page gives
    source        TEXT NOT NULL,              -- bookmarks | contents | running_header | item_heading | fallback
    confirmed_by  TEXT NOT NULL,              -- JSON: the other sources that agree
    text          TEXT                        -- leaf sections: the section's blocks as text (for Claude, later)
);

-- Each page outside the notes and statements, read into blocks.
CREATE TABLE narrative_pages (
    pdf_page      INTEGER PRIMARY KEY,
    section_id    TEXT,                       -- doc_sections.id (the deepest section holding the page)
    layout_kind   TEXT NOT NULL,              -- prose | designed | table | contents | image | fallback
    header        TEXT NOT NULL,              -- JSON: running header lines cut from the page
    footer        TEXT NOT NULL               -- JSON: footer lines / page number
);

CREATE TABLE page_blocks (
    pdf_page      INTEGER NOT NULL,
    seq           INTEGER NOT NULL,           -- reading order on the page
    kind          TEXT NOT NULL,              -- heading | paragraph | list | table | metric | panel | diagram | chart | figure | rotated | flat
    bbox          TEXT NOT NULL,              -- JSON [x0, y0, x1, y1] in PDF points
    data          TEXT NOT NULL,              -- JSON: the block (text, items, head/rows, value/label, children, labels)
    PRIMARY KEY (pdf_page, seq)
);

-- Passages: the pieces of the report's sections a question can use (schema 1.2,
-- INFO/ANALYZE-NON-FIN-DATA-PLAN.md §3): a section cut at its headings into ~1.5-8 K characters.
CREATE TABLE doc_passages (
    id            TEXT PRIMARY KEY,           -- P-MANAGEMENT-DISCUSSION-ANALYS-03
    section_id    TEXT NOT NULL,              -- doc_sections.id
    seq           INTEGER NOT NULL,           -- order in the report
    path          TEXT NOT NULL,              -- "Statutory Reports › Management Discussion & Analysis › …"
    kind          TEXT NOT NULL,              -- the section's kind (mdna, risk …)
    start_page    INTEGER NOT NULL,
    end_page      INTEGER NOT NULL,
    start_block   INTEGER NOT NULL,           -- page_blocks.seq of its first block on start_page
    text          TEXT NOT NULL,              -- the passage as sent to Claude (page markers, block text)
    chars         INTEGER NOT NULL,
    lead          TEXT NOT NULL,              -- its first words (selector, step 3)
    blocks        TEXT NOT NULL,              -- JSON [[pdf_page, seq], …]
    figures       TEXT NOT NULL               -- JSON: money amounts [{raw, currency, amount, page, context}]
);

-- Automatic checks (Phase 3).
CREATE TABLE checks (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,                -- subtotal | balance | carries | note_tie
    subject     TEXT NOT NULL,                -- a line or unit id
    status      TEXT NOT NULL,                -- ok | warn | fail
    expected    REAL,
    actual      REAL,
    message     TEXT NOT NULL
);
