import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

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
    assert "gptme-util skills list" in result.output
    assert "gptme-util skills show NAME" in result.output


def test_version(runner: CliRunner):
    result = runner.invoke(cli.main, ["--version"])
    assert result.exit_code == 0
    assert "gptme" in result.output


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


@pytest.mark.parametrize("bad_name", ["../bad-name", ".", "..", "foo/bar", "foo\\bar"])
def test_name_rejects_path_traversal(bad_name: str, runner: CliRunner):
    result = runner.invoke(
        cli.main,
        ["--name", bad_name, "--non-interactive", "hello"],
    )
    assert result.exit_code == 2
    assert "conversation name must be a single path component" in result.output


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

    monkeypatch.setattr(cli, "get_user_conversations", fail_get_user_conversations)

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

    monkeypatch.setattr(cli, "chat", fake_chat)

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

    monkeypatch.setattr(cli, "chat", fake_chat)
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

    monkeypatch.setattr(cli, "chat", fake_chat)
    monkeypatch.setattr(cli.Path, "cwd", classmethod(lambda cls: workspace_a))

    result = runner.invoke(
        cli.main,
        ["--resume", "--workspace", "@log", "--non-interactive"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert selected_logdirs == [newest_other]


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


def test_unknown_agent_profile_stays_off_stdout_in_json_mode(runid: int):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gptme",
            "--non-interactive",
            "--output-format",
            "json",
            "--name",
            f"test-agent-profile-json-{runid}",
            "--agent-profile",
            "definitely-missing-profile",
            "hello",
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Invalid value for '--agent-profile'" in result.stderr
    assert "gptme-util profile list" in result.stderr
    assert "Traceback" not in result.stderr


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
