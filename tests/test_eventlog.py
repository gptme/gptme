"""Tests for gptme/logmanager/eventlog.py"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from gptme.logmanager import Log, LogManager
from gptme.logmanager.eventlog import (
    CHECKPOINT_INTERVAL,
    EVENT_CHECKPOINT,
    EVENT_LOG_NAME,
    EVENT_MESSAGE_APPEND,
    EVENT_MESSAGE_EDIT,
    EVENT_UNDO,
    _event_log_lock,
    _event_log_path,
    append_event,
    append_next_event,
    compact_events,
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


def test_event_log_lock_uses_windows_fallback(logdir: Path):
    """Event persistence remains available when fcntl is unavailable."""
    calls: list[tuple[int, int]] = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, count: int) -> None:
            assert count == 1
            calls.append((mode, count))

    class UnlockedThreadLock:
        def __enter__(self) -> None:
            pass

        def __exit__(self, *args: object) -> None:
            pass

    with (
        patch("gptme.logmanager.eventlog.fcntl", None),
        patch("gptme.logmanager.eventlog.msvcrt", FakeMsvcrt),
        patch(
            "gptme.logmanager.eventlog._get_event_log_thread_lock",
            return_value=UnlockedThreadLock(),
        ),
    ):
        append_event(
            logdir,
            {"seq": 1, "ts": "", "type": "test", "payload": {}},
        )
        append_event(
            logdir,
            {"seq": 2, "ts": "", "type": "test", "payload": {}},
        )

    assert calls == [
        (FakeMsvcrt.LK_LOCK, 1),
        (FakeMsvcrt.LK_UNLCK, 1),
        (FakeMsvcrt.LK_LOCK, 1),
        (FakeMsvcrt.LK_UNLCK, 1),
    ]
    assert (logdir / f".{EVENT_LOG_NAME}.lock").read_bytes() == b"\0"
    assert [event["seq"] for event in read_events(logdir)] == [1, 2]


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


def test_different_event_logs_do_not_share_an_io_lock(logdir: Path):
    """Holding one conversation lock must not block another conversation."""
    other_logdir = logdir.parent / "other-conversation"
    event = {"seq": 1, "ts": "", "type": "test", "payload": {}}

    with (
        _event_log_lock(logdir),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        executor.submit(append_event, other_logdir, event).result(timeout=1)

    assert read_events(other_logdir) == [event]


def test_append_next_event_assigns_unique_sequences_concurrently(logdir: Path):
    """Sequence assignment and append are one serialized operation."""

    def build(seq: int) -> dict[str, object]:
        return {"seq": seq, "ts": "", "type": "test", "payload": {}}

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(
            executor.map(lambda _: append_next_event(logdir, build), range(40))
        )

    assert sorted(event["seq"] for event in events) == list(range(1, 41))
    assert sorted(event["seq"] for event in read_events(logdir)) == list(range(1, 41))


def test_checkpoint_and_append_assign_unique_sequences_concurrently(logdir: Path):
    """A checkpoint cannot race another append onto the same sequence."""
    message = {"role": "user", "content": "tail"}

    def append_message() -> dict[str, object]:
        return append_next_event(
            logdir,
            lambda seq: {
                "seq": seq,
                "ts": "",
                "type": EVENT_MESSAGE_APPEND,
                "payload": {"message": message},
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        checkpoint = executor.submit(write_checkpoint, logdir, [message])
        append = executor.submit(append_message)

    events = [checkpoint.result(), append.result()]
    assert sorted(event["seq"] for event in events) == [1, 2]
    persisted = read_events(logdir)
    assert len({event["seq"] for event in persisted}) == len(persisted)
    assert recover_messages(logdir) in ([message], [message, message])


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
    write_checkpoint(logdir, messages)

    # Read events, find checkpoint
    events = read_events(logdir)
    assert len(events) == 1
    assert events[0]["type"] == EVENT_CHECKPOINT
    assert events[0]["seq"] == 1
    assert len(events[0]["payload"]["messages"]) == 2

    # find_latest_checkpoint
    cp = find_latest_checkpoint(events)
    assert cp is not None
    assert cp["seq"] == 1


def test_find_latest_among_multiple(logdir: Path):
    """find_latest_checkpoint returns the most recent checkpoint."""
    write_checkpoint(logdir, [])
    write_checkpoint(logdir, [{"role": "user", "content": "last"}])

    cp = find_latest_checkpoint(read_events(logdir))
    assert cp is not None
    assert cp["seq"] == 2
    assert cp["payload"]["messages"][0]["content"] == "last"


def test_compact_events_keeps_latest_checkpoint_and_tail(logdir: Path):
    """Compaction drops history already represented by the latest checkpoint."""
    append_event(
        logdir,
        {
            "seq": 49,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "old"}},
        },
    )
    write_checkpoint(logdir, [{"role": "user", "content": "snapshot"}])
    append_event(
        logdir,
        {
            "seq": 51,
            "ts": "2026-01-01T00:00:01Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "assistant", "content": "tail"}},
        },
    )

    compact_events(logdir)

    events = read_events(logdir)
    assert [event["seq"] for event in events] == [50, 51]
    recovered = recover_messages(logdir)
    assert recovered is not None
    assert [message["content"] for message in recovered] == ["snapshot", "tail"]


def test_compact_events_without_checkpoint_is_noop(logdir: Path):
    """Compaction preserves append-only history until a checkpoint exists."""
    append_event(
        logdir,
        {
            "seq": 1,
            "ts": "2026-01-01T00:00:00Z",
            "type": EVENT_MESSAGE_APPEND,
            "payload": {"message": {"role": "user", "content": "hello"}},
        },
    )
    before = (logdir / EVENT_LOG_NAME).read_bytes()

    compact_events(logdir)

    assert (logdir / EVENT_LOG_NAME).read_bytes() == before


def test_compaction_does_not_lose_concurrent_append(logdir: Path):
    """The compaction replacement is serialized with append_event."""
    write_checkpoint(logdir, [{"role": "user", "content": "snapshot"}])
    tail = {
        "seq": 2,
        "ts": "2026-01-01T00:00:01Z",
        "type": EVENT_MESSAGE_APPEND,
        "payload": {"message": {"role": "assistant", "content": "tail"}},
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(compact_events, logdir),
            executor.submit(append_event, logdir, tail),
        ]
        for future in futures:
            future.result()

    assert [event["seq"] for event in read_events(logdir)] == [1, 2]


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
        [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ],
    )

    # Append events after the checkpoint
    append_event(
        logdir,
        {
            "seq": 2,
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


def test_logmanager_keeps_non_main_branch_event_histories_separate(
    logdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Events from distinct branches remain independently recoverable."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(logdir.parent))
    with LogManager(logdir=logdir, branch="alpha", lock=False) as lm:
        assert lm.current_branch == "alpha"
        assert lm.logdir == logdir
        lm.append(Message("user", "alpha message"))
        assert (logdir / "branches" / "alpha" / EVENT_LOG_NAME).exists()
        lm.branch("beta")
        lm.append(Message("user", "beta message"))

    alpha = recover_messages(logdir / "branches" / "alpha")
    beta = recover_messages(logdir / "branches" / "beta")
    assert alpha is not None
    assert beta is not None
    assert [message["content"] for message in alpha] == ["alpha message"]
    assert [message["content"] for message in beta] == ["beta message"]


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
    """LogManager checkpoints compact superseded append events."""
    with LogManager(logdir=logdir) as lm:
        for i in range(CHECKPOINT_INTERVAL + 5):
            lm.append(Message("user", f"msg{i}"))

    events = read_events(logdir)
    checkpoints = [e for e in events if e["type"] == EVENT_CHECKPOINT]
    assert len(checkpoints) == 1
    assert events[0]["type"] == EVENT_CHECKPOINT
    assert len(events) == 1 + 5

    recovered = recover_messages(logdir)
    assert recovered is not None
    assert [message["content"] for message in recovered] == [
        f"msg{i}" for i in range(CHECKPOINT_INTERVAL + 5)
    ]


def test_compact_events_write_failure_does_not_close_reused_fd(logdir: Path):
    """A failed compaction must not close a descriptor it no longer owns.

    ``os.fdopen`` takes ownership of the ``mkstemp`` descriptor, so the file
    object closes it on error.  Closing the raw number again would hit whatever
    file a concurrent thread opened in the meantime.
    """
    write_checkpoint(logdir, [{"role": "user", "content": "snapshot"}])

    closed: list[int] = []
    real_close = os.close
    real_fdopen = os.fdopen
    opened_fd: list[int] = []

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def tracking_fdopen(fd: int, *args, **kwargs):
        opened_fd.append(fd)
        return real_fdopen(fd, *args, **kwargs)

    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    with (
        patch("gptme.logmanager.eventlog.os.close", tracking_close),
        patch("gptme.logmanager.eventlog.os.fdopen", tracking_fdopen),
        patch("gptme.logmanager.eventlog.os.fsync", boom),
        pytest.raises(OSError, match="no space left on device"),
    ):
        compact_events(logdir)

    assert opened_fd, "fdopen was never reached"
    assert opened_fd[0] not in closed, (
        "raw descriptor closed after fdopen took ownership — "
        "a concurrently opened file could be reusing that number"
    )
    # The temp file is still cleaned up and the log is left intact.
    assert not list(logdir.glob(f".{EVENT_LOG_NAME}.*.tmp"))
    assert [event["seq"] for event in read_events(logdir)] == [1]
