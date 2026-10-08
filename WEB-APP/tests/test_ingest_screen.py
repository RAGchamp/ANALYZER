"""The Ingest screen (INFO/SPLIT-FUNCTIONALITY-PLAN.md §6.3, Phase 5): report
status, ingesting one report, page images, and moving packages between PCs."""

import io
import json
import shutil
import time
from pathlib import Path

import pytest

import app as app_module
import config
from ingest import pipeline
from rptpkg import store

FIXTURES = Path(__file__).parent / "fixtures"
OLD = "Reliance-1976-1977.pdf"
BRK = "BRK-1968.pdf"


def _sandbox(tmp_path, monkeypatch, name="pc1"):
    """A PC of its own: reports folder and cache."""
    root = tmp_path / name
    for setting, sub in (("REPORTS_DIR", "reports"), ("CACHE_DIR", "cache"), ("HISTORY_DIR", "hist"),
                         ("THREADS_DIR", "threads"), ("ANALYSIS_HISTORY_DIR", "html")):
        (root / sub).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, setting, root / sub)
    return root / "reports"


def _wait(client, job_id):
    for _ in range(600):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            assert job["status"] == "done", job
            return job["result"]
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def client(tmp_path, monkeypatch):
    reports = _sandbox(tmp_path, monkeypatch)
    shutil.copy2(FIXTURES / OLD, reports / OLD)
    client = app_module.app.test_client()
    client.reports = reports
    return client


def test_screen_and_status(client):
    html = client.get("/ingest").get_data(as_text=True)
    assert "Ingest reports" in html and "Import a package" in html and "ingest.js" in html
    assert 'href="/ingest"' in client.get("/").get_data(as_text=True)          # linked from the Analyze screen
    rows = client.get("/api/ingest/reports").get_json()["reports"]
    assert [(r["name"], r["status"]) for r in rows] == [(OLD, "not ingested")]

    row = _wait(client, client.post("/api/ingest/run", json={"report": OLD}).get_json()["job_id"])
    assert row["status"] == "up to date" and row["format"] == "legacy"
    assert any(line.startswith("Links:") for line in row["quality"])
    assert row["model"].startswith("TYPE-2 INDIA")
    # forcing a format only chooses which models to try: the modern models' rules fail here
    row = _wait(client, client.post("/api/ingest/run", json={"report": OLD, "format": "modern", "force": True})
                .get_json()["job_id"])
    assert row["status"] == "new model document" and "doesn't match any" in row["new_model"]["message"]
    assert row["format"] == "legacy"                                   # the package it had is kept
    row = _wait(client, client.post("/api/ingest/run", json={"report": OLD, "format": "auto", "force": True})
                .get_json()["job_id"])
    assert row["format"] == "legacy"
    assert client.post("/api/ingest/run", json={"report": OLD, "format": "weird"}).status_code == 400


def test_stale_package_is_reported(client, monkeypatch):
    pipeline.ensure_package(client.reports / OLD)
    monkeypatch.setattr(pipeline, "INGESTER_VERSION", pipeline.INGESTER_VERSION + 1)
    assert client.get("/api/ingest/reports").get_json()["reports"][0]["status"] == "stale"


def test_page_image(client):
    res = client.get(f"/page-image?report={OLD}&page=13")
    assert res.status_code == 200 and res.mimetype == "image/png" and res.data[:4] == b"\x89PNG"
    assert client.get(f"/page-image?report={OLD}&page=999").status_code == 400
    assert client.get("/page-image?report=..%5Csecret.pdf&page=1").status_code == 400


def test_export_and_import_on_another_pc(tmp_path, monkeypatch):
    reports = _sandbox(tmp_path, monkeypatch, "pc1")
    shutil.copy2(FIXTURES / OLD, reports / OLD)
    client = app_module.app.test_client()
    assert client.get(f"/api/packages/export?report={OLD}").status_code == 400      # not ingested yet
    client.post("/api/index", json={"report": OLD})
    exported = client.get(f"/api/packages/export?report={OLD}")
    assert exported.status_code == 200 and exported.data[:15] == b"SQLite format 3"

    # the second PC has the PDF under another name, and no package
    reports2 = _sandbox(tmp_path, monkeypatch, "pc2")
    shutil.copy2(FIXTURES / OLD, reports2 / "Reliance-copy.pdf")
    monkeypatch.setattr(pipeline.notes_index, "build_index", lambda *a, **k: pytest.fail("rebuilt"))
    res = client.post("/api/packages/import", data={"file": (io.BytesIO(exported.data), "x.rptpkg.db")},
                      content_type="multipart/form-data")
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["matching_reports"] == ["Reliance-copy.pdf"]
    data = client.post("/api/index", json={"report": "Reliance-copy.pdf"}).get_json()
    assert data["format"] == "legacy" and data["report"] == "Reliance-copy.pdf"

    bad = client.post("/api/packages/import", data={"file": (io.BytesIO(b"not a package"), "x.rptpkg.db")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400 and "not a report package" in bad.get_json()["error"]


def test_imported_scanned_report_needs_no_transcription(tmp_path, monkeypatch):
    """A scanned report's package, transcribed on another PC, works without its OCR cache."""
    source = config.REPORTS_DIR / BRK
    if not source.exists():
        pytest.skip("BRK-1968.pdf not found")
    reports = _sandbox(tmp_path, monkeypatch, "pc1")
    shutil.copy2(source, reports / BRK)
    cache = config.CACHE_DIR
    shutil.copytree(FIXTURES / "brk1968.ocr", cache / "BRK-1968.ocr")
    manifest_path = cache / "BRK-1968.ocr" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stat = (reports / BRK).stat()
    manifest.update(size=stat.st_size, mtime=stat.st_mtime)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    exported = pipeline.ensure_package(reports / BRK).read_bytes()

    reports2 = _sandbox(tmp_path, monkeypatch, "pc2")
    shutil.copy2(source, reports2 / BRK)
    client = app_module.app.test_client()
    data = client.post("/api/index", json={"report": BRK}).get_json()
    assert data.get("needs_transcription")                              # nothing to go on yet
    client.post("/api/packages/import", data={"file": (io.BytesIO(exported), "brk.rptpkg.db")},
                content_type="multipart/form-data")
    data = client.post("/api/index", json={"report": BRK}).get_json()
    assert data["format"] == "transcribed" and not data.get("needs_transcription")
    assert store.find_package(reports2 / BRK) is not None
