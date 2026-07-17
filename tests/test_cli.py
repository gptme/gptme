import importlib
import os
import random
import signal
import tempfile
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import click
import pytest
from click.testing import CliRunner

import gptme.cli.main as cli
import gptme.constants
import gptme.tools.browser
from gptme.tools import ToolUse

project_root = Path(__file__).parent.parent
logo = project_root / "media" / "logo.png"


@pytest.fixture(scope="session", autouse=True)
def tmp_data_dir():
    with TemporaryDirectory() as tmpdir:
        # set the environment variable
        print(f"setting XDG_DATA_HOME to {tmpdir}")
        os.environ["XDG_DATA_HOME"] = tmpdir
        yield tmpdir


@pytest.fixture(scope="session")
def runid():
    return random.randint(0, 100000)


runid_retries: dict[str, int] = {}


@pytest.fixture
def name(runid, request):
    attempt = runid_retries.get(request.node.nodeid, 0)
    runid_retries[request.node.nodeid] = attempt + 1
    return f"test-{runid}-{request.node.name}" + (
        f"-retry-{attempt}" if attempt else ""
    )


@pytest.fixture
def args(name: str) -> list[str]:
    return [
        "--name",
        name,
    ]


@pytest.fixture
def runner():
    runner = CliRunner()
    with runner.isolated_filesystem():
        yield runner


def _write_conversation(
    conv_id: str, content: str = "hello", workspace: Path | None = None
) -> Path:
    conv_dir = cli.get_logs_dir() / conv_id
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "conversation.jsonl").write_text(
        f'{{"role":"user","content":"{content}"}}\n'
    )
    if workspace is not None:
        workspace = workspace.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        (conv_dir / "config.toml").write_text(f'[chat]\nworkspace = "{workspace}"\n')
    return conv_dir


def test_help(runner: CliRunner):
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "gptme skills list" in result.output
    assert "gptme skills show NAME" in result.output
    assert "Available tools:" in result.output
    # plugin loading during help rendering should not leak status lines
    assert "Using plugins" not in result.output


def test_discover_gptme_plugins_finds_external_binaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """External gptme-* binaries in PATH appear in the discovery list."""
    # Create a fake gptme-sessions executable
    fake_bin = tmp_path / "gptme-sessions"
    fake_bin.write_text("#!/bin/sh\necho sessions\n")
    fake_bin.chmod(0o755)

    monkeypatch.setenv("PATH", str(tmp_path))

    from gptme.cli.main import _discover_gptme_plugins

    plugins = _discover_gptme_plugins()
    assert "gptme-sessions" in plugins


def test_discover_gptme_plugins_excludes_core_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Core gptme-* scripts are not included in discovered plugins."""
    # Create fake versions of core scripts
    for name in ("gptme-util", "gptme-server", "gptme-eval"):
        p = tmp_path / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)

    monkeypatch.setenv("PATH", str(tmp_path))

    from gptme.cli.main import _discover_gptme_plugins

    plugins = _discover_gptme_plugins()
    assert "gptme-util" not in plugins
    assert "gptme-server" not in plugins
    assert "gptme-eval" not in plugins


def test_help_shows_discovered_external_subcommands(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """gptme --help lists gptme-* binaries discovered in PATH."""
    fake_bin = tmp_path / "gptme-sessions"
    fake_bin.write_text("#!/bin/sh\necho sessions\n")
    fake_bin.chmod(0o755)

    monkeypatch.setenv("PATH", str(tmp_path))

    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "Installed external subcommands" in result.output
    assert "gptme sessions" in result.output
    assert "gptme-sessions" in result.output


def test_help_no_external_subcommands_section_when_none_installed(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
):
    """gptme --help omits the external-subcommands section when PATH has none."""
    monkeypatch.setenv("PATH", "")

    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "Installed external subcommands" not in result.output


def test_version(runner: CliRunner):
    result = runner.invoke(cli.main, ["--version"])
    assert result.exit_code == 0
    assert "gptme" in result.output


def test_show_prompt_stats_exits_before_chat(monkeypatch, tmp_path: Path, runner):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    fake_config = SimpleNamespace(
        chat=SimpleNamespace(
            agent_config=None,
            tools=["shell", "read"],
            interactive=False,
            tool_format="markdown",
            model="local/test",
            workspace=tmp_path,
            stream=False,
            agent=None,
            gear=None,
        ),
        project=None,
    )
    seen: dict[str, Any] = {}

    monkeypatch.setattr("gptme.config.setup_config_from_cli", lambda **_: fake_config)
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr(
        "gptme.prompts.get_prompt_stats",
        lambda **kwargs: (
            seen.update(kwargs=kwargs)
            or SimpleNamespace(
                sections=[],
                total_messages=0,
                total_chars=0,
                total_tokens=0,
                cacheable_tokens=0,
                dynamic_tokens=0,
            )
        ),
    )
    monkeypatch.setattr(
        "gptme.prompts.format_prompt_stats",
        lambda stats, header=None, extra_sections=None: "prompt-stats-output",
    )
    monkeypatch.setattr(
        importlib.import_module("gptme.chat"),
        "chat",
        lambda *args, **kwargs: pytest.fail("chat ran"),
    )
    monkeypatch.setattr(
        "gptme.telemetry.init_telemetry",
        lambda **kwargs: pytest.fail("telemetry should not start for prompt stats"),
    )

    result = runner.invoke(
        cli.main, ["--show-prompt-stats", "query-dependent stats"], input=""
    )

    assert result.exit_code == 0
    assert "prompt-stats-output" in result.output
    assert seen["kwargs"]["workspace"] is not None
    assert seen["kwargs"]["model"] == "local/test"
    assert seen["kwargs"]["initial_prompt"] == "query-dependent stats"


def test_no_workspace_flag_wires_correctly(monkeypatch, tmp_path: Path, runner):
    """--no-workspace should pass context_mode=selective, context_include=[] to get_prompt_stats."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    fake_config = SimpleNamespace(
        chat=SimpleNamespace(
            agent_config=None,
            tools=["shell", "read"],
            interactive=False,
            tool_format="markdown",
            model="local/test",
            workspace=tmp_path,
            stream=False,
            agent=None,
            gear=None,
        ),
        project=None,
    )
    seen: dict[str, Any] = {}

    monkeypatch.setattr("gptme.config.setup_config_from_cli", lambda **_: fake_config)
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr(
        "gptme.prompts.get_prompt_stats",
        lambda **kwargs: (
            seen.update(kwargs=kwargs)
            or SimpleNamespace(
                sections=[],
                total_messages=0,
                total_chars=0,
                total_tokens=0,
                cacheable_tokens=0,
                dynamic_tokens=0,
            )
        ),
    )
    monkeypatch.setattr(
        "gptme.prompts.format_prompt_stats",
        lambda stats, header=None, extra_sections=None: "prompt-stats-output",
    )
    monkeypatch.setattr(
        importlib.import_module("gptme.chat"),
        "chat",
        lambda *args, **kwargs: pytest.fail("chat ran"),
    )
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)

    result = runner.invoke(
        cli.main, ["--no-workspace", "--show-prompt-stats"], input=""
    )

    assert result.exit_code == 0, result.output
    assert seen["kwargs"]["context_mode"] == "selective"
    assert seen["kwargs"]["context_include"] == []


