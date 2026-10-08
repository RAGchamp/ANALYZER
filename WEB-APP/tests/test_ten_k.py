"""A US 10-K (Chubb 2025): statement titles in 7pt bold capitals, US GAAP
names ("Statements of Operations", "Balance Sheets"), an index page that
lists "Notes to Consolidated Financial Statements", and "F-6" page numbers."""

from ingest import statements
from ingest.clip import _rows_to_text


def _span(text, x0, x1, y=100):
    return {"text": text, "bbox": (x0, y, x1, y + 6)}


def test_ten_k_statements_found(ten_k_index):
    section = ten_k_index["sections"]["consolidated"]
    found = {s["type"]: s["pages"] for s in section["statements"]}
    assert {t: [p["pdf_page"] for p in pages] for t, pages in found.items()} == {
        "profit_loss": [108], "balance_sheet": [107], "cash_flow": [110], "equity": [109],
    }
    assert [p["printed"] for p in found["balance_sheet"]] == ["F-6"]


def test_ten_k_index_page_is_not_a_notes_page(ten_k_index):
    section = ten_k_index["sections"]["consolidated"]
    assert section["first_page"] == 111
    assert len(section["notes"]) == 22
    assert "103" not in ten_k_index["pages"]


def test_ten_k_statement_text(ten_k_index):
    by_type = {s["type"]: s["text"] for s in ten_k_index["sections"]["consolidated"]["statements"]}
    assert "Income tax expense | 2,422 | 1,815 | 511" in by_type["profit_loss"]
    assert "Net income | $10,622 | $9,640 | $9,015" in by_type["profit_loss"]
    assert "(printed page F-7)" in by_type["profit_loss"]
    # the "F-7" footer is cut off
    assert not by_type["profit_loss"].rstrip().endswith("F-7")


def test_us_gaap_statement_titles():
    assert statements.statement_type("CONSOLIDATED BALANCE SHEETS") == "balance_sheet"
    assert statements.statement_type(
        "CONSOLIDATED STATEMENTS OF OPERATIONS AND COMPREHENSIVE INCOME") == "profit_loss"
    assert statements.statement_type("Consolidated Statements of Income") == "profit_loss"
    assert statements.statement_type("CONSOLIDATED STATEMENTS OF SHAREHOLDERS’ EQUITY") == "equity"
    assert statements.statement_type("Consolidated Statements of Stockholders' Equity") == "equity"
    assert statements.statement_type("CONSOLIDATED STATEMENTS OF CASH FLOWS") == "cash_flow"
    assert statements.statement_type("Consolidated Statements of Financial Condition") == "balance_sheet"


def test_dollar_sign_joins_its_amount():
    spans = [_span("Net income", 20, 60), _span("$", 300, 304), _span("10,622", 330, 355),
             _span("$", 360, 364), _span("9,640", 390, 412)]
    assert _rows_to_text(spans) == "Net income | $10,622 | $9,640"
    spans = [_span("Short-term investments $", 20, 290), _span("4,840", 330, 355)]
    assert _rows_to_text(spans) == "Short-term investments | $4,840"


def test_ten_k_note_titles_stop_at_the_heading_line(ten_k_index):
    """Short headings are followed by bold table headings or sub-headings,
    which are not part of the title."""
    titles = {n["no"]: n["title"] for n in ten_k_index["sections"]["consolidated"]["notes"]}
    assert titles[7] == "Goodwill, Value of business acquired, and Other intangible assets"
    assert titles[12] == "Taxation"
    assert titles[13] == "Debt"
    assert titles[15] == "Shareholders’ equity"


def test_ten_k_group_line_below_running_header_is_skipped(ten_k_index):
    from ingest import clip as extractor
    note = extractor.extract_note(ten_k_index, "consolidated", 12)
    text = note["text"] if isinstance(note, dict) else note
    assert "Chubb Limited and Subsidiaries" not in text
    assert "12. Taxation" in text
