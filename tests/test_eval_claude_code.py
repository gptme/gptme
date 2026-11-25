"""Tests for the Claude Code eval agent."""

import os
import subprocess
from unittest.mock import patch

import pytest

from gptme.eval.agents.claude_code import (
    WORKSPACE_INSTRUCTION,
    ClaudeCodeAgent,
    is_claude_code_model,
    map_tools_to_claude_code,
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
        patch("gptme.eval.agents.claude_code.shutil.which", return_value=None),
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
        patch(
            "gptme.eval.agents.claude_code.shutil.which",
            return_value="/usr/bin/claude",
        ),
        patch(
            "gptme.eval.agents.claude_code.subprocess.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        files: dict[str, str | bytes] = {"hello.py": 'print("hello")'}
        result = agent.act(files, "fix the code")

        # Verify claude was called with correct args
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        prompt_arg = cmd[cmd.index("-p") + 1]
        assert prompt_arg == WORKSPACE_INSTRUCTION + "fix the code"
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
            patch(
                "gptme.eval.agents.claude_code.shutil.which",
                return_value="/usr/bin/claude",
            ),
            patch(
                "gptme.eval.agents.claude_code.subprocess.run",
                return_value=mock_result,
            ) as mock_run,
        ):
            agent.act(None, "test")

            # Check that env passed to subprocess doesn't have CLAUDECODE
            call_env = mock_run.call_args[1]["env"]
            assert "CLAUDECODE" not in call_env
            assert "CLAUDE_CODE_ENTRYPOINT" not in call_env
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def test_agent_custom_max_turns_forwarded_locally():
    """Test that custom max_turns is forwarded to the Claude CLI."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", max_turns=42)

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
        patch(
            "gptme.eval.agents.claude_code.shutil.which",
            return_value="/usr/bin/claude",
        ),
        patch(
            "gptme.eval.agents.claude_code.subprocess.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        agent.act(None, "test")

        cmd = mock_run.call_args[0][0]
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "42"


def test_agent_tools_forwarded():
    """Test that tools are mapped to Claude Code names and restrict the run."""
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
        patch(
            "gptme.eval.agents.claude_code.shutil.which",
            return_value="/usr/bin/claude",
        ),
        patch(
            "gptme.eval.agents.claude_code.subprocess.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        agent.act(None, "test")

        cmd = mock_run.call_args[0][0]
        # gptme names are translated; --tools restricts, --allowedTools approves
        assert cmd[cmd.index("--tools") + 1] == "Bash,Read,Glob,Grep"
        assert cmd[cmd.index("--allowedTools") + 1] == "Bash,Read,Glob,Grep"


def test_agent_docker_mode():
    """Test that use_docker=True delegates to DockerClaudeCodeEnv."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", use_docker=True)
    assert agent.use_docker is True

    # Mock the Docker environment so we don't need real Docker
    with patch("gptme.eval.agents.claude_code.DockerClaudeCodeEnv") as MockDockerEnv:
        mock_env = MockDockerEnv.return_value
        mock_env.run_claude_code.return_value = ("", "", 0)

        files: dict[str, str | bytes] = {"test.py": "print('hi')"}
        agent.act(files, "test prompt")

        # Verify DockerClaudeCodeEnv was instantiated with correct args
        MockDockerEnv.assert_called_once_with(
            host_dir=agent.workspace_dir,
            timeout=agent.timeout,
        )
        mock_env.start_container.assert_called_once()
        # Verify run_claude_code was called with workspace instruction prepended
        mock_env.run_claude_code.assert_called_once_with(
            prompt=WORKSPACE_INSTRUCTION + "test prompt",
            model="claude-sonnet-4-6",
            tools=None,
            max_turns=30,
            include_user_context=False,
        )
        mock_env.cleanup.assert_called_once()


def test_agent_docker_timeout_propagates():
    """Test that Docker timeout is propagated as subprocess.TimeoutExpired, not swallowed."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", use_docker=True)

    with patch("gptme.eval.agents.claude_code.DockerClaudeCodeEnv") as MockDockerEnv:
        mock_env = MockDockerEnv.return_value
        mock_env.run_claude_code.side_effect = subprocess.TimeoutExpired(
            cmd="docker exec claude -p ...", timeout=600
        )

        with pytest.raises(subprocess.TimeoutExpired):
            agent.act(None, "test prompt")

        mock_env.start_container.assert_called_once()
        mock_env.cleanup.assert_called_once()


def test_agent_docker_mode_with_tools():
    """Test that tools are forwarded in Docker mode."""
    agent = ClaudeCodeAgent(
        model="claude-code/claude-sonnet-4-6",
        use_docker=True,
        tools=["shell", "read"],
    )

    with patch("gptme.eval.agents.claude_code.DockerClaudeCodeEnv") as MockDockerEnv:
        mock_env = MockDockerEnv.return_value
        mock_env.run_claude_code.return_value = ("", "", 0)

        agent.act(None, "test")

        mock_env.start_container.assert_called_once()
        mock_env.run_claude_code.assert_called_once_with(
            prompt=WORKSPACE_INSTRUCTION + "test",
            model="claude-sonnet-4-6",
            tools=["Bash", "Read", "Glob", "Grep"],
            max_turns=30,
            include_user_context=False,
        )


def test_agent_docker_startup_failure_does_not_start_cost_session():
    """Test that Docker startup failures happen before cost tracking starts."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", use_docker=True)

    with patch("gptme.eval.agents.claude_code.DockerClaudeCodeEnv") as MockDockerEnv:
        mock_env = MockDockerEnv.return_value
        mock_env.start_container.side_effect = RuntimeError("docker unavailable")

        with pytest.raises(RuntimeError, match="docker unavailable"):
            agent.act(None, "test prompt")

        assert CostTracker.get_session_costs() is None
        mock_env.run_claude_code.assert_not_called()
        mock_env.cleanup.assert_called_once()