def test_no_workspace_and_context_mutually_exclusive(runner):
    """--no-workspace and --context together should produce a UsageError."""
    result = runner.invoke(cli.main, ["--no-workspace", "--context", "files", "hello"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


@pytest.mark.skipif(os.name == "nt", reason="SIGALRM-based pipe guard is POSIX-only")
def test_read_stdin_open_pipe_without_data_returns_empty(monkeypatch):
    """An idle pipe should not block forever waiting for stdin bytes."""
    read_fd, write_fd = os.pipe()
    read_file = os.fdopen(read_fd)
    monkeypatch.setattr(cli.sys, "stdin", read_file)

    def _timeout(_signum, _frame):
        raise TimeoutError("stdin read blocked on an idle pipe")

    previous_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, 1.5)
    try:
        assert cli._read_stdin() == ""
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        read_file.close()
        os.close(write_fd)


def test_read_stdin_pipe_with_data_reads_all(monkeypatch):
    """Actual piped stdin should still be consumed fully."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"hello from pipe")
    os.close(write_fd)
    read_file = os.fdopen(read_fd)
    monkeypatch.setattr(cli.sys, "stdin", read_file)

    try:
        assert cli._read_stdin() == "hello from pipe"
    finally:
        read_file.close()


def test_read_stdin_waits_briefly_for_slow_pipe_writer(monkeypatch):
    """A slightly slow producer should still count as piped stdin."""
    read_fd, write_fd = os.pipe()
    read_file = os.fdopen(read_fd)
    monkeypatch.setattr(cli.sys, "stdin", read_file)

    def _writer():
        time.sleep(0.2)
        os.write(write_fd, b"hello after delay")
        os.close(write_fd)

    writer = threading.Thread(target=_writer)
    writer.start()
    try:
        assert cli._read_stdin() == "hello after delay"
    finally:
        writer.join()
        read_file.close()


def test_read_stdin_does_not_truncate_chunks_separated_by_subtimeout(monkeypatch):
    """Multiple chunks arriving with inter-chunk gaps > 50ms must not be truncated.

    Regression test for the Greptile-flagged concern: the previous 50ms inner
    sub-timeout could silently drop chunks from pipelines that write in bursts
    with inter-chunk gaps slightly above 50ms. The new sub-timeout (100ms via
    `_STDIN_PIPE_INTER_CHUNK_TIMEOUT`) bridges typical small producer gaps.
    """
    read_fd, write_fd = os.pipe()
    read_file = os.fdopen(read_fd)
    monkeypatch.setattr(cli.sys, "stdin", read_file)

    chunks = [b"first ", b"second ", b"third"]

    def _writer():
        for chunk in chunks:
            time.sleep(0.08)  # > old 50ms, < new 100ms sub-timeout
            os.write(write_fd, chunk)
        os.close(write_fd)

    writer = threading.Thread(target=_writer)
    writer.start()
    try:
        assert cli._read_stdin() == "first second third"
    finally:
        writer.join()
        read_file.close()


@pytest.mark.parametrize("bad_name", ["../bad-name", ".", "..", "foo/bar", "foo\\bar"])
def test_name_rejects_path_traversal(bad_name: str, runner: CliRunner):
    result = runner.invoke(
        cli.main,
        ["--name", bad_name, "--non-interactive", "hello"],
    )
    assert result.exit_code == 2
    assert "conversation name must be a single path component" in result.output


@pytest.mark.parametrize("bad_name", ["", " ", "   ", "\t"])
def test_name_defaults_to_random_for_empty_or_whitespace(
    monkeypatch, tmp_path: Path, bad_name: str, runner: CliRunner
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    logdir = tmp_path / "existing-conversation"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text('{"role":"user","content":"hello"}\n')

    selected_names: list[str] = []
    selected_logdirs: list[Path] = []

    def fake_get_logdir(name: str) -> Path:
        selected_names.append(name)
        return logdir

    def fake_chat(prompt_msgs, initial_msgs, chat_logdir, *args, **kwargs):
        selected_logdirs.append(chat_logdir)

    monkeypatch.setattr(cli, "get_logdir", fake_get_logdir)
    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", fake_chat)

    result = runner.invoke(
        cli.main,
        ["--name", bad_name, "--non-interactive", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert selected_names == ["random"]
    assert selected_logdirs == [logdir]


def test_name_empty_before_output_format(
    monkeypatch, tmp_path: Path, runner: CliRunner
):
    """Regression: --name "" with later flags must still normalize to random."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    logdir = tmp_path / "existing-conversation"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text('{"role":"user","content":"hello"}\n')

    selected_names: list[str] = []
    selected_logdirs: list[Path] = []

    def fake_get_logdir(name: str) -> Path:
        selected_names.append(name)
        return logdir

    def fake_chat(prompt_msgs, initial_msgs, chat_logdir, *args, **kwargs):
        selected_logdirs.append(chat_logdir)

    monkeypatch.setattr(cli, "get_logdir", fake_get_logdir)
    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", fake_chat)

    result = runner.invoke(
        cli.main,
        ["--name", "", "--output-format", "json", "--non-interactive", "hello"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert selected_names == ["random"]
    assert selected_logdirs == [logdir]


@pytest.mark.parametrize("bad_model", ["openai/", "/gpt-4o", "/", "openrouter//gpt-4o"])
def test_model_rejects_empty_path_components(bad_model: str, runner: CliRunner):
    result = runner.invoke(cli.main, ["--model", bad_model, "--version"])

    assert result.exit_code == 2
    assert "Model path components cannot be empty" in result.output
    assert "Traceback" not in result.output


def test_model_allows_provider_owned_nested_model_path(runner: CliRunner):
    result = runner.invoke(
        cli.main,
        ["--model", "openrouter/anthropic/claude-sonnet-4-6", "--version"],
    )

    assert result.exit_code == 0, result.output
    assert "gptme v" in result.output


def test_model_allows_nested_path_through_validation_block(
    monkeypatch, tmp_path: Path, runner: CliRunner
):
    """A valid nested-path model should pass the early validation block.

    Unlike test_model_allows_provider_owned_nested_model_path which uses
    --version and exits before the validation block runs, this test
    actually exercises the early validation code path.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    # Track that get_prompt was called (means validation passed)
    called: dict[str, bool] = {"get_prompt": False}

    def _fake_get_prompt(**kwargs):
        called["get_prompt"] = True
        return []

    monkeypatch.setattr("gptme.prompts.get_prompt", _fake_get_prompt)
    monkeypatch.setattr(
        importlib.import_module("gptme.chat"), "chat", lambda *args, **kwargs: None
    )
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)

    result = runner.invoke(
        cli.main,
        [
            "--model",
            "openrouter/anthropic/claude-sonnet-4-6",
            "--non-interactive",
            "hello",
        ],
    )

    assert result.exit_code == 0, result.output
    assert called["get_prompt"], "execution should reach get_prompt (validation passed)"


def test_model_rejects_unknown_provider_before_context_cmd(
    monkeypatch, tmp_path: Path, runner: CliRunner
):
    """--model with an unknown provider prefix should fail fast before running context_cmd."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(
        "gptme.prompts.get_prompt",
        lambda **kwargs: pytest.fail("get_prompt was called before model validation"),
    )
    monkeypatch.setattr(
        importlib.import_module("gptme.chat"),
        "chat",
        lambda *args, **kwargs: pytest.fail("chat ran"),
    )
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)

    result = runner.invoke(
        cli.main,
        ["--model", "badprovider/some-model", "--non-interactive", "hello"],
    )

    assert result.exit_code == 2, result.output
    assert "Unknown provider: badprovider" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("bad_name", "expected_message"),
    [
        (" leading", "conversation name cannot start or end with whitespace"),
        ("trailing ", "conversation name cannot start or end with whitespace"),
        (" leading-trailing ", "conversation name cannot start or end with whitespace"),
        ("foo\tbar", "conversation name cannot contain control characters"),
        ("foo\nbar", "conversation name cannot contain control characters"),
    ],
)
def test_name_rejects_control_characters_and_edge_whitespace(
    bad_name: str, expected_message: str, runner: CliRunner
):
    result = runner.invoke(
        cli.main,
        ["--name", bad_name, "--non-interactive", "hello"],
    )
    assert result.exit_code == 2
    assert expected_message in result.output


