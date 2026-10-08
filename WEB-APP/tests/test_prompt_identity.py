"""Everything sent to Claude stays byte-identical to the frozen baseline
(tests/fixtures/baseline, see tests/prompt_baseline.py).

This is the proof that the ingester / analyzer split (INFO/SPLIT-FUNCTIONALITY-PLAN.md,
Phase 1) changed no prompt. When a prompt is meant to change, re-freeze with
`python tests/prompt_baseline.py --freeze` and review the diff."""

import difflib

import pytest

import prompt_baseline


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    if not prompt_baseline.available_reports():
        pytest.skip("No sample reports found")
    monkeypatch = pytest.MonkeyPatch()
    try:
        yield prompt_baseline.collect(tmp_path_factory.mktemp("baseline"), monkeypatch)
    finally:
        monkeypatch.undo()


def _baseline_names():
    return sorted(p.name for p in prompt_baseline.BASELINE_DIR.glob("*") if p.is_file())


@pytest.mark.parametrize("name", _baseline_names())
def test_matches_baseline(outputs, name):
    expected = (prompt_baseline.BASELINE_DIR / name).read_text(encoding="utf-8")
    if name not in outputs:
        pytest.skip(f"{name}: its sample report is not available")
    actual = outputs[name]
    if actual != expected:
        diff = "".join(difflib.unified_diff(
            expected.splitlines(True), actual.splitlines(True), "baseline", "now", n=2))
        pytest.fail(f"{name} differs from the baseline:\n{diff[:4000]}")


def test_no_unexpected_outputs(outputs):
    assert sorted(outputs) == [n for n in _baseline_names() if n in outputs]
