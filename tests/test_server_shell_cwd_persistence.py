"""A server conversation keeps its shell (and ``cd``) across tool calls.

The server runs every tool call in a fresh thread, so the context-local shell
starts empty each turn. The conversation's registered shell must be reused,
otherwise a ``cd`` from one turn is silently lost in the next even though the
shell output reported it (gptme/gptme#5187).
"""

import contextvars
import threading
import time
from pathlib import Path

import pytest

from gptme.hooks import current_conversation_id
from gptme.tools.base import current_workspace
from gptme.tools.shell import (
    close_conversation_shell,
    get_shell,
    set_workspace_cwd,
)


def _tool_call(conv_id: str, workspace: Path, fn):
    """Run ``fn`` as a server tool worker does: a fresh context per call."""

    def run():
        current_conversation_id.set(conv_id)
        set_workspace_cwd(str(workspace))
        return fn()

    return contextvars.Context().run(run)


@pytest.fixture
def conv(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    conv_id = f"conv-cwd-{tmp_path.name}"
    yield conv_id
    close_conversation_shell(conv_id)


def test_cd_persists_across_tool_calls(tmp_path: Path, conv: str) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()

    def cd():
        shell = get_shell()
        status, _, _ = shell.run(f"cd {sub} && export GPTME_5187=kept", output=False)
        assert status == 0
        return shell

    first = _tool_call(conv, tmp_path, cd)

    def inspect():
        shell = get_shell()
        _, out, _ = shell.run("echo $GPTME_5187", output=False)
        return shell, current_workspace(), out.strip()

    second, workspace, env = _tool_call(conv, tmp_path, inspect)
    assert second is first
    assert workspace == sub.resolve()
    assert env == "kept"


def test_changed_workspace_gets_a_fresh_shell(tmp_path: Path, conv: str) -> None:
    other = tmp_path / "other"
    other.mkdir()
    first = _tool_call(conv, tmp_path, get_shell)
    second, workspace = _tool_call(
        conv, other, lambda: (get_shell(), current_workspace())
    )
    assert second is not first
    assert workspace == other.resolve()
    assert first._closed


def test_closed_shell_is_not_reused(tmp_path: Path, conv: str) -> None:
    first = _tool_call(conv, tmp_path, get_shell)
    close_conversation_shell(conv)
    second = _tool_call(conv, tmp_path, get_shell)
    assert second is not first


def _run_call(conv_id: str, workspace: Path, cmd: str, out: dict, key: str) -> None:
    from gptme.tools.shell import execute_shell_impl

    def call():
        msgs = list(execute_shell_impl(cmd, logdir=None, timeout=30))
        out[key] = ("\n".join(m.content for m in msgs), time.monotonic())

    _tool_call(conv_id, workspace, call)


@pytest.mark.timeout(30)
def test_parallel_calls_do_not_share_the_shell(tmp_path: Path, conv: str) -> None:
    """Two approved calls of one reply can run in parallel server threads.

    The reused shell is one Bash process, so the second call must wait for the
    first instead of interleaving with it.
    """
    _tool_call(conv, tmp_path, get_shell)  # register the conversation shell
    out: dict[str, tuple[str, float]] = {}
    first = threading.Thread(
        target=_run_call,
        args=(conv, tmp_path, "sleep 1; echo first-done", out, "first"),
    )
    first.start()
    time.sleep(0.3)
    second_started = time.monotonic()
    _run_call(conv, tmp_path, "echo second-done", out, "second")
    first.join(10)

    assert "first-done" in out["first"][0]
    assert "second-done" not in out["first"][0]
    assert "second-done" in out["second"][0]
    assert "first-done" not in out["second"][0]
    # The second call waited for the first one to finish.
    assert out["second"][1] >= out["first"][1]
    assert out["second"][1] - second_started >= 0.5


@pytest.mark.timeout(30)
def test_promoted_call_hands_the_shell_off(
    tmp_path: Path, conv: str, monkeypatch
) -> None:
    """A waiting call gets a new shell once the first is promoted to a job."""
    from gptme.tools.shell_background import reset_background_jobs

    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.5")
    reset_background_jobs()
    try:
        _tool_call(conv, tmp_path, get_shell)
        out: dict[str, tuple[str, float]] = {}
        first = threading.Thread(
            target=_run_call, args=(conv, tmp_path, "sleep 5", out, "first")
        )
        first.start()
        time.sleep(0.2)
        started = time.monotonic()
        _run_call(conv, tmp_path, "echo second-done", out, "second")
        first.join(10)

        assert "Promoted to background shell job" in out["first"][0]
        assert "second-done" in out["second"][0]
        # Not blocked behind the 5s background job.
        assert out["second"][1] - started < 3
    finally:
        reset_background_jobs()
