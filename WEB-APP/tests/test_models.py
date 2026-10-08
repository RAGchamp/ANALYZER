"""Model documents: every report is matched to a model document or refused as
a new model document (INFO/MODEL-DOCS-FUNCTIONALITY-PLAN.md §11)."""

import dataclasses
import io
import time
import shutil
from pathlib import Path

import pytest

import app as app_module
import config
from analyzer import jobs as analyzer_jobs
from ingest import notes_index, pipeline, profiles, statements
from ingest.__main__ import main as ingest_main
from ingest.models import acceptance, gap_report, match, registry
from ingest.models.fingerprint import FINGERPRINT_VERSION, fingerprint
from rptpkg import store

FIXTURE = Path(__file__).parent / "fixtures" / "Reliance-1976-1977.pdf"
MODEL_DOCS = config.MODEL_DOCS_DIR
SAMPLES = {"Bharat-Forge-IR-2026-conv-single-page.pdf": "TYPE-1", "Reliance-1976-1977.pdf": "TYPE-2",
           "Chubb-10-K-2025.pdf": "TYPE-3", "BRK-1994.pdf": "TYPE-4", "BRK-1968.pdf": "TYPE-5"}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A PC of its own: reports, cache and MODEL-DOCS/_candidates in tmp."""
    for name, sub in (("REPORTS_DIR", "reports"), ("CACHE_DIR", "cache"), ("HISTORY_DIR", "hist"),
                      ("THREADS_DIR", "threads"), ("ANALYSIS_HISTORY_DIR", "html"), ("MODEL_DOCS_DIR", "models")):
        (tmp_path / sub).mkdir()
        monkeypatch.setattr(config, name, tmp_path / sub)
    return tmp_path


def make_pending(monkeypatch, type_prefix):
    """Every model document has rules now: to test the "pending" path, one of them
    is registered without its profile in a copy of the registry."""
    reg = registry.load()
    models = [dataclasses.replace(m, profile=None) if m.id.startswith(type_prefix + "-") else m
              for m in reg.models]
    pending = registry.Registry(reg.version + 1000, reg.fingerprint_version, models)
    monkeypatch.setattr(registry, "load", lambda *a, **k: pending)
    return pending


def _model_pdf(type_prefix):
    model = registry.load().by_id(type_prefix)
    path = MODEL_DOCS / model.file
    if not path.exists():
        pytest.skip(f"model document not found: {path}")
    return model, path


# ---------------------------------------------------------------- the registry

def test_registry():
    reg = registry.load()
    assert reg.fingerprint_version == FINGERPRINT_VERSION        # else: python -m ingest.models refresh
    assert [m.id.split("-")[1] for m in reg.models] == [str(n) for n in range(1, 9)]
    assert {"-".join(m.id.split("-")[:2]): m.profile for m in reg.supported()} == {
        "TYPE-1": "in-indas-2020s", "TYPE-2": "in-schedule-vi-1970s", "TYPE-3": "us-10k-edgar",
        "TYPE-4": "us-10k-typewriter", "TYPE-5": "scanned-ocr", "TYPE-6": "ca-40f-usgaap",
        "TYPE-7": "in-20f-ifrs", "TYPE-8": "au-20f-ifrs"}
    assert not any(m.pending for m in reg.models)                         # every model has rules
    assert all(m.profile is None or m.profile in profiles.PROFILES for m in reg.models)
    assert reg.by_id("TYPE-3").short == "TYPE-3 USA 10-K 2025 Chubb"


def test_sample_reports_are_model_documents():
    """The five reports in Annual-reports are copies of TYPE-1 ... TYPE-5."""
    reg = registry.load()
    for name, prefix in SAMPLES.items():
        pdf = config.REPORTS_DIR / name
        if pdf.exists():
            assert reg.by_sha(store.pdf_sha256(pdf)).id.startswith(prefix + "-"), name


# ---------------------------------------------------------------- fingerprints and similarity

def test_fingerprint_is_deterministic(tmp_path):
    first = fingerprint(FIXTURE)
    copy = tmp_path / "renamed.pdf"
    shutil.copy2(FIXTURE, copy)
    assert fingerprint(copy) == first
    assert first["document"]["frameworks"] == ["companies-act-1956"] and first["structure"]["schedules"] >= 10


def _fp(**changes):
    fp = {"physical": {"text_share": 1.0, "page_shape": "letter", "pages": 100, "pages_band": "60-150",
                       "producer": "browser"},
          "typography": {"mono": 0.0, "serif": 0.9, "sans": 0.1, "bold": 0.05, "big_title_pages": 0.0,
                         "body_size": 8},
          "document": {"form": "10-K", "frameworks": ["us-gaap"], "currencies": ["usd"], "units": ["millions"]},
          "era": {"decade": 2020},
          "structure": {"notes_header_pages": 20.0, "note_headings": {"number-dot": 10.0},
                        "statement_title_pages": 4, "numbered_statement_titles": 0, "schedules": 0,
                        "item_8": True, "item_18": False}}
    for path, value in changes.items():
        group, key = path.split("__")
        fp[group] = dict(fp[group], **{key: value})
    return fp


def test_hard_constraints():
    base = _fp()
    assert match.conflict(base, _fp(physical__text_share=0.0)) == "one is scanned, the other has text"
    assert "typewriter" in match.conflict(base, _fp(typography__mono=0.99))
    assert "Form 20-F" in match.conflict(base, _fp(document__form="20-F"))
    assert match.conflict(base, _fp(document__form=None)) is None          # no cover form: no conflict
    assert match.similarity(base, base) == pytest.approx(1.0)
    other = _fp(document__frameworks=["ifrs"], structure__notes_header_pages=0.0,
                structure__note_headings={"section-numbered": 20.0})
    assert match.similarity(base, other) < 0.75


# ---------------------------------------------------------------- acceptance checks

def test_acceptance_fails_doctored_indexes(index):
    profile = profiles.get("in-indas-2020s")
    texts = ["Ind AS"]
    assert acceptance.evaluate(profile, index, texts)["passed"]
    no_notes = {**index, "sections": {k: {**s, "notes": []} for k, s in index["sections"].items()}}
    result = acceptance.evaluate(profile, no_notes, texts)
    assert not result["passed"] and "0 notes found (need 5)" in result["summary"]
    two = {**index, "sections": {k: {**s, "statements": s["statements"][:2]} for k, s in index["sections"].items()}}
    assert "2 of 4 statements found" in acceptance.evaluate(profile, two, texts)["summary"]
    assert not acceptance.evaluate(profile, index, ["no such wording"])["passed"]


def test_profile_switches_are_isolated():
    rows = [{"y0": 10, "x0": 50, "x1": 200, "size": 7, "bold": False, "text": "CONSOLIDATED BALANCE SHEETS"}]
    assert statements._title_row(rows, 792) is None                        # no profile: no plain-capitals titles
    with profiles.using(profiles.get("us-10k-typewriter")):
        assert statements._title_row(rows, 792)["text"] == "CONSOLIDATED BALANCE SHEETS"
    with profiles.using(profiles.get("us-10k-edgar")):
        assert statements._title_row(rows, 792) is None


# ---------------------------------------------------------------- matching

def test_model_document_matches_itself(sandbox):
    pdf = sandbox / "reports" / "Reliance.pdf"
    shutil.copy2(FIXTURE, pdf)
    result = match.match(pdf)
    assert (result.model.id, result.how, result.profile.id) == (
        "TYPE-2-INDIA-Ann-rpt-1977-Reliance", "model document", "in-schedule-vi-1970s")
    assert result.checks["passed"]


def test_unseen_report_matches_by_fingerprint(sandbox):
    """Not a model document (other bytes), but the same format: matched by its
    fingerprint and a passing trial extraction."""
    pdf = sandbox / "reports" / "Reliance-copy.pdf"
    pdf.write_bytes(FIXTURE.read_bytes() + b"\n% another copy\n")
    result = match.match(pdf)
    assert result.model.id.startswith("TYPE-2-") and result.how == "fingerprint" and result.score > 0.95
    package = pipeline.open_report(pdf)
    assert package.meta["model"].startswith("TYPE-2-") and package.meta["model_match"] == "fingerprint"
    assert package.meta["registry_version"] == registry.load().version


def test_doubt_means_new_model(sandbox, monkeypatch):
    """Q8: two different formats whose rules both pass, equally close: new model document."""
    pdf = sandbox / "reports" / "Reliance-copy.pdf"
    pdf.write_bytes(FIXTURE.read_bytes() + b"\n% another copy\n")
    reg = registry.load()
    twin = registry.Model(**{**reg.by_id("TYPE-2").to_json(), "id": "TYPE-99-X-Y-2000-Twin",
                             "sha256": "0" * 64, "profile": "in-indas-2020s"})
    doubled = registry.Registry(reg.version, reg.fingerprint_version, reg.models + [twin])
    monkeypatch.setattr(match, "trial", lambda *a, **k: ({}, {"passed": True, "summary": "checks passed",
                                                               "checks": []}))
    with pytest.raises(match.NewModelDocument, match="could be"):
        match.match(pdf, reg=doubled)


def test_registry_change_rematches_without_rebuilding(sandbox, monkeypatch):
    """Q7: a newer registry re-matches the report; same model, so only the meta changes."""
    pdf = sandbox / "reports" / "Reliance.pdf"
    shutil.copy2(FIXTURE, pdf)
    path = pipeline.ensure_package(pdf)
    reg = registry.load()
    newer = registry.Registry(reg.version + 1, reg.fingerprint_version, reg.models)
    monkeypatch.setattr(registry, "load", lambda *a, **k: newer)
    monkeypatch.setattr(notes_index, "build_index", lambda *a, **k: pytest.fail("rebuilt"))
    assert pipeline.ensure_package(pdf) == path
    assert pipeline.rptpkg.open_package(path).meta["registry_version"] == reg.version + 1


@pytest.mark.slow
@pytest.mark.parametrize("prefix", ["TYPE-7", "TYPE-8"])
def test_pending_models_are_new_model_documents(sandbox, prefix, monkeypatch):
    model, source = _model_pdf(prefix)
    make_pending(monkeypatch, prefix)
    pdf = sandbox / "reports" / source.name
    shutil.copy2(source, pdf)
    with pytest.raises(match.NewModelDocument) as info:
        pipeline.ensure_package(pdf)
    error = info.value
    assert error.pending_model == model.id and "extraction rules have not been written yet" in str(error)
    assert Path(error.gap_report).exists() and "## Evidence pages" in Path(error.gap_report).read_text("utf-8")
    assert pipeline.package_status(pdf)["status"] == "new model document"       # remembered


@pytest.mark.slow
@pytest.mark.parametrize("prefix", ["TYPE-1", "TYPE-2", "TYPE-3", "TYPE-4", "TYPE-6", "TYPE-7", "TYPE-8"])
def test_no_cross_match(sandbox, prefix):
    """With itself left out, a model document is not accepted by another format's rules."""
    model, pdf = _model_pdf(prefix)
    reg = registry.load()
    others = registry.Registry(reg.version, reg.fingerprint_version, [m for m in reg.models if m.id != model.id])
    try:
        other = match.match(pdf, reg=others)
    except match.NewModelDocument:
        return
    assert other.profile.id == model.profile, f"{model.id} also matches {other.model.id}"


