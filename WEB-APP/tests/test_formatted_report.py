"""The formatted document (INFO/MODEL-FORMATTED-REPORTS-PLAN.md §9): the text
parser, the whole-report document built from the package, the routes, and the
links from the Model testing screen."""

import dataclasses
import re
import time

import pytest

import app as app_module
import config
from ingest import formatted_report, model_testing, pipeline
from ingest.formatted_report import format_unit_text

TYPE1 = "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf"
TYPE3 = "TYPE-3-USA-10-K-2025-Chubb.pdf"
TYPE5 = "TYPE-5-USA-Ann-rpt-1968-Berkshire.pdf"
TYPE7 = "TYPE-7-INDIA-20-F-2025-Infosys.pdf"


def _need(name):
    if not (config.MODEL_DOCS_DIR / name).exists():
        pytest.skip(f"Model document not found: {name}")
    return model_testing.resolve_model_doc(name)


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(formatted_report, "CACHE", formatted_report.HtmlCache())


def _html(client, name, **args):
    response = client.get("/model-testing/formatted", query_string={"doc": name, **args})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    if "Preparing the formatted document" in html:       # package out of date: prepare, as the page does
        pipeline.open_report(config.MODEL_DOCS_DIR / name)
        html = client.get("/model-testing/formatted", query_string={"doc": name, **args}).get_data(as_text=True)
    return html


def _tables(blocks):
    return [b for b in blocks if b["t"] == "table"]


# ---------------------------------------------------------------- the parser (no PDF)

NOTE = """=== Note 21 — INCOME AND DEFERRED TAXES (Consolidated) ===
--- PDF page 405 (printed page 402) ---
21. INCOME AND DEFERRED TAXES
The major components of income tax expense for the year ended March 31, 2026 and
March 31, 2025 are:
In ₹Million
Particulars | Year ended | Year ended
March 31, 2026 | March 31, 2025
Current income tax:
Current income tax charge | 5,701.41 | 5,994.80
Tax for prior years | (95.34) | (146.26)
Exceptional items not deductible | 210.20 | -
Total income tax expense | 5,777.74 | 5,425.50
--- PDF page 406 (printed page 403) ---
DEFERRED TAX
Rate of 25.168% | 25.168% | 25.168%"""


def test_parser_markers_heading_caption_and_paragraph():
    blocks = format_unit_text(NOTE, "INCOME AND DEFERRED TAXES")
    assert blocks[0] == {"t": "marker", "page": 405, "printed": "402"}
    assert not any(b["t"] == "h" and "INCOME AND DEFERRED" in b["text"] for b in blocks)   # own heading dropped
    assert blocks[1]["t"] == "p" and blocks[1]["text"].endswith("March 31, 2025 are:")    # wrapped lines joined
    assert {"t": "caption", "text": "In ₹Million"} in blocks
    assert {"t": "marker", "page": 406, "printed": "403"} in blocks
    assert {"t": "h", "text": "DEFERRED TAX"} in blocks


def test_parser_table_heading_rows_figures_and_totals():
    table = _tables(format_unit_text(NOTE, "INCOME AND DEFERRED TAXES"))[0]
    assert table["width"] == 3
    assert [c["text"] for c in table["head"][0]] == ["Particulars", "Year ended", "Year ended"]
    # the short heading row sits over the figure columns, not under the label
    assert [c["text"] for c in table["head"][1]] == ["", "March 31, 2026", "March 31, 2025"]
    kinds = [r["kind"] for r in table["body"]]
    assert kinds == ["section", "data", "data", "data", "total"]
    assert table["body"][0]["span"] == 3 and table["body"][0]["cells"][0]["text"] == "Current income tax:"
    charge = table["body"][1]["cells"]
    assert [(c["text"], c["num"]) for c in charge] == [("Current income tax charge", False),
                                                       ("5,701.41", True), ("5,994.80", True)]
    assert table["body"][2]["cells"][1]["num"]                       # (95.34)
    assert table["body"][3]["cells"][2] == {"text": "-", "num": True}


