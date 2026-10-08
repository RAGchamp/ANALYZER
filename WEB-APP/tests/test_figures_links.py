"""Statement lines, figures, links and checks (INFO/SPLIT-FUNCTIONALITY-PLAN.md
§6.2, §7, §11), on the real Bharat Forge and Chubb reports."""

import pytest

from ingest import figures, links, quality


def _line(package, label, section="consolidated", unit=None):
    rows = package.query("SELECT * FROM lines WHERE label = ? AND section_id = ?"
                         + (" AND unit_id = ?" if unit else "") + " ORDER BY id",
                         (label, section, unit) if unit else (label, section))
    assert rows, f"no line {label!r}"
    return rows[0]


def _values(package, line):
    return {v["period_label"]: v for v in package.values(line["id"])}


def _links(package, line, kinds=("references", "ties_to", "mentions")):
    return {(e["dst"], e["kind"]) for e in package.edges(src=line["id"]) if e["kind"] in kinds}


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("raw, expected", [
    ("1,198.28", (1198.28, False)),
    ("5,95,11,000", (59511000.0, False)),         # Indian grouping (old reports)
    ("(1,828)", (-1828.0, False)),
    ("$10,622", (10622.0, False)),
    ("$ 46,002,417", (46002417.0, False)),
    ("₹ 215.24", (215.24, False)),
    ("—", (0.0, True)), ("–", (0.0, True)), ("-", (0.0, True)), ("––", (0.0, True)),
    ("(.17)", (-0.17, False)),
    ("11,57,05.424", (None, False)),              # a retyping error: kept raw, no number
    ("728,00[?]", (None, False)),                 # unreadable in the scan
    ("n.a.", (None, False)),
])
def test_parse_value(raw, expected):
    assert figures.parse_value(raw) == expected


@pytest.mark.parametrize("heading, fiscal, expected", [
    ("Year ended March 31, 2026", (3, 31), ("2026-03-31", "FY2026")),
    ("As at March 31, 2025", (3, 31), ("2025-03-31", "FY2025")),
    ("December 31, 2025", (12, 31), ("2025-12-31", "FY2025")),
    ("2024", (12, 31), ("2024-12-31", "FY2024")),
    ("As at 30th September, 1977", (9, 30), ("1977-09-30", "FY1977")),
    ("Balance as on April 1, 2025", (3, 31), ("2025-04-01", "2025-04-01")),
    ("Previous Year Rs.", None, (None, "previous year")),
    ("FY 2025-26", (3, 31), ("2026-03-31", "FY2026")),
    ("Securities premium", (3, 31), (None, None)),
    ("", None, (None, None)),
])
def test_parse_period(heading, fiscal, expected):
    assert figures.parse_period(heading, fiscal) == expected


def test_label_references():
    def refs(label):
        import re
        notes = [n for m in links.NOTE_IN_LABEL_RE.finditer(label) for n in re.findall(r"\d+", m.group(1))]
        return notes + [m.group(1) for m in links.SCHEDULE_IN_LABEL_RE.finditer(label)]
    assert refs("Equity in earnings (notes 5 and 6) (Schedule XVII)") == ["5", "6", "XVII"]
    assert refs("Share application monies (Refer Note No. 15 in Schedule 'O')") == ["15", "O"]
    assert refs("Scheduled payments") == [] and refs("Notes payable") == []


# ---------------------------------------------------------------- Bharat Forge (Ind AS)

def test_bharat_figures(package):
    assert package.meta["units"] == "In ₹Million"
    current_tax = _values(package, _line(package, "Current tax", unit="C-PL"))
    assert current_tax["FY2026"]["value"] == 5606.07 and current_tax["FY2025"]["raw"] == "5,848.54"
    assert current_tax["FY2026"]["period_end"] == "2026-03-31"
    dtl = _line(package, "(c) Deferred tax liabilities (net)", unit="C-BS")
    assert dtl["note_ref"] == "21"
    assert _values(package, dtl)["FY2026"]["value"] == 215.24
    assert {("C21", "references"), ("C21", "ties_to")} <= _links(package, dtl)


