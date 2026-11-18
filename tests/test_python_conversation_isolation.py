"""The in-process IPython backend must not share state between conversations.

A single process serves several server conversations. One ``InteractiveShell``
shared by all of them leaked variables from one conversation into another
(gptme/gptme#4951).
"""

import contextvars
from collections.abc import Iterator
from typing import Any

import pytest

from gptme.hooks import current_conversation_id
from gptme.tools import python as python_tool


def _close_shell(shell: Any) -> None:
    history = getattr(shell, "history_manager", None)
    if history is None:
        return
    history.end_session()
    if history.save_thread is not None:
        history.save_thread.stop()
        history.save_thread.join(timeout=2)


@pytest.fixture(autouse=True)
def inprocess(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    monkeypatch.setattr(python_tool, "_resolve_kernel_backend", lambda: False)
    monkeypatch.setattr(python_tool, "_ipython", None)
    monkeypatch.setattr(python_tool, "_conversation_ipython", {})
    yield
    for shell in python_tool._conversation_ipython.values():
        _close_shell(shell)
    if python_tool._ipython is not None:
        _close_shell(python_tool._ipython)


def _in_context(conversation_id: str | None, fn: Any) -> Any:
    ctx = contextvars.copy_context()
    if conversation_id is not None:
        ctx.run(current_conversation_id.set, conversation_id)
    return ctx.run(fn)


def _shell(conversation_id: str | None) -> Any:
    return _in_context(conversation_id, python_tool._get_ipython)


def _run(conversation_id: str | None, code: str) -> None:
    _shell(conversation_id).run_cell(code, store_history=False)


def test_two_conversations_get_distinct_shells() -> None:
    assert _shell("conv-a") is not _shell("conv-b")


def test_same_conversation_reuses_its_shell() -> None:
    assert _shell("conv-a") is _shell("conv-a")


def test_variables_do_not_leak_between_conversations() -> None:
    _run("conv-a", "leak_marker = 'from-a'")

    leaked = _in_context(
        "conv-b", lambda: "leak_marker" in python_tool._get_ipython().user_ns
    )
    assert leaked is False

    kept = _in_context(
        "conv-a", lambda: python_tool._get_ipython().user_ns.get("leak_marker")
    )
    assert kept == "from-a"


def _registered_helper() -> str:
    return "ok"


def test_registered_functions_present_in_every_conversation() -> None:
    python_tool.register_function(_registered_helper, tool_name="testtool")
    try:
        assert "_registered_helper" in _shell("conv-a").user_ns
        assert "_registered_helper" in _shell("conv-b").user_ns
    finally:
        python_tool.registered_functions.clear()


def test_cli_context_keeps_the_process_global_shell() -> None:
    # Without a conversation id (CLI ``chat``) there is one shell per process.
    assert _shell(None) is _shell(None)
    assert _shell(None) is python_tool._ipython


def test_reset_drops_the_conversation_shell() -> None:
    first = _shell("conv-a")
    python_tool.reset_conversation_ipython("conv-a")
    _close_shell(first)
    assert _shell("conv-a") is not first
