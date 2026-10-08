"""TYPE-6: a Canadian Form 40-F financial statements exhibit (Magna, Exhibit 99.3,
US GAAP in U.S. dollars, printed from EDGAR), read with the ca-40f-usgaap profile:
the Ind AS layout rules with the Canadian wording checks."""

import pytest

import config
from ingest import model_testing, pipeline

MAGNA = config.MODEL_DOCS_DIR / "TYPE-6-CANADA-40-F-2025-Magna.pdf"


@pytest.fixture(scope="module")
def magna():
    if not MAGNA.exists():
        pytest.skip(f"Model document not found: {MAGNA}")
    return pipeline.open_report(MAGNA)


def test_matched_to_its_own_model(magna):
    meta = magna.meta
    assert meta["model"] == "TYPE-6-CANADA-40-F-2025-Magna" and meta["profile"] == "ca-40f-usgaap"
    assert meta["format"] == "modern"
    assert meta["units"] == "U.S. dollars in millions"          # not "rs in millions" (Rs inside "dollars")


def test_notes(magna):
    notes = magna.index["sections"]["consolidated"]["notes"]
    assert [n["no"] for n in notes] == list(range(1, 27))
    taxes = notes[12]
    assert (taxes["title"], taxes["start_page"], taxes["end_page"]) == ("INCOME TAXES", 24, 27)
    assert (notes[0]["start_page"], notes[-1]["end_page"]) == (10, 46)


def test_statements(magna):
    statements = magna.index["sections"]["consolidated"]["statements"]
    pages = {s["type"]: [p["pdf_page"] for p in s["pages"]] for s in statements}
    assert pages == {"profit_loss": [5, 6], "balance_sheet": [7], "cash_flow": [8], "equity": [9]}


def test_income_taxes_line_links_to_note_13(magna):
    line = next(ln for ln in magna.lines("C-PL") if ln["label"] == "Income taxes")
    assert line["note_ref"] == "13"
    assert [(v["raw"], v["period_label"]) for v in magna.values(line["id"])] == [("425", "FY2025"), ("446", "FY2024")]
    assert ("C13", "references") in {(e["dst"], e["kind"]) for e in magna.edges(src=line["id"])}


def test_model_testing_page(magna):
    result = model_testing.extract_page(MAGNA, 24)
    assert result["extracted"]["source"] == "package" and result["notices"] == []
    assert [u["id"] for u in result["extracted"]["units_on_page"] if u["starts_here"]] == ["C13"]
