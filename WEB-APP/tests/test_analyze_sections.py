"""The two flows (INFO/ANALYZE-NON-FIN-DATA-PLAN.md §16): "Identify notes" (mode "notes") picks notes
only and sends the original prompts; "Analyze non notes" (mode "business") picks report passages
and at most two confirming notes, and sends them with the business prompts (REPORT SECTIONS,
NARRATIVE FIGURES, notes, statements for reference). Manual pages on narrative pages are sent in
reading order. Claude is mocked."""

import json
import re
import time

import pytest

import app as app_module
import config
from analyzer import jobs as analyzer_jobs
from analyzer import note_selector

REPORT = "Bharat-Forge-IR-2026-conv-single-page.pdf"
CV_QUESTION = "How did the commercial vehicle export business perform this year?"


@pytest.fixture
def client(index, tmp_path, monkeypatch):
    for name, folder in (("HISTORY_DIR", "hist"), ("THREADS_DIR", "threads"), ("ANALYSIS_HISTORY_DIR", "reports")):
        (tmp_path / folder).mkdir()
        monkeypatch.setattr(config, name, tmp_path / folder)
    monkeypatch.setattr(config, "INPUT_FILE", tmp_path / "in.txt")
    monkeypatch.setattr(config, "OUTPUT_FILE", tmp_path / "out.txt")
    calls = []
    reply = {"notes": ["C49"], "passages": "first", "reason": "CV business"}

    def fake_claude(prompt, system, timeout, **kwargs):
        calls.append({"prompt": prompt, "system": system})
        if "Reply with ONLY this JSON" in prompt:
            data = dict(client.selector_reply)
            if data.get("passages") == "first":
                ids = re.findall(r"^(P-[A-Z0-9-]+) \|", prompt, re.M)
                data["passages"] = ids[:1]
            return json.dumps(data)
        return "## Summary\n- The CV export business fell 34% (MD&A, p.76)."

    monkeypatch.setattr(analyzer_jobs, "run_claude", fake_claude)
    monkeypatch.setattr(note_selector, "run_claude", fake_claude)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    client.calls = calls
    client.selector_reply = reply
    client.post("/api/index", json={"report": REPORT})
    return client


def wait_job(client, job_id):
    for _ in range(400):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def identify(client, question, mode="notes"):
    job = wait_job(client, client.post("/api/identify", json={"report": REPORT, "question": question, "mode": mode})
                   .get_json()["job_id"])
    assert job["status"] == "done", job
    return job["result"]


def analyze(client, question, notes=(), passages=(), pages="", mode="notes"):
    return wait_job(client, client.post("/api/analyze", json={
        "report": REPORT, "question": question, "notes": list(notes), "passages": list(passages),
        "pages": pages, "mode": mode}).get_json()["job_id"])


# ---------------------------------------------------------------- "Identify notes": notes only

def test_identify_notes_sees_no_passages(client):
    sel = identify(client, CV_QUESTION)
    prompt = client.calls[0]["prompt"]
    assert "P-" not in prompt and '"passages"' not in prompt and "REPORT PASSAGES" not in prompt
    assert [n["id"] for n in sel["notes"]] == ["C49"]
    assert "passages" not in sel and "passage_suggestions" not in sel and "mode" not in sel


def test_notes_analysis_is_the_original_prompt(client):
    job = analyze(client, "Analyze the tax liabilities", notes=["C21"], passages=["P-IGNORED-01"])
    assert job["status"] == "done", job
    prompt, system = client.calls[-1]["prompt"], client.calls[-1]["system"]
    assert "REPORT SECTIONS" not in prompt and "Report sections provided" not in prompt
    assert "NARRATIVE FIGURES" not in prompt and "## Impact on the financial statements" in prompt
    assert "equity research analyst" not in system
    thread = json.loads((config.THREADS_DIR / f"{job['result']['thread_id']}.json").read_text(encoding="utf-8"))
    assert "mode" not in thread and "passages" not in thread


def test_notes_mode_needs_a_note(client):
    job = analyze(client, CV_QUESTION)
    assert job["status"] == "error"
    assert "Select at least one note" in job["error"]


def test_an_unknown_mode_is_refused(client):
    res = client.post("/api/identify", json={"report": REPORT, "question": CV_QUESTION, "mode": "other"})
    assert res.status_code == 400


# ---------------------------------------------------------------- "Analyze non notes": step 3

def test_no_matching_passage_is_not_called_a_missing_ingest(client):
    """A report that has passages, none using the question's words, must not be told to re-ingest."""
    job = wait_job(client, client.post("/api/identify", json={
        "report": REPORT, "question": "zqxwv plorkt", "mode": "business"}).get_json()["job_id"])
    assert job["status"] == "error"
    assert "uses the words of the question" in job["error"] and "re-ingest" not in job["error"]
    assert not client.calls                           # no Claude call for nothing to choose from


