"""Tests for gptme/logmanager/eventlog.py"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from gptme.logmanager import Log, LogManager
from gptme.logmanager.eventlog import (
    CHECKPOINT_INTERVAL,
    EVENT_CHECKPOINT,
    EVENT_MESSAGE_APPEND,
    EVENT_MESSAGE_EDIT,
    EVENT_UNDO,
    _event_log_path,
    append_event,
    find_latest_checkpoint,
    read_events,
    recover_messages,
    sequence_number,
    should_checkpoint,
    write_checkpoint,
)
from gptme.message import Message
from gptme.tools import init_tools


@pytest.fixture(autouse=True)
def _init_tools():
    init_tools(allowlist=["save", "patch", "append"])


@pytest.fixture
def logdir(tmp_path: Path):
    """Create a logdir and set GPTME_LOGS_HOME so events/JSONL go there."""
    d = tmp_path / "logs" / "test-conv"
    d.mkdir(parents=True, exist_ok=True)
    monkey = pytest.MonkeyPatch()
    monkey.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    yield d
    monkey.undo()


def test_event_log_path(logdir: Path):
    """Event log path is derived correctly."""
    assert _event_log_path(logdir) == logdir / "events.jsonl"


def test_append_and_read_events(logdir: Path):
    """Appending events and reading them back works."""
    event1 = {"seq": 1, "ts": "2026-01-01T00:00:00Z", "type": "test", "payload": {}}
    event2 = {"seq": 2, "ts": "2026-01-01T00:00:01Z", "type": "test", "payload": {}}

    append_event(logdir, event1)
    append_event(logdir, event2)

    events = read_events(logdir)
    assert len(events) == 2
    assert events[0]["seq"] == 1
    assert events[1]["seq"] == 2


def test_read_events_empty_logdir(logdir: Path):
    """Reading events from a non-existent log returns empty list."""
    assert read_events(logdir) == []


def test_sequence_number_begins_at_one(logdir: Path):
    """Sequence number starts at 1 for a fresh log directory."""
    assert sequence_number(logdir) == 1


def test_sequence_number_increments(logdir: Path):
    """Sequence number increments after appending events."""
    append_event(logdir, {"seq": 1, "ts": "", "type": "test", "payload": {}})
    assert sequence_number(logdir) == 2

    append_event(logdir, {"seq": 2, "ts": "", "type": "test", "payload": {}})
    assert sequence_number(logdir) == 3


def test_should_checkpoint(logdir: Path):
    """Checkpoint is due every CHECKPOINT_INTERVAL events."""
    assert should_checkpoint(0) is False
    assert should_checkpoint(1) is False
    assert should_checkpoint(CHECKPOINT_INTERVAL - 1) is False
    assert should_checkpoint(CHECKPOINT_INTERVAL) is True
    assert should_checkpoint(CHECKPOINT_INTERVAL * 2) is True
    assert should_checkpoint(CHECKPOINT_INTERVAL * 2 + 1) is False


def test_write_and_find_checkpoint(logdir: Path):
    """Writing a checkpoint and finding it works."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    write_checkpoint(logdir, 50, messages)

    # Read events, find checkpoint
    events = read_events(logdir)
    assert len(events) == 1
    assert events[0]["type"] == EVENT_CHECKPOINT
    assert events[0]["seq"] == 50
    assert len(events[0]["payload"]["messages"]) == 2

    # find_latest_checkpoint
    cp = find_latest_checkpoint(events)
    assert cp is not None
    assert cp["seq"] == 50


def test_find_latest_among_multiple(logdir: Path):
    """find_latest_checkpoint returns the most recent checkpoint."""
    write_checkpoint(logdir, 50, [])
    write_checkpoint(logdir, 100, [{"role": "user", "content": "last"}])

    cp = find_latest_checkpoint(read_events(logdir))
    assert cp is not None
    assert cp["seq"] == 100
    assert cp["payload"]["messages"][0]["content"] == "last"


def test_recover_messages_no_event_log(logdir: Path):
    """recover_messages returns None when no event log exists."""
    assert recover_messages(logdir) is None


def test_recover_messages_from_append_events(logdir: Path):
    """Recovery reconstructs messages from append events."""
    append_event(
        logdir,
        {
            "seq": 1,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "hello"}},
        },
    )
    append_event(
        logdir,
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "assistant", "content": "world"}},
        },
    )

    result = recover_messages(logdir)
    assert result is not None
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "hello"
    assert result[1]["role"] == "assistant"
    assert result[1]["content"] == "world"


def test_recover_messages_with_checkpoint_and_replay(logdir: Path):
    """Recovery starts from latest checkpoint, then replays appends."""
    # Write a checkpoint with 2 messages
    write_checkpoint(
        logdir,
        10,
        [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ],
    )

    # Append events after the checkpoint
    append_event(
        logdir,
        {
            "seq": 11,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "msg3"}},
        },
    )

    result = recover_messages(logdir)
    assert result is not None
    assert len(result) == 3
    assert result[0]["content"] == "msg1"
    assert result[1]["content"] == "msg2"
    assert result[2]["content"] == "msg3"


