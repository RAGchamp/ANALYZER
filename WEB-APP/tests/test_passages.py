"""Passages - the pieces of the report's sections a question can use - and how they are found
(INFO/ANALYZE-NON-FIN-DATA-PLAN.md §3, §4, §8, §11): building them at ingest, the ranking on the
hand-checked question set, the money figures and the unit-converting ties."""

import json
from pathlib import Path

import pytest

import config
from analyzer import linked, passages
from ingest import pipeline
from ingest.narrative import passages as build

QUESTIONS = json.loads((Path(__file__).parent / "fixtures" / "passage_questions.json").read_text(encoding="utf-8"))
TYPE1 = "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf"
MODELS = [TYPE1, "TYPE-3-USA-10-K-2025-Chubb.pdf", "TYPE-7-INDIA-20-F-2025-Infosys.pdf",
          "TYPE-8-AUSTRALIA-20-F-2025-BHP.pdf", "TYPE-6-CANADA-40-F-2025-Magna.pdf",
          "TYPE-2-INDIA-Ann-rpt-1977-Reliance.pdf", "TYPE-4-USA-Ann-rpt-1994-Berkshire.pdf"]


def _package(name):
    path = config.MODEL_DOCS_DIR / name
    if not path.exists():
        pytest.skip(f"Model document not found: {name}")
    return pipeline.open_report(path)


# ---------------------------------------------------------------- building passages (no PDF)

def _page(*blocks):
    return {"section_id": "N-MDA", "blocks": list(blocks)}


def _h(text, level=3):
    return {"kind": "heading", "level": level, "text": text, "bbox": [0, 0, 0, 0]}


def _p(text):
    return {"kind": "paragraph", "text": text, "bbox": [0, 0, 0, 0]}


SECTIONS = [{"id": "N-REPORTS", "parent_id": None, "title": "Statutory Reports", "kind": "other"},
            {"id": "N-MDA", "parent_id": "N-REPORTS", "title": "Management Discussion & Analysis", "kind": "mdna"}]


def test_passages_follow_headings_merge_small_split_large():
    long = "Sentence of the business review. " * 400                       # ~13 K characters
    pages = {75: _page(_h("Management Discussion & Analysis (MD&A)", 1), _h("Indian Economy"), _p("x" * 2000),
                       _h("Outlook"), _p("y" * 300)),
             76: _page(_h("tensions, shifting trade dynamics, and relatively softer", 2), _p("z" * 800),
                       _h("Commercial Vehicles (CV)"), _p("The CV export business ... ₹1,324 crore was lower 34%. " * 30)),
             77: _page(_h("Aerospace Business"), *[_p(s) for s in long.split(". ") if s])}
    out = build.build(SECTIONS, pages, {75: "72", 76: "73", 77: "74"})
    paths = [p["path"] for p in out]
    # the section title and a pull quote typed as a heading do not name passages
    assert paths[0] == "Statutory Reports › Management Discussion & Analysis › Indian Economy"
    # "Outlook" (small) is merged into the next passage, named after both headings
    assert paths[1].endswith("› Outlook · Commercial Vehicles (CV)")
    # a long passage is split, and its parts say so
    assert any(p.endswith("Aerospace Business (part 2)") for p in paths)
    # the limit counts the text; the stored size also counts page markers and blank lines between blocks
    assert all(p["chars"] <= 1.25 * build.MAX_CHARS for p in out)
    # every block is in exactly one passage
    blocks = [tuple(b) for p in out for b in p["blocks"]]
    assert len(blocks) == len(set(blocks)) == sum(len(pg["blocks"]) for pg in pages.values())
    assert "--- PDF page 76 (printed page 73) ---" in out[1]["text"]
    assert out[1]["figures"][0]["raw"] == "₹1,324 crore" and out[1]["figures"][0]["amount"] == 1324e7


@pytest.mark.parametrize("text, amount, currency", [
    ("revenue of ₹16,812 crore", 16812e7, "INR"), ("US$1.2 billion", 1.2e9, "USD"),
    ("$10,622 million", 10622e6, "USD"), ("Rs. 35,00,000", 3.5e6, "INR"), ("₹12.2 lakh crore", 12.2e12, "INR"),
])
def test_money_figures(text, amount, currency):
    figures = build.money_figures(text)
    assert figures and figures[0]["currency"] == currency
    assert figures[0]["amount"] == pytest.approx(amount)


