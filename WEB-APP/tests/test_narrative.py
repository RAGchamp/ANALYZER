"""Pages outside the notes and statements (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §9):
the layout rules on made-up pages, the section map, the hand-checked gold pages of TYPE-1 and
TYPE-3, the coverage invariant on every page, the package tables, and what the profiles switch."""

import json
from pathlib import Path

import pytest

import config
from ingest import model_testing, pipeline, profiles
from ingest.narrative import kinds, layout, render, sections

GOLD = json.loads((Path(__file__).parent / "fixtures" / "narrative_gold" / "gold.json").read_text(encoding="utf-8"))
TYPE1 = "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf"
TYPE3 = "TYPE-3-USA-10-K-2025-Chubb.pdf"
TYPE7 = "TYPE-7-INDIA-20-F-2025-Infosys.pdf"


def _package(name):
    path = config.MODEL_DOCS_DIR / name
    if not path.exists():
        pytest.skip(f"Model document not found: {name}")
    return pipeline.open_report(path)


# ---------------------------------------------------------------- made-up pages (no PDF)

def seg(t, x0, y0, x1, size=9.0, bold=False):
    return {"t": t, "x0": x0, "y0": y0, "x1": x1, "y1": y0 + size * 1.2, "size": size, "bold": bold,
            "mono": False, "color": 0}


def geo(segments, drawings=(), images=()):
    return {"width": 595.0, "height": 842.0, "segments": list(segments), "rotated": [],
            "drawings": list(drawings), "images": list(images)}


def _read(g, profile="in-indas-2020s"):
    with profiles.using(profiles.get(profile)):
        return layout.read_page(g)


def test_two_columns_are_read_one_after_the_other():
    """A full-width heading, then the left column top to bottom, then the right one -
    even where both columns have a paragraph break at the same height."""
    left = [seg("Left one starts here and", 50, 100, 280), seg("goes on in the left column.", 50, 111, 280),
            seg("Left two is a new paragraph", 50, 140, 280), seg("in the same left column.", 50, 151, 280)]
    right = [seg("Right one starts here and", 310, 100, 540), seg("goes on in the right column.", 310, 111, 540),
             seg("Right two is a new paragraph", 310, 140, 540), seg("in the same right column.", 310, 151, 540)]
    title = seg("A HEADING OVER BOTH COLUMNS", 50, 60, 400, size=14, bold=True)
    blocks = _read(geo([title] + right + left))
    texts = [render.block_text(b) for b in blocks]
    assert texts[0].endswith("A HEADING OVER BOTH COLUMNS") and blocks[0]["kind"] == "heading"
    assert [t[:9] for t in texts[1:]] == ["Left one ", "Left two ", "Right one", "Right two"]


def test_bullets_badges_metric_and_coverage():
    marks = [{"x0": 58.5, "y0": y, "x1": 58.5, "y1": y + 7, "fill": False, "stroke": True, "width": 2.0,
              "curves": 0, "items": 1, "kinds": "l"} for y in (201, 221)]
    g = geo([seg("Things we use", 50, 180, 200, bold=True),
             seg("First item of the list", 68, 200, 250), seg("Second item", 68, 220, 250),
             seg("S1", 50, 260, 60, size=8.5), seg("Focusing on India", 75, 259, 200),
             seg("S2 Scaling verticals", 50, 280, 200),
             seg("238,355 tons", 50, 330, 300, size=36), seg("Total tonnage", 50, 376, 120, size=8)], drawings=marks)
    blocks = _read(g)
    kinds_ = [b["kind"] for b in blocks]
    items = [i for b in blocks if b["kind"] == "list" for i in b["items"]]
    assert "First item of the list" in items and "Second item" in items
    assert "S1 Focusing on India" in items and "S2 Scaling verticals" in items
    metric = next(b for b in blocks if b["kind"] == "metric")
    assert metric["value"] == "238,355 tons" and metric["label"] == "Total tonnage"
    assert kinds_[0] == "heading"
    assert layout.coverage_ok(g["segments"], blocks)
    assert not layout.coverage_ok(g["segments"], blocks[1:])            # a lost block is caught


