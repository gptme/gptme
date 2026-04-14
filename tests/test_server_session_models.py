"""Tests for gptme/server/session_models.py.

Covers ToolStatus, ToolExecution, ConversationSession, and SessionManager.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import TYPE_CHECKING

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

if TYPE_CHECKING:
    from gptme.server.api_v2_common import ErrorEvent

from gptme.server.session_models import (
    ConversationSession,
    SessionManager,
    ToolExecution,
    ToolStatus,
)
from gptme.tools.base import ToolUse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_session_manager(monkeypatch):
    """Reset SessionManager class-level state before each test.

    Also stubs out trigger_hook to prevent remove_session from calling
    LogManager.load (which would fail for fake conversation IDs).
    """
    monkeypatch.setattr("gptme.server.session_models.trigger_hook", lambda *a, **kw: [])
    SessionManager._sessions = {}
    SessionManager._conversation_sessions = defaultdict(set)
    yield
    # Cleanup after test as well
    SessionManager._sessions = {}
    SessionManager._conversation_sessions = defaultdict(set)


def make_tooluse(tool: str = "shell", content: str = "echo hi") -> ToolUse:
    """Helper to build a minimal ToolUse."""
    return ToolUse(tool=tool, args=None, content=content)


# ---------------------------------------------------------------------------
# ToolStatus
# ---------------------------------------------------------------------------


class TestToolStatus:
    """Tests for ToolStatus enum."""

    def test_pending_value(self):
        assert ToolStatus.PENDING.value == "pending"

    def test_executing_value(self):
        assert ToolStatus.EXECUTING.value == "executing"

    def test_completed_value(self):
        assert ToolStatus.COMPLETED.value == "completed"

    def test_skipped_value(self):
        assert ToolStatus.SKIPPED.value == "skipped"

    def test_failed_value(self):
        assert ToolStatus.FAILED.value == "failed"

    def test_all_statuses_present(self):
        values = {s.value for s in ToolStatus}
        assert values == {"pending", "executing", "completed", "skipped", "failed"}


# ---------------------------------------------------------------------------
# ToolExecution
# ---------------------------------------------------------------------------


class TestToolExecution:
    """Tests for ToolExecution dataclass."""

    def test_creation(self):
        """ToolExecution can be created with required fields."""
        tu = make_tooluse()
        execution = ToolExecution(tool_id="exec-1", tooluse=tu)
        assert execution.tool_id == "exec-1"
        assert execution.tooluse is tu

    def test_default_status_is_pending(self):
        """Default status is PENDING."""
        execution = ToolExecution(tool_id="t1", tooluse=make_tooluse())
        assert execution.status == ToolStatus.PENDING

    def test_default_auto_confirm_is_false(self):
        """Default auto_confirm is False."""
        execution = ToolExecution(tool_id="t1", tooluse=make_tooluse())
        assert execution.auto_confirm is False

    def test_custom_status(self):
        """Status can be set on creation."""
        execution = ToolExecution(
            tool_id="t1", tooluse=make_tooluse(), status=ToolStatus.EXECUTING
        )
        assert execution.status == ToolStatus.EXECUTING

    def test_auto_confirm_true(self):
        """auto_confirm can be set to True."""
        execution = ToolExecution(
            tool_id="t1", tooluse=make_tooluse(), auto_confirm=True
        )
        assert execution.auto_confirm is True

    def test_tool_id_stored(self):
        """tool_id is stored correctly."""
        execution = ToolExecution(tool_id="unique-id-42", tooluse=make_tooluse())
        assert execution.tool_id == "unique-id-42"

    def test_tooluse_accessible(self):
        """ToolUse is accessible from execution."""
        tu = ToolUse(tool="python", args=None, content="print('hi')")
        execution = ToolExecution(tool_id="t1", tooluse=tu)
        assert execution.tooluse.tool == "python"
        assert execution.tooluse.content == "print('hi')"


# ---------------------------------------------------------------------------
# ConversationSession
# ---------------------------------------------------------------------------


class TestConversationSession:
    """Tests for ConversationSession dataclass."""

    def test_create_via_session_manager(self):
        """ConversationSession is created via SessionManager."""
        session = SessionManager.create_session("conv-1")
        assert session.conversation_id == "conv-1"
        assert session.id is not None

    def test_default_generating_false(self):
        """generating defaults to False."""
        session = SessionManager.create_session("conv-1")
        assert session.generating is False

    def test_default_generating_since_none(self):
        """generating_since defaults to None."""
        session = SessionManager.create_session("conv-1")
        assert session.generating_since is None

    def test_default_events_empty(self):
        """events list defaults to empty."""
        session = SessionManager.create_session("conv-1")
        assert session.events == []

    def test_default_pending_tools_empty(self):
        """pending_tools defaults to empty dict."""
        session = SessionManager.create_session("conv-1")
        assert session.pending_tools == {}

    def test_default_auto_confirm_count_zero(self):
        """auto_confirm_count defaults to 0."""
        session = SessionManager.create_session("conv-1")
        assert session.auto_confirm_count == 0

    def test_default_clients_empty(self):
        """clients set defaults to empty."""
        session = SessionManager.create_session("conv-1")
        assert session.clients == set()

    def test_event_flag_is_threading_event(self):
        """event_flag is a threading.Event."""
        session = SessionManager.create_session("conv-1")
        assert isinstance(session.event_flag, threading.Event)

    def test_default_use_acp_false(self):
        """use_acp defaults to False."""
        session = SessionManager.create_session("conv-1")
        assert session.use_acp is False

    def test_default_acp_runtime_none(self):
        """acp_runtime defaults to None."""
        session = SessionManager.create_session("conv-1")
        assert session.acp_runtime is None

    def test_default_acp_last_user_msg_index(self):
        """acp_last_user_msg_index defaults to -1."""
        session = SessionManager.create_session("conv-1")
        assert session.acp_last_user_msg_index == -1

    def test_active_defaults_true(self):
        """Inherited active field defaults to True."""
        session = SessionManager.create_session("conv-1")
        assert session.active is True

    def test_touch_updates_last_activity(self):
        """touch() updates last_activity timestamp."""
        session = SessionManager.create_session("conv-1")
        before = session.last_activity
        import time

        time.sleep(0.01)
        session.touch()
        assert session.last_activity > before

    def test_events_are_independent_per_session(self):
        """Two sessions do not share the same events list."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-2")
        s1.events.append({"type": "ping"})  # type: ignore[arg-type]
        assert s2.events == []

    def test_events_count_matches_len_initially(self):
        """events_count equals len(events) when no trimming has occurred."""
        session = SessionManager.create_session("conv-1")
        for _i in range(5):
            session.events.append({"type": "ping"})  # type: ignore[arg-type]
        assert session.events_count == 5
        assert session.events_count == len(session.events)

    def test_events_offset_starts_at_zero(self):
        """_events_offset starts at 0."""
        session = SessionManager.create_session("conv-1")
        assert session._events_offset == 0

    def test_get_events_since_returns_all_from_zero(self):
        """get_events_since(0) returns all events."""
        session = SessionManager.create_session("conv-1")
        for i in range(3):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        result = session.get_events_since(0)
        assert len(result) == 3

    def test_get_events_since_returns_subset(self):
        """get_events_since returns events from the given index onward."""
        session = SessionManager.create_session("conv-1")
        for i in range(5):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        result = session.get_events_since(3)
        assert len(result) == 2
        assert result[0]["n"] == 3  # type: ignore[typeddict-item]
        assert result[1]["n"] == 4  # type: ignore[typeddict-item]

    def test_trim_events_no_clients(self):
        """trim_events trims when over threshold and no clients connected."""
        session = SessionManager.create_session("conv-1")
        # Fill beyond _MAX_EVENTS
        for i in range(session._MAX_EVENTS + 500):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        assert len(session.events) == session._MAX_EVENTS + 500

        session.trim_events()
        assert len(session.events) == session._KEEP_EVENTS
        assert (
            session._events_offset == session._MAX_EVENTS + 500 - session._KEEP_EVENTS
        )
        assert session.events_count == session._MAX_EVENTS + 500

    def test_trim_events_preserves_absolute_indexing(self):
        """After trimming, get_events_since still works with absolute indices."""
        session = SessionManager.create_session("conv-1")
        total = session._MAX_EVENTS + 100
        for i in range(total):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        session.trim_events()

        # The last event should still be retrievable
        result = session.get_events_since(total - 1)
        assert len(result) == 1
        assert result[0]["n"] == total - 1  # type: ignore[typeddict-item]

    def test_trim_events_skipped_when_clients_connected(self):
        """trim_events does not trim when clients are connected."""
        session = SessionManager.create_session("conv-1")
        session.clients.add("client-1")
        for i in range(session._MAX_EVENTS + 500):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]

        session.trim_events()
        # No trimming because a client is connected
        assert len(session.events) == session._MAX_EVENTS + 500
        assert session._events_offset == 0

    def test_trim_events_skipped_below_threshold(self):
        """trim_events does nothing when below _MAX_EVENTS."""
        session = SessionManager.create_session("conv-1")
        for i in range(100):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        session.trim_events()
        assert len(session.events) == 100
        assert session._events_offset == 0

    def test_get_events_since_clamps_negative_relative_index(self):
        """get_events_since with index before offset returns all available events."""
        session = SessionManager.create_session("conv-1")
        for i in range(session._MAX_EVENTS + 100):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        session.trim_events()
        # Requesting from index 0, which was trimmed away
        result = session.get_events_since(0)
        assert len(result) == session._KEEP_EVENTS


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class TestSessionManagerCreate:
    """Tests for SessionManager.create_session()."""

    def test_creates_session(self):
        """create_session returns a ConversationSession."""
        session = SessionManager.create_session("conv-1")
        assert isinstance(session, ConversationSession)

    def test_session_id_is_uuid(self):
        """Session ID is a UUID-like string."""
        import uuid

        session = SessionManager.create_session("conv-1")
        # Should be parseable as UUID
        uuid.UUID(session.id)

    def test_session_stored_in_manager(self):
        """Created session is retrievable via get_session."""
        session = SessionManager.create_session("conv-1")
        retrieved = SessionManager.get_session(session.id)
        assert retrieved is session

    def test_session_associated_with_conversation(self):
        """Session appears in get_sessions_for_conversation."""
        session = SessionManager.create_session("conv-1")
        sessions = SessionManager.get_sessions_for_conversation("conv-1")
        assert session in sessions

    def test_multiple_sessions_same_conversation(self):
        """Multiple sessions can be created for same conversation."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-1")
        sessions = SessionManager.get_sessions_for_conversation("conv-1")
        assert s1 in sessions
        assert s2 in sessions
        assert len(sessions) == 2

    def test_unique_ids_per_session(self):
        """Each call produces a unique session ID."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-1")
        assert s1.id != s2.id


