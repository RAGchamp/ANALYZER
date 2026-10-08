"""Milestone 3 tests. run_claude is mocked, so no Claude calls are made."""

import pytest

from analyzer import note_selector
from claude_client import ClaudeError


@pytest.mark.parametrize("question, scope", [
    ("Analyze the company's Tax related liabilities and comment on it.", "consolidated"),
    ("Analyze standalone tax liabilities", "standalone"),
    ("Analyze tax, do not use consolidated statements", "standalone"),
    ("Compare standalone and consolidated borrowings", "both"),
    ("Analyze both stand-alone and group debt", "both"),
    ("Parent company only: related party transactions", "standalone"),
])
def test_detect_scope(question, scope):
    assert note_selector.detect_scope(question)[0] == scope


@pytest.mark.parametrize("question, expected", [
    ("Analyze the company's Tax related liabilities and comment on it.", "C21"),
    ("Comment on the borrowings and debt maturity", "C18"),
    ("Analyze related party transactions", "C48"),
    ("What are the contingent liabilities?", "C41"),
    ("Analyze standalone tax liabilities", "S21"),
    ("Explain the lease obligations", "C43"),
])
def test_keyword_fallback_finds_primary_note(index, question, expected):
    scope = note_selector.detect_scope(question)[0]
    ids = note_selector.keyword_select(index, question, scope)
    assert expected in ids


def test_selector_uses_claude_json(index, monkeypatch):
    monkeypatch.setattr(note_selector, "run_claude",
                        lambda *a, **k: 'Sure: {"notes": ["C21", "C41", "X99"], "reason": "tax"}')
    result = note_selector.select_notes(index, "Analyze tax liabilities")
    assert result["method"] == "claude"
    assert [n["id"] for n in result["notes"]] == ["C21", "C41"]
    assert result["notes"][0]["start_page"] == 405


def test_selector_falls_back_when_claude_fails(index, monkeypatch):
    def boom(*a, **k):
        raise ClaudeError("CLI missing")
    monkeypatch.setattr(note_selector, "run_claude", boom)
    result = note_selector.select_notes(index, "Analyze the company's tax liabilities")
    assert result["method"] == "keywords"
    assert "C21" in [n["id"] for n in result["notes"]]


def test_explicit_note_reference_always_included(index, monkeypatch):
    monkeypatch.setattr(note_selector, "run_claude",
                        lambda *a, **k: '{"notes": ["C21"], "reason": "tax"}')
    result = note_selector.select_notes(index, "Analyze tax and also Note 41")
    assert [n["id"] for n in result["notes"]] == ["C41", "C21"]


def test_selector_prompt_lists_only_scope(index):
    prompt = note_selector._selector_prompt(index, "tax", "consolidated")
    assert "C21: INCOME AND DEFERRED TAXES" in prompt
    assert "S21" not in prompt
    assert "{{" not in prompt