def test_recover_messages_with_edit_events(logdir: Path):
    """Recovery handles message_edit events by replacing the full message list."""
    # Append two messages
    append_event(
        logdir,
        {
            "seq": 1,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "hello"}},
        },
    )
    append_event(
        logdir,
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "assistant", "content": "world"}},
        },
    )
    # Edit replaces the full message list (as written by _write_event_log)
    append_event(
        logdir,
        {
            "seq": 3,
            "ts": "2026-01-01T00:00:02Z",
            "type": EVENT_MESSAGE_EDIT,
            "payload": {"messages": [{"role": "user", "content": "edited hello"}]},
        },
    )

    result = recover_messages(logdir)
    assert result is not None
    assert len(result) == 1
    assert result[0]["content"] == "edited hello"


def test_recover_messages_with_undo_events(logdir: Path):
    """Recovery handles undo events by removing the last message."""
    append_event(
        logdir,
        {
            "seq": 1,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "hello"}},
        },
    )
    append_event(
        logdir,
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "assistant", "content": "world"}},
        },
    )
    # Legacy payload without "n" (backward compat: should pop 1 message)
    append_event(
        logdir,
        {
            "seq": 3,
            "ts": "2026-01-01T00:00:02Z",
            "type": EVENT_UNDO,
            "payload": {},
        },
    )

    result = recover_messages(logdir)
    assert result is not None
    assert len(result) == 1
    assert result[0]["content"] == "hello"


def test_recover_messages_multi_undo(logdir: Path):
    """Recovery handles undo(n>1) by popping the correct number of messages."""
    for i, (role, content) in enumerate(
        [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")], start=1
    ):
        append_event(
            logdir,
            {
                "seq": i,
                "ts": "2026-01-01T00:00:00Z",
                "type": EVENT_MESSAGE_APPEND,
                "payload": {"message": {"role": role, "content": content}},
            },
        )
    # undo(n=3): removes last 3 messages, leaving only "a"
    append_event(
        logdir,
        {
            "seq": 5,
            "ts": "2026-01-01T00:00:01Z",
            "type": EVENT_UNDO,
            "payload": {"n": 3},
        },
    )

    result = recover_messages(logdir)
    assert result is not None
    assert len(result) == 1
    assert result[0]["content"] == "a"


def test_recover_messages_fully_undone(logdir: Path):
    """fully-undone session returns [] not None (distinguishable from missing log)."""
    append_event(
        logdir,
        {
            "seq": 1,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "hello"}},
        },
    )
    append_event(
        logdir,
        {
            "seq": 2,
            "ts": "2026-01-01T00:00:01Z",
            "type": EVENT_UNDO,
            "payload": {"n": 1},
        },
    )

    result = recover_messages(logdir)
    # Must be [] (event log exists, no messages), not None (missing log)
    assert result == []


# ── Integration with LogManager ──────────────────────────────────────


def test_logmanager_append_writes_event_log(logdir: Path):
    """LogManager.append writes a message_append event."""
    with LogManager(logdir=logdir) as lm:
        lm.append(Message("user", "hello from event log test"))

    events = read_events(logdir)
    assert len(events) >= 1
    assert events[0]["type"] == EVENT_MESSAGE_APPEND
    assert events[0]["payload"]["message"]["content"] == "hello from event log test"


def test_logmanager_undo_writes_undo_event(logdir: Path):
    """LogManager.undo writes an undo event."""
    with LogManager(logdir=logdir) as lm:
        lm.append(Message("user", "hello"))
        lm.append(Message("assistant", "world"))
        lm.undo()

    events = read_events(logdir)
    types = [e["type"] for e in events]
    assert EVENT_UNDO in types


def test_logmanager_edit_writes_edit_event(logdir: Path):
    """LogManager.edit writes a message_edit event."""
    with LogManager(logdir=logdir) as lm:
        lm.append(Message("user", "hello"))
        new_log = Log([Message("user", "edited")])
        lm.edit(new_log)

    events = read_events(logdir)
    types = [e["type"] for e in events]
    assert EVENT_MESSAGE_EDIT in types


def test_integration_recovery(logdir: Path):
    """Can recover from event log after LogManager operations."""
    with LogManager(logdir=logdir) as lm:
        lm.append(Message("user", "hello"))
        lm.append(Message("assistant", "world"))

    # Delete the primary JSONL to simulate corruption
    jsonl_path = logdir / "conversation.jsonl"
    assert jsonl_path.exists()
    jsonl_path.unlink()

    # Recover from event log
    recovered = recover_messages(logdir)
    assert recovered is not None
    assert len(recovered) == 2
    assert recovered[0]["content"] == "hello"
    assert recovered[1]["content"] == "world"


def test_integration_checkpoint_logmanager(logdir: Path):
    """LogManager with many appends produces checkpoints."""
    with LogManager(logdir=logdir) as lm:
        for i in range(CHECKPOINT_INTERVAL + 5):
            lm.append(Message("user", f"msg{i}"))

    events = read_events(logdir)
    checkpoints = [e for e in events if e["type"] == EVENT_CHECKPOINT]
    assert len(checkpoints) >= 1, (
        f"Expected at least 1 checkpoint, got {len(checkpoints)}"
    )
