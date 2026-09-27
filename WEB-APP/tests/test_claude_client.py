"""claude_client streaming, against a fake `claude` that prints stream-json events."""

import sys

import pytest

import claude_client
import config

FAKE = r'''
import json, sys, time
prompt = sys.stdin.read()
mode = sys.argv[1]

def emit(obj):
    print(json.dumps(obj), flush=True)

emit({"type": "system", "subtype": "init"})
emit({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "thinking"}}})
if mode == "slow":
    time.sleep(10)
for piece in ["## Summ", "ary\n", "- got ", str(len(prompt)), " chars"]:
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": piece}}})
emit({"type": "result", "subtype": "success", "is_error": mode == "error",
      "result": "API Error: overloaded" if mode == "error" else "## Summary\n- final text"})
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")

    def use(mode):
        monkeypatch.setattr(claude_client, "_base_args",
                            lambda system, effort=None: [sys.executable, str(script), mode])
        monkeypatch.setattr(config, "CLAUDE_EXTRA_FLAGS", [])
        monkeypatch.setattr(config, "CLAUDE_VIA_POWERSHELL", False)
    return use


def test_streams_progress_and_returns_final_result(fake_claude):
    fake_claude("ok")
    seen = []
    prompt = "x" * 50_000     # bigger than a pipe buffer: stdin is fed on its own thread
    reply = claude_client.run_claude(prompt, "sys", 30, effort="low",
                                     on_progress=lambda phase, text: seen.append((phase, text)))
    assert reply == "## Summary\n- final text"
    assert seen[0] == ("thinking", "")
    assert seen[-1] == ("writing", "## Summary\n- got 50000 chars")
    assert [p for p, _ in seen].count("writing") == 5


def test_error_result_raises(fake_claude):
    fake_claude("error")
    with pytest.raises(claude_client.ClaudeError, match="overloaded"):
        claude_client.run_claude("hi", "sys", 30, on_progress=lambda *a: None)


def test_timeout_kills_stream(fake_claude):
    fake_claude("slow")
    with pytest.raises(claude_client.ClaudeError, match="longer than 1s"):
        claude_client.run_claude("hi", "sys", 1, on_progress=lambda *a: None)


def test_effort_flag_added():
    assert claude_client._base_args("sys", "low")[-2:] == ["--effort", "low"]
    assert "--effort" not in claude_client._base_args("sys")