def test_business_selector_sees_ranked_passages_and_picks_one(client):
    sel = identify(client, CV_QUESTION, mode="business")
    prompt = client.calls[0]["prompt"]
    assert "not from the notes to the financial statements" in prompt
    first = re.search(r"^(P-[A-Z0-9-]+) \| (.*?) \| PDF (\S+) \|", prompt, re.M)
    assert "Commercial Vehicles (CV)" in first.group(2) and first.group(3) == "76"
    assert f"at most {config.MAX_PASSAGES}" in prompt and f"at most {config.MAX_BUSINESS_NOTES} notes" in prompt
    assert sel["mode"] == "business"
    assert [n["id"] for n in sel["notes"]] == ["C49"]
    assert [p["id"] for p in sel["passages"]] == [first.group(1)]
    assert sel["passages"][0]["pages"] == "76"
    assert sel["passage_suggestions"] and first.group(1) not in [s["id"] for s in sel["passage_suggestions"]]
    assert sel["chars"] > sel["passages"][0]["chars"]


def test_business_notes_are_capped(client):
    client.selector_reply = {"notes": ["C49", "C21", "C22", "C23"], "passages": "first", "reason": "many"}
    sel = identify(client, CV_QUESTION, mode="business")
    assert len(sel["notes"]) <= config.MAX_BUSINESS_NOTES


def test_business_passages_only_reply_is_accepted(client):
    client.selector_reply = {"notes": [], "passages": "first", "reason": "business question"}
    sel = identify(client, CV_QUESTION, mode="business")
    assert sel["method"] == "claude" and sel["notes"] == [] and len(sel["passages"]) == 1


def test_business_keyword_fallback_picks_passages(client):
    client.selector_reply = {"notes": ["C999"], "reason": "no valid ids"}
    sel = identify(client, CV_QUESTION, mode="business")
    assert sel["method"] == "keywords"
    assert sel["passages"] and "Commercial Vehicles (CV)" in sel["passages"][0]["path"]


def test_a_named_section_comes_first(client):
    client.selector_reply = {"notes": ["C49"], "passages": "first", "reason": "tariffs"}
    sel = identify(client, "What does the MD&A say about the impact of US tariffs?", mode="business")
    assert sel["passages"] and sel["passages"][0]["kind"] == "mdna"


# ---------------------------------------------------------------- "Analyze non notes": analysis

def test_business_analysis_with_passages_and_no_notes(client):
    sel = identify(client, CV_QUESTION, mode="business")
    job = analyze(client, CV_QUESTION, passages=[p["id"] for p in sel["passages"]], mode="business")
    assert job["status"] == "done", job
    res = job["result"]
    call = client.calls[-1]
    prompt, system = call["prompt"], call["system"]
    assert "Notes provided (to confirm figures): none" in prompt
    assert "Report sections provided: Management Discussion & Analysis › Outlook · COMPANY REVIEW OF THE EXPORTS " \
           "AUTO MARKET Commercial Vehicles (CV) (PDF 76)" in prompt
    assert "----- REPORT SECTIONS: MANAGEMENT'S COMMENTARY AND OTHER NARRATIVE FROM THE ANNUAL REPORT" in prompt
    assert "For the Full Year revenue at ₹1,324 crore was lower 34%" in prompt
    assert "## How it shows in the audited numbers" in prompt
    assert "## Impact on the financial statements" not in prompt
    assert "₹1,324 crore (Management Discussion & Analysis, p.76) = 13,240 million -> no statement line" in prompt
    assert "(No notes were chosen to confirm figures.)" in prompt
    assert prompt.index("REPORT SECTIONS:") < prompt.index("PRIMARY FINANCIAL STATEMENTS (for reference")
    assert "equity research analyst" in system and "NOT audited" in system
    # the thread, the history and the HTML report carry the passages
    thread = json.loads((config.THREADS_DIR / f"{res['thread_id']}.json").read_text(encoding="utf-8"))
    assert thread["mode"] == "business"
    assert [p["id"] for p in thread["passages"]] == [p["id"] for p in sel["passages"]]
    question_file = next(config.HISTORY_DIR.glob("*-question.txt")).read_text(encoding="utf-8")
    assert "Report passages: Management Discussion & Analysis" in question_file
    assert "business analysis (non notes)" in question_file
    html = (config.ANALYSIS_HISTORY_DIR / res["saved_html"]).read_text(encoding="utf-8")
    assert "Report sections analyzed" in html
    assert res["passages"][0]["pages"] == "76"


def test_business_mode_needs_a_passage(client):
    job = analyze(client, CV_QUESTION, notes=["C49"], mode="business")
    assert job["status"] == "error"
    assert "Select at least one report passage" in job["error"]


