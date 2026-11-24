"""A server conversation keeps its shell (and ``cd``) across tool calls.

The server runs every tool call in a fresh thread, so the context-local shell
starts empty each turn. The conversation's registered shell must be reused,
otherwise a ``cd`` from one turn is silently lost in the next even though the
shell output reported it (gptme/gptme#5187).
"""

import contextvars
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
