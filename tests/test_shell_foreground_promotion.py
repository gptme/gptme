"""Foreground shell commands promote to conversation-owned jobs at a soft timeout."""

import os
import threading
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock

import pytest

from gptme.message import Message
from gptme.tools.shell import (
    PromotedJobProcess,
    ShellSession,
    _get_foreground_timeout,
    _promoted_next_cwd,
    _promoted_next_state,
    _workspace_cwd,
    execute_shell_impl,
    get_shell,
    get_workspace_cwd,
    set_shell,
    set_workspace_cwd,
)
from gptme.tools.shell_background import (
    _current_conversation_id,
    background_job_completion_hook,
    execute_output_command,
    list_background_jobs,
    reset_background_jobs,
)


@pytest.fixture(autouse=True)
def _clean_shell_jobs() -> Generator[None, None, None]:
    ws_token = _workspace_cwd.set(None)
    cwd_token = _promoted_next_cwd.set(None)
    state_token = _promoted_next_state.set(None)
    reset_background_jobs()
    yield
    get_shell().close()
    reset_background_jobs()
    _promoted_next_state.reset(state_token)
    _promoted_next_cwd.reset(cwd_token)
    _workspace_cwd.reset(ws_token)


def test_foreground_timeout_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPTME_SHELL_FOREGROUND_TIMEOUT", raising=False)
    assert _get_foreground_timeout() == 120.0

    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0")
    assert _get_foreground_timeout() is None

    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.25")
    assert _get_foreground_timeout() == 0.25


def test_slow_foreground_command_promotes_without_killing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    started = time.monotonic()
    messages = list(
        execute_shell_impl(
            "printf before; sleep 2; printf after", logdir=None, timeout=4
        )
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert "Promoted to background shell job #1" in messages[-1].content
    assert "before" in messages[-1].content
    assert "before" not in capsys.readouterr().out

    jobs = list_background_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.is_running()
    live_output = list(execute_output_command(str(job.id)))
    assert "before" in live_output[-1].content
    assert "Running" in live_output[-1].content
    job.process.wait(timeout=4)
    completions = _wait_for_completion()
    assert len(completions) == 1
    assert "finished (exit code 0)" in completions[0].content
    assert "before" in completions[0].content
    assert "after" in completions[0].content


def test_promoted_command_releases_a_shell_seeded_with_tracked_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    workdir = tmp_path / "tracked"
    workdir.mkdir()
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)
    assert shell.run(f"cd {workdir}")[0] == 0

    messages = list(execute_shell_impl("sleep 1", logdir=None, timeout=2))

    assert "Promoted to background shell job" in messages[-1].content
    replacement = get_shell()
    assert replacement is not shell
    assert replacement.get_cwd() == workdir
    returncode, stdout, _ = replacement.run("pwd")
    assert returncode == 0
    assert stdout == str(workdir)


def test_promoted_command_seeds_replacement_from_live_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In-command cd is visible on the process before the PWD marker lands."""
    # 0.05s is too tight: bash may not have executed `cd` yet. 0.25s is still
    # well under the remaining sleep, and /proc cwd has updated by ~0.10s.
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.25")
    dest = tmp_path / "services" / "api"
    dest.mkdir(parents=True)
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    messages = list(execute_shell_impl(f"cd {dest}; sleep 1", logdir=None, timeout=2))

    assert "Promoted to background shell job" in messages[-1].content
    replacement = get_shell()
    assert replacement is not shell
    assert replacement.get_cwd() == dest
    returncode, stdout, _ = replacement.run("pwd")
    assert returncode == 0
    assert stdout == str(dest)


def test_keyboard_interrupt_before_promotion_terminates_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-C during the soft-timeout wait must not leave a busy shell."""
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.5")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)
    real_join = threading.Thread.join
    interrupted = {"done": False}

    def join_soft_timeout(self: threading.Thread, timeout: float | None = None) -> None:
        if (
            not interrupted["done"]
            and timeout == 0.5
            and threading.current_thread() is threading.main_thread()
        ):
            interrupted["done"] = True
            raise KeyboardInterrupt
        return real_join(self, timeout)

    monkeypatch.setattr(threading.Thread, "join", join_soft_timeout)

    with pytest.raises(KeyboardInterrupt):
        list(execute_shell_impl("sleep 30", logdir=None, timeout=60))

    assert list_background_jobs() == []
    recovered = get_shell()
    returncode, stdout, _ = recovered.run("printf recovered")
    assert returncode == 0
    assert "recovered" in stdout


