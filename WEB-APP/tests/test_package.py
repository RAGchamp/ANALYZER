"""Report packages (INFO/SPLIT-FUNCTIONALITY-PLAN.md §5, §11): the index comes
back exactly, packages are reproducible, found again under another name,
rebuilt when stale, and the analyzer works from them without reading a PDF."""

import json
import shutil
import sqlite3
import time
from pathlib import Path

import pymupdf
import pytest

import app as app_module
import config
import rptpkg
from analyzer import jobs as analyzer_jobs
from ingest import clip, notes_index, pipeline
from rptpkg import store

FIXTURE = Path(__file__).parent / "fixtures" / "Reliance-1976-1977.pdf"
OLD = "Reliance-1976-1977.pdf"


@pytest.fixture
def reports(tmp_path, monkeypatch):
    """A private reports folder and cache holding the small old-format report."""
    folder = tmp_path / "reports"
    folder.mkdir()
    shutil.copy2(FIXTURE, folder / OLD)
    for name, sub in (("REPORTS_DIR", "reports"), ("CACHE_DIR", "cache"), ("HISTORY_DIR", "hist"),
                      ("THREADS_DIR", "threads"), ("ANALYSIS_HISTORY_DIR", "html")):
        monkeypatch.setattr(config, name, tmp_path / sub)
        (tmp_path / sub).mkdir(exist_ok=True)
    monkeypatch.setattr(config, "INPUT_FILE", tmp_path / "in.txt")
    monkeypatch.setattr(config, "OUTPUT_FILE", tmp_path / "out.txt")
    return folder


def _tables(path):
    """Every table of a package, minus the build time stamps."""
    conn = sqlite3.connect(path)
    try:
        out = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            if name == "meta":
                rows = [r for r in rows if r[0] not in ("built_at", "ingested_at", "ingest_seconds",
                                                        "quality", "index")]
            out[name] = sorted(map(repr, rows))
        return out
    finally:
        conn.close()


def test_index_round_trip(reports):
    pdf = reports / OLD
    built = notes_index.build_index(pdf)
    package = pipeline.open_report(pdf)
    expected = json.loads(json.dumps(built))
    expected["built_at"] = package.index["built_at"] = "-"
    assert package.index == expected


def test_unit_and_page_texts_are_what_the_ingester_clipped(reports):
    pdf = reports / OLD
    package = pipeline.open_report(pdf)
    index = package.index
    for unit_id, number in (("SCH-E", "SCH-E"), ("N25", "N25")):
        stored = package.unit_text("standalone", unit_id)
        assert stored["text"] == clip.extract_note(index, "standalone", number)["text"]
    extract = clip.extract_pages(index, [13, 14])
    assert extract["text"].endswith(package.page(14)["text"])
    assert package.page(99) is None
    assert package.meta["pdf_sha256"] == store.pdf_sha256(pdf)
    assert package.meta["schema_version"] == rptpkg.SCHEMA_VERSION


def test_reproducible(reports):
    pdf = reports / OLD
    first = _tables(pipeline.build_package(pdf))
    second = _tables(pipeline.build_package(pdf))
    assert first == second


def test_up_to_date_package_is_reused(reports, monkeypatch):
    pdf = reports / OLD
    path = pipeline.ensure_package(pdf)
    monkeypatch.setattr(notes_index, "build_index", lambda *a, **k: pytest.fail("rebuilt"))
    assert pipeline.ensure_package(pdf) == path


def test_renamed_pdf_finds_its_package(reports, monkeypatch):
    pipeline.ensure_package(reports / OLD)
    renamed = reports / "Reliance-renamed.pdf"
    shutil.copy2(reports / OLD, renamed)
    monkeypatch.setattr(notes_index, "build_index", lambda *a, **k: pytest.fail("rebuilt"))
    package = pipeline.open_report(renamed)
    assert package.meta["pdf_name"] == "Reliance-renamed.pdf"
    assert package.index["pdf_name"] == "Reliance-renamed.pdf"
    assert store.package_path(renamed).exists() and store.package_path(reports / OLD).exists()


def test_changed_pdf_gets_a_new_package(reports):
    pdf = reports / OLD
    old = pipeline.ensure_package(pdf)
    time.sleep(0.05)
    with open(pdf, "ab") as handle:           # still a valid PDF, different bytes
        handle.write(b"\n% changed\n")
    new = pipeline.ensure_package(pdf)
    assert new != old and new.exists() and not old.exists()


def test_stale_packages_are_rebuilt(reports, monkeypatch):
    pdf = reports / OLD
    path = pipeline.ensure_package(pdf)
    rptpkg.writer.update_meta(path, schema_version="0.9")
    with pytest.raises(rptpkg.PackageError, match="schema version 0.9"):
        rptpkg.open_package(path)
    assert pipeline.open_report(pdf).meta["schema_version"] == rptpkg.SCHEMA_VERSION
    monkeypatch.setattr(pipeline, "INGESTER_VERSION", pipeline.INGESTER_VERSION + 1)
    calls = []
    real = notes_index.build_index
    monkeypatch.setattr(notes_index, "build_index", lambda *a, **k: calls.append(1) or real(*a, **k))
    pipeline.ensure_package(pdf)
    assert calls == [1]


def test_analyzer_never_opens_a_pdf(reports, monkeypatch):
    """Once ingested, every analyzer step works with PDF reading switched off."""
    client = app_module.app.test_client()
    client.post("/api/index", json={"report": OLD})

    def no_pdf(*args, **kwargs):
        raise AssertionError("the analyzer opened a PDF")

    monkeypatch.setattr(pymupdf, "open", no_pdf)
    monkeypatch.setattr(pymupdf, "Document", no_pdf)
    sent = []
    monkeypatch.setattr(analyzer_jobs, "run_claude", lambda prompt, *a, **k: sent.append(prompt) or "ok")

    def wait(job_id):
        for _ in range(400):
            job = client.get(f"/api/jobs/{job_id}").get_json()
            if job["status"] != "running":
                assert job["status"] == "done", job
                return job["result"]
            time.sleep(0.02)
        raise AssertionError("job did not finish")

    assert client.post("/api/index", json={"report": OLD}).status_code == 200
    result = wait(client.post("/api/analyze", json={
        "report": OLD, "question": "Fixed assets", "notes": ["SCH-E"]}).get_json()["job_id"])
    wait(client.post("/api/followup", json={
        "thread_id": result["thread_id"], "question": "And schedule C?"}).get_json()["job_id"])
    wait(client.post("/api/analyze", json={
        "report": OLD, "question": "These pages", "pages": "13-14"}).get_json()["job_id"])
    assert client.get(f"/statements?report={OLD}").status_code == 200
    assert "=== Schedule 'C'" in sent[1] and "--- PDF page 14" in sent[2]
    thread = json.loads((config.THREADS_DIR / f"{result['thread_id']}.json").read_text(encoding="utf-8"))
    assert thread["pdf_sha256"] and thread["schema_version"] == rptpkg.SCHEMA_VERSION


def test_reports_list_shows_ingested(reports):
    client = app_module.app.test_client()
    assert client.get("/api/reports").get_json()["reports"][0]["indexed"] is False
    client.post("/api/index", json={"report": OLD})
    assert client.get("/api/reports").get_json()["reports"][0]["indexed"] is True


def test_cli(reports, capsys):
    from ingest.__main__ import main
    assert main([str(reports / OLD)]) == 0
    out = capsys.readouterr().out
    assert "15 schedules, 34 notes, 3 report sections" in out and "Package:" in out
