"""The "View the financial statements" page: tables rebuilt by position."""

import pytest

import app as app_module
import statements_view

REPORT = "Bharat-Forge-IR-2026-conv-single-page.pdf"

CONSOLIDATED_EQUITY_HEADINGS = [
    "Securities premium", "Capital reserves", "Employee stock option outstanding",
    "General reserve", "Retained Earnings", "Foreign currency translation reserve (FCTR)",
    "Equity instruments through other comprehensive income", "Non-controlling interest reserve",
    "Cash flow hedge reserve", "Total attributable to the owners of the Company",
    "Non-controlling interests", "Total",
]


@pytest.fixture(scope="module")
def view(index):
    return statements_view.report_view(index)


def _statement(view, section, kind):
    sec = next(s for s in view if s["key"] == section)
    return next(st for st in sec["statements"] if st["id"] == kind)


def _tables(statement):
    return [t for p in statement["pages"] for t in p["tables"]]


def _rows(statement):
    return [r for t in _tables(statement) for r in t["rows"]]


def _row(statement, label, **match):
    return next(r for r in _rows(statement)
                if r.get("label") == label and all(r.get(k) == v for k, v in match.items()))


def test_consolidated_first_then_standalone(view):
    assert [s["key"] for s in view] == ["consolidated", "standalone"]
    assert [st["id"] for st in view[0]["statements"]] == ["profit_loss", "balance_sheet", "cash_flow", "equity"]


def test_every_statement_page_has_a_heading_row(view):
    for section in view:
        for st in section["statements"]:
            for page in st["pages"]:
                assert any(r["kind"] == "header" for t in page["tables"] for r in t["rows"]), \
                    (section["key"], st["id"], page["pdf_page"])


def test_profit_and_loss_columns_by_position(view):
    pl = _statement(view, "consolidated", "profit_loss")
    table = _tables(pl)[0]
    assert table["width"] == 3 and table["notes_col"] == 0
    header = next(r for r in table["rows"] if r["kind"] == "header")
    assert header["cells"] == ["Notes", "Year ended March 31, 2026", "Year ended March 31, 2025"]
    assert _row(pl, "Revenue from operations")["cells"] == ["24", "168,116.53", "151,228.03"]
    # no note number: the figures stay in the year columns
    assert _row(pl, "Purchase of stock in trade")["cells"] == ["", "788.71", "2,318.22"]
    # note-only row: the note sits in the Notes column
    assert any(r["label"] == "Income tax expense" and r["cells"] == ["21", "", ""] for r in _rows(pl))


def test_wrapped_labels_are_joined(view):
    pl = _statement(view, "consolidated", "profit_loss")
    labels = [r.get("label") for r in _rows(pl)]
    assert ("(Increase) in inventories of finished goods, work-in-progress, stock in trade, dies and scrap"
            in labels)
    assert "and scrap" not in labels


def test_balance_sheet_heading_and_totals(view):
    bs = _statement(view, "consolidated", "balance_sheet")
    header = next(r for r in _rows(bs) if r["kind"] == "header")
    assert header["cells"] == ["Notes", "As at March 31, 2026", "As at March 31, 2025"]
    assert _row(bs, "(c) Deferred tax liabilities (net)")["cells"] == ["21", "215.24", "1,198.28"]
    assert _row(bs, "Total assets")["kind"] == "total"
    labels = " ".join(r.get("label", "") for r in _rows(bs))
    assert "Chartered Accountants" not in labels  # signature block dropped


def test_equity_multiline_column_headings_rebuilt(view):
    eq = _statement(view, "consolidated", "equity")
    assert eq["rotated"]
    for page in eq["pages"]:
        part_b = page["tables"][-1]
        assert part_b["width"] == 12
        header = next(r for r in part_b["rows"] if r["kind"] == "header")
        assert header["label"] == "Particulars"
        assert header["cells"] == CONSOLIDATED_EQUITY_HEADINGS


