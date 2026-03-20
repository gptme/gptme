"""Tests for the Claude Code eval agent."""

import os
from unittest.mock import patch

import pytest

from gptme.eval.agents.claude_code import (
    ClaudeCodeAgent,
    is_claude_code_model,
    parse_claude_code_model,
)
from gptme.util.cost_tracker import CostTracker


@pytest.fixture(autouse=True)
def reset_cost_tracker():
    """Reset CostTracker before and after each test to prevent session state leakage."""
    CostTracker.reset()
    yield
    CostTracker.reset()


def test_is_claude_code_model():
    assert is_claude_code_model("claude-code/claude-sonnet-4-6")
    assert is_claude_code_model("claude-code/claude-opus-4-6")
    assert not is_claude_code_model("anthropic/claude-sonnet-4-6")
    assert not is_claude_code_model("openai/gpt-4o")
    assert not is_claude_code_model("claude-sonnet-4-6")


def test_parse_claude_code_model():
    assert (
        parse_claude_code_model("claude-code/claude-sonnet-4-6") == "claude-sonnet-4-6"
    )
    assert parse_claude_code_model("claude-code/claude-opus-4-6") == "claude-opus-4-6"


def test_parse_claude_code_model_empty_suffix():
    with pytest.raises(ValueError, match="Missing model name"):
        parse_claude_code_model("claude-code/")


def test_agent_init():
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6")
    assert agent.cc_model == "claude-sonnet-4-6"
    assert agent.model == "claude-code/claude-sonnet-4-6"
    assert agent.workspace_dir.exists()


def test_agent_no_claude_binary():
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6")
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(FileNotFoundError, match="Claude Code CLI"),
    ):
        agent.act(None, "test prompt")
    # No CostTracker session should have been started (binary check happens before start_session)
    assert CostTracker.get_session_costs() is None


def test_agent_act_with_files():
    """Test that files are written to workspace before invoking claude."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6")

    # Mock subprocess.run so we don't actually call claude
    mock_result = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
    )()

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        files: dict[str, str | bytes] = {"hello.py": 'print("hello")'}
        result = agent.act(files, "fix the code")

        # Verify claude was called with correct args
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        assert "fix the code" in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd
        assert call_args[1]["cwd"] == agent.workspace_dir

        # Verify input file was written to workspace
        assert (agent.workspace_dir / "hello.py").exists()

        # Result should include the file
        assert "hello.py" in result


def test_agent_env_cleanup():
    """Test that CLAUDECODE env vars are stripped to prevent nested detection."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6")

    mock_result = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
    )()

    original_env = os.environ.copy()
    os.environ["CLAUDECODE"] = "1"
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "test"

    try:
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            agent.act(None, "test")

            # Check that env passed to subprocess doesn't have CLAUDECODE
            call_env = mock_run.call_args[1]["env"]
            assert "CLAUDECODE" not in call_env
            assert "CLAUDE_CODE_ENTRYPOINT" not in call_env
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def test_agent_tools_forwarded():
    """Test that tools parameter is forwarded as --allowedTools."""
    agent = ClaudeCodeAgent(
        model="claude-code/claude-sonnet-4-6", tools=["shell", "read"]
    )

    mock_result = type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
    )()

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        agent.act(None, "test")

        cmd = mock_run.call_args[0][0]
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "shell,read"


def test_parse_usage_ndjson():
    """Test that _parse_usage handles NDJSON and records usage into CostTracker."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6")

    # Start a CostTracker session so record() calls are captured
    CostTracker.start_session("test-parse-usage")

    # Simulate NDJSON output: per-turn assistant event carries "usage" but no
    # "total_cost_usd"; the final result line has both — only the latter should
    # be recorded to avoid double-counting.
    ndjson = (
        '{"type":"assistant","usage":{"input_tokens":80,"output_tokens":40}}\n'
        '{"type":"text","text":"Hello"}\n'
        '{"type":"result","total_cost_usd":0.0025,'
        '"usage":{"input_tokens":100,"output_tokens":50,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}\n'
    )
    agent._parse_usage(ndjson)

    # Assert only ONE entry was recorded (the result line, not per-turn events)
    costs = CostTracker.get_session_costs()
    assert costs is not None
    assert len(costs.entries) == 1
    entry = costs.entries[0]
    assert entry.input_tokens == 100
    assert entry.output_tokens == 50
    assert entry.cost == pytest.approx(0.0025)
    assert entry.model == "claude-sonnet-4-6"
    # autouse reset_cost_tracker fixture handles cleanup
