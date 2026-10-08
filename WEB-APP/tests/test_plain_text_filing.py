"""Plain-text filings: EDGAR 10-Ks of the 1990s rendered in a typewriter font
(Annual-reports/BRK-1994.pdf, Berkshire Hathaway's 1994 10-K). One font and size
throughout, no bold; each table row is one monospaced span; filing pages overflow
onto PDF pages without the notes' running header."""

import pytest

import config
from ingest import pipeline
from ingest.pdf_utils import split_monospace

BRK94 = config.REPORTS_DIR / "BRK-1994.pdf"


def _span(text, x0=58.0, char=4.2, flags=8):
    return {"text": text, "bbox": (x0, 100.0, x0 + char * len(text), 107.0), "flags": flags, "font": "Courier"}


def test_split_monospace_columns():
    parts = split_monospace(_span("Cash and cash equivalents . . . . .   $    273,881        $ 1,817,558"))
    assert [p["text"] for p in parts] == ["Cash and cash equivalents", "$", "273,881", "$ 1,817,558"]
    assert parts[1]["bbox"][0] == pytest.approx(58.0 + 4.2 * 38)            # positioned by character
    assert split_monospace(_span("----------     ----------")) == []          # underlines
    assert [p["text"] for p in split_monospace(_span("(10)   INCOME TAXES"))] == ["(10) INCOME TAXES"]
    page_no = split_monospace(_span("                    20"))
    assert page_no[0]["text"] == "20" and page_no[0]["bbox"][0] == pytest.approx(58.0 + 4.2 * 20)
    proportional = _span("Net income   10,622", flags=0)
    assert split_monospace(proportional) == [proportional]                   # other fonts: unchanged


@pytest.fixture(scope="module")
def brk94():
    if not BRK94.exists():
        pytest.skip(f"Sample report not found: {BRK94}")
    return pipeline.open_report(BRK94)


def test_notes(brk94):
    section = brk94.index["sections"]["consolidated"]
    notes = {n["no"]: n for n in section["notes"]}
    assert sorted(notes) == list(range(1, 18))
    assert (section["first_page"], section["last_page"], section["page_count"]) == (26, 44, 19)
    assert notes[10]["title"] == "INCOME TAXES" and (notes[10]["start_page"], notes[10]["end_page"]) == (37, 38)
    # its heading is on a page without the running header (a continuation page)
    assert notes[6]["title"] == "INVESTMENT IN USAIR GROUP, INC. PREFERRED STOCK" and notes[6]["start_page"] == 34
    text = brk94.unit_text("consolidated", "C10")["text"]
    assert "(10) INCOME TAXES" in text and "Payable currently | $62,401 | $289,686" in text


def test_statements_and_figures(brk94):
    statements = {s["type"]: s for s in brk94.index["sections"]["consolidated"]["statements"]}
    assert {t: [p["pdf_page"] for p in s["pages"]] for t, s in statements.items()} == {
        "balance_sheet": [23], "profit_loss": [24], "cash_flow": [25]}
    assert "Sales and service revenues | $2,351,918 | $1,962,862 | $1,774,436" in statements["profit_loss"]["text"]
    line = brk94.query("SELECT * FROM lines WHERE unit_id = 'C-PL' AND label = 'Sales and service revenues'")[0]
    values = {v["period_label"]: v["value"] for v in brk94.values(line["id"])}
    assert values == {"FY1994": 2351918.0, "FY1993": 1962862.0, "FY1992": 1774436.0}
    view = brk94.statement_view("consolidated", "C-PL")
    assert view["pages"][0]["tables"][0]["width"] == 3                     # years and figures share columns
