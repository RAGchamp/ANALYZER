"""Old-format reports (INFO/ANALYZE-OLD-REPORTS-FUNC-PLAN.md §11).

Fixture: tests/fixtures/Reliance-1976-1977.pdf - Reliance Textile Industries,
year ended 30 September 1977; re-typeset text, schedules A-O, notes 1-34 in
Schedule 'O', Fixed Assets schedule printed sideways. Claude is mocked.
"""

import shutil
import time
from pathlib import Path

import pytest

import app as app_module
import config
from analyzer import context, note_selector
from analyzer import jobs as analyzer_jobs
from ingest import clip as extractor
from ingest import legacy_index, notes_index, report_format
from ingest.pdf_utils import open_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "Reliance-1976-1977.pdf"
OLD = "Reliance-1976-1977.pdf"


@pytest.fixture(scope="module")
def old_index():
    return notes_index.build_index(FIXTURE)


def units(index):
    return {u["id"]: u for u in index["sections"]["standalone"]["notes"]}


# ---------------------------------------------------------------- detection

def test_format_detection(old_index, index):
    assert old_index["format"] == "legacy" and old_index["format_source"] == "detected"
    assert index["format"] == "modern"          # Bharat Forge: no regression
    fmt, signals = report_format.detect(["Notes to Consolidated Financial Statements"] * 3)
    assert fmt == "modern"
    assert report_format.detect(["nothing here"])[0] == "modern"


def test_report_facts(old_index):
    assert old_index["company"] == "Reliance Textile Industries Limited"
    assert old_index["fiscal_year_end"] == "1977-09-30"
    assert old_index["statements_present"] == ["Profit and Loss Account", "Balance Sheet"]
    assert old_index["statements_missing"] == ["Cash Flow Statement", "Statement of Changes in Equity"]
    assert list(old_index["sections"]) == ["standalone"]


# ---------------------------------------------------------------- index

def test_statements(old_index):
    stmts = {s["type"]: s for s in old_index["sections"]["standalone"]["statements"]}
    assert [p["pdf_page"] for p in stmts["balance_sheet"]["pages"]] == [13]   # divider p.12 rejected
    assert [p["pdf_page"] for p in stmts["profit_loss"]["pages"]] == [14]
    bs = stmts["balance_sheet"]["text"]
    assert "Balance Sheet as at 30th September, 1977" in bs
    assert "FIXED ASSETS | 'E' | 14,51,44,752 | 11,64,80,272" in bs
    assert "11,57,05.424" in bs                   # a transcription error, kept as printed
    assert "As per our Report" not in bs          # signature block cut off


def test_schedules(old_index):
    u = units(old_index)
    expected = {"A": 15, "B": 15, "C": 16, "D": 17, "E": 18, "F": 19, "G": 20, "H": 22, "I": 22,
                "J": 23, "K": 23, "L": 24, "M": 25, "N": 26, "O": 27}
    assert {k: u[f"SCH-{k}"]["start_page"] for k in expected} == expected
    assert all(u[f"SCH-{k}"]["part_of"] == "Balance Sheet" for k in "ABCDEFGHI")
    assert all(u[f"SCH-{k}"]["part_of"] == "Profit and Loss Account" for k in "JKLMN")
    assert u["SCH-O"]["part_of"] == "Accounts"
    # A and B share p.15: A ends exactly where B starts
    assert (u["SCH-A"]["end_page"], u["SCH-A"]["end_y"]) == (15, u["SCH-B"]["start_y"])
    assert u["SCH-E"]["title"] == "Fixed Assets"          # from the Balance Sheet's Schedule column
    assert u["SCH-E"]["printed_pages"] == "21"            # rotated page: upright page number
    assert u["SCH-A"]["label"] == "Schedule 'A' — Share Capital"


def test_numbered_notes(old_index):
    notes = [x for x in old_index["sections"]["standalone"]["notes"] if x["kind"] == "note"]
    assert [n["number"] for n in notes] == list(range(1, 35))
    assert notes[0]["start_page"] == 27 and notes[-1]["end_page"] == 32
    n25 = units(old_index)["N25"]
    assert (n25["title"], n25["title_is_excerpt"], n25["start_page"]) == ("Contingent Liabilities", False, 30)
    n5 = units(old_index)["N5"]
    assert n5["title_is_excerpt"] and n5["title"].startswith("Depreciation is provided in accounts")
    assert all(n["parent"] == "SCH-O" for n in notes)