def test_bharat_notes_column_references_resolve(package):
    """Every note number in a Notes column links to that note."""
    notes = {f"{'C' if k == 'consolidated' else 'S'}{n['no']}"
             for k, s in package.index["sections"].items() for n in s["notes"]}
    cited = package.query("SELECT * FROM lines WHERE note_ref GLOB '[0-9]*'")
    assert len(cited) > 80
    for line in cited:
        refs = [e["dst"] for e in package.edges(src=line["id"]) if e["kind"] == "references"]
        prefix = "C" if line["section_id"] == "consolidated" else "S"
        number = int("".join(ch for ch in line["note_ref"] if ch.isdigit())[:3].rstrip() or 0)
        if f"{prefix}{number}" in notes:
            assert f"{prefix}{number}" in refs, line


def test_bharat_checks(package):
    balance = [c for c in package.checks() if c["kind"] == "balance"]
    assert len(balance) == 4 and all(c["status"] == "ok" for c in balance)
    subtotals = [c for c in package.checks() if c["kind"] == "subtotal"]
    assert not [c for c in subtotals if c["status"] == "fail"]
    # simple sums verify; "Profit before tax" (income - expenses) is only "unverified"
    assert len([c for c in subtotals if c["status"] == "ok"]) >= 20
    by_line = {c["subject"]: c["status"] for c in subtotals}
    assert by_line[_line(package, "Total expenses [ii]", unit="C-PL")["id"]] == "ok"
    assert by_line[_line(package, "Profit before tax", unit="C-PL")["id"]] == "unverified"


def test_title_matches_avoid_weak_words(package):
    assert ("C38", "mentions") not in _links(package, _line(package, "Interest income", unit="C-CF"))
    assert not _links(package, _line(package, "Purchase of stock in trade", unit="C-PL"))
    assert ("C22", "mentions") in _links(package, _line(package, "Increase in trade payables", unit="C-CF"))


# ---------------------------------------------------------------- Chubb (US 10-K, no Notes column)

def test_chubb_figures_and_periods(ten_k_package):
    tax = _values(ten_k_package, _line(ten_k_package, "Income tax expense", unit="C-PL"))
    assert {p: v["value"] for p, v in tax.items()} == {"FY2025": 2422.0, "FY2024": 1815.0, "FY2023": 511.0}
    assert tax["FY2025"]["period_end"] == "2025-12-31"
    assert ten_k_package.meta["fiscal_month_day"] == [12, 31]


@pytest.mark.parametrize("label, unit, note", [
    ("Income tax expense", "C-PL", "C12"),
    ("Deferred tax liabilities", "C-BS", "C12"),
    ("Deferred tax assets", "C-BS", "C12"),
    ("Goodwill", "C-BS", "C7"),
    ("Short-term debt", "C-BS", "C13"),
    ("Unpaid losses and loss expenses", "C-BS", "C8"),
    ("Deferred policy acquisition costs", "C-BS", "C6"),
])
def test_chubb_links(ten_k_package, label, unit, note):
    assert note in {dst for dst, _ in _links(ten_k_package, _line(ten_k_package, label, unit=unit))}


def test_chubb_no_coincidental_links(ten_k_package):
    dpac = {dst for dst, _ in _links(ten_k_package, _line(ten_k_package, "Deferred policy acquisition costs",
                                                             unit="C-BS"))}
    assert dpac == {"C6"}                                   # not "Acquisitions"
    accrued = _line(ten_k_package, "Accrued investment income", unit="C-BS")
    assert ("C8", "ties_to") not in _links(ten_k_package, accrued)   # a 4-digit coincidence


def test_chubb_checks(ten_k_package):
    checks = ten_k_package.checks()
    assert [c["status"] for c in checks if c["kind"] == "balance"] == ["ok", "ok"]
    carries = [c for c in checks if c["kind"] == "carries"]
    assert carries and carries[0]["status"] == "ok" and "10,622" in carries[0]["message"]
    total_assets = _line(ten_k_package, "Total assets", unit="C-BS")
    assert [c["status"] for c in checks if c["subject"] == total_assets["id"] and c["kind"] == "subtotal"] == ["ok"]


def test_quality_report(ten_k_package, package):
    text = quality.report_text(ten_k_package)
    assert "statements: P&L p.108 · BS p.107 · CF p.110 · Equity p.109" in text
    assert "periods FY2025, FY2024, FY2023" in text
    assert "Links:" in text and "Checks:" in text
    assert package.meta["quality"]["links"]["linked"] >= 240
