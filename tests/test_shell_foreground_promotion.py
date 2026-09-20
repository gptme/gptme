"""Foreground shell commands promote to conversation-owned jobs at a soft timeout."""

import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

if TYPE_CHECKING:
    from gptme.message import Message
from gptme.tools.shell import (
    ShellSession,
    _get_foreground_timeout,
    execute_shell_impl,
    get_shell,
    set_shell,
)
from gptme.tools.shell_background import (
    background_job_completion_hook,
    execute_output_command,
    list_background_jobs,
    reset_background_jobs,
)


@pytest.fixture(autouse=True)
def _clean_shell_jobs() -> Generator[None, None, None]:
    reset_background_jobs()
    yield
    get_shell().close()
    reset_background_jobs()


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
    deadline = time.monotonic() + 1
    completions: list[Message] = []
    while not completions and time.monotonic() < deadline:
        completions = list(background_job_completion_hook(Mock(chat_id=None)))
        time.sleep(0.01)
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

    def join_soft_timeout(self: threading.Thread, timeout: float | None = None) -> None:
        if timeout == 0.5 and threading.current_thread() is threading.main_thread():
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
    deadline = time.monotonic() + 1
    completions: list[Message] = []
    while not completions and time.monotonic() < deadline:
        completions = list(background_job_completion_hook(Mock(chat_id=None)))
        time.sleep(0.01)
    assert len(completions) == 1
    assert "exit code -124" in completions[0].content