# ---------------------------------------------------------------- the app and the CLI

@pytest.fixture
def pending_report(sandbox, monkeypatch):
    """A copy of a pending model document (TYPE-7 Infosys, its rules taken away) in the reports folder."""
    _, source = _model_pdf("TYPE-7")
    make_pending(monkeypatch, "TYPE-7")
    shutil.copy2(source, sandbox / "reports" / "Infosys-20-F.pdf")
    return "Infosys-20-F.pdf"


@pytest.mark.slow
def test_app_blocks_a_new_model_document(sandbox, pending_report, monkeypatch):
    client = app_module.app.test_client()
    data = client.post("/api/index", json={"report": pending_report}).get_json()
    assert data["new_model"]["pending_model"].startswith("TYPE-7-") and "sections" not in data
    assert data["gap_report_url"] == f"/gap-report?report={pending_report}"
    html = client.get(data["gap_report_url"]).get_data(as_text=True)
    assert "Gap report" in html and "Evidence pages" in html
    # blocked completely (Q1): no analysis, not even of chosen pages
    monkeypatch.setattr(analyzer_jobs, "run_claude", lambda *a, **k: pytest.fail("Claude called"))
    for body in ({"notes": ["C1"]}, {"pages": "3"}):
        job_id = client.post("/api/analyze", json={"report": pending_report, "question": "x", **body}
                             ).get_json()["job_id"]
        for _ in range(400):
            job = client.get(f"/api/jobs/{job_id}").get_json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "error" and job["error"].startswith("New model document")
    assert client.get(f"/statements?report={pending_report}").status_code == 400
    # proposing it copies the PDF and its gap report for the developer (Q3)
    folder = Path(client.post("/api/models/propose", json={"report": pending_report}).get_json()["folder"])
    assert (folder / pending_report).exists() and (folder / "GAP-REPORT.md").exists()
    assert folder.parent == config.MODEL_DOCS_DIR / "_candidates"
    rows = client.get("/api/ingest/reports").get_json()["reports"]
    assert rows[0]["status"] == "new model document"
    models = client.get("/api/models").get_json()
    assert len(models["models"]) == 8 and [m["pending"] for m in models["models"]].count(True) == 1


@pytest.mark.slow
def test_describe_with_claude(sandbox, pending_report, monkeypatch):
    """Q9: optional, off by default; Claude's description is added to the gap report."""
    client = app_module.app.test_client()
    client.post("/api/index", json={"report": pending_report})
    seen = {}

    def fake(prompt, folder, timeout, **kw):
        seen["files"] = sorted(p.name for p in Path(folder).glob("*.png"))
        return "The notes are headed 'Note 1'.", {}

    path = gap_report.describe(config.REPORTS_DIR / pending_report, [3, 4], run=fake)
    assert seen["files"] == ["page-3.png", "page-4.png"]
    text = path.read_text(encoding="utf-8")
    assert "## Evidence pages" in text and "## Claude's description (pages 3, 4)" in text


def test_cli_exit_code_for_new_model(sandbox, monkeypatch):
    pdf = sandbox / "reports" / "Reliance.pdf"
    shutil.copy2(FIXTURE, pdf)
    assert ingest_main([str(pdf), "--format", "modern"]) == 3             # no modern model's rules pass
    assert ingest_main([str(pdf)]) == 0