def test_small_amounts_are_not_figures():
    assert build.money_figures("a face value of ₹2 each and $5 par value") == []


# ---------------------------------------------------------------- passages of the model documents

@pytest.mark.parametrize("name", MODELS)
def test_every_narrative_block_is_in_one_passage(name):
    package = _package(name)
    stored = [package.passage(p["id"]) for p in package.passages()]
    assert stored, "the report's sections are cut into passages"
    # the passages of narrative units (U-…) are cut from unit pages, whose blocks are not stored
    blocks = [tuple(b) for p in stored if not p["section_id"].startswith("U-") for b in p["blocks"]]
    assert len(blocks) == len(set(blocks))
    all_blocks = {(pg, s) for pg, bl in package.page_blocks().items()
                  if package.narrative_pages()[pg]["section_id"] for s in range(len(bl))}
    assert set(blocks) == all_blocks
    assert all(p["chars"] <= 12_000 for p in stored)
    assert not any(ch < " " for p in stored for ch in p["path"])


def test_type1_cv_passage():
    package = _package(TYPE1)
    cv = next(p for p in package.passages() if "Commercial Vehicles (CV)" in p["path"])
    assert cv["start_page"] == 76 and cv["kind"] == "mdna"
    text = package.passage(cv["id"])["text"]
    assert "For the Full Year revenue at ₹1,324 crore was lower 34%" in text
    lines = package.meta["quality"]["lines_text"]
    assert any(ln.startswith("Narrative passages: ") for ln in lines)


# ---------------------------------------------------------------- the ranking (plan §4, §11)

@pytest.mark.parametrize("q", QUESTIONS["questions"], ids=lambda q: q["question"][:40])
def test_question_set_recall_at_30(q):
    package = _package(q["doc"])
    found = passages.candidates(package, q["question"])
    text, page = q["must_find"]
    assert any(text in c["path"] and c["start_page"] == page for c in found), [c["path"] for c in found[:5]]


def test_question_set_keyword_recall_at_4():
    hits = total = 0
    for q in QUESTIONS["questions"]:
        path = config.MODEL_DOCS_DIR / q["doc"]
        if not path.exists():
            continue
        found = passages.candidates(pipeline.open_report(path), q["question"])[:4]
        text, page = q["must_find"]
        total += 1
        hits += any(text in c["path"] and c["start_page"] == page for c in found)
    if not total:
        pytest.skip("no model documents")
    assert hits / total >= 0.8, f"{hits}/{total}"


def test_synonyms_and_named_sections():
    terms = passages.query_terms("How is the CV business doing?")
    assert terms["cv"] == 1.0 and terms["commercial"] == passages.SYNONYM_WEIGHT
    assert passages.named_kinds("What does the MD&A say about exports?") == {"mdna"}
    assert passages.named_kinds("Summarise the risk factors") == {"risk"}
    assert passages.named_kinds("What does the Board's report say?") == {"board_report"}
    assert passages.named_kinds("Analyze the tax liabilities") == set()


def test_candidates_are_diverse_and_labelled():
    package = _package(TYPE1)
    found = passages.candidates(package, "outlook for the business")
    per_section = {}
    for c in found:
        per_section[c["section_id"]] = per_section.get(c["section_id"], 0) + 1
    assert max(per_section.values()) <= passages.MAX_PER_SECTION
    assert {"id", "path", "pages", "chars", "lead", "score"} <= set(found[0])


def test_narrative_units_are_passages_too():
    """Old and scanned reports keep their prose as "narrative" units on the notes side (Directors'
    Report, a 10-K's filing text); "Analyze non notes" must still find it."""
    package = _package("TYPE-2-INDIA-Ann-rpt-1977-Reliance.pdf")
    units = {p["section_id"]: p for p in package.passages() if p["section_id"].startswith("U-")}
    assert {"U-SUM", "U-DIR", "U-AUD"} <= set(units)
    assert units["U-DIR"]["kind"] == "board_report" and units["U-AUD"]["kind"] == "auditor_report"
    assert not package.narrative_pages().keys() & {5, 6, 7, 8}      # the unit pages stay units
    found = passages.candidates(package, "what did the directors say about the dividend")
    assert found[0]["section_id"] == "U-DIR"


