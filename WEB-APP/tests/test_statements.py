"""Primary financial statements: detection, rotation, extraction."""

import pytest

import extractor
import statements


def _by_type(index, section):
    return {s["type"]: s for s in index["sections"][section]["statements"]}


def test_consolidated_statements_found(index):
    found = _by_type(index, "consolidated")
    pages = {t: [p["pdf_page"] for p in s["pages"]] for t, s in found.items()}
    assert pages == {
        "balance_sheet": [341],
        "profit_loss": [342, 343],
        "equity": [344, 345],
        "cash_flow": [346, 347],
    }
    assert found["balance_sheet"]["title"] == "Consolidated Balance Sheet"
    assert found["equity"]["title"] == "Consolidated Statement of Changes in Equity"


def test_standalone_statements_found(index):
    pages = {t: [p["pdf_page"] for p in s["pages"]]
             for t, s in _by_type(index, "standalone").items()}
    assert pages == {
        "balance_sheet": [205],
        "profit_loss": [206],
        "equity": [207],
        "cash_flow": [208, 209],
    }


def test_statements_listed_in_order(index):
    types = [s["type"] for s in index["sections"]["consolidated"]["statements"]]
    assert types == ["profit_loss", "balance_sheet", "cash_flow", "equity"]


def test_sideways_equity_pages_are_rotated_clockwise(index):
    equity = _by_type(index, "consolidated")["equity"]
    assert [p["rotation"] for p in equity["pages"]] == [-90, -90]
    assert [p["printed"] for p in equity["pages"]] == ["341", "342"]
    text = equity["text"]
    assert "rotated 90° clockwise before extraction" in text
    # Read left-to-right after rotation, with every number in its own column.
    assert ("Balance as at March 31, 2026 | 23,103.22 | 15.50 | 127.36 | 3,230.48 | 68,659.35"
            in text)
    assert "(230.16) | 94,611.97" in text
    # The sideways running header / page number are not mixed into the table.
    assert "Integrated Annual Report" not in text


def test_upright_statements_are_not_rotated(index):
    for s in index["sections"]["consolidated"]["statements"]:
        if s["type"] != "equity":
            assert all(p["rotation"] == 0 for p in s["pages"])


def test_balance_sheet_rows_link_to_notes(index):
    text = _by_type(index, "consolidated")["balance_sheet"]["text"]
    assert "(c) Deferred tax liabilities (net) | 21 | 215.24 | 1,198.28" in text
    assert "(j) Deferred tax assets (net) | 21 | 2,121.99 | 1,901.43" in text
    assert "Total assets | 222,599.55 | 200,883.21" in text


def test_profit_and_loss_and_cash_flow_content(index):
    found = _by_type(index, "consolidated")
    # P&L tax lines tie to Note 21 (current tax 5,701.41 - 95.34 prior years = 5,606.07)
    assert "Current tax | 5,606.07 | 5,848.54" in found["profit_loss"]["text"]
    assert "Deferred tax charge/(credit) | 171.67 | (423.04)" in found["profit_loss"]["text"]
    assert "Income taxes paid (net of refunds) | (5,646.25) | (6,050.07)" in found["cash_flow"]["text"]


def test_sections_text_and_label(index):
    text = statements.sections_text(index, ["consolidated"])
    for title in ("Consolidated Statement of Profit and Loss", "Consolidated Balance Sheet",
                  "Consolidated Cash Flow Statement", "Consolidated Statement of Changes in Equity"):
        assert f"=== {title} (Consolidated) ===" in text
    assert "Balance Sheet (Standalone)" not in text
    label = statements.label(index, ["consolidated"])
    assert "Consolidated Balance Sheet (PDF p.341)" in label
    assert "Consolidated Statement of Changes in Equity (PDF p.344–345)" in label


@pytest.mark.parametrize("title, kind", [
    ("Consolidated Balance Sheet", "balance_sheet"),
    ("Balance Sheet", "balance_sheet"),
    ("Consolidated Statement of Profit and Loss", "profit_loss"),
    ("Income Statement", "profit_loss"),
    ("Consolidated Cash Flow Statement", "cash_flow"),
    ("Statement of Cash Flows", "cash_flow"),
    ("Consolidated Statement of Changes in Equity Integrated Annual Report", "equity"),
    ("Statement of Changes in Stockholders' Equity", "equity"),
    ("Notes to Consolidated Financial Statements", None),
    ("Independent Auditor's Report", None),
])
def test_statement_type(title, kind):
    assert statements.statement_type(title) == kind


def test_adjacent_numbers_split_into_columns():
    def span(x0, x1, text):
        return {"bbox": (x0, 100, x1, 108), "text": text}
    row = [span(50, 90, "Opening balance"), span(200, 230, "127.36"), span(233, 262, "3,230.48"),
           span(265, 290, "(327.53)"), span(292, 330, "91,249.54")]
    assert extractor._rows_to_text(row) == "Opening balance | 127.36 | 3,230.48 | (327.53) | 91,249.54"
    # ordinary words close together still join
    assert extractor._rows_to_text([span(50, 70, "Deferred"), span(72, 80, "tax")]) == "Deferred tax"
