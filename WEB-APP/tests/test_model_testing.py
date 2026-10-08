"""Model testing and model feedback (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md §10):
the model documents, Page# validation, one page's extraction, the feedback
files and their audit trail (feedback-status.json), the routes and the CLI."""

import dataclasses
import json
import time

import pytest

import app as app_module
import config
from ingest import feedback, model_testing
from webcommon import UserError

TYPE1 = "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf"
TYPE3 = "TYPE-3-USA-10-K-2025-Chubb.pdf"
TYPE6 = "TYPE-6-CANADA-40-F-2025-Magna.pdf"
TYPE7 = "TYPE-7-INDIA-20-F-2025-Infosys.pdf"      # made pending in the tests that need one


def _need(name):
    if not (config.MODEL_DOCS_DIR / name).exists():
        pytest.skip(f"Model document not found: {name}")
    return model_testing.resolve_model_doc(name)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """Feedback and snapshots in a folder of their own."""
    fb, mt = tmp_path / "Model-Feedback", tmp_path / "Model-Testing"
    fb.mkdir()
    mt.mkdir()
    monkeypatch.setattr(config, "MODEL_FEEDBACK_DIR", fb)
    monkeypatch.setattr(config, "MODEL_TESTING_DIR", mt)
    return fb, mt


@pytest.fixture
def pending(monkeypatch):
    """TYPE-7 without its rules, as a model document whose rules are not written yet."""
    from ingest.models import registry
    model = registry.load().by_id("TYPE-7")
    monkeypatch.setattr(model_testing, "registry_model",
                        lambda path: dataclasses.replace(model, profile=None) if path.name == TYPE7
                        else next((m for m in registry.load().models if m.file == path.name), None))


@pytest.fixture
def client(folders):
    return app_module.app.test_client()


def _result(page=12):
    return {"page": page, "printed": "9", "notices": [],
            "extracted": {"source": "package", "page_text": "21. INCOME TAXES\nCurrent tax | 1 | 2"},
            "context": {"pdf_sha256": "ab" * 32, "model_id": "TYPE-1", "profile": "in-indas-2020s"}}


def _wait(client, job_id):
    for _ in range(1200):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---------------------------------------------------------------- documents and Page#

def test_model_docs_listed_in_type_order():
    docs = model_testing.list_model_docs()
    if not docs:
        pytest.skip("No model documents")
    names = [d.name for d in docs]
    assert names == sorted(names, key=lambda n: int(n.split("-")[1]))
    assert all(d.parent == config.MODEL_DOCS_DIR for d in docs)       # not _candidates
    info = {d.name: model_testing.doc_info(d) for d in docs}
    if TYPE1 in info:
        assert info[TYPE1]["pages"] == 508 and info[TYPE1]["profile"] == "in-indas-2020s"
    if TYPE6 in info:
        assert info[TYPE6]["profile"] == "ca-40f-usgaap" and not info[TYPE6]["pending"]
    if TYPE7 in info:
        assert info[TYPE7]["profile"] == "in-20f-ifrs" and not info[TYPE7]["pending"]


@pytest.mark.parametrize("value", ["", "abc", "1.5", "-3", "12a", None, True])
def test_page_not_a_number(value):
    with pytest.raises(UserError, match=r"Enter a PDF page number \(1–508\)"):
        model_testing.validate_page(value, 508)


@pytest.mark.parametrize("value", [0, "525", 509])
def test_page_out_of_range(value):
    with pytest.raises(UserError, match=r"out of range: this PDF has 508 pages \(1–508\)"):
        model_testing.validate_page(value, 508)


def test_page_in_range():
    assert model_testing.validate_page(" 508 ", 508) == 508
    assert model_testing.validate_page(1, 508) == 1


@pytest.mark.parametrize("name", ["..\\Annual-reports\\BRK-1994.pdf", "_candidates", "README.md", ""])
def test_only_listed_model_docs(name):
    with pytest.raises(UserError, match="Model document not found"):
        model_testing.resolve_model_doc(name)


# ---------------------------------------------------------------- extraction

@pytest.mark.slow
def test_type1_notes_page():
    result = model_testing.extract_page(_need(TYPE1), 405)
    ex = result["extracted"]
    assert result["printed"] == "402" and result["notices"] == []
    assert ex["source"] == "package" and ex["section"] == "consolidated" and ex["page_type"] == "notes"
    c21 = next(u for u in ex["units_on_page"] if u["id"] == "C21")
    assert c21["pages"] == "405-407" and c21["starts_here"]
    assert "INCOME AND DEFERRED TAXES" in ex["page_text"]
    assert result["context"]["model_id"] == "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge"
    assert result["context"]["profile"] == "in-indas-2020s"
    text = model_testing.format_extracted(result)
    assert "PDF page 405 (printed 402)" in text and "C21" in text


