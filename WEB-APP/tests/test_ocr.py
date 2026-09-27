"""Scanned reports (INFO/OCR-CAPABILITY-PLAN.md §10).

Claude is never called: transcriptions are the recorded ones from the real
run on Annual-reports/BRK-1968.pdf (tests/fixtures/brk1968.ocr), or a fake
transcriber.
"""

import json
import shutil
import threading
import time
from pathlib import Path

import pymupdf
import pytest

import app as app_module
import config
import extractor
import note_selector
import notes_index
import ocr_quality
import ocr_transcribe

FIXTURE_OCR = Path(__file__).parent / "fixtures" / "brk1968.ocr"
BRK = "BRK-1968.pdf"
SOURCE_PDF = config.REPORTS_DIR / BRK


# ---------------------------------------------------------------- parsing

def test_parse_reply():
    reply = ('```json\n{"page_type": "statement", "entity": "X Inc.", "headings": ["A"]}\n```\n'
             "### Balance Sheet\n| Cash | $ 1,2[?]0 |")
    header, md = ocr_transcribe.parse_reply(reply)
    assert header["page_type"] == "statement" and header["unreadable"] == 1
    assert md.startswith("### Balance Sheet")
    header, md = ocr_transcribe.parse_reply("just text, no JSON")
    assert header["page_type"] == "other" and md == "just text, no JSON"
    header, _ = ocr_transcribe.parse_reply('```json\n{"page_type": "weird"}\n```\nx')
    assert header["page_type"] == "other"


# ---------------------------------------------------------------- checks

P8 = """| | |
|---|---:|
| Net sales | $ 46,002,417 |
| Equity in earnings | 1,789,402 |
| Dividends | 223,024 |
| Interest | 131,558 |
| Interest - other | 34.456 |
| | 48,180,857 |
| Cost of sales | 41,321,747 |
| Other | 3,110,628 |
| Tax | 728,00[?] |
| | 45,160,375 |
| Earnings from operations | 3,020,482 |
"""


def test_tie_outs():
    result = ocr_quality.tie_outs(P8)
    assert [t["figure"] for t in result["tied"]] == ["48,180,857"]     # "34.456" read as 34,456
    assert [t["figure"] for t in result["unchecked"]] == ["45,160,375"]  # a figure above is unreadable
    assert result["untied"] == []
    assert "46002417" in result["confirmed"] and "48180857" in result["confirmed"]
    bad = ocr_quality.tie_outs(P8.replace("48,180,857", "48,180,858"))
    assert [t["figure"] for t in bad["untied"]] == ["48,180,858"]


def test_tie_outs_nil_dash_cross_column_and_labels():
    md = """| | | |
|---|---|---|
| Common stock | $ 5,087,735 | |
| Retained earnings | 31,694,706 | |
| | 36,782,441 | |
| Less treasury stock | 577,170 | |
| Total stockholders' equity | | 36,205,271 |
| Profit | 2,006,697 | |
| Other income | - | |
| | 2,006,697 | |
| Net income | 2,199,870 | |
"""
    result = ocr_quality.tie_outs(md)
    assert {t["figure"] for t in result["tied"]} >= {"36,782,441", "36,205,271", "2,006,697"}
    assert result["untied"] == []      # "Net income" is not expected to be a total


def test_figure_cross_check():
    claude = "Earnings 2,654,399 · Cash 1,605,600 · Total 48,180,857 · Year 1968 · Tax 728,000"
    tesseract = "Earnings 2 654,599 Cash 1,605,600 Tax 728, 00C"
    flagged = ocr_quality.figure_cross_check(claude, tesseract, confirmed={"48180857"})
    assert flagged == ["2,654,399", "728,000"]   # Tesseract misread both; the year 1968 is ignored


# ---------------------------------------------------------------- the transcription job