def test_aligned_table_with_heading_rows():
    rows = [("Particulars", "2026", "2025"), ("Revenue", "1,234.5", "1,100.0"), ("Other income", "(12.0)", "8.5"),
            ("Total income", "1,222.5", "1,108.5")]
    segs = []
    for i, (label, a, b) in enumerate(rows):
        y = 100 + 14 * i
        segs += [seg(label, 50, y, 150), seg(a, 400, y, 440), seg(b, 480, y, 520)]
    blocks = _read(geo(segs), "us-10k-edgar")
    table = next(b for b in blocks if b["kind"] == "table")
    assert table["head"] == [["Particulars", "2026", "2025"]]
    assert table["rows"][0] == ["Revenue", "1,234.5", "1,100.0"] and table["rows"][-1][0] == "Total income"


def test_contents_page_numbers_before_or_after_titles():
    first = geo([seg("Contents", 70, 100, 170, size=22)] + [
        s for i, (n, t) in enumerate([("06", "About the Report"), ("10", "Highlights"), ("22", "From the CMD’s Desk"),
                                      ("72", "Management Discussion & Analysis"), ("84", "Board’s Report")])
        for s in (seg(n, 77, 220 + 14 * i, 91), seg(t, 105, 221 + 14 * i, 250))]
        + [seg("38", 303, 262, 314)])                        # a divider's page number in the other column
    entries = sections.parse_contents(first)
    assert [(e["title"], e["printed"]) for e in entries] == [
        ("About the Report", "06"), ("Highlights", "10"), ("From the CMD’s Desk", "22"),
        ("Management Discussion & Analysis", "72"), ("Board’s Report", "84")]
    last = geo([seg("CHUBB LIMITED INDEX TO FORM 10-K", 18, 39, 150, bold=True), seg("PART I", 19, 76, 42, bold=True)]
               + [s for i, (item, title, n) in enumerate([("ITEM 1.", "Business", "2"), ("ITEM 1A.", "Risk Factors", "19"),
                                                          ("ITEM 2.", "Properties", "33"), ("ITEM 3.", "Legal", "33"),
                                                          ("ITEM 4.", "Mine Safety", "33")])
                  for s in (seg(item, 19, 86 + 10 * i, 44), seg(title, 67, 86 + 10 * i, 160, bold=True),
                            seg(n, 559, 86 + 10 * i, 567))])
    entries = sections.parse_contents(last)
    assert entries[0] == {"title": "ITEM 1. Business", "printed": "2", "group": "PART I"}
    assert len(entries) == 5


def test_printed_pages_to_pdf_pages():
    to_pdf = sections.printed_to_pdf({25: "22", 41: None, 90: "87"}, footers={22: "19", 60: "7"})
    assert to_pdf("22") == 25 and to_pdf("06") == 9 and to_pdf("84") == 87
    assert to_pdf("7") == 10          # a stray "7" at the foot of PDF 60 disagrees with the offset: ignored


def test_titles_and_kinds():
    assert sections.same_title("Board’s Report", "Board's Report")
    assert not sections.same_title("Governance", "Report on Corporate Governance")
    assert kinds.kind_of("Board’s Report") == "board_report"
    assert kinds.kind_of("ITEM 7. Management’s Discussion and Analysis") == "mdna"
    assert kinds.kind_of("ITEM 1A. Risk Factors") == "risk"
    assert kinds.kind_of("ITEM 15. Exhibits, Financial Statement Schedules") == "exhibits"
    assert kinds.kind_of("Something new") == "other"


# ---------------------------------------------------------------- the model documents

