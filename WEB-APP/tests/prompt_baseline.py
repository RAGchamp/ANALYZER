"""Baseline of everything the app sends to Claude, for the four sample reports
(INFO/SPLIT-FUNCTIONALITY-PLAN.md §11, "Baseline freeze" / "Prompt identity").

Each case goes through the Flask routes with Claude mocked, exactly as a user
would: load the report, identify notes, analyze, follow up, manual pages. The
prompts and system prompts Claude would receive, the report-load JSON and the
"View the financial statements" page are recorded as text files in
tests/fixtures/baseline/.

    python tests/prompt_baseline.py --freeze     # (re)write the baseline files

tests/test_prompt_identity.py re-runs the cases and compares every file byte
for byte. Only re-freeze on purpose, when a prompt is meant to change.
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent
BASELINE_DIR = TESTS_DIR / "fixtures" / "baseline"
FIXTURE_OCR = TESTS_DIR / "fixtures" / "brk1968.ocr"

BHARAT = "Bharat-Forge-IR-2026-conv-single-page.pdf"
CHUBB = "Chubb-10-K-2025.pdf"
RELIANCE = "Reliance-1976-1977.pdf"
BRK = "BRK-1968.pdf"
BRK94 = "BRK-1994.pdf"

# report -> (question, selector reply, notes to analyze, follow-up, manual pages)
CASES = {
    BHARAT: ("Analyze the company's Tax related liabilities and comment on it.",
             '{"notes": ["C21", "C41"], "reason": "tax"}', ["C21"],
             "Also consider Note 41 tax disputes", "405-406"),
    CHUBB: ("Analyze the company's tax position and comment on it.",
            '{"notes": ["C12"], "reason": "tax"}', ["C12"],
            "What about Note 13?", "108"),
    RELIANCE: ("Analyze fixed assets",
               '{"notes": ["SCH-E", "N25", "DIR"], "reason": "fixed assets"}', ["SCH-E", "SCH-O", "N25"],
               "What about schedule C?", "13"),
    BRK: ("Analyze the marketable securities",
          '{"notes": ["BH-N2", "BH-SCH-I"], "reason": "securities"}', ["BH-N2", "BH-SCH-I"],
          "And note 7?", "23"),
    # a plain-text (typewriter) EDGAR filing
    BRK94: ("Analyze the company's income taxes",
            '{"notes": ["C10"], "reason": "taxes"}', ["C10"],
            "What about note 12?", "37"),
}
EXTRA_ANALYSES = {BHARAT: ("Analyze standalone tax", ["S21"])}
ANSWER = "## Summary\n- Effective tax rate fell to 34.66%\n\n| a | b |\n|---|---|\n| 1 | 2 |"


def _patch_claude(monkeypatch, fake):
    """Replace run_claude wherever a module imported it, so this harness keeps
    working when code moves between modules."""
    import claude_client
    original = claude_client.run_claude
    for module in list(sys.modules.values()):
        if getattr(module, "run_claude", None) is original:
            monkeypatch.setattr(module, "run_claude", fake)


def _sandbox(tmp, monkeypatch):
    import config
    cache = tmp / "cache"
    for name, folder in (("CACHE_DIR", cache), ("THREADS_DIR", cache / "threads"),
                         ("HISTORY_DIR", tmp / "hist"), ("ANALYSIS_HISTORY_DIR", tmp / "html")):
        folder.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, name, folder)
    monkeypatch.setattr(config, "INPUT_FILE", tmp / "in.txt")
    monkeypatch.setattr(config, "OUTPUT_FILE", tmp / "out.txt")
    # BRK-1968 is scanned: use the recorded transcriptions, matched to the real PDF.
    brk_pdf = config.REPORTS_DIR / BRK
    if brk_pdf.exists():
        shutil.copytree(FIXTURE_OCR, cache / "BRK-1968.ocr")
        manifest_path = cache / "BRK-1968.ocr" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stat = brk_pdf.stat()
        manifest.update(size=stat.st_size, mtime=stat.st_mtime)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _wait(client, job_id):
    for _ in range(2400):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            if job["status"] != "done":
                raise AssertionError(job)
            return job["result"]
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def available_reports():
    import config
    return [name for name in CASES if (config.REPORTS_DIR / name).exists()]


def collect(tmp, monkeypatch, reports=None):
    """Run every case; returns {file name: text}."""
    _sandbox(tmp, monkeypatch)
    import app as app_module

    sent = []
    selector_reply = {"text": ""}

    def fake(prompt, system, timeout, **kwargs):
        sent.append((prompt, system))
        return selector_reply["text"] if "Reply with ONLY this JSON" in prompt else ANSWER

    _patch_claude(monkeypatch, fake)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    out = {}

    def record(name):
        prompt, system = sent[-1]
        out[f"{name}.prompt.txt"] = prompt
        out[f"{name}.system.txt"] = system

    for report in reports or available_reports():
        stem = Path(report).stem
        question, reply, notes, followup, pages = CASES[report]
        selector_reply["text"] = reply

        loaded = client.post("/api/index", json={"report": report}).get_json()
        out[f"{stem}.index.json"] = json.dumps(loaded, indent=1, ensure_ascii=False, sort_keys=True)
        out[f"{stem}.statements.html"] = client.get(f"/statements?report={report}").get_data(as_text=True)

        selection = _wait(client, client.post("/api/identify", json={
            "report": report, "question": question}).get_json()["job_id"])
        record(f"{stem}.1-selector")
        out[f"{stem}.1-selector.result.json"] = json.dumps(selection, indent=1, ensure_ascii=False,
                                                           sort_keys=True)

        result = _wait(client, client.post("/api/analyze", json={
            "report": report, "question": question, "notes": notes}).get_json()["job_id"])
        record(f"{stem}.2-analysis")

        _wait(client, client.post("/api/followup", json={
            "thread_id": result["thread_id"], "question": followup}).get_json()["job_id"])
        record(f"{stem}.3-followup")

        _wait(client, client.post("/api/analyze", json={
            "report": report, "question": "What is on these pages?", "pages": pages}).get_json()["job_id"])
        record(f"{stem}.4-pages")

        if report in EXTRA_ANALYSES:
            extra_q, extra_notes = EXTRA_ANALYSES[report]
            _wait(client, client.post("/api/analyze", json={
                "report": report, "question": extra_q, "notes": extra_notes}).get_json()["job_id"])
            record(f"{stem}.5-extra")
    return out


def freeze():
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch = pytest.MonkeyPatch()
        try:
            files = collect(Path(tmp), monkeypatch)
        finally:
            monkeypatch.undo()
    if BASELINE_DIR.exists():
        shutil.rmtree(BASELINE_DIR)
    BASELINE_DIR.mkdir(parents=True)
    for name, text in files.items():
        (BASELINE_DIR / name).write_text(text, encoding="utf-8", newline="")
    print(f"Wrote {len(files)} baseline files to {BASELINE_DIR}")


if __name__ == "__main__":
    sys.path.insert(0, str(APP_DIR))
    if "--freeze" not in sys.argv:
        sys.exit("Usage: python tests/prompt_baseline.py --freeze")
    freeze()