def test_command_fork_rejects_path_traversal(runner: CliRunner, runid: int, name: str):
    escape_name = f"../escape-{runid}"
    escape_path = cli.get_logs_dir().parent / f"escape-{runid}"

    result = runner.invoke(
        cli.main,
        ["--name", name, "--non-interactive", f"/fork {escape_name}"],
    )

    assert result.exit_code == 0
    assert "conversation name must be a single path component" in result.output
    assert not escape_path.exists()


def test_get_logdir_resume_named_conversation_prefers_explicit_name(runid: int):
    target_dir = _write_conversation(f"resume-target-{runid}", content="target")
    recent_dir = _write_conversation(f"resume-recent-{runid}", content="recent")
    os.utime(target_dir / "conversation.jsonl", (1, 1))
    os.utime(recent_dir / "conversation.jsonl", (2, 2))

    assert cli.get_logdir_resume(f"resume-target-{runid}") == target_dir


def test_resume_named_missing_conversation_does_not_fallback_to_latest(
    runner: CliRunner, runid: int
):
    _write_conversation(f"resume-recent-{runid}", content="recent")
    missing = f"resume-missing-{runid}"

    result = runner.invoke(
        cli.main,
        ["--resume", "--name", missing, "--non-interactive"],
    )

    assert result.exit_code == 2
    assert f"No conversation named '{missing}' to resume" in result.output


def test_get_logdir_resume_named_conversation_skips_conversation_scan(
    monkeypatch, runid: int
):
    conv_id = f"resume-fast-{runid}"
    conv_dir = _write_conversation(conv_id, content="fast")

    def fail_get_user_conversations(*args, **kwargs):
        raise AssertionError("named resume should not scan conversation metadata")

    monkeypatch.setattr(
        "gptme.logmanager.get_user_conversations", fail_get_user_conversations
    )

    assert cli.get_logdir_resume(conv_id) == conv_dir


def test_get_logdir_resume_random_filters_by_workspace(tmp_path: Path, runid: int):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"

    older_match = _write_conversation(
        f"resume-a-older-{runid}", content="older", workspace=workspace_a
    )
    newest_other = _write_conversation(
        f"resume-b-newest-{runid}", content="other", workspace=workspace_b
    )
    newest_match = _write_conversation(
        f"resume-a-newest-{runid}", content="newest", workspace=workspace_a
    )

    os.utime(older_match / "conversation.jsonl", (1, 1))
    os.utime(newest_match / "conversation.jsonl", (2, 2))
    os.utime(newest_other / "conversation.jsonl", (3, 3))

    assert cli.get_logdir_resume(workspace=workspace_a) == newest_match


def test_get_logdir_resume_random_workspace_without_match_errors(
    tmp_path: Path, runid: int
):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"

    _write_conversation(
        f"resume-b-only-{runid}", content="other", workspace=workspace_b
    )

    with pytest.raises(
        ValueError,
        match=f"No previous conversations to resume for workspace '{workspace_a.resolve()}'",
    ):
        cli.get_logdir_resume(workspace=workspace_a)