@pytest.mark.parametrize("text", ["5,701.41", "(95.34)", "-", "–", "$10,622", "$ 1,705,000", "25.168%",
                                  "1,051,687(d)", "Rs. 35,00,000", "US$ 44", "(1,828)"])
def test_figures(text):
    assert formatted_report._is_figure(text)


@pytest.mark.parametrize("text", ["March 31, 2026", "Note 21", "Year ended", "i)", "Rs."])
def test_not_figures(text):
    assert not formatted_report._is_figure(text)


def test_parser_markdown_tables_and_headings():
    """Transcribed (scanned) reports store Markdown."""
    text = ("### (2) Marketable Securities\n\nIntro line.\n\n"
            "| Name | Jurisdiction |\n|---|---|\n| National Indemnity Company | Nebraska |\n\n"
            "### Item 10. Financial Statements\n")
    blocks = format_unit_text(text, "Marketable Securities")
    assert blocks[0] == {"t": "p", "text": "Intro line."}
    table = _tables(blocks)[0]
    assert [c["text"] for c in table["head"][0]] == ["Name", "Jurisdiction"]
    assert [c["text"] for c in table["body"][0]["cells"]] == ["National Indemnity Company", "Nebraska"]
    assert {"t": "h", "text": "Item 10. Financial Statements"} in blocks


def test_parser_ragged_rows_and_lone_figures():
    text = "Common stock | Cost\nCoca-Cola | 1,298,888\n3,851,112 | 5,150,000 | 9,000\n$4,296,122"
    table = _tables(format_unit_text(text))[0]
    assert table["width"] == 3
    rows = [[c["text"] for c in r["cells"]] for r in table["body"]]
    assert rows == [["Coca-Cola", "", "1,298,888"], ["3,851,112", "5,150,000", "9,000"], ["", "", "$4,296,122"]]


def test_parser_never_drops_words():
    """Display only: every word of the stored text (except the title and markers) is still there."""
    blocks = format_unit_text(NOTE)          # no title given: the heading line stays
    shown = []
    for b in blocks:
        if b["t"] == "table":
            shown += [c["text"] for row in b["head"] for c in row]
            shown += [c["text"] for row in b["body"] for c in row["cells"]]
        elif "text" in b:
            shown.append(b["text"])
    words = " ".join(shown).split()
    for line in NOTE.splitlines()[2:]:
        if not line.startswith("---"):
            for word in line.replace(" | ", " ").split():
                assert word in words


# ---------------------------------------------------------------- the whole document

def test_type1_document(client):
    _need(TYPE1)
    html = _html(client, TYPE1)
    # every PDF page has exactly one anchor
    anchors = re.findall(r'id="page-(\d+)"', html)
    assert sorted(map(int, anchors)) == list(range(1, 509))
    # Note 21 (PDF 405-407) with its table figures right-aligned
    note = html[html.index('id="unit-C21"'):]
    note = note[:note.index("</article>")]
    assert "Note 21 — " in note and "PDF 405–407" in note and "(printed 402-404)" in note
    assert '<td class="num">5,701.41</td>' in note
    # one card per note unit (115) and per statement (8)
    assert len(re.findall(r'<article class="doc-card unit"', html)) == 115
    assert len(re.findall(r'<article class="doc-card statement"', html)) == 8
    # standalone before consolidated, statements before their notes
    assert html.index('id="sec-standalone"') < html.index('id="unit-S-BS"') < html.index('id="unit-S1"') \
        < html.index('id="sec-consolidated"') < html.index('id="unit-C-BS"') < html.index('id="unit-C1"')
    # the P&L's current tax, and the rotated equity statement
    pl = html[html.index('id="unit-C-PL"'):]
    pl = pl[:pl.index("</article>")]
    assert "Current tax" in pl and "5,606.07" in pl
    eq = html[html.index('id="unit-C-EQ"'):]
    assert "rotated" in eq[:eq.index("</header>")]
    assert "Test this page ↗" in html and "/model-testing?doc=TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf&amp;page=405" in html


