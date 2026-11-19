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


# ---------------------------------------------------------------------------
# StepPersister: the reply is on disk while its tools run (#4956)
# ---------------------------------------------------------------------------


def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_step_persister_writes_reply_once_before_tools(
    monkeypatch, tmp_path, output_format, capsys
):
    """A live reader sees the running call; completion only appends results,
    preserving the saved reply byte-for-byte, including generation timings."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    from gptme.logmanager import LogManager, eventlog
    from gptme.message import get_output_format, set_output_format
    from gptme.tools import init_tools

    init_tools(allowlist=["shell"])
    chat_module = importlib.import_module("gptme.chat")

    manager = LogManager.load("test-step-persister", create=True, lock=False)
    manager.append(Message("user", "run something", quiet=True))
    logfile = manager.logfile

    mock_meta: MessageMetadata = {
        "model": "mock/model",
        "timings": {"gen_ms": 50.0, "ttft_ms": 10.0},
    }
    mock_response = Message("assistant", "```shell\nsleep 300\n```", metadata=mock_meta)
    seen_during_tool: list[dict] = []
    saved_prefix: list[bytes] = []

    def fake_execute_msg(
        msg, log, workspace=None, tool_timings=None, on_tool_execution=None
    ):
        # What a TUI child view reading the transcript sees mid-tool.
        seen_during_tool.extend(_read_jsonl(logfile))
        saved_prefix.append(logfile.read_bytes())
        assert tool_timings is not None
        tool_timings["shell"] = 1234.0
        yield Message("system", "Ran command: sleep 300", quiet=True)

    persister = chat_module.StepPersister(manager)
    previous_format = get_output_format()
    capsys.readouterr()
    try:
        set_output_format(output_format)
        with (
            patch.object(chat_module, "reply", return_value=mock_response),
            patch.object(chat_module, "execute_msg", fake_execute_msg),
        ):
            for msg in chat_module.step(
                manager.log,
                stream=False,
                on_before_tool_execution=persister.before_tools,
                logdir=manager.logdir,
            ):
                persister.append(msg)
    finally:
        set_output_format(previous_format)

    output = capsys.readouterr().out
    if output_format == "json":
        events = [json.loads(line) for line in output.splitlines() if line]
        assert len(events) == 1
        assert events[0]["role"] == "assistant"
        assert events[0]["metadata"]["timings"] == mock_meta["timings"]

    assert [m["role"] for m in seen_during_tool] == ["user", "assistant"]
    assert "sleep 300" in seen_during_tool[-1]["content"]

    on_disk = _read_jsonl(logfile)
    assert [m["role"] for m in on_disk] == ["user", "assistant", "system"]
    assert logfile.read_bytes().startswith(saved_prefix[0])
    assert on_disk[1] == seen_during_tool[1]
    assert on_disk[1]["metadata"]["timings"] == {"gen_ms": 50.0, "ttft_ms": 10.0}
    assert [m.role for m in manager.log] == ["user", "assistant", "system"]
    assert (manager.log[1].metadata or {})["timings"] == mock_meta["timings"]
    recovered = eventlog.recover_messages(manager.logdir)
    assert recovered == on_disk


def test_step_persister_keeps_reply_when_tools_are_interrupted(monkeypatch, tmp_path):
    """An exception escaping mid-tool must not lose the already-persisted reply."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    from gptme.logmanager import LogManager
    from gptme.tools import init_tools

    init_tools(allowlist=["shell"])
    chat_module = importlib.import_module("gptme.chat")

    manager = LogManager.load("test-step-persister-int", create=True, lock=False)
    manager.append(Message("user", "run something", quiet=True))
    mock_response = Message("assistant", "```shell\nsleep 300\n```")

    def crashing_execute_msg(
        msg, log, workspace=None, tool_timings=None, on_tool_execution=None
    ):
        raise KeyboardInterrupt
        yield  # pragma: no cover

    persister = chat_module.StepPersister(manager)
    with (
        patch.object(chat_module, "reply", return_value=mock_response),
        patch.object(chat_module, "execute_msg", crashing_execute_msg),
        pytest.raises(KeyboardInterrupt),
    ):
        for msg in chat_module.step(
            manager.log,
            stream=False,
            on_before_tool_execution=persister.before_tools,
            logdir=manager.logdir,
        ):
            persister.append(msg)

    on_disk = _read_jsonl(manager.logfile)
    assert [m["role"] for m in on_disk] == ["user", "assistant"]
    assert "sleep 300" in on_disk[1]["content"]


def test_step_persister_without_tools_appends_once(monkeypatch, tmp_path):
    """A reply with no tool calls is persisted exactly once."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    from gptme.logmanager import LogManager

    chat_module = importlib.import_module("gptme.chat")
    manager = LogManager.load("test-step-persister-plain", create=True, lock=False)
    manager.append(Message("user", "hi", quiet=True))

    persister = chat_module.StepPersister(manager)
    with patch.object(chat_module, "reply", return_value=Message("assistant", "hello")):
        for msg in chat_module.step(
            manager.log,
            stream=False,
            on_before_tool_execution=persister.before_tools,
            logdir=manager.logdir,
        ):
            persister.append(msg)

    assert [m["role"] for m in _read_jsonl(manager.logfile)] == ["user", "assistant"]


@pytest.mark.parametrize("branch", ["main", "experiment"])
def test_step_persister_compaction_does_not_duplicate_history(
    monkeypatch, tmp_path, branch
):
    """Compaction must not reintroduce or mutate the saved reply in any history."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    from gptme.logmanager import LogManager

    chat_module = importlib.import_module("gptme.chat")
    manager = LogManager.load("test-persister-compaction", create=True, lock=False)
    manager.append(Message("user", "compact now", quiet=True))
    if branch != "main":
        manager.branch(branch)
    persister = chat_module.StepPersister(manager)
    early = Message("assistant", "```ipython\ncompact()\n```", quiet=True)
    persister.before_tools(early)
    manager.preserve_lossless_log()
    manager.create_view("compacted", [Message("system", "summary", quiet=True)])
    manager.switch_view("compacted")
    timed = early.replace(metadata={"timings": {"tool_ms": 5.0}})
    persister.append(timed)
    persister.append(Message("system", "compacted", quiet=True))

    assert [m.content for m in manager.log] == ["summary", "compacted"]
    for history in (manager.full_log, manager._branches[branch]):
        replies = [m for m in history if m.role == "assistant"]
        assert len(replies) == 1
        assert replies[0].to_dict() == early.to_dict()
    manager.switch_to_master()
    assert sum(m.role == "assistant" for m in manager.log) == 1
    reloaded = LogManager.load("test-persister-compaction", branch=branch, lock=False)
    replies = [m for m in reloaded.full_log if m.role == "assistant"]
    assert len(replies) == 1
    assert replies[0].to_dict() == early.to_dict()
