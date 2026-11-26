"""Tests for the Claude Code eval agent."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.eval.agents.claude_code import (
    WORKSPACE_INSTRUCTION,
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


def test_agent_tools_forwarded() -> None:
    """Test that selected tools are available and pre-approved."""
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
        for flag in ("--tools", "--allowedTools"):
            assert cmd[cmd.index(flag) + 1] == "Bash,Read,Glob,Grep"


@pytest.mark.skipif(os.name != "posix", reason="The Claude CLI shim uses a shebang")
@pytest.mark.parametrize(
    "user_context", [False, True], ids=["isolated", "user-context"]
)
def test_claude_code_eval_cli_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user_context: bool,
) -> None:
    """Run a patch-only eval through the CLI and capture the Claude Code argv."""
    argv_file = tmp_path / "claude-argv.json"
    claude = tmp_path / "claude"
    claude.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        """Path('hello.py').write_text('print("Hello, human!")')\n"""
    )
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path / "results"))
    cmd = [
        sys.executable,
        "-m",
        "gptme.eval",
        "hello-patch",
        "-m",
        "claude-code/claude-sonnet-4-6@markdown",
        "--timeout",
        "15",
    ]
    if user_context:
        cmd.append("--user-context")
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv_file.exists(), result.stdout + result.stderr

    args = json.loads(argv_file.read_text())
    flags = {
        flag: args[args.index(flag) + 1] if flag in args else None
        for flag in ("--allowedTools", "--tools", "--setting-sources")
    }
    assert flags == {
        "--allowedTools": "Read,Edit",
        "--tools": "Read,Edit",
        "--setting-sources": None if user_context else "",
    }
    assert "--strict-mcp-config" in args


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
            tools=["shell", "read"],
            max_turns=30,
            include_user_context=False,
        )


@pytest.mark.parametrize(
    ("tools", "user_context", "selected", "approved"),
    [
        pytest.param(
            ["read", "save", "append", "morph", "browser", "Bash", "ipython", "Read"],
            False,
            "Read,Glob,Grep,Write,Edit,WebFetch,WebSearch,Bash",
            "Read,Glob,Grep,Write,Edit,WebFetch,WebSearch,Bash",
            id="mixed-tools",
        ),
        pytest.param(["save"], False, "Read,Write", "Read,Write", id="save-only"),
        pytest.param(
            ["append"], True, "Read,Edit,Write", "Read,Edit,Write", id="append-only"
        ),
        pytest.param([], True, "", None, id="explicit-empty"),
        pytest.param(["ipython"], False, "", None, id="unknown-only"),
        pytest.param(
            None,
            False,
            None,
            "Bash,Read,Glob,Grep,Write,Edit,WebFetch,WebSearch",
            id="isolated-unrestricted",
        ),
        pytest.param(None, True, None, None, id="user-context-unrestricted"),
    ],
)
def test_agent_docker_cli_flags(
    tools: list[str] | None,
    user_context: bool,
    selected: str | None,
    approved: str | None,
) -> None:
    """Exercise the Docker launcher through the real agent and adapter."""
    agent = ClaudeCodeAgent(
        model="claude-code/claude-sonnet-4-6",
        use_docker=True,
        tools=tools,
        include_user_context=user_context,
    )
    with (
        patch("gptme.eval.agents.claude_code._MANAGED_MCP_CONFIG") as managed_mcp,
        patch("gptme.eval.execenv.subprocess.run") as mock_run,
        patch("gptme.eval.execenv.subprocess.Popen") as mock_popen,
    ):
        managed_mcp.is_file.return_value = True
        mock_run.return_value.stdout = "container123\n"
        mock_process = mock_popen.return_value
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")

        agent.act(None, "fix 'this' file")

    managed_mcp.is_file.assert_not_called()
    args = shlex.split(mock_popen.call_args.args[0][-1])
    assert args[:3] == ["claude", "-p", WORKSPACE_INSTRUCTION + "fix 'this' file"]
    flags = {
        flag: args[args.index(flag) + 1] if flag in args else None
        for flag in ("--tools", "--allowedTools", "--setting-sources")
    }
    assert flags == {
        "--tools": selected,
        "--allowedTools": approved,
        "--setting-sources": None if user_context else "",
    }
    assert ("--strict-mcp-config" in args) == (not user_context or tools is not None)


def test_agent_refuses_isolation_with_managed_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_mcp = tmp_path / "managed-mcp.json"
    managed_mcp.write_text('{"mcpServers": {}}')
    monkeypatch.setattr(
        "gptme.eval.agents.claude_code._MANAGED_MCP_CONFIG", managed_mcp
    )
    agent = ClaudeCodeAgent(
        model="claude-code/claude-sonnet-4-6", tools=[], include_user_context=True
    )
    with (
        patch(
            "gptme.eval.agents.claude_code.shutil.which", return_value="/usr/bin/claude"
        ),
        patch("gptme.eval.agents.claude_code.subprocess.run") as mock_run,
        pytest.raises(RuntimeError, match="Cannot isolate MCP"),
    ):
        agent.act(None, "test")
    mock_run.assert_not_called()
    assert CostTracker.get_session_costs() is None


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