def test_equity_group_headings_span_their_columns(view):
    eq = _statement(view, "consolidated", "equity")
    part_b = eq["pages"][0]["tables"][-1]
    groups = [[(s["span"], s["text"]) for s in r["segments"]] for r in part_b["rows"] if r["kind"] == "group"]
    assert [(9, "Attributable to the owners of the parent"), (3, "")] in groups
    assert [(5, "Reserves and Surplus (Refer note 16)"), (4, "Items of OCI (Refer note 16)"), (3, "")] in groups


def test_equity_figures_under_the_right_headings(view):
    eq = _statement(view, "consolidated", "equity")
    closing = _row(eq, "Balance as at March 31, 2026")
    by_heading = dict(zip(CONSOLIDATED_EQUITY_HEADINGS, closing["cells"]))
    assert by_heading["Retained Earnings"] == "68,659.35"
    assert by_heading["Cash flow hedge reserve"] == "(3,414.71)"
    assert by_heading["Total attributable to the owners of the Company"] == "94,842.13"
    assert by_heading["Non-controlling interests"] == "(230.16)"
    assert by_heading["Total"] == "94,611.97"


def test_equity_part_a_is_its_own_table(view):
    eq = _statement(view, "consolidated", "equity")
    part_a = eq["pages"][0]["tables"][1]
    assert part_a["rows"][0] == {"kind": "section", "label": "A. EQUITY SHARE CAPITAL:"}
    headers = [r for r in part_a["rows"] if r["kind"] == "header"]
    assert headers[1]["cells"] == ["Balance as on April 1, 2025", "Changes in equity share capital during the year",
                                   "", "Balance as on March 31, 2026"]
    assert _row(eq, "As at March 31, 2026")["cells"] == ["", "", "478,088,632", ""]


def test_equity_wrapped_labels(view):
    eq = _statement(view, "consolidated", "equity")
    labels = [r.get("label") for r in _rows(eq)]
    assert "- Other Comprehensive Income/(Loss)" in labels       # page 345
    assert "Income/(Loss)" not in labels and "(Loss)" not in labels
    st_eq = _statement(view, "standalone", "equity")
    assert "- Premium on allotment of equity shares (Refer note 15)" in [r.get("label") for r in _rows(st_eq)]


def test_standalone_equity_headings(view):
    eq = _statement(view, "standalone", "equity")
    part_b = eq["pages"][0]["tables"][-1]
    header = next(r for r in part_b["rows"] if r["kind"] == "header")
    assert header["cells"] == ["Securities premium", "Capital reserves", "General reserve", "Retained earnings",
                               "Equity Instruments through Other Comprehensive Income",
                               "Cash flow hedge reserve", "Total"]


def test_claude_gets_the_rebuilt_equity_headings(index):
    eq = next(s for s in index["sections"]["consolidated"]["statements"] if s["type"] == "equity")
    assert ("[Column headings, left to right: 1) Securities premium | 2) Capital reserves"
            in eq["text"])
    assert "10) Total attributable to the owners of the Company | 11) Non-controlling interests | 12) Total]" in eq["text"]
    # narrow statements don't get the extra line
    bs = next(s for s in index["sections"]["consolidated"]["statements"] if s["type"] == "balance_sheet")
    assert "[Column headings" not in bs["text"]


def test_route_renders_page(index):
    client = app_module.app.test_client()
    html = client.get(f"/statements?report={REPORT}").get_data(as_text=True)
    assert "Bharat Forge Limited — Financial Statements" in html
    assert html.index("Consolidated Financial Statements") < html.index("Standalone Financial Statements")
    assert 'id="consolidated-equity"' in html and 'id="standalone-balance_sheet"' in html
    assert "rotated 90° clockwise before extraction" in html
    assert '<td class="num">168,116.53</td>' in html
    assert 'colspan="5" class="grp">Reserves and Surplus (Refer note 16)</td>' in html
    assert ">Foreign currency translation reserve (FCTR)</td>" in html
    assert "RAG Champ" in html and "ANNUAL REPORT ANALYZER" in html
    assert client.get("/statements?report=..%5Csecret.pdf").status_code == 400


def test_home_page_has_link():
    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert "View the financial statements" in html
    assert 'target="_blank"' in html