@pytest.fixture
def scanned_pdf(tmp_path, monkeypatch):
    """A 6-page image-only PDF and an isolated cache."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    config.CACHE_DIR.mkdir()
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    for _ in range(6):
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, pixmap=pix)
    path = tmp_path / "Scan.pdf"
    doc.save(path)
    return path


def fake_transcriber(calls, fail=()):
    def transcribe(pdf_path, pno, model=None, dpi=None):
        calls.append(pno)
        time.sleep(0.05)
        if pno in fail:
            raise ocr_transcribe.ClaudeError("boom")
        ocr_transcribe.page_file(pdf_path, pno, "md").write_text(f"### Page {pno}\n", encoding="utf-8")
        ocr_transcribe.page_file(pdf_path, pno, "json").write_text(
            json.dumps({"page_type": "notes", "unreadable": 0}), encoding="utf-8")
        return {"status": "done", "seconds": 1.0, "cost_usd": 0.01, "unreadable": 0, "page_type": "notes",
                "figures_to_check": [], "untied": []}
    return transcribe


def test_run_is_resumable_and_records_failures(scanned_pdf):
    with pymupdf.open(scanned_pdf) as doc:
        assert ocr_transcribe.is_scanned(doc)
    calls = []
    status = ocr_transcribe.run(scanned_pdf, pages=[1, 2, 3], transcriber=fake_transcriber(calls, fail={2}))
    assert sorted(calls) == [1, 2, 3]
    assert status["done"] == 2 and status["failed"] == [2] and status["complete"]
    calls.clear()
    status = ocr_transcribe.run(scanned_pdf, transcriber=fake_transcriber(calls))
    assert sorted(calls) == [2, 4, 5, 6]                  # done pages are skipped
    assert status["done"] == 6 and status["failed"] == [] and status["complete"]
    minutes, usd, todo = ocr_transcribe.estimate(scanned_pdf, range(1, 7))
    assert todo == 0 and usd == 0


def test_run_cancel(scanned_pdf, monkeypatch):
    monkeypatch.setattr(config, "OCR_WORKERS", 1)
    calls, stop = [], threading.Event()

    def progress(done, total, text):
        if done >= 2:
            stop.set()

    with pytest.raises(ocr_transcribe.Cancelled):
        ocr_transcribe.run(scanned_pdf, transcriber=fake_transcriber(calls), progress=progress,
                           cancel=stop.is_set)
    assert 2 <= ocr_transcribe.status(scanned_pdf)["done"] < 6


def test_prompt_version_change_resets(scanned_pdf, monkeypatch):
    ocr_transcribe.run(scanned_pdf, pages=[1], transcriber=fake_transcriber([]))
    assert ocr_transcribe.status(scanned_pdf)["done"] == 1
    monkeypatch.setattr(config, "OCR_PROMPT_VERSION", config.OCR_PROMPT_VERSION + 1)
    assert ocr_transcribe.status(scanned_pdf)["done"] == 0


def test_untranscribed_scan_needs_transcription(scanned_pdf):
    with pytest.raises(notes_index.NeedsTranscription) as info:
        notes_index.load_or_build(scanned_pdf)
    assert info.value.page_count == 6


# ---------------------------------------------------------------- BRK 1968, recorded transcriptions

@pytest.fixture
def brk(tmp_path, monkeypatch):
    if not SOURCE_PDF.exists():
        pytest.skip(f"Sample report not found: {SOURCE_PDF}")
    reports, cache = tmp_path / "reports", tmp_path / "cache"
    reports.mkdir()
    shutil.copy2(SOURCE_PDF, reports / BRK)
    shutil.copytree(FIXTURE_OCR, cache / "BRK-1968.ocr")
    for name, folder in (("REPORTS_DIR", reports), ("CACHE_DIR", cache), ("HISTORY_DIR", tmp_path / "hist"),
                         ("THREADS_DIR", tmp_path / "threads"), ("ANALYSIS_HISTORY_DIR", tmp_path / "html")):
        folder.mkdir(exist_ok=True)
        monkeypatch.setattr(config, name, folder)
    monkeypatch.setattr(config, "INPUT_FILE", tmp_path / "in.txt")
    monkeypatch.setattr(config, "OUTPUT_FILE", tmp_path / "out.txt")
    manifest_path = cache / "BRK-1968.ocr" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stat = (reports / BRK).stat()
    manifest.update(size=stat.st_size, mtime=stat.st_mtime)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return reports / BRK


def test_brk_index(brk):
    idx = notes_index.load_or_build(brk)
    assert idx["format"] == "transcribed" and idx["document_type"] == "SEC Form 10-K"
    assert idx["company"] == "Berkshire Hathaway Inc." and idx["currency"] == "$"
    assert idx["fiscal_year_end_label"] == "December 28, 1968"
    sections = idx["sections"]
    assert [s["code"] for s in sections.values()] == ["BH", "NI", "NFMI"]
    assert [s["label"] for s in sections.values()] == [
        "Berkshire Hathaway Inc. (consolidated)", "National Indemnity Company",
        "National Fire & Marine Insurance Company"]
    bh = sections["berkshire-hathaway"]
    assert {s["title"]: [p["pdf_page"] for p in s["pages"]] for s in bh["statements"]} == {
        "Consolidated Statement of Earnings and Retained Earnings": [8], "Consolidated Balance Sheet": [7]}
    kinds = {}
    for unit in bh["notes"]:
        kinds.setdefault(unit["kind"], []).append(unit["id"])
    assert kinds["note"] == [f"BH-N{n}" for n in range(1, 15)]
    assert kinds["schedule"] == ["BH-SCH-I", "BH-SCH-III", "BH-SCH-V", "BH-SCH-VI", "BH-SCH-IX",
                                 "BH-SCH-XII", "BH-SCH-XVII"]
    assert kinds["narrative"] == ["BH-TXT", "BH-AUD"]
    units = {u["id"]: u for s in sections.values() for u in s["notes"]}
    assert units["BH-N5"]["title"] == "Unconsolidated Subsidiaries"
    assert units["NI-N7"]["title"] == "Federal Income Taxes"
    assert len([u for u in sections["national-indemnity"]["notes"] if u["kind"] == "note"]) == 8
    assert len([u for u in sections["national-fire-marine"]["notes"] if u["kind"] == "note"]) == 5
    assert all(p["page_type"] == "blank" for n, p in idx["pages"].items() if n in ("19", "39"))
    assert idx["ocr"]["untied"] == 1       # NI p.23: 1968 liabilities add up to 28,288,582, not 28,288,632


def test_brk_extract_and_scope(brk):
    idx = notes_index.load_or_build(brk)
    e = extractor.extract_note(idx, "berkshire-hathaway", "BH-N5")
    assert e["text"].startswith("=== Berkshire Hathaway Inc. — Note 5 — Unconsolidated Subsidiaries ===")
    assert "--- PDF page 10 (printed page 11) ---" in e["text"] and "(6) Taxes" not in e["text"]
    sched = extractor.extract_note(idx, "berkshire-hathaway", "BH-SCH-V")["text"]
    assert "PDF page 15" in sched and "|" in sched
    scope, _ = note_selector.entity_scope(idx, "How did National Indemnity's reserves change?")
    assert scope == ["national-indemnity"]
    scope, _ = note_selector.entity_scope(idx, "Compare the insurance subsidiaries")
    assert scope == ["national-indemnity", "national-fire-marine"]
    assert note_selector.entity_scope(idx, "Analyze the textile margins")[0] == ["berkshire-hathaway"]
    refs = note_selector.explicit_unit_ids("see note 7 and Schedule V", idx, ["berkshire-hathaway"])
    assert refs == ["BH-N7", "BH-SCH-V"]


def wait_job(client, job_id):
    for _ in range(400):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_brk_analysis_prompt(brk, monkeypatch):
    calls = []

    def fake(prompt, system, timeout, **kw):
        calls.append((prompt, system))
        if "Reply with ONLY this JSON" in prompt:
            return '{"notes": ["BH-N2", "BH-SCH-I"], "reason": "securities"}'
        return "## Summary\n- ok"

    monkeypatch.setattr(app_module, "run_claude", fake)
    monkeypatch.setattr(note_selector, "run_claude", fake)
    client = app_module.app.test_client()
    data = client.post("/api/index", json={"report": BRK}).get_json()
    assert data["format"] == "transcribed" and data["review_url"].startswith("/ocr-review")
    sel = wait_job(client, client.post("/api/identify", json={
        "report": BRK, "question": "Analyze the marketable securities"}).get_json()["job_id"])["result"]
    assert [n["id"] for n in sel["notes"]] == ["BH-N2", "BH-SCH-I"]
    assert sel["scope_label"] == "Berkshire Hathaway Inc. (consolidated)"
    selector_prompt = calls[-1][0]
    assert "== Berkshire Hathaway Inc. (consolidated)" in selector_prompt and "NI-N7" not in selector_prompt
    job = wait_job(client, client.post("/api/analyze", json={
        "report": BRK, "question": "Analyze the marketable securities",
        "notes": ["BH-N2", "BH-SCH-I"]}).get_json()["job_id"])
    assert job["status"] == "done", job
    prompt, system = calls[-1]
    assert prompt.startswith("REPORT PROFILE\nFormat: Scanned report, transcribed by AI")
    assert "TRANSCRIPTION CHECKS (automatic)" in prompt
    assert "### Consolidated Statement of Earnings and Retained Earnings\n### Consolidated Balance Sheet" in prompt
    assert "=== Berkshire Hathaway Inc. — Schedule I — Marketable Securities ===" in prompt
    assert "SCANNED REPORT" in system
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": job["result"]["thread_id"], "question": "And note 7?"}).get_json()["job_id"])
    assert [n["id"] for n in job["result"]["added_notes"]] == ["BH-N7"]


def test_review_page_and_image(brk):
    client = app_module.app.test_client()
    html = client.get(f"/ocr-review?report={BRK}&page=23").get_data(as_text=True)
    assert "Statement of Assets and Liabilities" in html and "28,288,632" in html
    assert "Re-transcribe this page" in html
    assert client.get(f"/ocr-image?report={BRK}&page=23").mimetype == "image/png"


def test_needs_transcription_route_and_ocr_job(scanned_pdf, tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    shutil.copy2(scanned_pdf, reports / "Scan.pdf")
    monkeypatch.setattr(config, "REPORTS_DIR", reports)
    calls = []
    monkeypatch.setattr(ocr_transcribe, "transcribe_page", fake_transcriber(calls))
    client = app_module.app.test_client()
    data = client.post("/api/index", json={"report": "Scan.pdf"}).get_json()
    assert data["needs_transcription"] and data["page_count"] == 6 and data["estimate"]["pages"] == 6
    job = wait_job(client, client.post("/api/ocr/start", json={"report": "Scan.pdf", "pages": "1-3"})
                   .get_json()["job_id"])
    assert job["status"] == "done" and job["result"]["done"] == 3 and job["total"] == 3
    assert sorted(calls) == [1, 2, 3]


def test_transcribed_statements_use_the_statement_table_layout(brk):
    """The "View the financial statements" page styles scanned statements like
    the others: header / section / data / total rows, no repeated title block."""
    import statements_view

    idx = notes_index.load_or_build(brk)
    views = statements_view.report_view(idx)
    earnings = views[0]["statements"][0]
    rows = [r for t in earnings["pages"][0]["tables"] for r in t["rows"]]
    labels = [r["label"] for r in rows]
    assert "BERKSHIRE HATHAWAY INC." not in labels and "Year ended December 28, 1968" not in labels
    kinds = {r["label"]: r["kind"] for r in rows if r["label"]}
    assert kinds["Operating income:"] == "section" and kinds["Net sales of textile products"] == "data"
    assert kinds["Total investment gains"] == "total" and kinds["Net earnings"] == "total"   # tied total
    assert any(r["kind"] == "total" and r["cells"] == ["48,180,857"] for r in rows)       # unlabelled
    assert kinds["See accompanying notes to financial statements."] == "text"
    ni_assets = views[1]["statements"][2]
    header = ni_assets["pages"][0]["tables"][0]["rows"][0]
    assert (header["kind"], header["label"], header["cells"]) == ("header", "Assets", ["1968", "1967"])
    html = app_module.app.test_client().get(f"/statements?report={BRK}").get_data(as_text=True)
    assert '<table class="fs' in html and '<tr class="section">' in html and '<tr class="total">' in html