def test_resume_with_workspace_uses_matching_conversation(
    monkeypatch, runner: CliRunner, tmp_path: Path, runid: int
):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"

    target = _write_conversation(
        f"resume-workspace-target-{runid}", content="target", workspace=workspace_a
    )
    newer_other = _write_conversation(
        f"resume-workspace-other-{runid}", content="other", workspace=workspace_b
    )
    os.utime(target / "conversation.jsonl", (1, 1))
    os.utime(newer_other / "conversation.jsonl", (2, 2))

    selected_logdirs: list[Path] = []

    def fake_chat(prompt_msgs, initial_msgs, logdir, *args, **kwargs):
        selected_logdirs.append(logdir)

    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", fake_chat)

    result = runner.invoke(
        cli.main,
        ["--resume", "--workspace", str(workspace_a), "--non-interactive"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert selected_logdirs == [target]


def test_resume_without_explicit_workspace_uses_cwd(
    monkeypatch, runner: CliRunner, tmp_path: Path, runid: int
):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"

    target = _write_conversation(
        f"resume-cwd-target-{runid}", content="target", workspace=workspace_a
    )
    newer_other = _write_conversation(
        f"resume-cwd-other-{runid}", content="other", workspace=workspace_b
    )
    os.utime(target / "conversation.jsonl", (1, 1))
    os.utime(newer_other / "conversation.jsonl", (2, 2))

    selected_logdirs: list[Path] = []

    def fake_chat(prompt_msgs, initial_msgs, logdir, *args, **kwargs):
        selected_logdirs.append(logdir)

    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", fake_chat)
    monkeypatch.setattr(cli.Path, "cwd", classmethod(lambda cls: workspace_a))

    result = runner.invoke(
        cli.main,
        ["--resume", "--non-interactive"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert selected_logdirs == [target]


def test_resume_with_log_workspace_uses_global_latest(
    monkeypatch, runner: CliRunner, tmp_path: Path, runid: int
):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"

    older_target = _write_conversation(
        f"resume-log-target-{runid}", content="target", workspace=workspace_a
    )
    newest_other = _write_conversation(
        f"resume-log-other-{runid}", content="other", workspace=workspace_b
    )
    now = time.time()
    os.utime(older_target / "conversation.jsonl", (now, now))
    os.utime(newest_other / "conversation.jsonl", (now + 1, now + 1))

    selected_logdirs: list[Path] = []

    def fake_chat(prompt_msgs, initial_msgs, logdir, *args, **kwargs):
        selected_logdirs.append(logdir)

    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", fake_chat)
    monkeypatch.setattr(cli.Path, "cwd", classmethod(lambda cls: workspace_a))

    result = runner.invoke(
        cli.main,
        ["--resume", "--workspace", "@log", "--non-interactive"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert selected_logdirs == [newest_other]


def test_workspace_tilde_path_is_expanded(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))

    result = cli.WorkspacePath().convert("~/workspace", None, None)

    assert result == str(workspace.resolve())


def test_missing_custom_tool_path_is_reported_as_usage_error(
    runner: CliRunner, tmp_path: Path
):
    missing_tool = tmp_path / "missing_tool.py"

    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            "missing-custom-tool",
            "-t",
            str(missing_tool),
            "hello",
        ],
    )

    assert result.exit_code == 2
    assert "Tool" in result.output
    assert str(missing_tool) in result.output
    assert "Traceback" not in result.output


def test_missing_explicit_path_prompt_is_reported_as_usage_error(
    runner: CliRunner, tmp_path: Path
):
    missing_path = tmp_path / "missing-prompt.txt"
    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            "missing-explicit-path-prompt",
            str(missing_path),
        ],
    )

    assert result.exit_code == 2
    assert "explicit local path" in result.output
    assert str(missing_path) in result.output
    assert "Traceback" not in result.output


def test_malformed_output_schema_is_reported_as_usage_error(runner: CliRunner):
    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            "malformed-output-schema",
            "--output-schema",
            "notamodule",  # missing ':ClassName'
            "hello",
        ],
    )

    assert result.exit_code == 2
    assert "--output-schema" in result.output
    assert "notamodule" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "schema",
    [
        "gptme.message:NoSuchClass",  # real module, missing class
        ":ClassName",  # empty module name
        "invalid/path:ClassName",  # invalid module name
    ],
)
def test_unloadable_output_schema_is_reported_as_usage_error(
    runner: CliRunner, schema: str
):
    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            "unloadable-output-schema",
            "--output-schema",
            schema,
            "hello",
        ],
    )

    assert result.exit_code == 2
    assert "--output-schema" in result.output
    assert schema in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "prompts",
    [
        ["MISSING_PATH", "hello"],
        ["hello", "MISSING_PATH"],
    ],
)
def test_missing_explicit_path_prompt_with_other_prompt_is_usage_error(
    runner: CliRunner, tmp_path: Path, prompts: list[str]
):
    missing_path = tmp_path / "missing-prompt.txt"
    argv = [
        "--non-interactive",
        "--name",
        "missing-explicit-path-plus-prompt",
    ]
    argv.extend(
        str(missing_path) if prompt == "MISSING_PATH" else prompt for prompt in prompts
    )

    result = runner.invoke(cli.main, argv)

    assert result.exit_code == 2
    assert "explicit local path" in result.output
    assert str(missing_path) in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("flag", ["--architect-model", "--editor-model"])
def test_architect_model_unqualified_is_usage_error(
    flag: str, runner: CliRunner, runid: int
):
    # A malformed architect/editor model name (no provider prefix) should be
    # reported as a clean usage error, not a raw ValueError traceback from
    # llm_reply mid-planning. The other model is given a valid value so the
    # failure is unambiguously the unqualified one being validated.
    other = "--editor-model" if flag == "--architect-model" else "--architect-model"

    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            f"test-architect-model-{runid}-{flag.strip('-')}",
            "--architect",
            flag,
            "bad-no-provider",
            other,
            "anthropic/claude-sonnet-4-5",
            "hello",
        ],
    )

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert flag in result.output
    assert "provider prefix" in result.output


def test_empty_model_is_usage_error(runner: CliRunner, runid: int):
    # --model "" should be caught at parse time by the click callback,
    # producing a clean BadParameter error (not a late ValueError).
    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            f"test-empty-model-{runid}",
            "--model",
            "",
            "hello",
        ],
    )

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "empty" in result.output.lower()