def _flat(blocks):
    """(kind, text) in reading order; panels give their children, lists their items."""
    out = []
    for b in blocks:
        if b["kind"] == "panel":
            out += _flat(b["children"])
        elif b["kind"] == "list":
            out += [("list", item) for item in b["items"]]
        else:
            out.append((b["kind"], render.block_text(b).lstrip("# ")))
    return out


@pytest.mark.parametrize("gold", GOLD["pages"], ids=lambda g: f"{g['doc'][:6]}-p{g['page']}")
def test_gold_reading_order(gold):
    package = _package(gold["doc"])
    flat = _flat(package.page_blocks(gold["page"]))
    at, offset = 0, -1                 # (block, character): items may follow each other inside one table
    for kind, text in gold["order"]:
        found = None
        for i in range(at, len(flat)):
            if kind != "*" and flat[i][0] != kind:
                continue
            pos = flat[i][1].find(text, offset + 1 if i == at else 0)
            if pos >= 0:
                found = (i, pos)
                break
        assert found is not None, f"p.{gold['page']}: {kind} {text!r} not found after block {at}: {flat}"
        at, offset = found
    info = package.narrative_pages()[gold["page"]]
    if gold.get("layout_kind"):
        assert info["layout_kind"] == gold["layout_kind"]
    section = next(s for s in package.doc_sections() if s["id"] == info["section_id"])
    assert section["title"] == gold["section"]


def test_type1_section_map():
    package = _package(TYPE1)
    leaves = {s["title"]: (s["start_page"], s["end_page"], s["kind"]) for s in package.doc_sections()
              if not any(c["parent_id"] == s["id"] for c in package.doc_sections())}
    assert leaves["About the Report"][:2] == (9, 12)
    assert leaves["Value Creation Model"] == (21, 24, "strategy")
    assert leaves["From the CMD’s Desk"][0] == 25
    assert leaves["Management Discussion & Analysis"] == (75, 86, "mdna")
    assert leaves["Board’s Report"] == (87, 115, "board_report")
    assert leaves["Report on Corporate Governance"][:2] == (116, 142)
    assert leaves["Business Responsibility and Sustainability Reporting"][:2] == (143, 188)
    assert leaves["Standalone Financial Statements"][0] == 189
    parents = {s["title"] for s in package.doc_sections() if s["level"] == 1}
    assert {"Corporate Overview", "Statutory Reports", "Financial Statements"} <= parents
    assert package.meta["quality"]["narrative"]["warnings"] == []


def test_type3_section_map():
    package = _package(TYPE3)
    by_title = {s["title"]: s for s in package.doc_sections()}
    item1, item7 = by_title["ITEM 1. Business"], by_title[
        "ITEM 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations"]
    assert (item1["start_page"], item1["end_page"], item1["source"]) == (4, 20, "contents")
    assert item1["confirmed_by"] == ["item_heading"]
    assert (item7["start_page"], item7["end_page"], item7["kind"]) == (38, 85, "mdna")
    assert by_title["ITEM 1A. Risk Factors"]["start_page"] == 21
    assert by_title["SIGNATURES"]["start_page"] == 100
    assert by_title["PART II"]["level"] == 1 and item7["parent_id"] == by_title["PART II"]["id"]


@pytest.mark.parametrize("name", [TYPE1, TYPE3, TYPE7, "TYPE-8-AUSTRALIA-20-F-2025-BHP.pdf",
                                  "TYPE-6-CANADA-40-F-2025-Magna.pdf", "TYPE-2-INDIA-Ann-rpt-1977-Reliance.pdf",
                                  "TYPE-4-USA-Ann-rpt-1994-Berkshire.pdf"])
