"""Tests for the generic agent runner."""

import json
from pathlib import Path

import pytest

from bb_review.rules.agent_runner import AgentRunError, _consume_claude_stream, run_agent


def test_run_agent_rejects_unknown_method(tmp_path: Path):
    with pytest.raises(AgentRunError, match="Unknown agent method"):
        run_agent(method="bogus", repo_path=tmp_path, prompt="hi")


def _ev(**kwargs) -> str:
    return json.dumps(kwargs)


def test_consume_claude_stream_extracts_result_and_emits_progress():
    events = [
        _ev(type="system", subtype="init"),
        _ev(
            type="assistant",
            message={"content": [{"type": "tool_use", "name": "Grep"}]},
        ),
        _ev(
            type="assistant",
            message={"content": [{"type": "text", "text": "Looking at the files..."}]},
        ),
        _ev(type="result", subtype="success", result="final answer"),
    ]
    progress: list[str] = []
    text, raw = _consume_claude_stream(iter(events), progress.append, start_monotonic=0.0)
    assert text == "final answer"
    assert raw == events
    assert len(progress) == 2
    assert "tool: Grep" in progress[0]
    assert "Looking at the files" in progress[1]
    assert progress[0].startswith("  [turn 1,")
    assert progress[1].startswith("  [turn 2,")


def test_consume_claude_stream_skips_non_json_lines():
    events = [
        "not json at all",
        "",
        _ev(type="result", subtype="success", result="ok"),
    ]
    progress: list[str] = []
    text, raw = _consume_claude_stream(iter(events), progress.append, start_monotonic=0.0)
    assert text == "ok"
    # Empty lines dropped from raw; the malformed line is preserved.
    assert "not json at all" in raw
    assert "" not in raw
    assert progress == []


def test_consume_claude_stream_truncates_long_text_preview():
    long_text = "x" * 200
    events = [
        _ev(type="assistant", message={"content": [{"type": "text", "text": long_text}]}),
        _ev(type="result", result="done"),
    ]
    progress: list[str] = []
    _consume_claude_stream(iter(events), progress.append, start_monotonic=0.0)
    assert progress[0].endswith("...")
    # 80-char cap + "..." suffix + leading "  [turn 1, 0s] " prefix
    assert len(progress[0]) < 120
