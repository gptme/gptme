"""Tests for the centralized CWD detector.

A server process runs several conversations concurrently. Each request sets its
own workspace ContextVar, but ``os.chdir`` is process-wide, so comparing raw
``os.getcwd()`` reports a *different* conversation's chdir as a change here: the
subscribers then announce that directory and inject its AGENTS.md into the wrong
conversation. The detector must observe the session directory.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

import pytest

from gptme.hooks import HookRegistry, get_registry, set_registry
from gptme.hooks.cwd_awareness import register as register_cwd_awareness
from gptme.hooks.cwd_changed import _detect_change, _store_cwd
from gptme.hooks.types import ToolExecutePostData, ToolExecutePreData
from gptme.message import Message
from gptme.tools.shell import set_workspace_cwd

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


@pytest.fixture()
def cwd_awareness() -> Generator[None, None, None]:
    """Register the CWD_CHANGED subscriber so detection yields its message."""
    previous = get_registry()
    set_registry(HookRegistry())
    register_cwd_awareness()
    try:
        yield
    finally:
        set_registry(previous)


def _yielded_messages(
    hook: Callable[[Any], Generator[Any, None, None]], data: Any
) -> list[Message]:
    return [msg for msg in hook(data) if isinstance(msg, Message)]


def _conversation(workspace: Path) -> contextvars.Context:
    """A conversation context whose session directory is ``workspace``."""
    ctx = contextvars.copy_context()
    ctx.run(set_workspace_cwd, str(workspace))
    return ctx


def test_foreign_process_chdir_is_not_a_session_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd_awareness: None
) -> None:
    """Another conversation's ``os.chdir`` must not fire CWD_CHANGED here."""
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()

    monkeypatch.chdir(ws_a)
    ctx_a = _conversation(ws_a)
    ctx_a.run(lambda: _yielded_messages(_store_cwd, ToolExecutePreData()))

    # Conversation B's step chdir's the whole process into B's workspace.
    monkeypatch.chdir(ws_b)

    assert (
        ctx_a.run(lambda: _yielded_messages(_detect_change, ToolExecutePostData()))
        == []
    )


def test_session_directory_change_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd_awareness: None
) -> None:
    """A change of this conversation's own directory is still announced."""
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()

    monkeypatch.chdir(ws_a)
    ctx_a = _conversation(ws_a)
    ctx_a.run(lambda: _yielded_messages(_store_cwd, ToolExecutePreData()))

    # This conversation's shell followed a ``cd``: only its own context moves.
    ctx_a.run(set_workspace_cwd, str(ws_b))

    messages = ctx_a.run(
        lambda: _yielded_messages(_detect_change, ToolExecutePostData())
    )
    assert len(messages) == 1
    assert str(ws_b) in messages[0].content


def test_cli_process_chdir_still_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd_awareness: None
) -> None:
    """Without a session workspace (CLI), the process cwd is the session dir."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    monkeypatch.chdir(first)
    _yielded_messages(_store_cwd, ToolExecutePreData())

    monkeypatch.chdir(second)
    messages = _yielded_messages(_detect_change, ToolExecutePostData())
    assert len(messages) == 1
    assert str(second) in messages[0].content