def test_every_narrative_page_passes_the_coverage_check(name):
    """Nothing lost, nothing added: no page had to fall back to its flat text."""
    package = _package(name)
    facts = package.meta["quality"]["narrative"]
    assert facts["fallback_pages"] == []
    assert facts["pages"] == len(package.narrative_pages()) >= 4
    covered = {u["start_page"] for u in package.query("SELECT start_page FROM units")}
    assert not covered & set(package.narrative_pages())          # notes and statements are never re-read
    lines = package.meta["quality"]["lines_text"]
    assert any(ln.startswith("Narrative: ") for ln in lines)
    assert not any(ln.startswith("Warning:") and "arrative" in ln for ln in lines)


def test_section_text_for_claude():
    package = _package(TYPE1)
    text = package.section_text("N-VALUE-CREATION-MODEL")
    assert text.startswith("=== Report section — Value Creation Model ===")
    assert "--- PDF page 22 (printed page 19) ---" in text
    assert "238,355 tons — Total tonnage" in text and "[Diagram: " in text
    assert package.section_text("N-STATUTORY-REPORTS") is None            # parents have no text of their own


def test_pages_text_is_unchanged_by_the_narrative_reader():
    """Q3: the stored page text (analyze specific PDF pages) is still the row-by-row text."""
    package = _package(TYPE1)
    assert package.page(22)["text"].startswith("CORPORATE OVERVIEW\nValue Creation Model\nStrategy\nS1 Focusing")


def test_every_profile_reads_its_narrative_pages():
    """Phase 6: every format, each with the section sources that fit it."""
    sources = {pid: p.switches.narrative_sections for pid, p in profiles.PROFILES.items()}
    assert all(sources.values())
    assert sources["in-20f-ifrs"] == ("bookmarks", "contents", "form_items")
    assert "running_headers" not in sources["au-20f-ifrs"]           # BHP's are the browser's print lines
    assert sources["scanned-ocr"] == ("headings",)
    assert profiles.get("au-20f-ifrs").switches.narrative_designed_pages


# ---------------------------------------------------------------- Phase 6: the other formats (no PDF)

def test_bookmarks_give_parts_and_sections():
    toc = [[1, "Document", 1], [2, "Base", 2], [3, "Cover Page", 2], [3, "Part I", 4], [4, "Business", 4],
           [5, "Competition", 6], [4, "Properties", 9], [3, "Part II", 11], [4, "Management Discussion", 12],
           [3, "Signatures", 43]]
    entries = sections.bookmark_entries(toc)
    assert [(e["title"], e["start"], e["group"]) for e in entries] == [
        ("Cover Page", 2, None), ("Business", 4, "Part I"), ("Properties", 9, "Part I"),
        ("Management Discussion", 12, "Part II"), ("Signatures", 43, None)]


def test_numbered_contents_groups_restart_in_each_part():
    entries = [{"title": t, "printed": str(i), "group": None} for i, t in enumerate(
        ["1. Safety", "4. Our assets", "4.1 Copper", "4.2 Iron Ore", "Sustainability Report", "1. Introduction",
         "4. Risk management", "4.1 Approach to risk management"], 1)]
    grouped = sections._number_groups(entries)
    assert [(e["title"], e["group"]) for e in grouped] == [
        ("1. Safety", None), ("4.1 Copper", "4. Our assets"), ("4.2 Iron Ore", "4. Our assets"),
        ("Sustainability Report", None), ("1. Introduction", None),
        ("4.1 Approach to risk management", "4. Risk management")]


def test_dot_leader_contents_under_a_list_of_directors():
    g = geo([seg("BOARD OF DIRECTORS:", 50, 60, 200, bold=True), seg("REGISTERED OFFICE:", 50, 300, 200, bold=True),
             seg("CONTENTS", 300, 300, 360, bold=True)]
            + [seg(f"{t} {'.' * 30} {n}", 300, 320 + 14 * i, 540) for i, (t, n) in enumerate(
                [("Board of Directors", "1"), ("Financial Summary", "2-3"), ("Directors' Report", "5 - 8"),
                 ("Auditors' Report", "9-12"), ("Balance Sheet", "13")])]
            + [seg("COURT HOUSE, DHOBI TALAO,", 50, 320, 200)])
    entries = sections.parse_contents(g)
    assert [(e["title"], e["printed"]) for e in entries] == [
        ("Board of Directors", "1"), ("Financial Summary", "2-3"), ("Directors' Report", "5 - 8"),
        ("Auditors' Report", "9-12"), ("Balance Sheet", "13")]