@pytest.mark.slow
def test_type1_statement_page():
    ex = model_testing.extract_page(_need(TYPE1), 342)["extracted"]
    assert ex["page_type"] == "statement"
    assert [u["id"] for u in ex["units_on_page"]] == ["C-PL"]
    tax = next(ln for ln in ex["statement_lines"] if ln["label"] == "Current tax")
    assert [(v["raw"], v["period"]) for v in tax["values"]][:2] == [("5,606.07", "FY2026"), ("5,848.54", "FY2025")]


@pytest.mark.slow
def test_type1_sideways_page_image():
    path = _need(TYPE1)
    ex = model_testing.extract_page(path, 344)["extracted"]
    assert ex["page_type"] == "statement" and ex["units_on_page"][0]["id"] == "C-EQ"
    png = model_testing.page_png(path, 344, dpi=40)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.slow
def test_type3_statement_page():
    ex = model_testing.extract_page(_need(TYPE3), 108)["extracted"]
    assert ex["page_type"] == "statement" and ex["statement_lines"]


def test_pending_model_shows_raw_text(pending):
    result = model_testing.extract_page(_need(TYPE7), 3)
    assert result["extracted"]["source"] == "raw-pdf-text"
    assert len(result["extracted"]["page_text"]) > 100
    assert "pending" in result["notices"][0]
    assert "NOT extracted by the app's rules" in model_testing.format_extracted(result)


def test_extract_rejects_out_of_range(pending):
    with pytest.raises(UserError, match="out of range"):
        model_testing.extract_page(_need(TYPE7), 9999)


# ---------------------------------------------------------------- feedback store

def test_submit_writes_file_snapshot_and_status(folders):
    fb, mt = folders
    saved = feedback.submit(TYPE1, _result(), "  Heading missing  ", png=b"\x89PNG fake")
    name = saved["feedback_file"]
    assert name.startswith("TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-") and name.endswith(".json")
    record = json.loads((fb / name).read_text(encoding="utf-8"))
    assert record["pdf_file"] == TYPE1 and record["page"] == 12 and record["printed_page"] == "9"
    assert record["user_feedback"] == "Heading missing"
    assert record["extracted_data"]["page_text"].startswith("21. INCOME TAXES")
    assert record["context"]["page_image"].endswith(f"{name[:-5]}-p12.png")
    assert (mt / f"{name[:-5]}-p12.png").read_bytes() == b"\x89PNG fake"
    status = json.loads((fb / "feedback-status.json").read_text(encoding="utf-8"))
    (entry,) = status["entries"]
    assert entry["feedback_file"] == name and entry["status"] == "Reported" and entry["date_fixed"] is None
    assert entry["date_reported"] == record["reported_at"]
    assert entry["history"] == [{"status": "Reported", "at": record["reported_at"]}]


def test_same_second_gets_a_suffix(folders, monkeypatch):
    from datetime import datetime
    fixed = datetime(2026, 9, 28, 10, 15, 33).astimezone()
    monkeypatch.setattr(feedback, "_now", lambda: fixed)
    a = feedback.submit(TYPE1, _result(), "one")["feedback_file"]
    b = feedback.submit(TYPE1, _result(), "two")["feedback_file"]
    assert a == "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533.json"
    assert b == "TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge-20260928-101533-2.json"
    assert len(feedback.entries()) == 2


def test_comment_limits(folders):
    fb, _ = folders
    with pytest.raises(UserError, match="Describe what is wrong"):
        feedback.submit(TYPE1, _result(), "   ")
    with pytest.raises(UserError, match="401 characters; the limit is 400"):
        feedback.submit(TYPE1, _result(), "x" * 401)
    assert list(fb.iterdir()) == []
    feedback.submit(TYPE1, _result(), "₹" * 400)                   # characters, not bytes


def test_mark_fixed_keeps_the_trail(folders):
    name = feedback.submit(TYPE1, _result(), "wrong")["feedback_file"]
    entry = feedback.mark_fixed(name, "joined split headings", ["ingest/notes_index.py"], "tests/x.py::test_y")
    assert entry["status"] == "Fixed by Claude code" and entry["date_fixed"]
    assert [h["status"] for h in entry["history"]] == ["Reported", "Fixed by Claude code"]
    assert entry["history"][1]["summary"] == "joined split headings"
    assert entry["history"][1]["changed"] == ["ingest/notes_index.py"]
    with pytest.raises(UserError, match="already fixed"):
        feedback.mark_fixed(name, "again")
    with pytest.raises(UserError, match="not in feedback-status.json"):
        feedback.mark_fixed("nope.json", "x")
    with pytest.raises(UserError, match="--summary"):
        feedback.mark_fixed(name, " ")