def test_fast_command_is_not_promoted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "1")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    messages = list(execute_shell_impl("printf fast", logdir=None, timeout=2))

    assert "Ran command" in messages[-1].content
    assert "fast" in messages[-1].content
    assert list_background_jobs() == []
    assert get_shell() is shell


def test_hard_timeout_still_finishes_promoted_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.03")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    messages = list(execute_shell_impl("sleep 1", logdir=None, timeout=0.12))
    assert "Promoted to background shell job" in messages[-1].content

    job = list_background_jobs()[0]
    job.process.wait(timeout=2)
    assert job.process.returncode == -124
    completions = _wait_for_completion()
    assert len(completions) == 1
    assert "exit code -124" in completions[0].content


def test_promoted_command_preserves_exported_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Replacement shells must restore exports from the last completed command."""
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)
    assert shell.run("export GPTME_PROMOTION_TEST=restored")[0] == 0

    messages = list(execute_shell_impl("sleep 1", logdir=None, timeout=2))

    assert "Promoted to background shell job" in messages[-1].content
    replacement = get_shell()
    assert replacement is not shell
    returncode, stdout, _ = replacement.run('printf %s "$GPTME_PROMOTION_TEST"')
    assert returncode == 0
    assert stdout.strip() == "restored"


def test_promoted_command_preserves_inflight_exports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exports in the promoted command itself must seed the replacement shell."""
    # Same timing as live-cwd: 0.05s can fire before bash reaches `export`.
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.25")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    messages = list(
        execute_shell_impl(
            "export GPTME_PROMOTION_TEST=inflight; sleep 1",
            logdir=None,
            timeout=2,
        )
    )

    assert "Promoted to background shell job" in messages[-1].content
    replacement = get_shell()
    assert replacement is not shell
    returncode, stdout, _ = replacement.run('printf %s "$GPTME_PROMOTION_TEST"')
    assert returncode == 0
    assert stdout.strip() == "inflight"


def test_detached_promoted_shell_does_not_chdir_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A promoted CLI command's later `cd` must not jump the process cwd."""
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    dest = tmp_path / "after"
    dest.mkdir()
    original_cwd = os.getcwd()
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)
    try:
        messages = list(
            execute_shell_impl(f"sleep 0.2; cd {dest}", logdir=None, timeout=2)
        )
        assert "Promoted to background shell job" in messages[-1].content
        assert get_workspace_cwd() is None
        replacement = get_shell()
        assert replacement is not shell
        assert replacement._skip_os_chdir is False
        after_replace = os.getcwd()
        job = list_background_jobs()[0]
        job.process.wait(timeout=4)
        time.sleep(0.1)
        # Replacement may chdir (CLI contract); the detached command must not.
        assert os.getcwd() == after_replace
        assert Path(os.getcwd()).resolve() != dest.resolve()
        assert shell._skip_os_chdir is True
    finally:
        os.chdir(original_cwd)


def test_foreground_promotion_disabled_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("gptme.tools.shell._is_windows", True)
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    messages = list(execute_shell_impl("sleep 0.2", logdir=None, timeout=2))

    assert "Promoted to background shell job" not in messages[-1].content
    assert list_background_jobs() == []


def test_promoted_job_process_finish_is_first_writer_wins() -> None:
    process = PromotedJobProcess()
    process.finish(0)
    process.finish(-15)
    assert process.returncode == 0
    assert process.poll() == 0


