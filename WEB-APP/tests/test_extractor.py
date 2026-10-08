"""Milestone 4 acceptance tests: clipping, header stripping, tables."""

import pytest

from ingest import clip as extractor
from webcommon import parse_page_spec


@pytest.fixture(scope="module")
def note21(index):
    return extractor.extract_note(index, "consolidated", 21)


def test_note21_contains_key_tables(note21):
    text = note21["text"]
    assert "Income tax expense reported in the statement of profit and loss | 5,777.74 | 5,425.50" in text
    assert "Total deferred tax liability | 215.24 | 1,198.28" in text
    assert "Opening balance | 1,198.28 | 1,690.49" in text
    assert "At the effective income tax rate of 34.66%" in text


def test_note21_pages_labelled(note21):
    assert [p["pdf_page"] for p in note21["pages"]] == [405, 406, 407]
    assert "--- PDF page 405 (printed page 402) ---" in note21["text"]


def test_note21_has_no_neighbouring_notes(note21):
    text = note21["text"]
    assert "20. PROVISIONS" not in text
    assert "Provision for warranties" not in text      # Note 20 content (p.404)
    assert "22. TRADE PAYABLES" not in text
    assert "Dues to micro enterprises" not in text     # Note 22 content (p.408)


def test_running_headers_and_contd_removed(note21):
    text = note21["text"]
    assert "Notes to Consolidated Financial Statements" not in text
    assert "Integrated Annual Report" not in text
    assert "(CONTD.)" not in text


def test_rupee_and_ligatures_normalized(note21):
    assert "₹" in note21["text"]
    assert "`" not in note21["text"]
    assert "ﬁ" not in note21["text"]


def test_mid_page_note_is_clipped(index):
    # Note 25 shares page 411 with notes 24 and 26.
    text = extractor.extract_note(index, "consolidated", 25)["text"]
    assert "Total | 1,986.83 | 2,137.64" in text
    assert "26. COST OF RAW MATERIALS" not in text
    assert "REVENUE FROM OPERATIONS" not in text


def test_manual_pages(index):
    extract = extractor.extract_pages(index, [405])
    assert "21. INCOME AND DEFERRED TAXES" in extract["text"]


def test_parse_page_spec():
    assert parse_page_spec("405-407, 410") == [405, 406, 407, 410]
    assert parse_page_spec("7 7 8") == [7, 8]
    assert parse_page_spec("405 – 407") == [405, 406, 407]
    with pytest.raises(ValueError):
        parse_page_spec("abc")
    with pytest.raises(ValueError):
        parse_page_spec("10-5")


def _spans(rows):
    """[(y, [(text, x0, x1), ...]), ...] -> text spans as PyMuPDF gives them."""
    return [{"text": t, "bbox": (x0, y, x1, y + 8), "flags": 0} for y, cells in rows for t, x0, x1 in cells]


TRIANGLE = _spans([
    (100, [("Accident Year", 20, 60), ("2016", 97, 111), ("2017", 147, 162), ("2018", 198, 212)]),
    (110, [("2016", 20, 35), ("$", 67, 71), ("1,039", 93, 111), ("$", 118, 121), ("1,704", 143, 162),
           ("$", 168, 172), ("1,899", 194, 212)]),
    (120, [("2017", 20, 35), ("1,088", 143, 162), ("1,894", 194, 212)]),
    (130, [("2018", 20, 35), ("1,039", 194, 212)]),
    (140, [("Total", 20, 40), ("$", 168, 172), ("4,621", 194, 212)]),
    (150, [("Accident years prior to 2016", 20, 120), ("$126", 190, 212)]),
])


def test_triangle_columns_kept_by_profile_switch():
    """Loss triangles (Chubb 10-K p.157): figures stay under their columns, empty cells before them."""
    from ingest import profiles
    with profiles.using(profiles.get("us-10k-edgar")):
        lines = extractor._rows_to_text(TRIANGLE).splitlines()
    assert lines == [
        "Accident Year | 2016 | 2017 | 2018",
        "2016 | $1,039 | $1,704 | $1,899",
        "2017 |  | 1,088 | 1,894",
        "2018 |  |  | 1,039",
        "Total |  |  | $4,621",
        "Accident years prior to 2016 |  |  | $126",
    ]


def test_triangle_unchanged_without_the_switch():
    lines = extractor._rows_to_text(TRIANGLE).splitlines()
    assert lines[2:5] == ["2017 | 1,088 | 1,894", "2018 | 1,039", "Total | $4,621"]


def test_misaligned_row_left_as_is():
    from ingest import profiles
    spans = _spans([
        (100, [("Label", 20, 60), ("2025", 97, 111), ("2024", 147, 162), ("2023", 198, 212)]),
        (110, [("A", 20, 30), ("1", 105, 111), ("2", 155, 162), ("3", 205, 212)]),
        (120, [("B", 20, 30), ("7", 120, 128)]),       # between two columns: not guessed
    ])
    with profiles.using(profiles.get("us-10k-edgar")):
        assert extractor._rows_to_text(spans).splitlines()[2] == "B | 7"


def test_headings_above_triangle_kept_in_their_columns():
    """"Unaudited" over 2017, the period over 2018; headings joined by a small gap split per column."""
    from ingest import profiles
    spans = _spans([
        (80, [("Years Ended December 31", 140, 212)]),
        (90, [("(in millions of U.S. dollars)", 20, 88), ("Unaudited", 133, 162)]),
        (95, [("Gross", 90, 111), ("Net", 150, 162), ("Ceded", 170, 212)]),     # 8pt apart: joined, then split
    ]) + TRIANGLE
    with profiles.using(profiles.get("us-10k-edgar")):
        lines = extractor._rows_to_text(spans).splitlines()
    assert lines[:4] == [
        " |  |  | Years Ended December 31",
        "(in millions of U.S. dollars) |  | Unaudited",
        " | Gross | Net | Ceded",
        "Accident Year | 2016 | 2017 | 2018",
    ]