def test_heading_sections_as_a_last_resort():
    body = [seg("Plain text of the report on this page.", 50, 200 + 12 * i, 500) for i in range(20)]
    geos = {1: geo([seg("Magna International Inc.", 50, 60, 200, size=10.8, bold=True)] + body),
            2: geo([seg("REPORT OF INDEPENDENT REGISTERED", 50, 60, 400, size=10.8, bold=True),
                    seg("PUBLIC ACCOUNTING FIRM", 50, 73, 300, size=10.8, bold=True)] + body),
            3: geo([seg("How the Critical Audit Matter Was Addressed", 50, 60, 300, bold=True)] + body)}
    entries = sections.heading_entries(geos, covered=set())
    assert [(e["title"], e["start"]) for e in entries] == [
        ("Magna International Inc", 1), ("REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM", 2)]


def test_typewriter_rows_are_split_into_cells():
    from ingest.narrative import geometry
    text = "Sales and service revenues . . .   $ 2,351,918   $ 1,962,862"
    span = {"text": text, "font": "Courier", "size": 7.2, "flags": 0, "color": 0,
            "bbox": (18.0, 100.0, 18.0 + 4.32 * len(text), 108.0)}          # 4.32 pt per character
    pieces = geometry._split_mono(span)
    assert [p["text"] for p in pieces] == ["Sales and service revenues . . .", "$ 2,351,918", "$ 1,962,862"]
    assert pieces[1]["bbox"][0] == pytest.approx(18.0 + 4.32 * 35)
    segs = geometry._line_segments({"spans": [span]})
    assert [s["t"] for s in segs] == ["Sales and service revenues . . .", "$ 2,351,918", "$ 1,962,862"]
    assert all(s["mono"] for s in segs)


def test_paragraphs_without_space_between_them():
    lines = [seg("First paragraph line one runs the full width of the column here", 50, 100, 500),
             seg("and it ends here.", 50, 111, 200),
             seg("Second paragraph starts on the next line and runs the full width too", 50, 122, 500),
             seg("and ends.", 50, 133, 120)]
    blocks = _read(geo(lines), "in-20f-ifrs")
    assert [b["text"][:6] for b in blocks] == ["First ", "Second"]


def test_scanned_pages_from_the_transcription():
    from ingest.narrative import markdown
    text = "### EXHIBIT 1\n\n### NATIONAL INDEMNITY COMPANY\n\nAccountants' Report\n\n| Year | Premiums |\n|---|---|\n| 1968 | 12,345 |\n\n- first\n- second\n\n24"
    blocks = markdown.blocks(text)
    assert [b["kind"] for b in blocks] == ["heading", "heading", "paragraph", "table", "list", "paragraph"]
    assert blocks[3]["head"] == [["Year", "Premiums"]] and blocks[3]["rows"] == [["1968", "12,345"]]
    assert markdown.coverage_ok(text, blocks) and not markdown.coverage_ok(text, blocks[:-1])
    entries = markdown.heading_entries({21: text, 19: "BLANK PAGE", 30: "EXHIBIT 2\n\nNATIONAL FIRE & MARINE"},
                                       covered=set())
    assert [(e["title"], e["start"]) for e in entries] == [
        ("EXHIBIT 1 NATIONAL INDEMNITY COMPANY", 21), ("EXHIBIT 2 NATIONAL FIRE & MARINE", 30)]