class TestSessionManagerGet:
    """Tests for SessionManager.get_session()."""

    def test_returns_none_for_unknown_id(self):
        """get_session returns None for unknown ID."""
        assert SessionManager.get_session("nonexistent") is None

    def test_returns_session_for_known_id(self):
        """get_session returns session for known ID."""
        session = SessionManager.create_session("conv-1")
        assert SessionManager.get_session(session.id) is session

    def test_returns_correct_session_when_multiple(self):
        """get_session returns the correct session among multiple."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-2")
        assert SessionManager.get_session(s1.id) is s1
        assert SessionManager.get_session(s2.id) is s2


class TestSessionManagerGetForConversation:
    """Tests for SessionManager.get_sessions_for_conversation()."""

    def test_returns_empty_for_unknown_conversation(self):
        """Returns empty list for unknown conversation."""
        result = SessionManager.get_sessions_for_conversation("unknown")
        assert result == []

    def test_returns_single_session(self):
        """Returns the one session for a conversation."""
        session = SessionManager.create_session("conv-1")
        result = SessionManager.get_sessions_for_conversation("conv-1")
        assert result == [session]

    def test_does_not_include_other_conversations(self):
        """Does not return sessions from other conversations."""
        SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-2")
        result = SessionManager.get_sessions_for_conversation("conv-2")
        assert result == [s2]


class TestSessionManagerRemove:
    """Tests for SessionManager.remove_session()."""

    def test_removes_from_sessions(self):
        """remove_session removes session from internal dict."""
        session = SessionManager.create_session("conv-1")
        SessionManager.remove_session(session.id)
        assert SessionManager.get_session(session.id) is None

    def test_removes_from_conversation_mapping(self):
        """remove_session cleans up conversation mapping."""
        session = SessionManager.create_session("conv-1")
        SessionManager.remove_session(session.id)
        assert SessionManager.get_sessions_for_conversation("conv-1") == []

    def test_remove_one_of_two_sessions(self):
        """Removing one session leaves the other."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-1")
        SessionManager.remove_session(s1.id)
        sessions = SessionManager.get_sessions_for_conversation("conv-1")
        assert s2 in sessions
        assert s1 not in sessions

    def test_remove_nonexistent_session_is_noop(self):
        """Removing non-existent session ID does nothing."""
        # Should not raise
        SessionManager.remove_session("nonexistent-id")

    def test_conversation_mapping_cleaned_when_last_session_removed(self):
        """Conversation entry is removed when the last session is removed."""
        session = SessionManager.create_session("conv-1")
        SessionManager.remove_session(session.id)
        # The conversation key should be cleaned up
        assert "conv-1" not in SessionManager._conversation_sessions


