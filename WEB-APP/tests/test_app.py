"""End-to-end through the Flask routes with Claude mocked (no real calls)."""

import re
import time

import pytest

import app as app_module
import config
from analyzer import jobs as analyzer_jobs
from analyzer import note_selector

REPORT = "Bharat-Forge-IR-2026-conv-single-page.pdf"


@pytest.fixture
def client(index, tmp_path, monkeypatch):
    # Keep test history/threads out of the real folders.
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "hist")
    monkeypatch.setattr(config, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(config, "INPUT_FILE", tmp_path / "in.txt")
    monkeypatch.setattr(config, "OUTPUT_FILE", tmp_path / "out.txt")
    monkeypatch.setattr(config, "ANALYSIS_HISTORY_DIR", tmp_path / "reports")
    (tmp_path / "reports").mkdir()
    (tmp_path / "hist").mkdir()
    (tmp_path / "threads").mkdir()

    sent = []

    def fake_claude(prompt, system, timeout, **kwargs):
        sent.append(prompt)
        client.claude_kwargs.append(kwargs)
        if "Reply with ONLY this JSON" in prompt:
            return '{"notes": ["C21"], "reason": "Income tax note"}'
        return "## Summary\n- Effective tax rate fell to 34.66%\n\n| a | b |\n|---|---|\n| 1 | 2 |"

    monkeypatch.setattr(analyzer_jobs, "run_claude", fake_claude)
    monkeypatch.setattr(note_selector, "run_claude", fake_claude)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    client.sent = sent
    client.claude_kwargs = []
    return client


def wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_full_flow(client):
    reports = client.get("/api/reports").get_json()["reports"]
    assert REPORT in [r["name"] for r in reports]

    idx = client.post("/api/index", json={"report": REPORT}).get_json()
    assert idx["sections"]["consolidated"]["count"] == 59
    loaded = idx["sections"]["consolidated"]["statements"]
    assert [st["pages"] for st in loaded] == ["342–343", "341", "346–347", "344–345"]
    assert [st["rotated"] for st in loaded] == [False, False, False, True]
    assert "Total assets | 222,599.55" in loaded[1]["text"]

    q = "Analyze the company's Tax related liabilities and comment on it."
    job = wait_job(client, client.post("/api/identify", json={"report": REPORT, "question": q})
                   .get_json()["job_id"])
    assert job["status"] == "done", job
    sel = job["result"]
    assert sel["scope"] == "consolidated"
    assert [n["id"] for n in sel["notes"]] == ["C21"]
    assert (sel["notes"][0]["start_page"], sel["notes"][0]["end_page"]) == (405, 407)

    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": q, "notes": ["C21"]}).get_json()["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert "<table>" in res["answer_html"]
    assert res["pages"] == "405, 406, 407"
    prompt = client.sent[-1]
    assert "QUESTION: " + q in prompt
    assert "Total deferred tax liability | 215.24 | 1,198.28" in prompt
    # The consolidated primary statements go with every notes question...
    assert "PRIMARY FINANCIAL STATEMENTS" in prompt
    assert "=== Consolidated Balance Sheet (Consolidated) ===" in prompt
    assert "=== Consolidated Statement of Changes in Equity (Consolidated) ===" in prompt
    assert "(c) Deferred tax liabilities (net) | 21 | 215.24 | 1,198.28" in prompt
    assert "=== Balance Sheet (Standalone) ===" not in prompt
    # ...and Claude is asked to explain the impact on each of them.
    assert "## Impact on the financial statements" in prompt
    assert "### Statement of Changes in Equity" in prompt
    assert "Consolidated Balance Sheet (PDF p.341)" in res["statements_list"]
    assert "Consolidated Cash Flow Statement" in res["statements"]
    assert config.INPUT_FILE.read_text(encoding="utf-8") == prompt
    assert config.OUTPUT_FILE.read_text(encoding="utf-8").startswith("## Summary")

    # follow-up that adds Note 41
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": res["thread_id"], "question": "Also consider Note 41 tax disputes"})
        .get_json()["job_id"])
    assert job["status"] == "done", job
    assert [n["id"] for n in job["result"]["added_notes"]] == ["C41"]
    prompt = client.sent[-1]
    assert "Conversation so far" in prompt and "Effective tax rate fell" in prompt
    assert "Income tax demand matters under dispute" in prompt
    assert "=== Consolidated Cash Flow Statement (Consolidated) ===" in prompt
    assert "Impact on the financial statements" in prompt

    # One styled HTML report per prompt: <pdf name>-<prompt no>-<timestamp>.html
    first, second = res["saved_html"], job["result"]["saved_html"]
    assert re.fullmatch(r"Bharat-Forge-IR-2026-conv-single-page-1-\d{8}-\d{6}\.html", first)
    assert re.fullmatch(r"Bharat-Forge-IR-2026-conv-single-page-2-\d{8}-\d{6}\.html", second)
    html1 = (config.ANALYSIS_HISTORY_DIR / first).read_text(encoding="utf-8")
    assert "Bharat Forge Limited" in html1 and "RAG Champ" in html1
    assert "ANNUAL REPORT ANALYZER" in html1 and q.replace("'", "&#39;") in html1
    assert "405–407" in html1 and "Initial question" in html1
    assert "Financial statements referenced:" in html1
    assert "Consolidated Statement of Changes in Equity (PDF p.344–345)" in html1
    assert 'class="table table-sm table-bordered' in html1
    assert '<td class="num">1</td>' in html1
    html2 = (config.ANALYSIS_HISTORY_DIR / second).read_text(encoding="utf-8")
    assert "Follow-up question 1" in html2 and "Original question" in html2
    assert client.get(f"/analysis-history/{first}").status_code == 200
    assert client.get("/analysis-history/..%5Capp.py").status_code == 404

    entries = client.get("/api/history").get_json()["entries"]
    assert [e["kind"] for e in entries] == ["follow-up", "analysis"]
    entry = client.get(f"/api/history/{entries[1]['sno']}").get_json()
    assert entry["meta"]["PDF pages"] == "405, 406, 407"
    assert "Consolidated Balance Sheet (PDF p.341)" in entry["meta"]["Financial statements"]
    assert entry["thread_available"]
    assert entry["meta"]["Saved HTML"] == first
    thread = client.get(f"/api/threads/{res['thread_id']}").get_json()
    assert [t["saved_html"] for t in thread["turns"]] == [first, second]


