"""Tests for per-step timing breakdown (ttft_ms, gen_ms, tool_ms, tool_ms_by_name).

Covers:
- MessageTimings TypedDict round-trips through JSON.
- execute_msg() populates a tool_timings dict when provided.
- step() attaches timing to the assistant message before yielding it.
"""

import importlib
import json
import threading
from unittest.mock import patch

import pytest

from gptme.message import Message, MessageMetadata, MessageTimings


def _has_flask() -> bool:
    """Check if flask is available and importable (required for server components)."""
    try:
        import flask  # noqa: F401

        return True
    except (ImportError, TypeError):
        return False


# ---------------------------------------------------------------------------
# MessageTimings round-trip tests
# ---------------------------------------------------------------------------


def test_message_timings_roundtrip_json():
    """MessageTimings survives a JSON round-trip via to_dict()."""
    timings: MessageTimings = {
        "ttft_ms": 820.0,
        "gen_ms": 4200.0,
        "tool_ms": 1850.0,
        "tool_ms_by_name": {"shell": 1600.0, "read": 250.0},
    }
    meta: MessageMetadata = {"model": "anthropic/claude-sonnet", "timings": timings}
    msg = Message("assistant", "hello", metadata=meta)

    d = msg.to_dict()
    assert d["metadata"]["timings"]["ttft_ms"] == 820.0
    assert d["metadata"]["timings"]["gen_ms"] == 4200.0
    assert d["metadata"]["timings"]["tool_ms"] == 1850.0
    assert d["metadata"]["timings"]["tool_ms_by_name"] == {
        "shell": 1600.0,
        "read": 250.0,
    }

    # JSON serialization
    j = json.dumps(d)
    restored = json.loads(j)
    assert restored["metadata"]["timings"]["ttft_ms"] == 820.0
    assert restored["metadata"]["timings"]["tool_ms_by_name"]["shell"] == 1600.0


def test_message_timings_partial():
    """MessageTimings with only LLM fields (no tool fields) serializes correctly."""
    timings: MessageTimings = {"ttft_ms": 350.0, "gen_ms": 1100.0}
    meta: MessageMetadata = {"model": "openai/gpt-4o", "timings": timings}
    msg = Message("assistant", "hi", metadata=meta)

    d = msg.to_dict()
    assert d["metadata"]["timings"]["ttft_ms"] == 350.0
    assert d["metadata"]["timings"]["gen_ms"] == 1100.0
    assert "tool_ms" not in d["metadata"]["timings"]
    assert "tool_ms_by_name" not in d["metadata"]["timings"]


def test_message_timings_absent_when_empty():
    """A message without timings has no 'timings' key in its dict."""
    meta: MessageMetadata = {"model": "openai/gpt-4o", "cost": 0.001}
    msg = Message("assistant", "hi", metadata=meta)
    d = msg.to_dict()
    assert "timings" not in d["metadata"]


# ---------------------------------------------------------------------------
# execute_msg() tool_timings accumulation
# ---------------------------------------------------------------------------


def test_execute_msg_collects_tool_timings():
    """execute_msg accumulates per-tool durations in the tool_timings dict."""
    from gptme.tools import execute_msg, init_tools

    init_tools(allowlist=["shell"])

    # The shell tool registers the "shell" block type (not "bash")
    msg = Message("assistant", "```shell\necho hello\n```")
    tool_timings: dict[str, float] = {}

    outputs = list(execute_msg(msg, tool_timings=tool_timings))

    # At least one tool output should have been produced
    assert outputs, "expected tool output messages"
    # shell tool duration should be present
    assert "shell" in tool_timings, (
        f"expected 'shell' in tool_timings, got {tool_timings}"
    )
    assert tool_timings["shell"] > 0, "timing should be positive"


def test_execute_msg_accumulates_repeated_tool():
    """Multiple calls to the same tool accumulate correctly in tool_timings."""
    from gptme.tools import execute_msg, init_tools

    init_tools(allowlist=["shell"])

    # Two shell blocks in one message (uses "shell" block type)
    msg = Message("assistant", "```shell\necho one\n```\n\n```shell\necho two\n```")
    tool_timings: dict[str, float] = {}

    list(execute_msg(msg, tool_timings=tool_timings))

    assert "shell" in tool_timings
    # Both invocations should contribute — sum must be positive
    assert tool_timings["shell"] > 0


def test_execute_msg_no_timings_when_no_tools():
    """execute_msg produces no timings for messages with no runnable tool blocks."""
    from gptme.tools import execute_msg, init_tools

    init_tools(allowlist=["shell"])

    msg = Message("assistant", "Just a plain response, no tools.")
    tool_timings: dict[str, float] = {}

    outputs = list(execute_msg(msg, tool_timings=tool_timings))

    assert outputs == []
    assert tool_timings == {}


# ---------------------------------------------------------------------------
# step() integration: timing attached to assistant message
# ---------------------------------------------------------------------------