def test_type1_links_between_statements_and_notes(client):
    _need(TYPE1)
    html = _html(client, TYPE1)
    row = re.search(r'<tr class="data" id="(line-C-BS-L\d+)">\s*<td class="label">\(c\) Deferred tax liabilities \(net\)</td>'
                    r'\s*<td class="note"><a href="#unit-C21">21</a></td>', html)
    assert row, "the BS line citing note 21 links to it"
    note = html[html.index('id="unit-C21"'):]
    linked = note[:note.index('<div class="doc-body">')]
    assert "Linked from" in linked and f'href="#{row.group(1)}"' in linked
    # the checks appendix, problems first
    checks = html[html.index('id="checks"'):]
    assert checks.index(">warn<") < checks.index(">ok<")


def test_other_pages_are_collapsed_and_labelled(client):
    """A package without narrative pages (the scanned model, imported here without its transcription
    cache, so never rebuilt) keeps the collapsed runs of other pages."""
    _need(TYPE5)
    html = _html(client, TYPE5)
    if "not been transcribed" in html:
        pytest.skip("TYPE-5 is not transcribed on this PC")
    if 'class="doc-card narrative"' in html:
        pytest.skip("TYPE-5 was rebuilt with its narrative pages on this PC")
    assert re.search(r'<section class="doc-card pages-run" id="pages-19-19">\s*<details>', html)
    assert "not part of any note or statement" in html


def test_infosys_items_are_report_sections(client):
    _need(TYPE7)
    html = _html(client, TYPE7)
    assert 'class="doc-card pages-run"' not in html
    assert '<h3>Item 5. Operating and Financial Review and Prospects</h3>' in html


def test_narrative_pages_are_report_sections(client):
    """TYPE-1: the pages outside notes and statements are shown by report section, from their blocks
    (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §6.1)."""
    _need(TYPE1)
    html = _html(client, TYPE1)
    assert 'class="doc-card pages-run"' not in html
    card = html[html.index('id="nsec-N-VALUE-CREATION-MODEL-21"'):]
    card = card[:card.index("</article>")]
    assert "<h3>Value Creation Model</h3>" in card and "Corporate Overview ›" in card
    assert '<div class="kpi-value">238,355 tons</div>' in card and '<div class="kpi-label">Total tonnage</div>' in card
    assert '<li>S1 Focusing on the Indian market</li>' in card
    assert "/api/model-testing/page-image?doc=TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf&amp;page=22&amp;clip=" in card
    assert 'id="page-22"' in card
    assert html.index('id="nsec-N-BOARD-S-REPORT-87"') < html.index('id="sec-standalone"')
    assert 'title="Statutory Reports › Board’s Report (PDF 87–115)"' in html
    # the stored page text is still there to compare with
    text = client.get("/api/model-testing/formatted/stored-text",
                      query_string={"doc": TYPE1, "pages": "22-22"}).get_json()["text"]
    assert text.startswith("--- PDF page 22 (printed page 19) ---\nCORPORATE OVERVIEW")


def test_page_image_crop(client):
    _need(TYPE1)
    whole = client.get("/api/model-testing/page-image", query_string={"doc": TYPE1, "page": 22})
    crop = client.get("/api/model-testing/page-image", query_string={"doc": TYPE1, "page": 22, "clip": "258,370,543,514"})
    assert crop.status_code == 200 and crop.mimetype == "image/png" and len(crop.data) < len(whole.data)
    bad = client.get("/api/model-testing/page-image", query_string={"doc": TYPE1, "page": 22, "clip": "9,9,1,1"})
    assert bad.status_code == 400


def test_type3_chubb(client):
    _need(TYPE3)
    html = _html(client, TYPE3)
    assert len(re.findall(r'<article class="doc-card unit"', html)) == 22
    assert sorted(map(int, re.findall(r'id="page-(\d+)"', html))) == list(range(1, 461))
    assert 'id="unit-C12"' in html


