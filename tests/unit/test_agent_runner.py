"""Tests for the generic agent runner."""

from pathlib import Path

import pytest

from bb_review.rules.agent_runner import AgentRunError, run_agent


def test_run_agent_rejects_unknown_method(tmp_path: Path):
    with pytest.raises(AgentRunError, match="Unknown agent method"):
        run_agent(method="bogus", repo_path=tmp_path, prompt="hi")