class TestSessionManagerRemoveAll:
    """Tests for SessionManager.remove_all_sessions_for_conversation()."""

    def test_removes_all_sessions(self):
        """All sessions for a conversation are removed."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-1")
        SessionManager.remove_all_sessions_for_conversation("conv-1")
        assert SessionManager.get_session(s1.id) is None
        assert SessionManager.get_session(s2.id) is None

    def test_does_not_affect_other_conversations(self):
        """Removing all sessions for conv-1 leaves conv-2 intact."""
        SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-2")
        SessionManager.remove_all_sessions_for_conversation("conv-1")
        sessions = SessionManager.get_sessions_for_conversation("conv-2")
        assert s2 in sessions

    def test_noop_for_unknown_conversation(self):
        """remove_all_sessions_for_conversation with unknown ID is a no-op."""
        # Should not raise
        SessionManager.remove_all_sessions_for_conversation("unknown-conv")


class TestSessionManagerAddEvent:
    """Tests for SessionManager.add_event()."""

    def test_event_added_to_session(self):
        """add_event appends event to all sessions for the conversation."""
        session = SessionManager.create_session("conv-1")
        event: ErrorEvent = {"type": "error", "error": "something went wrong"}
        SessionManager.add_event("conv-1", event)
        assert len(session.events) == 1
        assert session.events[0] == event

    def test_event_flag_set_after_add(self):
        """add_event sets the event_flag for the session."""
        session = SessionManager.create_session("conv-1")
        assert not session.event_flag.is_set()
        event: ErrorEvent = {"type": "error", "error": "test"}
        SessionManager.add_event("conv-1", event)
        assert session.event_flag.is_set()

    def test_event_added_to_all_sessions_for_conversation(self):
        """add_event sends to ALL sessions for the conversation."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-1")
        event: ErrorEvent = {"type": "error", "error": "broadcast"}
        SessionManager.add_event("conv-1", event)
        assert len(s1.events) == 1
        assert len(s2.events) == 1

    def test_event_not_added_to_other_conversation(self):
        """add_event does not affect sessions from other conversations."""
        SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-2")
        event: ErrorEvent = {"type": "error", "error": "isolated"}
        SessionManager.add_event("conv-1", event)
        assert s2.events == []

    def test_noop_for_unknown_conversation(self):
        """add_event with unknown conversation is a no-op."""
        # Should not raise
        event: ErrorEvent = {"type": "error", "error": "nowhere"}
        SessionManager.add_event("unknown-conv", event)

    def test_add_event_trims_when_over_threshold(self):
        """add_event triggers trimming when events exceed threshold."""
        session = SessionManager.create_session("conv-1")
        # Fill to just below threshold
        for i in range(session._MAX_EVENTS):
            session.events.append({"type": "ping", "n": i})  # type: ignore[arg-type]
        assert len(session.events) == session._MAX_EVENTS

        # One more event via add_event pushes over threshold and triggers trim
        event: ErrorEvent = {"type": "error", "error": "trigger trim"}
        SessionManager.add_event("conv-1", event)
        assert len(session.events) == session._KEEP_EVENTS
        assert session.events_count == session._MAX_EVENTS + 1