def test_agent_local_timeout_propagates():
    """Test that local-mode TimeoutExpired is propagated, not swallowed."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", timeout=5)

    with (
        patch(
            "gptme.eval.agents.claude_code.shutil.which",
            return_value="/usr/bin/claude",
        ),
        patch(
            "gptme.eval.agents.claude_code.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude -p ...", timeout=5),
        ),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        agent.act(None, "test prompt")


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


@pytest.fixture(autouse=True)
def _no_host_managed_mcp(monkeypatch):
    """Keep the host's managed-MCP config (if any) out of flag assertions."""
    monkeypatch.setattr("gptme.eval.agents.claude_code.MANAGED_MCP_CONFIG_PATHS", ())


def _local_cmd(agent: ClaudeCodeAgent) -> list[str]:
    mock_result = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    with (
        patch(
            "gptme.eval.agents.claude_code.shutil.which",
            return_value="/usr/bin/claude",
        ),
        patch(
            "gptme.eval.agents.claude_code.subprocess.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        agent.act(None, "test")
    return mock_run.call_args[0][0]


def test_map_tools_to_claude_code():
    assert map_tools_to_claude_code(["read", "save"]) == [
        "Read",
        "Glob",
        "Grep",
        "Write",
    ]
    # Claude Code refuses to overwrite an unread file, so save carries Read
    assert map_tools_to_claude_code(["save"]) == ["Read", "Write"]
    # Claude Code names pass through; duplicates collapse, order is kept
    assert map_tools_to_claude_code(["patch", "Edit", "Bash", "shell"]) == [
        "Read",
        "Edit",
        "Bash",
    ]


def test_agent_patch_only_can_read_before_editing():
    """Claude Code refuses to Edit an unread file, so ``patch`` must carry Read."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", tools=["patch"])
    cmd = _local_cmd(agent)
    assert cmd[cmd.index("--tools") + 1] == "Read,Edit"


def test_agent_explicit_empty_tools_disables_all_tools():
    """``tools=[]`` must not fall back to Claude Code's default tool set."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", tools=[])
    cmd = _local_cmd(agent)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd


def test_map_tools_drops_unknown_with_warning(caplog):
    with caplog.at_level("WARNING", logger="gptme.eval.agents.claude_code"):
        assert map_tools_to_claude_code(["save", "ipython"]) == ["Read", "Write"]
    assert "ipython" in caplog.text


def test_agent_only_unknown_tools_disables_all_tools():
    """Unmappable tools must not silently fall back to an unrestricted run."""
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", tools=["ipython"])
    cmd = _local_cmd(agent)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd


def test_agent_no_tools_means_no_restriction():
    """No tool list: the built-in set stays unrestricted and is pre-approved.

    ``-p`` cannot answer permission prompts and host allowlists are isolated
    by default, so without pre-approval Write/Edit/Bash would be denied.
    """
    cmd = _local_cmd(ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6"))
    assert "--tools" not in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert {"Bash", "Read", "Write", "Edit"} <= set(allowed)


def test_agent_append_only_can_read_before_editing():
    agent = ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6", tools=["append"])
    cmd = _local_cmd(agent)
    assert cmd[cmd.index("--tools") + 1] == "Read,Edit,Write"


def test_agent_tool_limit_excludes_mcp_with_user_context():
    """--tools limits only built-ins, so a tool-limited run loads no MCP servers."""
    agent = ClaudeCodeAgent(
        model="claude-code/claude-sonnet-4-6",
        tools=["save"],
        include_user_context=True,
    )
    cmd = _local_cmd(agent)
    assert "--setting-sources" not in cmd
    assert "--strict-mcp-config" in cmd


def test_agent_isolates_user_settings_by_default():
    cmd = _local_cmd(ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6"))
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd


def test_agent_include_user_context_keeps_user_settings():
    cmd = _local_cmd(
        ClaudeCodeAgent(
            model="claude-code/claude-sonnet-4-6", include_user_context=True
        )
    )
    assert "--setting-sources" not in cmd
    assert "--strict-mcp-config" not in cmd
    # The host's own permission settings decide; nothing is pre-approved.
    assert "--allowedTools" not in cmd


def test_agent_omits_strict_mcp_config_under_managed_mcp(tmp_path):
    """Claude Code rejects --strict-mcp-config when an admin-managed MCP config
    exists, so the run would exit at startup instead of executing the eval.
    Managed MCP already excludes user servers, so isolation is unchanged."""
    managed = tmp_path / "managed-mcp.json"
    managed.write_text('{"mcpServers": {}}')
    with patch("gptme.eval.agents.claude_code.MANAGED_MCP_CONFIG_PATHS", (managed,)):
        cmd = _local_cmd(ClaudeCodeAgent(model="claude-code/claude-sonnet-4-6"))
        limited = _local_cmd(
            ClaudeCodeAgent(
                model="claude-code/claude-sonnet-4-6",
                tools=["save"],
                include_user_context=True,
            )
        )
    assert "--strict-mcp-config" not in cmd
    assert "--strict-mcp-config" not in limited
    assert cmd[cmd.index("--setting-sources") + 1] == ""


def test_docker_tool_args_ignore_host_managed_mcp(tmp_path):
    """The Docker run checks its own container, not the host's managed config."""
    from gptme.eval.agents.claude_code import claude_code_tool_args

    managed = tmp_path / "managed-mcp.json"
    managed.write_text("{}")
    with patch("gptme.eval.agents.claude_code.MANAGED_MCP_CONFIG_PATHS", (managed,)):
        args = claude_code_tool_args(None, False, managed_mcp=False)
    assert "--strict-mcp-config" in args