def test_worker_path_preserves_process_cwd_when_workspace_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Promotion runs shell.run() on a worker; ContextVars are not copied.

    Server sessions set workspace cwd so ``_set_cwd`` must not ``os.chdir``.
    Force the worker path (foreground timeout < hard timeout) to match CI,
    where GPTME_SHELL_TIMEOUT defaults to 1200s.
    """
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "5")
    monkeypatch.setenv("GPTME_SHELL_TIMEOUT", "30")
    dest = tmp_path / "ws"
    dest.mkdir()
    original_cwd = os.getcwd()
    set_workspace_cwd(original_cwd)
    shell = ShellSession(cwd=original_cwd)
    set_shell(shell)
    try:
        messages = list(execute_shell_impl(f"cd {dest}", logdir=None, timeout=30))
        assert os.getcwd() == original_cwd
        assert shell.get_cwd() == dest
        assert "Ran" in messages[-1].content
    finally:
        os.chdir(original_cwd)


def _wait_for_completion(timeout: float = 2.0) -> list[Message]:
    """Poll the hook using the same conversation id jobs were registered with."""
    manager = Mock(chat_id=_current_conversation_id())
    deadline = time.monotonic() + timeout
    completions: list[Message] = []
    while not completions and time.monotonic() < deadline:
        completions = list(background_job_completion_hook(manager))
        time.sleep(0.01)
    return completions


def _confirmed_shell(command: str) -> list[Message]:
    """Run a whole shell tool call (dispatch + confirmation), auto-confirmed."""
    from unittest.mock import patch

    from gptme.hooks.confirm import ConfirmationResult
    from gptme.tools.shell import execute_shell

    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ):
        return list(execute_shell(command, [], None))


def test_promotion_message_names_job_control_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    set_shell(ShellSession(cwd=str(tmp_path)))

    content = _confirmed_shell("sleep 1")[-1].content

    assert "Promoted to background shell job #1" in content
    for call in ("`output 1`", "`wait 1 [timeout]`", "`kill 1`", "`jobs`"):
        assert call in content
    assert "*entire* shell command" in content


def test_output_of_promoted_job_is_a_whole_tool_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    set_shell(ShellSession(cwd=str(tmp_path)))
    _confirmed_shell("printf progress; sleep 1")

    content = _confirmed_shell("output 1")[-1].content

    assert "**Job #1** - Running" in content
    assert "progress" in content


@pytest.mark.parametrize(
    "script",
    [
        "output 1 | tail -5",
        "sleep 0; output 1",
        "touch ran && output 1",
        "touch ran\noutput 1",
        "touch ran; if true; then output 1; fi",
        "touch ran; { output 1; }",
        "touch ran; echo $(output 1)",
        "touch ran; f() { output 1; }; f",
    ],
)
def test_output_inside_script_is_explained_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str
) -> None:
    """`output N` in a script used to reach bash: 'output: command not found'."""
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    monkeypatch.setattr("gptme.tools.shell.shutil.which", lambda _name: None)
    set_shell(ShellSession(cwd=str(tmp_path)))
    _confirmed_shell("sleep 1")

    content = _confirmed_shell(script)[-1].content

    assert "command not found" not in content
    assert "Not run: `output 1` is a gptme job-control command" in content
    assert "`wait 1 [timeout]`" in content
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize(
    "script",
    [
        "echo 'output 1'",
        "cat <<EOF\noutput 1\nEOF",
        "echo hi # output 1",
        "echo do output 1",
        "echo then { output 1",
    ],
)
def test_output_as_data_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str
) -> None:
    monkeypatch.setattr("gptme.tools.shell.shutil.which", lambda _name: None)
    set_shell(ShellSession(cwd=str(tmp_path)))

    content = _confirmed_shell(script)[-1].content

    assert content.startswith("Ran ")
    assert "Not run" not in content


def test_output_for_unknown_job_explains_instead_of_bash_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("gptme.tools.shell.shutil.which", lambda _name: None)
    set_shell(ShellSession(cwd=str(tmp_path)))

    content = _confirmed_shell("output 9")[-1].content

    assert "No background job #9" in content
    assert "command not found" not in content


def test_explicit_timeout_prefix_defers_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`timeout N cmd` is waited on in the foreground up to its own limit."""
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    shell = ShellSession(cwd=str(tmp_path))
    set_shell(shell)

    messages = list(execute_shell_impl("timeout 5 sleep 0.5", logdir=None, timeout=30))

    assert "Ran command" in messages[-1].content
    assert list_background_jobs() == []
    assert get_shell() is shell


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("timeout 400 python run.py", 400.0),
        ("timeout 5m make test", 300.0),
        ("timeout 1.5h ./train", 5400.0),
        ("timeout -s KILL 30 sleep 60", 30.0),
        ("timeout --signal=INT -k 10 30s sleep 60", 40.0),
        ("timeout --kill-after=1m --preserve-status 2m cmd", 180.0),
        ("/usr/bin/timeout 7 cmd arg", 7.0),
        ("timeout 0 sleep 60", None),
        ("timeout 30", None),
        ("sleep 400", None),
        ("echo timeout 400 cmd", None),
        ("timeout 'unbalanced", None),
        ("timeout 10m make test 2>&1", 600.0),
        ("timeout 5 cmd > log", 5.0),
        ("timeout 5 cmd \\\n  --flag", 5.0),
        # Anything outside the timeout's scope keeps normal promotion.
        ("timeout 10m sleep 1; sleep 500", None),
        ("timeout 10m a && b", None),
        ("timeout 10m a | tee log", None),
        ("timeout 10m a &", None),
        ("timeout 10m a\nb", None),
        ("timeout 10m a;", None),
        ('timeout 400 echo "$(sleep 500)"', None),
        ("timeout 400 diff <(sleep 500) b", None),
        # Quoted scripts run entirely inside the timeout.
        ("timeout 400 bash -c 'sleep 500; do_work' 2>&1", 400.0),
        ("timeout 5 cmd <<EOF\nline; other\nEOF", 5.0),
    ],
)
def test_explicit_timeout_seconds(command: str, expected: float | None) -> None:
    from gptme.tools.shell import _explicit_timeout_seconds

    assert _explicit_timeout_seconds(command) == expected