def test_passages_beyond_the_budget_are_left_out(client, monkeypatch):
    sel = identify(client, CV_QUESTION, mode="business")
    ids = [p["id"] for p in sel["passages"] + sel["passage_suggestions"]]
    # the statements are ~19 K characters: room for two or three passages, not all of them
    monkeypatch.setattr(config, "MAX_CONTEXT_CHARS", 29_000)
    job = analyze(client, CV_QUESTION, notes=[], passages=ids, mode="business")
    assert job["status"] == "done", job
    res = job["result"]
    assert res["passages"] and res["dropped_passages"]
    assert res["passages"][0]["id"] == ids[0]                   # the best-ranked are kept


def test_business_followup_keeps_the_passages_and_adds_a_named_section(client):
    sel = identify(client, CV_QUESTION, mode="business")
    res = analyze(client, CV_QUESTION, notes=["C49"], passages=[p["id"] for p in sel["passages"]],
                  mode="business")["result"]
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": res["thread_id"], "question": "What does the Board's report say about exports?"})
        .get_json()["job_id"])
    assert job["status"] == "done", job
    follow = job["result"]
    assert follow["added_passages"] and follow["added_passages"][0]["kind"] == "board_report"
    prompt = client.calls[-1]["prompt"]
    assert "For the Full Year revenue at ₹1,324 crore" in prompt and "Board’s Report" in prompt
    assert "FOLLOW-UP QUESTION: What does the Board's report say about exports?" in prompt
    assert "equity research analyst" in client.calls[-1]["system"]


def test_business_followup_searches_the_report_again(client):
    """A follow-up on another subject gets the passages the business selector picks for it, added to
    the thread's; the thread's first question goes with it to the selector."""
    sel = identify(client, CV_QUESTION, mode="business")
    first = [p["id"] for p in sel["passages"]]
    res = analyze(client, CV_QUESTION, notes=[], passages=first, mode="business")["result"]
    question = "What is the outlook for the aerospace business?"
    job = wait_job(client, client.post("/api/followup", json={"thread_id": res["thread_id"], "question": question})
                   .get_json()["job_id"])
    assert job["status"] == "done", job
    follow = job["result"]
    selector = next(c["prompt"] for c in reversed(client.calls) if "Reply with ONLY this JSON" in c["prompt"])
    assert question in selector and CV_QUESTION in selector
    added = [p["id"] for p in follow["added_passages"]]
    assert added and not set(added) & set(first) and follow["rescanned"]
    assert [p["id"] for p in follow["passages"]][:len(first)] == first    # the earlier passages stay
    assert "aerospace" in client.calls[-1]["prompt"].lower()


def test_notes_followup_adds_no_passages(client):
    res = analyze(client, "Analyze the tax liabilities", notes=["C21"])["result"]
    job = wait_job(client, client.post("/api/followup", json={
        "thread_id": res["thread_id"], "question": "What does the Board's report say about tax?"})
        .get_json()["job_id"])
    assert job["status"] == "done", job
    assert "added_passages" not in job["result"]
    assert "REPORT SECTIONS" not in client.calls[-1]["prompt"]


def test_manual_pages_on_a_narrative_page_are_in_reading_order(client):
    job = analyze(client, "Summarise this page", pages="76")
    assert job["status"] == "done", job
    prompt = client.calls[-1]["prompt"]
    # the left column's sentence is whole; the stored row-by-row text interleaves it with the right column
    assert ("Despite global macroeconomic instability, India continues to stand out as the world’s fastest "
            "growing major economy.") in prompt
    assert prompt.index("#### Indian Economy") < prompt.index("The United States automotive market")


# ---------------------------------------------------------------- step 3 search and show

def test_passage_search_and_show(client):
    found = client.get("/api/passages/search", query_string={"report": REPORT, "q": "aerospace"}).get_json()
    assert any("Aerospace Business" in p["path"] for p in found["passages"])
    pid = found["passages"][0]["id"]
    shown = client.get(f"/api/passages/{pid}", query_string={"report": REPORT}).get_json()
    assert shown["id"] == pid and shown["text"].startswith("--- PDF page")
    assert client.get("/api/passages/P-NOPE-01", query_string={"report": REPORT}).status_code == 404
    assert client.get("/api/passages/search", query_string={"report": REPORT, "q": ""}).get_json()["passages"] == []


# ---------------------------------------------------------------- narrative figure ties

def test_a_loose_tie_needs_a_shared_word():
    from analyzer.linked import _tie_words, _ties
    oci = {"label": "Other comprehensive income for the year", "value": 3973.98}
    revenue = _tie_words("For the Full Year revenue at ₹397 crore was lower 12%")
    # ₹397 crore = 3,970 million, ±5 million: close to OCI by coincidence, but no word in common
    assert not _ties(3970, 5, oci, revenue)
    assert _ties(3970, 5, {"label": "Revenue from operations", "value": 3973.98}, revenue)
    # an exact match (within half a statement unit) ties on its own
    assert _ties(3970, 5, {"label": "Other comprehensive income", "value": 3970.4}, revenue)