def test_whitespace_model_is_usage_error(runner: CliRunner, runid: int):
    # --model "  " should also be caught at parse time, same as empty string.
    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--name",
            f"test-whitespace-model-{runid}",
            "--model",
            "   ",
            "hello",
        ],
    )

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "empty" in result.output.lower()


def test_unknown_agent_profile_stays_off_stdout_in_json_mode():
    # Click < 8.2 defaults to mix_stderr=True; Click 8.2 removed that kwarg
    # and separates streams by default. Use try/except to handle both.
    try:
        runner = CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--non-interactive",
            "--output-format",
            "json",
            "--agent-profile",
            "definitely-missing-profile",
            "hello",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 2
    assert (
        result.stdout == ""
    )  # stdout clean — no profile error leaking into JSON stream
    assert "Invalid value for '--agent-profile'" in result.stderr
    assert "gptme-util profile list" in result.stderr


def test_noninteractive_missing_prompt_does_not_leave_orphan_logdir(
    monkeypatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    runner = CliRunner()

    named = runner.invoke(
        cli.main,
        ["--non-interactive", "--name", "missing-prompt"],
        input="",
    )
    assert named.exit_code != 0
    assert not (cli.get_logs_dir() / "missing-prompt").exists()

    random = runner.invoke(cli.main, ["--non-interactive"], input="")
    assert random.exit_code != 0
    logs_dir = cli.get_logs_dir()
    assert not logs_dir.exists() or not any(logs_dir.iterdir())


def test_noninteractive_whitespace_only_prompt_rejected(monkeypatch, tmp_path: Path):
    """Whitespace-only prompts should be treated as missing, not sent to the LLM."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    runner = CliRunner()

    for whitespace_prompt in ["   ", "\t", "\n", "  \t  "]:
        result = runner.invoke(
            cli.main,
            ["--non-interactive", "--name", "ws-prompt", whitespace_prompt],
            input="",
        )
        assert result.exit_code != 0, (
            f"Expected non-zero exit for whitespace prompt {whitespace_prompt!r}"
        )
        assert not (cli.get_logs_dir() / "ws-prompt").exists(), (
            f"Orphan logdir created for whitespace prompt {whitespace_prompt!r}"
        )


def test_should_print_resume_hint_requires_nonempty_conversation_log(tmp_path: Path):
    logdir = tmp_path / "resume-hint"
    logdir.mkdir()

    assert not cli._should_print_resume_hint(logdir, "text")

    log_file = logdir / "conversation.jsonl"
    log_file.write_text("")
    assert not cli._should_print_resume_hint(logdir, "text")

    log_file.write_text('{"role":"user","content":"hello"}\n')
    assert cli._should_print_resume_hint(logdir, "text")


def test_should_print_resume_hint_is_disabled_for_json_output(tmp_path: Path):
    logdir = tmp_path / "json-output"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text('{"role":"user","content":"hello"}\n')

    assert not cli._should_print_resume_hint(logdir, "json")


def test_format_resume_hint_shell_quotes_space_containing_name():
    assert cli._format_resume_hint("foo bar") == "gptme --name 'foo bar'"


def test_command_exit(args: list[str], runner: CliRunner):
    args.append("/exit")
    result = runner.invoke(cli.main, args)
    assert "/exit" in result.output
    assert "Missing dependency" not in result.output
    assert result.exit_code == 0


def test_command_help(args: list[str], runner: CliRunner):
    args.append("/help")
    result = runner.invoke(cli.main, args)
    assert "/help" in result.output
    assert result.exit_code == 0


def test_command_tokens(args: list[str], runner: CliRunner):
    args.append("/tokens")
    result = runner.invoke(cli.main, args)
    assert "/tokens" in result.output
    # When no LLM calls have been made, shows "No cost data available"
    # When data exists, shows "Tokens:" - either is valid
    assert "Tokens:" in result.output or "No cost data available" in result.output
    assert result.exit_code == 0


def test_command_context(args: list[str], runner: CliRunner):
    args.append("/context")
    result = runner.invoke(cli.main, args)
    assert "/context" in result.output
    assert "Token Usage by Role:" in result.output
    assert "Total Context:" in result.output
    assert result.exit_code == 0


@pytest.mark.slow
def test_command_log(args: list[str], runner: CliRunner):
    args.append("/log")
    result = runner.invoke(cli.main, args)
    assert "/log" in result.output
    assert result.exit_code == 0


def test_command_tools(args: list[str], runner: CliRunner):
    args.append("/tools")
    result = runner.invoke(cli.main, args)
    assert "/tools" in result.output
    assert result.exit_code == 0


def test_command_doctor(args: list[str], runner: CliRunner):
    args.append("/doctor")
    result = runner.invoke(cli.main, args)
    # The /doctor command runs diagnostics and outputs results
    # Check for expected output elements from the doctor command
    # Note: Exit code may be non-zero if there are warnings/errors in diagnostics
    output_lower = result.output.lower()
    assert any(
        term in output_lower
        for term in ["config", "api key", "tool", "doctor", "diagnostics"]
    ), f"Expected diagnostic output, got: {result.output[:500]}"


@pytest.mark.slow
@pytest.mark.requires_api
def test_command_summarize(args: list[str], runner: CliRunner, monkeypatch):
    # tests the /summarize command
    # Set timeout to 5 minutes to avoid Anthropic's streaming recommendation
    # (Anthropic requires streaming for timeouts >= 10 minutes)
    monkeypatch.setenv("LLM_API_TIMEOUT", "300")
    # Force reinitialization of the Anthropic client to pick up the new timeout
    from gptme.config import get_config
    from gptme.llm import init_anthropic

    config = get_config()
    init_anthropic(config)
    args.append("/summarize")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0


def test_command_fork(args: list[str], runner: CliRunner, name: str):
    # tests the /fork command
    name += "-fork"
    args.append(f"/fork {name}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0


def test_command_rename(args: list[str], runner: CliRunner, name: str):
    # tests the /rename command
    name += "-rename"
    args.append(f"/rename {name}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0


@pytest.mark.requires_api
def test_command_rename_auto(args: list[str], runner: CliRunner, name: str):
    # test with "auto" name
    args.append("/rename auto")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0, (result.output, result.exception)


@pytest.mark.slow
def test_fileblock(args: list[str], runner: CliRunner):
    args_orig = args.copy()

    # tests saving with a ```filename.txt block
    tooluse = ToolUse("save", ["hello.py"], "print('hello')")
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0

    # read the file
    with open("hello.py") as f:
        content = f.read()
    assert content == "print('hello')\n"

    # test append
    args = args_orig.copy()
    tooluse = ToolUse("append", ["hello.py"], "print('world')")
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0

    # read the file
    with open("hello.py") as f:
        content = f.read()
    assert content == "print('hello')\nprint('world')\n"

    # test write file to directory that doesn't exist
    tooluse = ToolUse("save", ["hello/hello.py"], 'print("hello")')
    args = args_orig.copy()
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0

    # test patch on file in directory
    patch = '<<<<<<< ORIGINAL\nprint("hello")\n=======\nprint("hello world")\n>>>>>>> UPDATED'
    tooluse = ToolUse("patch", ["hello/hello.py"], patch)
    args = args_orig.copy()
    args.append(f"/impersonate {tooluse.to_output()}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0

    # read the file
    with open("hello/hello.py") as f:
        content = f.read()
    assert content == 'print("hello world")\n'


@pytest.mark.timeout(30)
def test_shell(args: list[str], runner: CliRunner):
    args.append("/shell echo 'yes'")
    result = runner.invoke(cli.main, args)
    output = result.output.split("System")[-1]
    # check for two 'yes' in output (both command and stdout)
    assert output.count("yes") == 2, result.output
    assert result.exit_code == 0


def test_shell_file(args: list[str], runner: CliRunner):
    # test running the shell tool with a filename
    # make sure we don't accidentally expand the filename and include it in the shell command
    # create new file with contents
    tmp_path = tempfile.mktemp()
    with open(tmp_path, "w") as f:
        f.write("yes")
    args.append(f"/shell cat {tmp_path}")
    result = runner.invoke(cli.main, args)
    assert result.exit_code == 0
    # "yes" should appear in output (from cat stdout)
    assert "yes" in result.output, f"Expected 'yes' in output: {result.output}"
    # The total count of "yes" should be 2-3: typically 2 (once in echoed command,
    # once in stdout), but output formatting may vary. More than 3 indicates filename expansion.
    # Tolerates output variations that caused flakiness (#1325, #1327).
    yes_count = result.output.count("yes")
    assert 2 <= yes_count <= 3, (
        f"Expected 2-3 'yes' occurrences (command echo + stdout), got {yes_count}: "
        f"{result.output}"
    )


@pytest.mark.slow
def test_python(args: list[str], runner: CliRunner):
    args.append("/py print('yes')")
    result = runner.invoke(cli.main, args)
    assert "yes\n" in result.output
    assert result.exit_code == 0


@pytest.mark.slow
def test_python_error(args: list[str], runner: CliRunner):
    args.append("/py raise Exception('yes')")
    result = runner.invoke(cli.main, args)
    assert "Exception: yes" in result.output
    assert result.exit_code == 0


_block_sh = """function test() {
    echo "start"  # start

    echo "after empty line"
}
"""
_block_py = """def test():
    print("start")  # start

    print("after empty line")
"""
blocks = {"ipython": _block_py, "sh": _block_sh}


@pytest.mark.slow
@pytest.mark.parametrize("lang", blocks.keys())
def test_block(args: list[str], lang: str, runner: CliRunner):
    # tests that shell codeblocks are formatted correctly such that whitespace and newlines are preserved
    code = blocks[lang]
    code = f"""```{lang}
{code.strip()}
```"""
    assert "'" not in code

    args.append(f"/impersonate {code}")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    output = result.output
    print(f"output: {output}\nEND")
    # check everything after the second '# start'
    # (get not the user impersonation command, but the assistant message and everything after)
    output = output.split("# start", 2)[-1]
    printcmd = "print" if lang == "ipython" else "echo"
    assert f"\n\n    {printcmd}" in output
    assert result.exit_code == 0


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    os.environ.get("MODEL") == "openai/gpt-4o-mini", reason="unreliable for gpt-4o-mini"
)
def test_generate_primes(args: list[str], runner: CliRunner):
    args.append("compute the first 10 prime numbers using ipython")
    result = runner.invoke(cli.main, args)
    # check that the 9th and 10th prime is present
    assert "23" in result.output
    assert "29" in result.output
    assert result.exit_code == 0


def test_stdin(args: list[str], runner: CliRunner):
    args.append("/exit")
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args, input="hello")
    assert "```stdin\nhello\n```" in result.output
    assert result.exit_code == 0


# Flaky, seems to not always get "User:" outputted?
@pytest.mark.xfail(strict=False)
@pytest.mark.slow
@pytest.mark.requires_api
def test_chain(args: list[str], runner: CliRunner):
    """tests that the "-" argument works to chain commands, executing after the agent has exhausted the previous command"""
    # first command needs to be something requiring two tools, so we can check both are ran before the next chained command
    args.append(
        "we are testing, follow instructions carefully without extra steps. write a test.txt file with the save tool"
    )
    args.append("-")
    args.append("patch it to contain emojis")
    args.append("-")
    args.append("read the contents")
    args.extend(["--tool-format", "markdown"])
    args.extend(["--tools", "save,patch,shell"])
    result = runner.invoke(cli.main, args)
    print(result.output)
    # check that outputs came in expected order
    user1_loc = result.output.index("User:")
    user2_loc = result.output.index("User:", user1_loc + 1)
    user3_loc = result.output.index("User:", user2_loc + 1)
    save_loc = result.output.index("```save")
    patch_loc = result.output.index("```patch")
    print_loc = result.output.rindex("cat test.txt")
    print(
        f"{user1_loc=} {save_loc=} {user2_loc=} {patch_loc=} {user3_loc=} {print_loc=}"
    )
    assert user1_loc < save_loc
    assert save_loc < user2_loc
    assert user2_loc < patch_loc
    assert patch_loc < user3_loc
    assert user3_loc < print_loc
    assert result.exit_code == 0


# TODO: move elsewhere
@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.xdist_group("tmux_new_session")
def test_tmux(args: list[str], runner: CliRunner, cleanup_tmux_sessions):
    """
    $ gptme '/impersonate lets find out the current load
    ```tmux
    new-session top
    ```'
    """
    args.append(
        "/impersonate lets find out the current load\n```tmux\nnew-session top\n```"
    )
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    assert "%CPU" in result.output
    assert result.exit_code == 0


# TODO: move elsewhere
@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.flaky(retries=2, delay=5)
@pytest.mark.skipif(
    os.environ.get("MODEL") in ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5"],
    reason="unreliable/slow for gpt-4o-mini and claude-haiku-4-5",
)
def test_subagent(args: list[str], runner: CliRunner):
    # f14: 377
    # f15: 610
    # f16: 987
    args.extend(["--tools", "ipython,subagent"])
    args.extend(
        [
            "We are in a test. Use the subagent tool to compute `fib(15)`, where `fib(1) = 1` and `fib(2) = 1`.",
            "-",
            "Answer with the value.",
        ]
    )
    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)
    print(result.output)

    # apparently this is not obviously 610
    accepteds = ["377", "610"]
    assert any(accepted in result.output for accepted in accepteds), (
        f"Accepteds '{accepteds}' not in output: {result.output}"
    )
    assert any(
        accepted in "```".join(result.output.split("```")) for accepted in accepteds
    ), "more complex case, not sure if needed"


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    gptme.tools.browser.browser is None,
    reason="no browser tool available",
)
def test_url(args: list[str], runner: CliRunner):
    args.append("Who is the CEO of https://superuserlabs.org?")
    result = runner.invoke(cli.main, args)
    assert "Erik Bjäreholt" in result.output
    assert result.exit_code == 0


@pytest.mark.slow
@pytest.mark.requires_api
def test_vision(args: list[str], runner: CliRunner):
    args.append(f"can you see the image at {logo}? answer with yes or no")
    result = runner.invoke(cli.main, args)
    if result.exception:
        raise result.exception
    assert result.exit_code == 0
    assert "yes" in result.output


@pytest.mark.slow
def test_nested_gptme_calls(args: list[str], runner: CliRunner):
    """Test that gptme can call itself recursively, even without --non-interactive flag."""
    # Run a nested gptme instance that echoes a message
    args.append('/shell gptme "/shell echo we are testing nested gptme"')

    print(f"running: gptme {' '.join(args)}")
    result = runner.invoke(cli.main, args)

    # Check that the nested echo output is present
    assert "we are testing nested gptme" in result.output
    # Check that both gptme instances exited successfully
    assert result.exit_code == 0


def test_comma_separated_choice_minus_prefix():
    """Test that CommaSeparatedChoice accepts '-' prefixed tools."""
    from gptme.cli.main import CommaSeparatedChoice

    csc = CommaSeparatedChoice(
        ["shell", "browser", "save", "read"], allow_prefixes=["+", "-"]
    )
    # Should accept '-browser'
    result = csc.convert("-browser", None, None)
    assert result == "-browser"

    # Should accept '-browser,-save'
    result = csc.convert("-browser,-save", None, None)
    assert result == "-browser,-save"

    # Should still accept '+shell'
    result = csc.convert("+shell", None, None)
    assert result == "+shell"

    # Should accept bare tool names
    result = csc.convert("shell,save", None, None)
    assert result == "shell,save"

    # Should reject invalid tool name even with '-' prefix
    with pytest.raises(click.exceptions.BadParameter):
        csc.convert("-nonexistent", None, None)


def test_comma_separated_choice_lenient_prefixes_allow_plugin_tools():
    """Only '+'-prefixed unknown names pass (plugin tools); '-' stays strict.

    Plugin tools aren't known when the CLI is built, so '+tool' must pass
    parse-time validation (resolved against the loaded toolset later). '-tool'
    exclusions stay strict so typos are caught early.
    """
    from gptme.cli.main import CommaSeparatedChoice

    csc = CommaSeparatedChoice(
        ["shell", "save", "read"],
        allow_prefixes=["+", "-"],
        extra_choices_for_prefix={"-": ["browser"]},
        lenient_prefixes=["+"],
    )

    # Unknown plugin tool with '+' passes
    assert csc.convert("+tts", None, None) == "+tts"
    # Known tools still pass with either prefix
    assert csc.convert("+shell", None, None) == "+shell"
    assert csc.convert("-browser", None, None) == "-browser"

    # Unknown '-' exclusion is rejected (typo protection retained)
    with pytest.raises(click.exceptions.BadParameter):
        csc.convert("-tts", None, None)

    # Bare (unprefixed) unknown names are still rejected
    with pytest.raises(click.exceptions.BadParameter):
        csc.convert("tts", None, None)


def test_comma_separated_choice_strips_short_option_equals_prefix():
    """Accept Click's `-x=value` short-option form for comma-separated choices."""
    from gptme.cli.main import CommaSeparatedChoice

    csc = CommaSeparatedChoice(
        ["shell", "browser", "save", "read"], allow_prefixes=["+", "-"]
    )

    assert csc.convert("=browser", None, None) == "browser"
    assert csc.convert("=-browser,-save", None, None) == "-browser,-save"

    with pytest.raises(click.exceptions.BadParameter):
        csc.convert("=", None, None)


def test_comma_separated_choice_allows_excluding_unavailable_tools():
    """Allow `-tool` exclusions even when that tool is unavailable locally."""
    from gptme.cli.main import CommaSeparatedChoice

    csc = CommaSeparatedChoice(
        ["shell", "save", "read"],
        allow_prefixes=["+", "-"],
        extra_choices_for_prefix={"-": ["browser"]},
    )

    assert csc.convert("-browser", None, None) == "-browser"

    with pytest.raises(click.exceptions.BadParameter):
        csc.convert("browser", None, None)

    with pytest.raises(click.exceptions.BadParameter):
        csc.convert("+browser", None, None)


def test_tool_exclusion_mixed_bare_and_minus_raises():
    """Test that mixing bare tool names with '-' exclusion syntax raises UsageError."""
    from click.testing import CliRunner

    runner = CliRunner()
    # Passing 'shell,-tmux' should raise a UsageError because 'shell' is bare
    # Note: use a valid tool name so CommaSeparatedChoice passes before the mixing guard
    result = runner.invoke(cli.main, ["--tools", "shell,-tmux", "test prompt"])
    assert result.exit_code != 0
    # Check output directly; if it's empty, the error may be in result.exception
    error_text = result.output or (str(result.exception) if result.exception else "")
    assert "Cannot mix bare tool names" in error_text, (
        f"Expected 'Cannot mix bare tool names' in output, got: {error_text!r}"
    )


def test_tools_short_option_equals_syntax_works(runner: CliRunner):
    """The documented `-t=-browser` short form should parse successfully."""
    result = runner.invoke(cli.main, ["-t=-browser", "--version"])
    assert result.exit_code == 0, result.output
    assert "gptme v" in result.output


@pytest.mark.parametrize(
    "tool_spec",
    [
        "./definitely-missing-custom-tool.py",
        "+./definitely-missing-custom-tool.py",
        "-./definitely-missing-custom-tool.py",
    ],
)
def test_missing_custom_tool_path_fails_before_config_init(
    runner: CliRunner, tool_spec: str
):
    result = runner.invoke(cli.main, ["-n", "--tools", tool_spec, "hi"])

    assert result.exit_code != 0
    assert (
        "Tool file does not exist: ./definitely-missing-custom-tool.py" in result.output
    )
    assert "Skipping all confirmation prompts." not in result.output
    assert "stdin is not a TTY and prompts provided" not in result.output
    assert "Using project configuration" not in result.output
    assert "Using local configuration" not in result.output


@pytest.mark.parametrize(
    "tool_spec",
    [
        "./definitely-missing-custom-tool.bash",
        "+./definitely-missing-custom-tool.bash",
        "-./definitely-missing-custom-tool.bash",
    ],
)
def test_non_python_custom_tool_path_reports_suffix_before_existence(
    runner: CliRunner, tool_spec: str
):
    result = runner.invoke(cli.main, ["-n", "--tools", tool_spec, "hi"])

    assert result.exit_code != 0
    assert (
        "Tool file must be a .py file: ./definitely-missing-custom-tool.bash"
        in result.output
    )
    assert "Tool file does not exist" not in result.output
    assert "Skipping all confirmation prompts." not in result.output
    assert "stdin is not a TTY and prompts provided" not in result.output
    assert "Using project configuration" not in result.output
    assert "Using local configuration" not in result.output


def test_click_exception_inside_chat_exits_2(
    tmp_path: Path, monkeypatch, runner: CliRunner
):
    """ClickException raised inside chat() must propagate as exit code 2, not 1.

    Regression guard for the except clause ordering fix: before the fix,
    `except (RuntimeError, Exception)` swallowed ClickException and always
    exited 1; after the fix `except click.ClickException: raise` runs first.
    """
    # Use a pre-existing conversation so get_prompt() is skipped (no LLM call)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    logdir = tmp_path / "test-conv-exit2"
    logdir.mkdir(parents=True)
    (logdir / "conversation.jsonl").write_text('{"role":"user","content":"hello"}\n')

    monkeypatch.setattr(cli, "get_logdir", lambda name: logdir)
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)

    def _raise_usage_error(*args, **kwargs):
        raise click.UsageError("simulated usage error from inside chat")

    monkeypatch.setattr(
        importlib.import_module("gptme.chat"), "chat", _raise_usage_error
    )

    result = runner.invoke(
        cli.main, ["--name", "test-conv-exit2", "--non-interactive", "hello"]
    )

    assert result.exit_code == 2, result.output
    assert "Fatal error occurred" not in result.output