def test_type5_scanned_ocr(client):
    _need(TYPE5)
    html = _html(client, TYPE5)
    if "not been transcribed" in html:
        pytest.skip("TYPE-5 is not transcribed on this PC")
    assert "OCR transcription" in html
    for section in ("berkshire-hathaway", "national-indemnity", "national-fire-marine"):
        assert f'id="sec-{section}"' in html


def test_decimal_note_ids_have_valid_anchors(client):
    _need(TYPE7)
    html = _html(client, TYPE7)
    assert 'id="unit-C2_1"' in html and 'href="#unit-C2_1"' in html
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[A-Za-z][\w-]*", i) for i in ids)


def test_package_text_is_escaped():
    """Package text is displayed, never interpreted as HTML."""
    document = formatted_report.build_raw_document("x.pdf", [(1, "<script>alert(1)</script> & co")], "raw")
    with app_module.app.test_request_context():
        from ingest.model_test_routes import _render
        html = _render(document)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in html


# ---------------------------------------------------------------- routes

def test_unknown_or_traversal_document_is_404(client):
    for name in ("", "nope.pdf", "..\\x.pdf", "_candidates/x.pdf"):
        assert client.get("/model-testing/formatted", query_string={"doc": name}).status_code == 404


def test_download_and_cache(client, monkeypatch):
    _need(TYPE1)
    first = _html(client, TYPE1)
    calls = []
    monkeypatch.setattr(formatted_report, "build_document", lambda *a: calls.append(a))
    response = client.get("/model-testing/formatted", query_string={"doc": TYPE1, "download": "1"})
    assert response.headers["Content-Disposition"] == \
        'attachment; filename="TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-formatted.html"'
    assert response.get_data(as_text=True) == first and not calls        # served from the cache


def test_stale_package_shows_preparing_page_then_document(client, monkeypatch):
    _need(TYPE1)
    monkeypatch.setattr(pipeline, "package_status", lambda path: {"status": "stale"})
    html = client.get("/model-testing/formatted", query_string={"doc": TYPE1}).get_data(as_text=True)
    assert "Preparing the formatted document" in html and "/api/model-testing/formatted/prepare" in html
    job = client.post("/api/model-testing/formatted/prepare", json={"doc": TYPE1}).get_json()["job_id"]
    for _ in range(600):
        state = client.get(f"/api/jobs/{job}").get_json()
        if state["status"] != "running":
            break
        time.sleep(0.1)
    assert state["status"] == "done" and state["result"] == {"ready": True}
    html = client.get("/model-testing/formatted", query_string={"doc": TYPE1, "prepared": "1"}).get_data(as_text=True)
    assert 'id="unit-C21"' in html


def test_pending_model_shows_raw_text(client, monkeypatch):
    _need(TYPE7)
    from ingest.models import registry
    model = registry.load().by_id("TYPE-7")
    real = model_testing.registry_model
    monkeypatch.setattr(model_testing, "registry_model",
                        lambda path: dataclasses.replace(model, profile=None) if path.name == TYPE7 else real(path))
    html = _html(client, TYPE7)
    assert "No extraction rules for TYPE-7" in html and "raw PDF text" in html
    assert '<pre class="raw-text">' in html
    assert sorted(map(int, re.findall(r'id="page-(\d+)"', html))) == list(range(1, 257))
    assert 'id="unit-' not in html


def test_stored_text(client):
    _need(TYPE1)
    data = client.get("/api/model-testing/formatted/stored-text",
                      query_string={"doc": TYPE1, "section": "consolidated", "unit": "C21"}).get_json()
    assert data["text"].startswith("=== Note 21")
    missing = client.get("/api/model-testing/formatted/stored-text",
                         query_string={"doc": TYPE1, "section": "consolidated", "unit": "C999"})
    assert missing.status_code == 404


def test_model_testing_screen_has_the_links(client):
    html = client.get("/model-testing").get_data(as_text=True)
    assert 'id="formatted-link"' in html and "View formatted document" in html
    assert 'id="formatted-page-link"' in html
    js = client.get("/static/model_testing.js").get_data(as_text=True)
    assert "/model-testing/formatted?" in js and "prefillFromUrl" in js