class TestSessionManagerCleanInactive:
    """Tests for SessionManager.clean_inactive_sessions()."""

    def test_does_not_remove_active_sessions(self):
        """Recently-active sessions are not cleaned up."""
        session = SessionManager.create_session("conv-1")
        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        assert SessionManager.get_session(session.id) is not None

    def test_removes_stale_sessions(self):
        """Sessions older than max_age_minutes are cleaned up."""
        from datetime import datetime, timedelta, timezone

        session = SessionManager.create_session("conv-1")
        # Make it look old
        session.last_activity = datetime.now(tz=timezone.utc) - timedelta(minutes=120)
        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        assert SessionManager.get_session(session.id) is None

    def test_does_not_remove_generating_sessions(self):
        """Sessions that are currently generating are not cleaned up, even if old."""
        from datetime import datetime, timedelta, timezone

        session = SessionManager.create_session("conv-1")
        session.last_activity = datetime.now(tz=timezone.utc) - timedelta(minutes=120)
        session.generating = True
        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        # Still present because generating=True
        assert SessionManager.get_session(session.id) is not None

    def test_selective_cleanup(self):
        """Only old, non-generating sessions are removed; recent ones survive."""
        from datetime import datetime, timedelta, timezone

        s_old = SessionManager.create_session("conv-old")
        s_old.last_activity = datetime.now(tz=timezone.utc) - timedelta(minutes=200)

        s_recent = SessionManager.create_session("conv-recent")
        # s_recent has a fresh last_activity by default

        SessionManager.clean_inactive_sessions(max_age_minutes=60)

        assert SessionManager.get_session(s_old.id) is None
        assert SessionManager.get_session(s_recent.id) is not None

    def test_removes_stuck_generating_sessions(self):
        """Sessions stuck in generating=True beyond the timeout are force-cleaned."""
        from datetime import datetime, timedelta, timezone

        session = SessionManager.create_session("conv-stuck")
        session.generating = True
        # Simulate stuck for 15 minutes (exceeds 10-minute timeout)
        session.generating_since = datetime.now(tz=timezone.utc) - timedelta(minutes=15)
        # Keep last_activity recent so the normal inactive cleanup wouldn't catch it
        session.last_activity = datetime.now(tz=timezone.utc)

        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        assert SessionManager.get_session(session.id) is None

    def test_does_not_remove_recently_generating_sessions(self):
        """Sessions that started generating recently are not removed."""
        from datetime import datetime, timezone

        session = SessionManager.create_session("conv-gen")
        session.generating = True
        session.generating_since = datetime.now(tz=timezone.utc)  # just started
        session.last_activity = datetime.now(tz=timezone.utc)

        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        # Still present because generating started recently
        assert SessionManager.get_session(session.id) is not None

    def test_does_not_remove_generating_without_timestamp(self):
        """Sessions with generating=True but no generating_since are not force-cleaned.

        This handles the edge case of sessions created before this change was deployed.
        """
        from datetime import datetime, timedelta, timezone

        session = SessionManager.create_session("conv-legacy")
        session.generating = True
        session.generating_since = None  # no timestamp (legacy behavior)
        session.last_activity = datetime.now(tz=timezone.utc) - timedelta(minutes=5)

        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        # Still present — can't determine if stuck without timestamp
        assert SessionManager.get_session(session.id) is not None

    def test_stuck_session_generating_flag_reset(self):
        """Force-cleaned stuck sessions have their generating flag reset."""
        from datetime import datetime, timedelta, timezone

        session = SessionManager.create_session("conv-stuck-flag")
        session.generating = True
        session.generating_since = datetime.now(tz=timezone.utc) - timedelta(minutes=15)
        session.last_activity = datetime.now(tz=timezone.utc)

        # Capture session reference before it's removed
        assert session.generating is True
        SessionManager.clean_inactive_sessions(max_age_minutes=60)
        # Session is removed from the manager
        assert SessionManager.get_session(session.id) is None
        # generating flag is reset to False before removal (the key invariant this test verifies)
        assert session.generating is False


