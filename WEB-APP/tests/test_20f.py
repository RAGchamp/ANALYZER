"""TYPE-7 and TYPE-8: Form 20-F filings printed from EDGAR, whose notes heading is
printed once (profile switch notes_run_on).

- Infosys (in-20f-ifrs): notes numbered 2.1, 2.2 … and cited that way (decimal_notes);
  the Changes in Equity runs on to an untitled page (untitled_statement_pages).
- BHP (au-20f-ifrs): statement titles numbered "1.1 Consolidated Income Statement"
  (numbered_statement_titles); "1.6 Notes to the Financial Statements" (extra_notes_headers).
"""

import pytest

import config
from ingest import model_testing, pipeline

INFOSYS = config.MODEL_DOCS_DIR / "TYPE-7-INDIA-20-F-2025-Infosys.pdf"
BHP = config.MODEL_DOCS_DIR / "TYPE-8-AUSTRALIA-20-F-2025-BHP.pdf"


def _open(pdf):
    if not pdf.exists():
        pytest.skip(f"Model document not found: {pdf}")
    return pipeline.open_report(pdf)


@pytest.fixture(scope="module")
def infosys():
    return _open(INFOSYS)


@pytest.fixture(scope="module")
def bhp():
    return _open(BHP)


def _statement_pages(package):
    return {s["type"]: [p["pdf_page"] for p in s["pages"]]
            for s in package.index["sections"]["consolidated"]["statements"]}


def _line(package, unit, label):
    return next(ln for ln in package.lines(unit) if ln["label"] == label)


# ---------------------------------------------------------------- Infosys

def test_infosys_model(infosys):
    assert (infosys.meta["model"], infosys.meta["profile"]) == ("TYPE-7-INDIA-20-F-2025-Infosys", "in-20f-ifrs")


def test_infosys_decimal_notes(infosys):
    section = infosys.index["sections"]["consolidated"]
    notes = section["notes"]
    assert [n["no"] for n in notes] == [f"1.{m}" for m in range(1, 7)] + [f"2.{m}" for m in range(1, 22)]
    taxes = next(n for n in notes if n["no"] == "2.18")
    assert (taxes["title"], taxes["start_page"], taxes["end_page"]) == ("Income taxes", 238, 244)
    # run-on pages: from the heading page to the page before "Item 19. Exhibits"
    assert (section["first_page"], section["last_page"]) == (174, 253)
    assert infosys.unit_text("consolidated", "C2.18")["text"].startswith("=== Note 2.18 — Income taxes")


def test_infosys_statements(infosys):
    assert _statement_pages(infosys) == {"profit_loss": [170], "balance_sheet": [169],
                                         "equity": [171, 172], "cash_flow": [173]}


def test_infosys_tax_line_cites_note_2_18(infosys):
    line = _line(infosys, "C-PL", "Income tax expense")
    assert line["note_ref"] == "2.18"
    assert [(v["raw"], v["period_label"]) for v in infosys.values(line["id"])][:2] == [("1,190", "FY2026"),
                                                                                      ("1,285", "FY2025")]
    assert ("C2.18", "references") in {(e["dst"], e["kind"]) for e in infosys.edges(src=line["id"])}
    balance = [c for c in infosys.checks() if c["kind"] == "balance"]
    assert balance and all(c["status"] == "ok" for c in balance)


# ---------------------------------------------------------------- BHP

def test_bhp_model(bhp):
    assert (bhp.meta["model"], bhp.meta["profile"]) == ("TYPE-8-AUSTRALIA-20-F-2025-BHP", "au-20f-ifrs")
    assert bhp.meta["units"] == "US$M"


def test_bhp_notes(bhp):
    section = bhp.index["sections"]["consolidated"]
    notes = section["notes"]
    assert [n["no"] for n in notes] == list(range(1, 38))
    taxes = notes[5]
    assert (taxes["title"], taxes["start_page"], taxes["end_page"]) == ("Income tax expense", 303, 305)
    # a note's "Cash flow statement" table (p.295) does not end the notes; the auditor's report (p.359) does
    assert (notes[3]["start_page"], notes[3]["end_page"]) == (294, 301)
    assert (section["first_page"], section["last_page"]) == (288, 358)


def test_bhp_numbered_statement_titles(bhp):
    assert _statement_pages(bhp) == {"profit_loss": [279, 280], "balance_sheet": [281],
                                     "cash_flow": [282], "equity": [283]}
    titles = [s["title"] for s in bhp.index["sections"]["consolidated"]["statements"]]
    assert titles[0] == "Consolidated Income Statement for the year ended 30 June 2026"


def test_bhp_tax_line(bhp):
    line = _line(bhp, "C-PL", "Total taxation expense")
    assert line["note_ref"] == "6"
    # the unit row "US$M" under the years keeps the years as the column headings
    assert [(v["raw"], v["period_label"]) for v in bhp.values(line["id"])] == [
        ("(9,388)", "FY2026"), ("(7,210)", "FY2025"), ("(6,447)", "FY2024")]
    assert ("C6", "references") in {(e["dst"], e["kind"]) for e in bhp.edges(src=line["id"])}


# ---------------------------------------------------------------- the Model testing screen

@pytest.mark.parametrize("pdf, page, unit", [(INFOSYS, 238, "C2.18"), (BHP, 303, "C6")])
def test_model_testing_pages(pdf, page, unit):
    if not pdf.exists():
        pytest.skip(f"Model document not found: {pdf}")
    result = model_testing.extract_page(pdf, page)
    assert result["extracted"]["source"] == "package" and result["notices"] == []
    assert unit in [u["id"] for u in result["extracted"]["units_on_page"] if u["starts_here"]]