def test_narratives(old_index):
    u = units(old_index)
    assert (u["SUM"]["start_page"], u["SUM"]["end_page"]) == (3, 4)
    assert (u["DIR"]["start_page"], u["DIR"]["end_page"]) == (5, 8)
    assert (u["AUD"]["start_page"], u["AUD"]["end_page"]) == (9, 11)
    assert "Expansion Project" in u["DIR"]["subheadings"]


def test_legacy_clean():
    assert legacy_index.legacy_clean("SCHEDULE �C�") == "SCHEDULE 'C'"
    assert legacy_index.legacy_clean("Interest ₹M' 2,75,05,653") == "Interest 'M' 2,75,05,653"
    assert legacy_index.legacy_clean("Goodwill �� 1,26,24,281") == "Goodwill – 1,26,24,281"
    assert legacy_index.legacy_clean("Fees 750 ––") == "Fees 750 –"
    assert legacy_index.legacy_clean("11,57,05.424 Dated 1918") == "11,57,05.424 Dated 1918"


# ---------------------------------------------------------------- extraction

def test_extract_rotated_schedule_and_note(old_index):
    e = extractor.extract_note(old_index, "standalone", "SCH-E")
    assert e["text"].startswith("=== Schedule 'E' — Fixed Assets (forming part of the Balance Sheet) ===")
    assert "5. Plant & Machinery | 7,88,31,512" in e["text"]      # read after turning the page
    assert "[Rotated text" not in e["text"]
    n25 = extractor.extract_note(old_index, "standalone", "N25")["text"]
    assert "Counter guarantee given to the Bankers" in n25 and "26." not in n25
    b = extractor.extract_note(old_index, "standalone", "SCH-B")["text"]
    assert "Investment Allowance Reserve" in b and "SHARE CAPITAL" not in b


# ---------------------------------------------------------------- selection

def test_selector_prompt_and_reply(old_index, monkeypatch):
    seen = {}

    def fake(prompt, system, timeout, **kw):
        seen["prompt"], seen["system"] = prompt, system
        return '{"notes": ["SCH-E", "N25", "DIR", "X99"], "reason": "fixed assets"}'

    monkeypatch.setattr(note_selector, "run_claude", fake)
    sel = note_selector.select_notes(old_index, "Analyze the fixed assets and capex")
    assert sel["scope"] == "standalone" and "standalone accounts only" in sel["scope_reason"]
    assert [n["id"] for n in sel["notes"]] == ["SCH-E", "N25", "DIR"]
    assert sel["notes"][0]["label"] == "Schedule 'E' — Fixed Assets"
    assert "SCHEDULES:" in seen["prompt"] and "NUMBERED NOTES:" in seen["prompt"]
    assert "REPORT SECTIONS:" in seen["prompt"] and "year ended 30 September 1977" in seen["prompt"]
    assert "Companies Act, 1956" in seen["system"]


def test_keyword_fallback_and_explicit_refs(old_index, monkeypatch):
    def boom(*a, **k):
        raise note_selector.ClaudeError("offline")

    monkeypatch.setattr(note_selector, "run_claude", boom)
    sel = note_selector.select_notes(old_index, "Comment on sundry debtors, see schedule 'H' and note 25")
    ids = [n["id"] for n in sel["notes"]]
    assert sel["method"] == "keywords"
    assert ids[:2] == ["N25", "SCH-H"] and "SCH-G" in ids
    refs = note_selector.explicit_unit_ids("Check Sch. E, note 12 and the Directors' Report", old_index,
                                           ["standalone"])
    assert refs == ["N12", "SCH-E", "DIR"]


def test_whole_notes_schedule_covers_its_notes(old_index):
    choices = {c["id"]: c for c in note_selector.all_note_choices(old_index, "both")}
    kept = note_selector.drop_covered([choices["SCH-O"], choices["N25"], choices["SCH-E"]])
    assert [c["id"] for c in kept] == ["SCH-O", "SCH-E"]
    # the selector prefers the specific notes over the whole schedule
    picked = note_selector.prefer_specific([choices["N25"], choices["SCH-O"], choices["N10"]])
    assert [c["id"] for c in picked] == ["N25", "N10"]