class TestSessionManagerGetAllSessions:
    """Tests for SessionManager.get_all_sessions()."""

    def test_returns_empty_when_no_sessions(self):
        """get_all_sessions returns empty list when no sessions exist."""
        assert SessionManager.get_all_sessions() == []

    def test_returns_snapshot(self):
        """get_all_sessions returns a snapshot of all sessions."""
        s1 = SessionManager.create_session("conv-1")
        s2 = SessionManager.create_session("conv-2")
        result = SessionManager.get_all_sessions()
        ids = {sid for sid, _ in result}
        assert s1.id in ids
        assert s2.id in ids
        assert len(result) == 2


class TestSessionManagerThreadSafety:
    """Tests for SessionManager thread safety."""

    def test_concurrent_create_and_remove(self):
        """Concurrent creates and removes don't raise or corrupt state."""
        import time

        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def creator():
            barrier.wait()
            for i in range(50):
                try:
                    SessionManager.create_session(f"concurrent-{i}")
                except Exception as e:
                    errors.append(e)

        def remover():
            barrier.wait()
            for _ in range(50):
                try:
                    for sid, _ in SessionManager.get_all_sessions():
                        SessionManager.remove_session(sid)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        threads = [
            threading.Thread(target=creator),
            threading.Thread(target=creator),
            threading.Thread(target=remover),
            threading.Thread(target=remover),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

    def test_concurrent_add_event_and_remove(self):
        """Concurrent add_event and remove don't raise."""
        errors: list[Exception] = []
        barrier = threading.Barrier(3)

        for _i in range(10):
            SessionManager.create_session("event-conv")

        def event_adder():
            barrier.wait()
            for i in range(100):
                try:
                    SessionManager.add_event(
                        "event-conv",
                        {"type": "error", "error": f"e{i}"},  # type: ignore[arg-type]
                    )
                except Exception as e:
                    errors.append(e)

        def session_remover():
            barrier.wait()
            for sid, _ in SessionManager.get_all_sessions():
                try:
                    SessionManager.remove_session(sid)
                except Exception as e:
                    errors.append(e)

        threads = [
            threading.Thread(target=event_adder),
            threading.Thread(target=event_adder),
            threading.Thread(target=session_remover),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