def test_completion_report_includes_output_tail_and_full_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    logdir = tmp_path / "log"
    monkeypatch.setattr("gptme.tools.shell_background._current_log_dir", lambda: logdir)
    set_shell(ShellSession(cwd=str(tmp_path)))

    messages = list(
        execute_shell_impl(
            "sleep 0.3; printf '%s%s' FIR ST; head -c 20000 /dev/zero | tr '\\0' x; "
            "printf 'LAST'; printf 'oops' >&2",
            logdir=None,
            timeout=5,
        )
    )
    assert "Promoted to background shell job #1" in messages[-1].content

    completions = _wait_for_completion(timeout=5)
    assert len(completions) == 1
    content = completions[0].content
    assert "finished (exit code 0)" in content
    assert "LAST" in content and "oops" in content
    assert "FIRST" not in content
    assert "truncated, showing last 8000 of 20009 chars" in content
    log_files = list((logdir / "tool-outputs" / "shell-jobs").glob("job-1-*.log"))
    assert len(log_files) == 1
    assert f"Full output: `{log_files[0]}`" in content
    full = log_files[0].read_text()
    assert "FIRST" in full and "LAST" in full and "oops" in full


def test_explicit_timeout_beyond_hard_limit_still_promotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A timeout the hard limit would cut short must not pin the foreground."""
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.05")
    set_shell(ShellSession(cwd=str(tmp_path)))

    messages = list(execute_shell_impl("timeout 60 sleep 1", logdir=None, timeout=3))

    assert "Promoted to background shell job #1" in messages[-1].content


def test_log_of_trimmed_background_job_is_not_called_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    from gptme.tools.shell_background import (
        _MAX_BUFFER_SIZE,
        format_job_output,
        start_background_job,
    )

    monkeypatch.setattr(
        "gptme.tools.shell_background._current_log_dir", lambda: tmp_path
    )
    job = start_background_job(
        f"{sys.executable} -c \"print('A' * {_MAX_BUFFER_SIZE + 100000})\""
    )
    job.process.wait(timeout=10)
    assert job._reader_thread is not None
    job._reader_thread.join(timeout=5)

    content = format_job_output(job, *job.get_output())

    assert "Full output" not in content
    assert "chars were not retained" in content
    assert job.log_path is not None and job.log_path.exists()