@pytest.mark.slow
class TestPluginDiscovery:
    """Tests for gptme-<cmd> PATH-based plugin dispatch."""

    def test_dispatches_to_external_binary(self, runner, monkeypatch, tmp_path):
        """gptme <cmd> delegates to gptme-<cmd> when found in PATH."""
        fake_bin = str(tmp_path / "gptme-sessions")

        subprocess_calls: list[list[str]] = []
        monkeypatch.setattr(
            "gptme.cli.main.shutil.which",
            lambda x: fake_bin if x == "gptme-sessions" else None,
        )
        monkeypatch.setattr(
            "gptme.cli.main.subprocess.call",
            lambda args: _record_subprocess_call(args, subprocess_calls),
        )

        result = runner.invoke(cli.main, ["sessions", "list"])

        assert result.exit_code == 0
        assert subprocess_calls == [[fake_bin, "list"]]

    def test_no_dispatch_when_binary_missing(self, runner, monkeypatch, tmp_path):
        """gptme <cmd> falls through to chat when gptme-<cmd> is not in PATH."""
        subprocess_calls: list = []
        monkeypatch.setattr("gptme.cli.main.shutil.which", lambda x: None)
        monkeypatch.setattr(
            "gptme.cli.main.subprocess.call",
            lambda args: _record_subprocess_call(args, subprocess_calls),
        )

        # Short-circuit before any LLM/network setup to keep the test fast.
        # The point is only to verify subprocess was NOT called; we don't need a
        # full session run.
        def _early_exit(*args, **kwargs):
            raise SystemExit(1)

        monkeypatch.setattr("gptme.config.setup_config_from_cli", _early_exit)

        runner.invoke(cli.main, ["sessions"])

        assert subprocess_calls == []

    def test_search_alias_takes_precedence(self, runner, monkeypatch):
        """Built-in `search` alias is not intercepted by plugin dispatch."""
        subprocess_calls: list = []
        # Even if gptme-search existed in PATH, the built-in alias must run first.
        monkeypatch.setattr(
            "gptme.cli.main.shutil.which",
            lambda x: "/fake/gptme-search" if x == "gptme-search" else None,
        )
        monkeypatch.setattr(
            "gptme.cli.main.subprocess.call",
            lambda args: _record_subprocess_call(args, subprocess_calls),
        )

        # UsageError fires before search_chats is called (no query given),
        # so the built-in alias exits early — plugin dispatch must never run.
        result = runner.invoke(cli.main, ["search"])

        assert subprocess_calls == [], (
            "Plugin dispatch ran before built-in search alias"
        )
        assert result.exit_code != 0  # UsageError (no query given)


def _record_subprocess_call(args, calls: list[list[str]]) -> int:
    calls.append(list(args))
    return 0