def test_unreadable_status_file_is_never_overwritten(folders):
    fb, _ = folders
    (fb / "feedback-status.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(UserError, match="can't be read"):
        feedback.submit(TYPE1, _result(), "wrong")
    assert (fb / "feedback-status.json").read_text(encoding="utf-8") == "{ broken"
    assert [p.name for p in fb.iterdir()] == ["feedback-status.json"]


def test_read_feedback_only_listed_files(folders):
    fb, _ = folders
    name = feedback.submit(TYPE1, _result(), "wrong")["feedback_file"]
    assert feedback.read_feedback(name)["feedback_file"] == name
    (fb / "other.json").write_text("{}", encoding="utf-8")
    with pytest.raises(UserError):
        feedback.read_feedback("other.json")


def test_cli_list_and_fixed(folders, capsys):
    name = feedback.submit(TYPE1, _result(), "wrong")["feedback_file"]
    assert feedback.main(["list", "--open"]) == 0
    assert name in capsys.readouterr().out
    assert feedback.main(["fixed", name, "--summary", "done", "--changed", "a.py"]) == 0
    assert "Fixed by Claude code" in capsys.readouterr().out
    assert feedback.main(["list", "--open"]) == 0
    assert "0 report(s)" in capsys.readouterr().out
    assert feedback.main(["fixed", name, "--summary", "again"]) == 1


def test_cli_show_reextracts(folders, capsys, pending):
    _need(TYPE7)
    result = model_testing.extract_page(model_testing.resolve_model_doc(TYPE7), 3)
    name = feedback.submit(TYPE7, result, "no rules")["feedback_file"]
    assert feedback.main(["show", name]) == 0
    assert "The extraction is unchanged since the report" in capsys.readouterr().out


# ---------------------------------------------------------------- routes

def test_screen_and_nav_links(client):
    page = client.get("/model-testing")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="doc-select"' in html and 'maxlength="400"' in html and "Need correction" in html
    assert 'href="/ingest"' in html and 'href="/"' in html
    assert client.get("/Model-testing.html").status_code == 200
    for other in ("/", "/ingest"):
        assert 'href="/model-testing"' in client.get(other).get_data(as_text=True)


def test_api_docs(client):
    data = client.get("/api/model-testing/docs").get_json()
    if not data["docs"]:
        pytest.skip("No model documents")
    assert {"name", "pages", "pending", "profile"} <= set(data["docs"][0])


def test_api_docs_missing_folder(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODEL_DOCS_DIR", tmp_path / "missing")
    data = client.get("/api/model-testing/docs").get_json()
    assert data["docs"] == [] and "not found" in data["error"]


def test_api_extract_validates_page(client):
    _need(TYPE1)
    res = client.post("/api/model-testing/extract", json={"doc": TYPE1, "page": 525})
    assert res.status_code == 400
    assert res.get_json()["error"] == "Page 525 is out of range: this PDF has 508 pages (1–508)."
    res = client.post("/api/model-testing/extract", json={"doc": "..\\x.pdf", "page": 1})
    assert res.status_code == 400 and "not found" in res.get_json()["error"]


def test_api_extract_image_and_feedback(client, folders, pending):
    fb, mt = folders
    _need(TYPE7)
    job = client.post("/api/model-testing/extract", json={"doc": TYPE7, "page": "3"}).get_json()
    result = _wait(client, job["job_id"])["result"]
    assert result["doc"] == TYPE7 and result["page"] == 3
    assert "raw PDF text" in result["extracted_text"]
    image = client.get(result["image_url"])
    assert image.status_code == 200 and image.mimetype == "image/png"
    assert client.get(f"/api/model-testing/page-image?doc={TYPE7}&page=0").status_code == 400

    res = client.post("/api/model-testing/feedback", json={"doc": TYPE7, "page": 3, "comment": "x" * 401})
    assert res.status_code == 400
    saved = client.post("/api/model-testing/feedback",
                        json={"doc": TYPE7, "page": 3, "comment": "Tables not found",
                              "extracted": "ignored: the server extracts again"}).get_json()
    assert saved["status"] == "Reported"
    record = json.loads((fb / saved["feedback_file"]).read_text(encoding="utf-8"))
    assert record["extracted_data"]["source"] == "raw-pdf-text" and record["page"] == 3
    assert (mt / f"{saved['feedback_file'][:-5]}-p3.png").exists()

    entries = client.get("/api/model-testing/feedback").get_json()["entries"]
    assert entries[0]["feedback_file"] == saved["feedback_file"]
    one = client.get(f"/api/model-testing/feedback/{saved['feedback_file']}")
    assert one.get_json()["user_feedback"] == "Tables not found"
    assert client.get("/api/model-testing/feedback/feedback-status.json").status_code == 404


def test_box_aligns_table_columns():
    """Empty cells ("2017 |  | 1,088") would look shifted left in the box; its columns are padded."""
    text = model_testing._aligned_tables("Title\nYear | 2016 | 2017\n2016 | $1,039 | $1,704\n2017 |  | 1,088\nEnd")
    assert text.splitlines() == ["Title",
                                 "Year |   2016 |   2017",
                                 "2016 | $1,039 | $1,704",
                                 "2017 |        |  1,088",
                                 "End"]