def test_followup_searches_the_report_again(client, monkeypatch):
    """A follow-up runs the note selector again (with the thread's first question for context) and
    adds the notes it picks that the thread lacks; the earlier notes stay."""
    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": "Analyze the tax liabilities", "notes": ["C21"]}).get_json()["job_id"])
    thread_id = job["result"]["thread_id"]
    seen = []

    def selector(prompt, system, timeout, **kwargs):
        seen.append(prompt)
        return '{"notes": ["C21", "C12"], "reason": "tax and borrowings"}'

    monkeypatch.setattr(note_selector, "run_claude", selector)
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": thread_id, "question": "How are the borrowings secured?"}).get_json()["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert [n["id"] for n in res["added_notes"]] == ["C12"] and res["rescanned"]
    assert [n["id"] for n in res["notes"]] == ["C21", "C12"]
    assert "How are the borrowings secured?" in seen[0] and "Analyze the tax liabilities" in seen[0]
    prompt = client.sent[-1]
    assert "Total deferred tax liability" in prompt              # the first note is still sent

    # the selector unavailable: keyword guesses add nothing to a thread
    def broken(prompt, system, timeout, **kwargs):
        raise analyzer_jobs.ClaudeError("offline")

    monkeypatch.setattr(note_selector, "run_claude", broken)
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": thread_id, "question": "What about inventories?"}).get_json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["added_notes"] == [] and len(job["result"]["notes"]) == 2


def test_manual_pages_and_errors(client):
    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": "What is on these pages?", "pages": "405"}).get_json()["job_id"])
    assert job["status"] == "done"
    assert "Manually selected pages" in client.sent[-1]

    r = client.post("/api/analyze", json={"report": "..\\secret.pdf", "question": "x", "notes": ["C21"]})
    assert r.status_code == 400
    r = client.post("/api/analyze", json={"report": REPORT, "question": "x", "pages": "abc"})
    assert r.status_code == 400
    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": "x", "notes": []}).get_json()["job_id"])
    assert job["status"] == "error"


def test_claude_reply_html_is_escaped(client, monkeypatch):
    monkeypatch.setattr(analyzer_jobs, "run_claude",
                        lambda *a, **k: "Hello <script>alert(1)</script> **bold**")
    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": "x", "notes": ["C21"]}).get_json()["job_id"])
    html = job["result"]["answer_html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<strong>bold</strong>" in html


def test_standalone_notes_get_standalone_statements(client):
    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": "Analyze standalone tax", "notes": ["S21"]}).get_json()["job_id"])
    assert job["status"] == "done", job
    prompt = client.sent[-1]
    assert "=== Balance Sheet (Standalone) ===" in prompt
    assert "=== Consolidated Balance Sheet (Consolidated) ===" not in prompt


def test_analysis_uses_configured_effort(client, monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_EFFORT", "low")
    job = wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": "x", "notes": ["C21"]}).get_json()["job_id"])
    assert job["status"] == "done"
    assert client.claude_kwargs[-1]["effort"] == "low"
    assert callable(client.claude_kwargs[-1]["on_progress"])


def test_running_job_shows_answer_so_far(client, monkeypatch):
    import threading
    release = threading.Event()

    def slow_claude(prompt, system, timeout, effort=None, on_progress=None):
        on_progress("thinking", "")
        on_progress("writing", "## Summary\n- Tax <b>rate</b> fell")
        assert release.wait(5)
        return "## Summary\n- Tax rate fell to 34.66%"

    monkeypatch.setattr(analyzer_jobs, "run_claude", slow_claude)
    job_id = client.post("/api/analyze", json={
        "report": REPORT, "question": "x", "notes": ["C21"]}).get_json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job.get("partial_html"):
            break
        time.sleep(0.05)
    assert job["status"] == "running" and job["phase"] == "writing"
    assert "<h2>Summary</h2>" in job["partial_html"]
    assert "&lt;b&gt;" in job["partial_html"]          # escaped like the final answer
    release.set()
    job = wait_job(client, job_id)
    assert job["status"] == "done" and "partial_html" not in job
    assert "34.66%" in job["result"]["answer"]