# ---------------------------------------------------------------- through the app

@pytest.fixture
def client(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    shutil.copy2(FIXTURE, reports / OLD)
    for name, sub in (("REPORTS_DIR", "reports"), ("CACHE_DIR", "cache"), ("HISTORY_DIR", "hist"),
                      ("THREADS_DIR", "threads"), ("ANALYSIS_HISTORY_DIR", "html")):
        monkeypatch.setattr(config, name, tmp_path / sub)
        (tmp_path / sub).mkdir(exist_ok=True)
    monkeypatch.setattr(config, "INPUT_FILE", tmp_path / "in.txt")
    monkeypatch.setattr(config, "OUTPUT_FILE", tmp_path / "out.txt")
    calls = []

    def fake(prompt, system, timeout, **kw):
        calls.append((prompt, system))
        return "## Summary\n- Net block Rs. 1,451.45 lakhs (Schedule 'E', p.18)"

    monkeypatch.setattr(analyzer_jobs, "run_claude", fake)
    client = app_module.app.test_client()
    client.calls = calls
    return client


def wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_index_route_reports_format_and_override(client):
    data = client.post("/api/index", json={"report": OLD}).get_json()
    assert (data["format"], data["format_source"]) == ("legacy", "detected")
    assert "34 notes" in data["summary"] and "15 schedules" in data["summary"]
    assert data["sections"]["standalone"]["missing_statements"] == [
        "Cash Flow Statement", "Statement of Changes in Equity"]
    assert data["model"].startswith("TYPE-2 INDIA Ann rpt 1977 Reliance (model document; checks passed")
    # A forced format only chooses which model documents to try; they must pass their
    # checks (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §5.2). The modern models' rules fail here.
    forced = client.post("/api/index", json={"report": OLD, "format": "modern"}).get_json()
    assert forced["new_model"] and "TYPE-1 INDIA" in forced["new_model"]["message"]
    assert forced["gap_report_url"] == f"/gap-report?report={OLD}"
    kept = client.post("/api/index", json={"report": OLD}).get_json()         # its package is kept
    assert kept["format"] == "legacy"
    auto = client.post("/api/index", json={"report": OLD, "format": "auto"}).get_json()
    assert (auto["format"], auto["format_source"]) == ("legacy", "detected")
    assert client.post("/api/index", json={"report": OLD, "format": "weird"}).status_code == 400


def test_legacy_analysis_and_followup_prompts(client):
    client.post("/api/index", json={"report": OLD})
    job = wait_job(client, client.post("/api/analyze", json={
        "report": OLD, "question": "Analyze fixed assets", "notes": ["SCH-E", "SCH-O", "N25"]}).get_json()["job_id"])
    assert job["status"] == "done", job
    prompt, system = client.calls[-1]
    assert prompt.startswith("REPORT PROFILE\nFormat: Old Indian GAAP")
    assert "Year ended: 30 September 1977" in prompt
    assert "### Profit and Loss Account\n### Balance Sheet\n" in prompt
    assert "### Cash Flow Statement" not in prompt and "derived funds-flow" in prompt
    assert "=== Schedule 'E' — Fixed Assets" in prompt
    assert "=== Note 25" not in prompt                    # covered by the whole of Schedule 'O'
    assert "OLD-FORMAT REPORT" in system
    assert [n["id"] for n in job["result"]["notes"]] == ["SCH-E", "SCH-O"]

    thread_id = job["result"]["thread_id"]
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": thread_id, "question": "Now look at Schedule 'C' and note 25"}).get_json()["job_id"])
    assert job["status"] == "done", job
    assert [n["id"] for n in job["result"]["added_notes"]] == ["SCH-C"]    # N25 already in SCH-O
    prompt, _ = client.calls[-1]
    assert "=== Schedule 'C' — Secured Loans" in prompt
    assert "(Profit and Loss Account, Balance Sheet)" in prompt


def test_modern_prompt_has_no_profile(index, package):
    assert context.report_profile(package) == ""
    assert context.statement_sections_text(index) == context.MODERN_STATEMENT_SECTIONS
    assert "OLD-FORMAT" not in context.analysis_system(index)
