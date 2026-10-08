"""The analyzer's use of the package links (INFO/SPLIT-FUNCTIONALITY-PLAN.md §8,
Phase 4) and the US 10-K format (Q6)."""

from analyzer import context, linked, note_selector
from analyzer.note_selector import all_note_choices
from claude_client import ClaudeError

TAX_Q = "Analyze the company's Tax related liabilities and comment on it."


def test_question_words():
    assert linked.question_words(TAX_Q) == {"tax"}
    assert {"debt", "borrowing"} <= linked.question_words("How is the debt position?")
    assert linked._label_words("Profit before tax") == {"profit"}          # "before tax" is incidental
    assert "liability" in linked._label_words("Current tax liabilities (net)")


def test_statement_hints(package, ten_k_package):
    hints = linked.statement_hints(package, TAX_Q, ["consolidated"])
    assert hints[0]["id"] == "C21"
    assert "(c) Deferred tax liabilities (net) (Balance Sheet)" in hints[0]["lines"]
    assert linked.statement_hints(ten_k_package, "Analyze the tax position", ["consolidated"])[0]["id"] == "C12"
    assert linked.statement_hints(package, "Tell me about the weather") == []


def test_selector_shows_hints_and_falls_back_on_them(package, monkeypatch):
    index = package.index
    hints = [{"id": "C21", "lines": ["Current tax (P&L)"]}, {"id": "S21", "lines": ["x"]}]
    prompt = note_selector._selector_prompt(index, TAX_Q, "consolidated",
                                            note_selector.hints_text(hints, {"C21"}))
    assert "SUGGESTED BY THE FINANCIAL STATEMENTS" in prompt and "- C21: Current tax (P&L)" in prompt
    assert "S21: x" not in prompt                                       # outside the scope
    plain = note_selector._selector_prompt(index, TAX_Q, "consolidated")
    assert "SUGGESTED" not in plain and "\n\nChoose the notes" in plain

    def offline(*args, **kwargs):
        raise ClaudeError("offline")

    monkeypatch.setattr(note_selector, "run_claude", offline)
    result = note_selector.select_notes(index, "Comment on the reserves", hints=[{"id": "C21", "lines": ["y"]}])
    assert result["method"] == "keywords" and result["notes"][0]["id"] == "C21"
    assert result["hints"] == [{"id": "C21", "lines": ["y"]}]


def test_linked_block(package):
    choices = {c["id"]: c for c in all_note_choices(package.index, "both")}
    block = linked.linked_block(package, [choices["C21"]], ["consolidated"])
    assert block.startswith("----- LINKED FIGURES")
    assert ("- (c) Deferred tax liabilities (net) (Consolidated Balance Sheet, PDF p.341) | FY2026 215.24 | "
            "FY2025 1,198.28 -> Note 21 (cited in the Notes column, same figure in the note)") in block
    assert "- Current tax (Consolidated Statement of Profit and Loss, PDF p.342) | FY2026 5,606.07" in block
    assert "- ok: Total assets 222,599.55 vs 'Total equity and liabilities' 222,599.55 (FY2026)" in block
    assert "Standalone" not in block and "PDF p.205" not in block                # other section
    assert linked.linked_block(package, [choices["C1"]], ["consolidated"]) == ""   # nothing links to it


def test_us10k_format(ten_k_index, ten_k_package):
    assert ten_k_index["format"] == "us-10k"
    assert ten_k_index["format_signals"]["form_10k_cover"] >= 1
    profile = context.report_profile(ten_k_package, ["consolidated"])
    assert profile.startswith("REPORT PROFILE\nFormat: US annual report on Form 10-K (US GAAP)")
    assert "FY2025, FY2024, FY2023 (years ended December 31)" in profile
    assert context.statement_sections_text(ten_k_index).startswith("### Income Statement (Statement of Operations)")
    assert "US FORM 10-K REPORT" in context.analysis_system(ten_k_index)


def test_modern_report_is_not_us10k(index):
    assert index["format"] == "modern" and index["format_signals"]["form_10k_cover"] == 0
