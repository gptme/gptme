"""End-to-end tests: a real subagent run must not disturb its parent session.

Unlike most subagent tests (which stub ``_create_subagent_thread``), these run
the real subagent ``chat()`` with the offline ``mock/echo`` model. ``mock/echo``
echoes the prompt back, so a prompt that contains a ``complete`` block makes the
subagent finish through the real complete tool.

Regression coverage for a bug where, after a thread-mode subagent ran, no
later tool call in the parent session executed: the subagent's nested
``chat()`` re-entered ``init()``, which reset the *process-global* tool format
to the subagent's ``"markdown"``, so the parent's ``@tool(id): {...}`` calls
were no longer parsed at all.
"""

import json
import os
from pathlib import Path

import pytest

from gptme.hooks.registry import get_registry
from gptme.message import Message, get_output_format, set_output_format
from gptme.tools import execute_msg, get_tools, init_tools
from gptme.tools.base import ToolUse, get_tool_format, set_tool_format
from gptme.tools.subagent import Subagent, subagent, subagent_wait
from gptme.tools.subagent.types import (
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)

COMPLETE_PROMPT = "Say hi.\n```complete\nSUBAGENT-DONE\n```"


def _hook_names() -> set[str]:
    return {
        f"{hook_type}:{h.name}"
        for hook_type, hooks in get_registry().hooks.items()
        for h in hooks
    }


@pytest.fixture
def parent_session(tmp_path, monkeypatch):
    """A parent session using native tool calling (``tool_format="tool"``)."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    # keep workspace context small and deterministic
    monkeypatch.setenv("GPTME_NO_EXAMPLES", "1")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    init_tools(["shell", "subagent"])
    set_tool_format("tool")
    set_output_format("quiet")
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()
    yield workspace
    set_tool_format("markdown")
    set_output_format("text")


def _run_parent_shell_call(marker: str) -> list[Message]:
    """Run a native-format shell call as the parent would."""
    call = "@shell(call-1): " + json.dumps({"command": f"echo {marker}"})
    msg = Message("assistant", f"Running it:\n{call}")
    uses = list(ToolUse.iter_from_content(msg.content))
    assert [u.tool for u in uses] == ["shell"], "parent tool call not parsed"
    assert all(u.is_runnable for u in uses)
    return list(execute_msg(msg))


@pytest.mark.timeout(60)
def test_thread_subagent_leaves_parent_state_unchanged(parent_session):
    """After a thread-mode subagent runs, the parent's tools, hooks, tool format,
    output format and cwd are unchanged, and a following tool call executes."""
    tools_before = [t.name for t in get_tools()]
    hooks_before = _hook_names()
    cwd_before = os.getcwd()

    subagent("iso", COMPLETE_PROMPT, model="mock/echo")
    result = subagent_wait("iso", timeout=60)

    assert result["status"] == "success", result
    assert "SUBAGENT-DONE" in result["result"]

    assert get_tool_format() == "tool"
    assert get_output_format() == "quiet"
    assert [t.name for t in get_tools()] == tools_before
    assert _hook_names() == hooks_before
    assert os.getcwd() == cwd_before

    outputs = _run_parent_shell_call("after-subagent")
    assert any("after-subagent" in m.content for m in outputs), outputs


@pytest.mark.timeout(60)
def test_thread_subagent_with_workdir_restores_parent_cwd(parent_session, tmp_path):
    """chat() chdirs into the subagent workspace; the parent's cwd is restored."""
    other = tmp_path / "other"
    other.mkdir()
    cwd_before = os.getcwd()

    subagent("iso-cwd", COMPLETE_PROMPT, model="mock/echo", workdir=str(other))
    result = subagent_wait("iso-cwd", timeout=60)

    assert result["status"] == "success", result
    assert os.getcwd() == cwd_before


@pytest.mark.slow
def test_subprocess_subagent_roundtrip(parent_session, tmp_path, monkeypatch):
    """Subprocess mode: spawn, wait, success result with the complete summary,
    and the parent keeps executing tool calls afterwards."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parent.parent))

    subagent("iso-proc", COMPLETE_PROMPT, model="mock/echo", use_subprocess=True)
    result = subagent_wait("iso-proc", timeout=120)

    assert result["status"] == "success", result
    assert "SUBAGENT-DONE" in result["result"]
    assert get_tool_format() == "tool"
    outputs = _run_parent_shell_call("after-proc-subagent")
    assert any("after-proc-subagent" in m.content for m in outputs), outputs


def test_tool_format_set_in_other_thread_does_not_leak():
    """set_tool_format() is context-local: a nested session in another thread
    (what init() re-entry does for a thread-mode subagent) keeps its own."""
    import threading

    set_tool_format("tool")
    seen: list[str] = []

    def child() -> None:
        seen.append(get_tool_format())  # inherits the process default
        set_tool_format("markdown")
        seen.append(get_tool_format())

    t = threading.Thread(target=child)
    t.start()
    t.join()
    assert seen[1] == "markdown"
    assert get_tool_format() == "tool"
    set_tool_format("markdown")


def _write_log(logdir: Path, messages: list[tuple[str, str]]) -> None:
    logdir.mkdir(parents=True, exist_ok=True)
    with (logdir / "conversation.jsonl").open("w") as f:
        for role, content in messages:
            f.write(
                json.dumps(
                    {
                        "role": role,
                        "content": content,
                        "timestamp": "2026-01-01T00:00:00",
                    }
                )
                + "\n"
            )


def _read(tmp_path: Path, messages: list[tuple[str, str]]):
    logdir = tmp_path / "sa-log"
    _write_log(logdir, messages)
    sa = Subagent(
        agent_id="read-log", prompt="task", thread=None, logdir=logdir, model=None
    )
    return sa._read_log()


def test_read_log_clarify_despite_trailing_system_messages(tmp_path):
    result = _read(
        tmp_path,
        [
            ("user", "Do it"),
            ("assistant", "```clarify\nWhich one?\n```"),
            ("system", "<system_warning>Token usage: 1/2</system_warning>"),
        ],
    )
    assert result.status == "clarification_needed"


def test_read_log_failure_without_complete(tmp_path):
    result = _read(
        tmp_path,
        [
            ("user", "Do it"),
            ("assistant", "I did not finish."),
            ("system", "<system_warning>Token usage: 1/2</system_warning>"),
        ],
    )
    assert result.status == "failure"
