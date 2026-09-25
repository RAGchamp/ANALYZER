"""Milestone 4 acceptance tests: clipping, header stripping, tables."""

import pytest

import extractor


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
    assert extractor.parse_page_spec("405-407, 410") == [405, 406, 407, 410]
    assert extractor.parse_page_spec("7 7 8") == [7, 8]
    assert extractor.parse_page_spec("405 – 407") == [405, 406, 407]
    with pytest.raises(ValueError):
        extractor.parse_page_spec("abc")
    with pytest.raises(ValueError):
        extractor.parse_page_spec("10-5")
