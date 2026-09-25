"""Milestone 2 acceptance tests on the Bharat Forge FY2026 report."""

import notes_index


def test_golden_note_21_consolidated(index):
    note = notes_index.find_note(index, "consolidated", 21)
    assert note["title"] == "INCOME AND DEFERRED TAXES"
    assert (note["start_page"], note["end_page"]) == (405, 407)
    assert note["printed_pages"] == "402-404"


def test_section_ranges(index):
    cons = index["sections"]["consolidated"]
    stand = index["sections"]["standalone"]
    assert (cons["first_page"], cons["last_page"]) == (348, 503)
    assert (stand["first_page"], stand["last_page"]) == (210, 327)


def test_all_consolidated_notes_found_in_order(index):
    numbers = [n["no"] for n in index["sections"]["consolidated"]["notes"]]
    assert numbers == list(range(1, 60))


def test_all_standalone_notes_found_in_order(index):
    numbers = [n["no"] for n in index["sections"]["standalone"]["notes"]]
    assert numbers == list(range(1, 57))


def test_notes_do_not_overlap(index):
    for section in index["sections"].values():
        notes = section["notes"]
        for a, b in zip(notes, notes[1:]):
            assert a["end_page"] <= b["start_page"]
            assert a["start_page"] <= a["end_page"]


def test_heading_without_dot_and_subnote_only(index):
    # "5 INTANGIBLE ASSETS AND GOODWILL" has no dot after the number.
    assert notes_index.find_note(index, "consolidated", 5)["start_page"] == 381
    # Note 54 only appears as sub-headings 54.1 / 54.2.
    assert notes_index.find_note(index, "consolidated", 54)["start_page"] == 486


def test_standalone_tax_note(index):
    note = notes_index.find_note(index, "standalone", 21)
    assert note["title"] == "INCOME AND DEFERRED TAXES"
    assert (note["start_page"], note["end_page"]) == (259, 260)