def test_step_attaches_timings_to_assistant_message():
    """step() should yield an assistant message whose metadata.timings has ttft_ms/gen_ms."""
    from gptme.chat import step
    from gptme.tools import init_tools

    init_tools(allowlist=["shell"])

    messages = [Message("user", "say hello")]

    # Mock reply() so no real LLM call is made.
    # The returned Message simulates what _reply_stream produces: metadata with timings.
    mock_timings: MessageTimings = {"ttft_ms": 250.0, "gen_ms": 800.0}
    mock_meta: MessageMetadata = {"model": "mock/model", "timings": mock_timings}
    mock_response = Message("assistant", "Hello!", metadata=mock_meta)

    # Use patch.object on the module object (via importlib) rather than the
    # string form patch("gptme.chat.reply"): gptme/__init__.py lazily exports
    # the `chat` *function* as the `gptme.chat` package attribute, which can
    # shadow the `gptme.chat` submodule and break string-path attribute
    # lookup depending on test collection order.
    chat_module = importlib.import_module("gptme.chat")
    with patch.object(chat_module, "reply", return_value=mock_response):
        yielded = list(step(messages, stream=False))

    # The first yielded message should be the assistant response
    assistant_msgs = [m for m in yielded if m.role == "assistant"]
    assert assistant_msgs, "expected at least one assistant message"

    assistant_msg = assistant_msgs[0]
    assert assistant_msg.metadata is not None
    assert "timings" in assistant_msg.metadata, (
        f"expected 'timings' in assistant metadata, got {assistant_msg.metadata}"
    )
    timings = assistant_msg.metadata["timings"]
    assert timings.get("ttft_ms") == 250.0
    assert timings.get("gen_ms") == 800.0


def test_step_attaches_tool_timings():
    """step() should merge tool_ms/tool_ms_by_name into the assistant message timings."""
    from gptme.chat import step
    from gptme.tools import init_tools

    init_tools(allowlist=["shell"])

    messages = [Message("user", "run something")]

    # Mock response contains a shell tool call so execute_msg actually runs.
    mock_timings: MessageTimings = {"ttft_ms": 100.0, "gen_ms": 500.0}
    mock_meta: MessageMetadata = {"model": "mock/model", "timings": mock_timings}
    # Use "shell" block type (not "bash") to match the shell tool's block_types
    mock_response = Message(
        "assistant", "```shell\necho timing-test\n```", metadata=mock_meta
    )

    chat_module = importlib.import_module("gptme.chat")
    with patch.object(chat_module, "reply", return_value=mock_response):
        yielded = list(step(messages, stream=False))

    assistant_msgs = [m for m in yielded if m.role == "assistant"]
    assert assistant_msgs
    assistant_msg = assistant_msgs[0]
    assert assistant_msg.metadata is not None

    timings = assistant_msg.metadata.get("timings", {})
    # LLM timings from mock should be preserved
    assert timings.get("ttft_ms") == 100.0
    assert timings.get("gen_ms") == 500.0
    # Tool timings should be added
    assert "tool_ms" in timings, f"expected tool_ms in timings, got {timings}"
    assert timings["tool_ms"] > 0
    assert "tool_ms_by_name" in timings
    assert "shell" in timings["tool_ms_by_name"]


# ---------------------------------------------------------------------------
# _attach_tool_timings() concurrent confirmation threads (server path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_flask(), reason="flask not installed, install server extras (-E server)"
)
def test_attach_tool_timings_concurrent_threads_do_not_lose_updates(
    monkeypatch, tmp_path
):
    """Two confirmation threads racing to attach tool timings for the same
    assistant message must both survive — neither read-merge-write should
    silently discard the other's contribution (lost-update race)."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    from gptme.logmanager import LogManager
    from gptme.server.session_step import _attach_tool_timings

    conversation_id = "test-concurrent-tool-timings"
    manager = LogManager.load(conversation_id, create=True, lock=False)
    manager.append(Message("user", "run two tools"))
    manager.append(Message("assistant", "running tools"))

    # Force both threads to call _attach_tool_timings at (as close to)
    # the same instant as possible, to exercise the race.
    barrier = threading.Barrier(2)

    def run(tool_name: str, value: float) -> None:
        barrier.wait(timeout=5)
        _attach_tool_timings(conversation_id, {tool_name: value})

    t1 = threading.Thread(target=run, args=("shell", 100.0))
    t2 = threading.Thread(target=run, args=("read", 50.0))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()

    final = LogManager.load(conversation_id, lock=False)
    assistant_msgs = [m for m in final.log.messages if m.role == "assistant"]
    assert assistant_msgs
    metadata = assistant_msgs[-1].metadata
    assert metadata is not None
    timings = metadata.get("timings", {})
    assert timings.get("tool_ms_by_name") == {"shell": 100.0, "read": 50.0}, (
        f"expected both threads' timings preserved, got {timings}"
    )
    assert timings.get("tool_ms") == 150.0
