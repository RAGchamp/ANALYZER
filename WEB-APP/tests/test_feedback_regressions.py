"""One regression test per fixed model feedback report (INFO/MODEL-FEEDBACK-FUNCTIONALITY-PLAN.md §8,
.claude/CLAUDE.md §7a).

Name each test after its feedback file and assert the corrected extraction of that
page, e.g.

    @pytest.mark.slow
    def test_type1_20260928_101533_p405():
        ex = _page("TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf", 405)
        assert ...

Then record the fix: python -m ingest.feedback fixed <file> --test tests/test_feedback_regressions.py::<test>
"""

import pytest

import config
from ingest import model_testing


def _page(doc, page):
    """The extracted data of one model document page, as the Model testing screen shows it."""
    if not (config.MODEL_DOCS_DIR / doc).exists():
        pytest.skip(f"Model document not found: {doc}")
    return model_testing.extract_page(model_testing.resolve_model_doc(doc), page)["extracted"]


def test_type1_20260928_105411_p22():
    """Value Creation Model (an infographic): the flat page text read the strategy boxes, the diagram,
    the lists and the KPI mixed together. It is now read into blocks in reading order under its
    report section (INFO/ANNUAL-REPORT-NON-FIN-STMTS-NOTES-EXTRACTION-PLAN.md §6.1)."""
    ex = _page("TYPE-1-INDIA-Ann-rpt-2026-Bharat-Forge.pdf", 22)
    n = ex["narrative"]
    assert n["section"]["title"] == "Value Creation Model" and n["layout_kind"] == "designed"
    kinds = [b["kind"] for b in n["blocks"]]
    assert kinds[:2] == ["heading", "panel"] and n["blocks"][0]["text"] == "Strategy"
    strategy = [i for c in n["blocks"][1]["children"] if c["kind"] == "list" for i in c["items"]]
    assert [s[:2] for s in strategy] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    sources = next(b for b in n["blocks"] if b["kind"] == "list")
    assert sources["items"][0] == "Rich culture of research and engineering and product differentiation"
    assert len(sources["items"]) == 5
    diagram = next(b for b in n["blocks"] if b["kind"] == "diagram")
    assert set(diagram["labels"]) >= {"Principal Activities", "Manufacturing", "Customer relationships"}
    assert n["blocks"][-1] == {**n["blocks"][-1], "kind": "metric", "value": "238,355 tons", "label": "Total tonnage"}
    # the running header is cut from the blocks, and the page's heading order is kept
    assert kinds.index("diagram") < kinds.index("metric")
    assert "CORPORATE OVERVIEW" not in " ".join(str(b) for b in n["blocks"])


@pytest.mark.slow
def test_type3_20261005_215139_p157():
    """Loss development triangle (Net Cumulative Paid, Overseas General Non-Casualty Short-tail): each
    accident year's figures were written from the left, so 2017's first figure read as 2016's and the
    triangle looked reversed. They now stand under their own columns, with empty cells before them
    (profile switch aligned_note_columns)."""
    ex = _page("TYPE-3-USA-10-K-2025-Chubb.pdf", 157)
    rows = {line.split(" | ")[0]: line.split(" | ") for line in ex["page_text"].splitlines() if " | " in line}
    heading = rows["Accident Year"]
    assert heading[1:] == [str(y) for y in range(2016, 2026)]
    assert rows["2016"][1:] == ["$1,039", "$1,704", "$1,899", "$1,969", "$1,994",
                                "$2,003", "$2,009", "$2,013", "$2,018", "$2,027"]
    assert rows["2017"][1:3] == ["", "1,088"]
    assert rows["2020"] == ["2020", "", "", "", "", "1,138", "1,791", "1,935", "2,053", "2,053", "2,095"]
    assert rows["2025"] == ["2025"] + [""] * 9 + ["1,477"]
    assert rows["Total"] == ["Total"] + [""] * 9 + ["$21,863"]
    # each accident year's column holds one more figure than the year before: 2016 one, 2025 ten
    years = [rows[str(y)] for y in range(2016, 2026)]
    assert [sum(1 for r in years if len(r) > c and r[c]) for c in range(1, 11)] == list(range(1, 11))
    # a one-column table below it is not padded
    assert rows["Accident years prior to 2016"][1:] in (["$126"], ["$(17)"])
    # the headings above stand where they are printed: "Unaudited" over 2016-2024's last column,
    # "Years Ended December 31" over 2025
    lines = ex["page_text"].splitlines()
    head = lines.index(" | ".join(heading))
    assert lines[head - 1].split(" | ") == ["(in millions of U.S. dollars)"] + [""] * 8 + ["Unaudited"]
    assert lines[head - 2].split(" | ") == [""] * 10 + ["Years Ended December 31"]