def test_scanned_report_read_from_its_package():
    """The scanned model's package can't be rebuilt without its transcription cache: the stage is run
    on its stored pages, as the pipeline does when it rebuilds one."""
    from ingest import narrative
    package = _package("TYPE-5-USA-Ann-rpt-1968-Berkshire.pdf")
    pages = {r["pdf_page"]: r for r in package.query("SELECT pdf_page, printed, text FROM pages")}
    with profiles.using(profiles.get("scanned-ocr")):
        result = narrative.build(package.index, None, pages)
    titles = [s["title"] for s in result["sections"]]
    assert "EXHIBIT 1 NATIONAL INDEMNITY COMPANY" in titles
    assert result["stats"]["fallback_pages"] == []
    assert result["pages"][21]["blocks"][0] == {"kind": "heading", "level": 2, "text": "EXHIBIT 1", "bbox": [0, 0, 0, 0]}


# ---------------------------------------------------------------- Phase 6: section maps

def _leaves(package):
    parents = {s["parent_id"] for s in package.doc_sections()}
    return {s["title"]: s for s in package.doc_sections() if s["id"] not in parents}


def test_type7_infosys_items():
    leaves = _leaves(_package(TYPE7))
    item5 = leaves["Item 5. Operating and Financial Review and Prospects"]
    assert (item5["start_page"], item5["end_page"], item5["kind"], item5["source"]) == (66, 90, "mdna", "item_heading")
    assert item5["confirmed_by"] == []                          # a source does not confirm itself
    assert leaves["Item 3. Key Information"]["kind"] == "highlights"       # by its words, not "Item 3"
    assert leaves["Item 15. Controls and Procedures"]["kind"] == "controls"


def test_type8_bhp_contents_over_two_pages():
    package = _package("TYPE-8-AUSTRALIA-20-F-2025-BHP.pdf")
    by_title = {s["title"]: s for s in package.doc_sections()}
    assert by_title["Chair's review"]["start_page"] == 13
    copper = by_title["4.1 Copper"]
    assert by_title[next(s["title"] for s in package.doc_sections() if s["id"] == copper["parent_id"])]["title"] \
        == "4. Our assets"
    assert (copper["start_page"], copper["end_page"]) == (26, 30)
    assert by_title["6.1 Risk Factors"]["start_page"] == 45
    assert "Corporate Governance Statement" in by_title and "Remuneration Report" in by_title   # from the 2nd page
    assert by_title["Exhibits"]["start_page"] == 275


def test_type4_berkshire_bookmarks():
    leaves = _leaves(_package("TYPE-4-USA-Ann-rpt-1994-Berkshire.pdf"))
    assert (leaves["Business"]["start_page"], leaves["Business"]["end_page"], leaves["Business"]["source"]) == \
        (4, 8, "bookmarks")
    assert leaves["Management Discussion"]["kind"] == "mdna"


def test_type6_magna_and_type2_reliance():
    magna = _leaves(_package("TYPE-6-CANADA-40-F-2025-Magna.pdf"))
    report = magna["REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM"]
    assert (report["start_page"], report["kind"], report["source"]) == (2, "auditor_report", "heading")
    reliance = _leaves(_package("TYPE-2-INDIA-Ann-rpt-1977-Reliance.pdf"))
    assert reliance["Directors' Report"]["kind"] == "board_report" and reliance["Directors' Report"]["source"] == "contents"


def test_model_testing_shows_the_blocks():
    path = config.MODEL_DOCS_DIR / TYPE1
    if not path.exists():
        pytest.skip("TYPE-1 not found")
    result = model_testing.extract_page(path, 22)
    n = result["extracted"]["narrative"]
    assert result["extracted"]["page_type"] == "narrative"
    assert n["section"]["title"] == "Value Creation Model" and n["layout_kind"] == "designed"
    assert n["header"] == ["CORPORATE OVERVIEW", "Value Creation Model"] and n["footer"] == ["19"]
    text = model_testing.format_extracted(result)
    assert "== Blocks in reading order" in text and "[metric] 238,355 tons — Total tonnage" in text