def test_scanned_filing_text_is_a_passage():
    path = config.REPORTS_DIR / "BRK-1968.pdf"
    if not path.exists() or not (config.CACHE_DIR / "BRK-1968.ocr").exists():
        pytest.skip("BRK-1968 is not transcribed on this PC")
    package = pipeline.open_report(path)
    found = passages.candidates(package, "what percentage of shares was owned by Buffett partnership")
    assert found and found[0]["section_id"] == "U-BH-TXT"
    assert "69.99%" in package.passage(found[0]["id"])["text"]
    # a report of several companies names the company in each unit passage's path
    paths = [p["path"] for p in package.passages() if p["section_id"] == "U-NI-AUD"]
    assert paths and paths[0].startswith("National Indemnity Company › ")


def test_snippet_is_the_sentence_with_the_question_words():
    text = ("--- PDF page 2 ---\n\nThe Registrant has no parent. (a) As of April 7, 1969 Buffett Partnership, Ltd., "
            "a limited partnership, owned approximately 69.99% of Registrant's outstanding shares. Mr. Buffett is "
            "the sole general partner.")
    snip, words = passages.snippet(text, passages.query_terms("what percentage of shares was owned by Buffett partnership"))
    assert snip.startswith("(a) As of April 7, 1969") and snip.endswith("outstanding shares.")
    assert {"Buffett", "owned", "shares"} <= set(words)
    assert passages.snippet(text, passages.query_terms("aerospace tariffs")) == ("", [])


def test_candidates_carry_their_snippet():
    found = passages.candidates(_package(TYPE1), "How did the commercial vehicle export business perform?")
    assert found[0]["snippet"] and found[0]["matched"]
    assert all(w.lower() in found[0]["snippet"].lower() for w in found[0]["matched"])


def test_reports_without_passages_have_no_candidates(monkeypatch):
    package = _package(TYPE1)
    monkeypatch.setattr(type(package), "passages", lambda self: [])
    monkeypatch.setattr(type(package), "passage_texts", lambda self: {})
    passages._cache.clear()
    try:
        assert passages.candidates(package, "anything") == []
    finally:
        passages._cache.clear()


# ---------------------------------------------------------------- narrative figures (plan §8)

def test_narrative_figures_tie_and_no_tie():
    package = _package(TYPE1)
    cmd = [p for p in package.passages() if "CMD" in p["path"] and p["start_page"] <= 28 <= p["end_page"]]
    cv = [p for p in package.passages() if "Commercial Vehicles (CV)" in p["path"]]
    block = linked.narrative_figures_block(package, passages.by_ids(package, [p["id"] for p in cmd + cv]),
                                           ["consolidated"])
    assert "INR million" in block
    assert ('₹16,812 crore (From the CMD’s Desk, p.28) = 168,120 million -> ties to Consolidated Statement of '
            'Profit and Loss "Revenue from operations" 168,116.53 FY2026 (p.342)') in block
    assert "₹1,324 crore (Management Discussion & Analysis, p.76) = 13,240 million -> no statement line" in block


def test_tie_tolerance_follows_the_figures_rounding():
    # "₹1,324 crore" is ±0.5 crore = ±5 million: 13,193.66 (0.35 % away) is not a tie
    fig = {"raw": "₹1,324 crore", "amount": 1324e7}
    assert linked._tolerance(fig, 1e6) == pytest.approx(5.0)
    assert linked._tolerance({"raw": "$10,622", "amount": 10622e6}, 1e6) == 0.5


def test_statement_units():
    package = _package(TYPE1)
    assert linked.statement_unit(package) == ("INR", 1e6)
    chubb = _package("TYPE-3-USA-10-K-2025-Chubb.pdf")
    assert linked.statement_unit(chubb) == ("USD", 1e6)
