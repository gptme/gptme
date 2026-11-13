"""Tests for the time-awareness tool."""

from datetime import datetime, timedelta, timezone

import pytest

from gptme.hooks import HookType, clear_hooks, get_hooks, trigger_hook
from gptme.hooks.types import ToolExecutePostData
from gptme.logmanager import Log
from gptme.message import Message


@pytest.fixture(autouse=True)
def clear_all_hooks():
    """Clear all hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture
def load_time_awareness_tool():
    """Load the time-awareness hook."""
    from gptme.hooks.time_awareness import register

    register()


def test_time_awareness_hook_exists():
    """Test that the time-awareness hook can be imported."""
    from gptme.hooks import time_awareness

    assert time_awareness.register is not None


def test_time_awareness_hooks_registered(load_time_awareness_tool):
    """Test that time-awareness hooks are registered."""
    tool_post_hooks = get_hooks(HookType.TOOL_EXECUTE_POST)

    # Should have at least one TOOL_EXECUTE_POST (tool.execute.post) hook (time_message)
    assert len(tool_post_hooks) >= 1
    assert any("time_awareness.time_message" in h.name for h in tool_post_hooks)


def test_time_milestones(load_time_awareness_tool, tmp_path, monkeypatch):
    """Test that time messages appear at correct milestones."""
    from gptme.hooks.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _last_notice_var,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    # Set up test conversation
    workspace = tmp_path
    log = Log()

    # Mock conversation start time to 30 minutes ago
    now = datetime.now(tz=timezone.utc)
    start_time = now - timedelta(minutes=30)
    conversation_start_times = _conversation_start_times_var.get()
    shown_milestones = _shown_milestones_var.get()
    assert conversation_start_times is not None
    assert shown_milestones is not None
    conversation_start_times[str(workspace)] = start_time
    _conversation_start_times_var.set(conversation_start_times)
    shown_milestones[str(workspace)] = set()
    _shown_milestones_var.set(shown_milestones)

    # Trigger hook at different time points
    test_cases = [
        (1, "1min"),
        (5, "5min"),
        (10, "10min"),
        (15, "15min"),
        (20, "20min"),
        (30, "30min"),
    ]

    for elapsed_minutes, expected_time in test_cases:
        # Clear previous milestones to test each individually
        shown_milestones = _shown_milestones_var.get()
        assert shown_milestones is not None
        shown_milestones[str(workspace)] = set()
        _last_notice_var.set({})  # also forget the previous notice
        _shown_milestones_var.set(shown_milestones)

        # Set conversation start time
        conversation_start_times = _conversation_start_times_var.get()
        assert conversation_start_times is not None
        conversation_start_times[str(workspace)] = now - timedelta(
            minutes=elapsed_minutes
        )
        _conversation_start_times_var.set(conversation_start_times)

        # Trigger TOOL_EXECUTE_POST (tool.execute.post) hook
        messages = list(
            trigger_hook(
                HookType.TOOL_EXECUTE_POST,
                ToolExecutePostData(log=log, workspace=workspace, tool_use=None),
            )
        )

        # Should have exactly one message
        assert len(messages) == 1
        message = messages[0]

        # Check message properties
        assert message.role == "system"
        # Note: hide property may be disabled for testing purposes
        assert f"Time elapsed: {expected_time}" in message.content


def test_milestone_progression(load_time_awareness_tool, tmp_path):
    """Test that milestones are shown in sequence without repetition."""
    from gptme.hooks.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    workspace = tmp_path
    log = Log()
    now = datetime.now(tz=timezone.utc)

    # Set conversation start 25 minutes ago
    conversation_start_times = _conversation_start_times_var.get()
    shown_milestones = _shown_milestones_var.get()
    assert conversation_start_times is not None
    assert shown_milestones is not None
    conversation_start_times[str(workspace)] = now - timedelta(minutes=25)
    _conversation_start_times_var.set(conversation_start_times)
    shown_milestones[str(workspace)] = set()
    _shown_milestones_var.set(shown_milestones)

    # Trigger multiple times - should only show milestone once
    for i in range(3):
        messages = list(
            trigger_hook(
                HookType.TOOL_EXECUTE_POST,
                ToolExecutePostData(log=log, workspace=workspace, tool_use=None),
            )
        )

        # First trigger: should show 20min milestone
        # Subsequent triggers: no new messages (milestone already shown)
        if i == 0:
            assert len(messages) == 1
        else:
            assert len(messages) == 0


def test_no_workspace_graceful_handling(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Test that missing workspace is handled gracefully."""
    log = Log()

    # Trigger hook without workspace
    messages = list(
        trigger_hook(
            HookType.TOOL_EXECUTE_POST,
            ToolExecutePostData(log=log, workspace=None, tool_use=None),
        )
    )

    # Should not crash, should not produce messages
    assert len(messages) == 0


def test_time_format_hours(load_time_awareness_tool, tmp_path):
    """Test time formatting includes hours for long conversations."""
    from gptme.hooks.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    workspace = tmp_path
    log = Log()
    now = datetime.now(tz=timezone.utc)

    # Set conversation start 125 minutes ago (2h 5min)
    conversation_start_times = _conversation_start_times_var.get()
    shown_milestones = _shown_milestones_var.get()
    assert conversation_start_times is not None
    assert shown_milestones is not None
    conversation_start_times[str(workspace)] = now - timedelta(minutes=125)
    _conversation_start_times_var.set(conversation_start_times)
    shown_milestones[str(workspace)] = set()
    _shown_milestones_var.set(shown_milestones)

    # Trigger hook
    messages = list(
        trigger_hook(
            HookType.TOOL_EXECUTE_POST,
            ToolExecutePostData(log=log, workspace=workspace, tool_use=None),
        )
    )

    assert len(messages) == 1
    message = messages[0]

    # Should show "2h 5min" format
    assert "Time elapsed: 2h 5min" in message.content


def test_every_10min_after_20(load_time_awareness_tool, tmp_path):
    """Test that messages appear every 10 minutes after 20min mark."""
    from gptme.hooks.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _last_notice_var,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    workspace = tmp_path
    log = Log()
    now = datetime.now(tz=timezone.utc)

    # Test 30, 40, 50 minute marks
    for minutes in [30, 40, 50]:
        shown_milestones = _shown_milestones_var.get()
        assert shown_milestones is not None
        shown_milestones[str(workspace)] = set()
        _last_notice_var.set({})  # also forget the previous notice
        _shown_milestones_var.set(shown_milestones)

        conversation_start_times = _conversation_start_times_var.get()
        assert conversation_start_times is not None
        conversation_start_times[str(workspace)] = now - timedelta(minutes=minutes)
        _conversation_start_times_var.set(conversation_start_times)

        messages = list(
            trigger_hook(
                HookType.TOOL_EXECUTE_POST,
                ToolExecutePostData(log=log, workspace=workspace, tool_use=None),
            )
        )

        assert len(messages) == 1
        assert f"Time elapsed: {minutes}min" in messages[0].content


# --- Deadline awareness (GPTME_SESSION_DEADLINE) ---


@pytest.fixture(autouse=True)
def _clear_deadline_env(monkeypatch):
    """Keep deadline configuration from the host out of every test."""
    for name in (
        "GPTME_SESSION_DEADLINE",
        "GPTME_SESSION_START",
        "GPTME_TIME_NOTICE_FRACTIONS",
    ):
        monkeypatch.delenv(name, raising=False)


_DEADLINE_BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _at_minutes(minutes: float) -> datetime:
    return _DEADLINE_BASE + timedelta(minutes=minutes)


def _set_deadline_env(
    monkeypatch,
    *,
    start: datetime | None = None,
    deadline: datetime | None = None,
    fractions: str | None = None,
) -> None:
    monkeypatch.setenv("GPTME_SESSION_START", (start or _DEADLINE_BASE).isoformat())
    monkeypatch.setenv(
        "GPTME_SESSION_DEADLINE", (deadline or _at_minutes(100)).isoformat()
    )
    if fractions is not None:
        monkeypatch.setenv("GPTME_TIME_NOTICE_FRACTIONS", fractions)


def _trigger_at(monkeypatch, workspace, log, now: datetime):
    from gptme.hooks import time_awareness as ta

    monkeypatch.setattr(ta.clock, "now", lambda: now)
    return list(
        trigger_hook(
            HookType.TOOL_EXECUTE_POST,
            ToolExecutePostData(log=log, workspace=workspace, tool_use=None),
        )
    )


def _deadline_log_notice(
    at: datetime,
    threshold_percent: int,
    *,
    start: datetime | None = None,
    deadline: datetime | None = None,
) -> Message:
    from gptme.hooks import time_awareness as ta

    ident = ta._deadline_id(start or _DEADLINE_BASE, deadline or _at_minutes(100))
    return Message(
        "system",
        f"{ta.NOTICE_PREFIX}{at.isoformat()}. "
        f"Deadline warning: {threshold_percent}% of budget used "
        f"[deadline-id:{ident}].",
        timestamp=at,
        hide=True,
    )


def test_deadline_unset_keeps_elapsed_milestones(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Without deadline env the old elapsed milestones remain unchanged."""
    from gptme.hooks import time_awareness as ta

    ta._ensure_locals()
    now = _at_minutes(60)
    workspace = str(tmp_path)
    starts = ta._conversation_start_times_var.get()
    assert starts is not None
    starts[workspace] = now - timedelta(minutes=60)

    messages = _trigger_at(monkeypatch, tmp_path, Log(), now)

    assert len(messages) == 1
    assert "Time elapsed: 1h" in messages[0].content
    assert "Deadline" not in messages[0].content


@pytest.mark.parametrize(
    ("start", "deadline", "fractions"),
    [
        # naive timestamps are ambiguous and must not enable deadline mode
        ("2026-01-01T12:00:00", "2026-01-01T13:40:00+00:00", None),
        ("2026-01-01T12:00:00+00:00", "2026-01-01T13:40:00", None),
        # unparseable / non-ISO / nonfinite values
        ("not-a-date", "2026-01-01T13:40:00+00:00", None),
        ("2026-01-01T12:00:00+00:00", "inf", None),
        ("2026-01-01T12:00:00+00:00", "2026-01-01T13:40:00+00:00", "nan"),
        ("2026-01-01T12:00:00+00:00", "2026-01-01T13:40:00+00:00", "0.7,abc"),
        ("2026-01-01T12:00:00+00:00", "2026-01-01T13:40:00+00:00", "1.5"),
        # deadline must be after start
        ("2026-01-01T12:00:00+00:00", "2026-01-01T12:00:00+00:00", None),
        ("2026-01-01T12:00:00+00:00", "2026-01-01T11:00:00+00:00", None),
    ],
)
def test_deadline_invalid_config_falls_back(
    load_time_awareness_tool, tmp_path, monkeypatch, start, deadline, fractions
):
    """Invalid or incomplete deadline config must behave like the old hook."""
    from gptme.hooks import time_awareness as ta

    ta._ensure_locals()
    now = _at_minutes(60)
    workspace = str(tmp_path)
    starts = ta._conversation_start_times_var.get()
    assert starts is not None
    starts[workspace] = now - timedelta(minutes=60)
    monkeypatch.setenv("GPTME_SESSION_START", start)
    monkeypatch.setenv("GPTME_SESSION_DEADLINE", deadline)
    if fractions is not None:
        monkeypatch.setenv("GPTME_TIME_NOTICE_FRACTIONS", fractions)

    messages = _trigger_at(monkeypatch, tmp_path, Log(), now)

    assert len(messages) == 1
    assert "Time elapsed: 1h" in messages[0].content
    assert "Deadline warning" not in messages[0].content


def test_deadline_missing_start_falls_back(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """A deadline without a start timestamp must not enable deadline mode."""
    from gptme.hooks import time_awareness as ta

    ta._ensure_locals()
    now = _at_minutes(60)
    workspace = str(tmp_path)
    starts = ta._conversation_start_times_var.get()
    assert starts is not None
    starts[workspace] = now - timedelta(minutes=60)
    monkeypatch.setenv("GPTME_SESSION_DEADLINE", _at_minutes(100).isoformat())

    messages = _trigger_at(monkeypatch, tmp_path, Log(), now)

    assert len(messages) == 1
    assert "Time elapsed: 1h" in messages[0].content
    assert "Deadline warning" not in messages[0].content


def test_deadline_below_threshold_is_silent(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Deadline mode schedules by fraction, not by elapsed milestones."""
    _set_deadline_env(monkeypatch)

    messages = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(69))

    assert messages == []


def test_deadline_at_threshold_reports_remaining(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Crossing 70% reports the remaining time."""
    _set_deadline_env(monkeypatch)

    messages = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(70))

    assert len(messages) == 1
    content = messages[0].content
    assert "Deadline warning: 70% of budget used" in content
    assert "Remaining time: 30min" in content
    assert "Session deadline:" in content
    assert "Stop editing" not in content


def test_deadline_90_percent_asks_to_wrap_up(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """The 90% notice tells the assistant to stop editing and commit a handoff."""
    _set_deadline_env(monkeypatch)

    messages = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(90))

    assert len(messages) == 1
    content = messages[0].content
    assert "Deadline warning: 90% of budget used" in content
    assert "Remaining time: 10min" in content
    assert "Stop editing and send a reply now" in content
    assert "save a handoff" in content
    assert "commit" in content


def test_deadline_duplicate_suppressed_without_log_append(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """In-context state suppresses repeats when the log is not appended."""
    _set_deadline_env(monkeypatch)

    first = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(70))
    second = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(70))

    assert len(first) == 1
    assert second == []


def test_deadline_duplicate_suppressed_from_log(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Log notices suppress repeats across a fresh context (resume)."""
    _set_deadline_env(monkeypatch)
    now = _at_minutes(70)

    first = _trigger_at(monkeypatch, tmp_path / "before", Log(), now)
    assert len(first) == 1
    log = Log().append(first[0])

    # A fresh workspace key models a new process/context after resume.
    resumed = _trigger_at(monkeypatch, tmp_path / "after", log, now)

    assert resumed == []


def test_deadline_custom_fraction_not_repeated_after_resume(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """A fraction whose display rounds must still suppress after resume."""
    _set_deadline_env(monkeypatch, fractions="0.8333333333")
    now = _at_minutes(84)

    first = _trigger_at(monkeypatch, tmp_path / "before", Log(), now)
    assert len(first) == 1
    assert "83.3333% of budget used" in first[0].content
    log = Log().append(first[0])

    resumed = _trigger_at(monkeypatch, tmp_path / "after", log, _at_minutes(90))

    assert resumed == []


def test_deadline_close_fractions_both_fire(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Distinct fractions closer than any tolerance must each still fire."""
    _set_deadline_env(monkeypatch, fractions="0.7,0.7000000005")

    first = _trigger_at(monkeypatch, tmp_path / "before", Log(), _at_minutes(70))
    assert len(first) == 1
    assert "Stop editing" not in first[0].content
    log = Log().append(first[0])

    resumed = _trigger_at(monkeypatch, tmp_path / "after", log, _at_minutes(80))

    assert len(resumed) == 1
    assert "Stop editing" in resumed[0].content


def test_deadline_scientific_notation_not_repeated_after_resume(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """A tiny fraction displayed in scientific notation must suppress on resume."""
    _set_deadline_env(monkeypatch, fractions="0.0000001")

    first = _trigger_at(monkeypatch, tmp_path / "before", Log(), _at_minutes(1))
    assert len(first) == 1
    assert "1e-05% of budget used" in first[0].content
    log = Log().append(first[0])

    resumed = _trigger_at(monkeypatch, tmp_path / "after", log, _at_minutes(2))

    assert resumed == []


def test_deadline_resume_emits_only_next_threshold(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Resume must emit the next threshold, not repeat a stale 70% notice."""
    _set_deadline_env(monkeypatch)
    log = Log().append(_deadline_log_notice(_at_minutes(70), 70))

    resumed = _trigger_at(monkeypatch, tmp_path, log, _at_minutes(90))

    assert len(resumed) == 1
    content = resumed[0].content
    assert "Deadline warning: 90% of budget used" in content
    assert "Deadline warning: 70% of budget used" not in content


def test_deadline_expired_emits_highest_threshold(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """An already-expired deadline still warns once, with zero remaining."""
    _set_deadline_env(monkeypatch)

    messages = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(120))

    assert len(messages) == 1
    content = messages[0].content
    assert "Deadline warning: 90% of budget used" in content
    assert "Remaining time: 0min" in content
    assert "Stop editing" in content


def test_deadline_long_jump_only_highest_threshold(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """A long tool jump warns once at the highest crossed threshold only."""
    _set_deadline_env(monkeypatch)

    jumped = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(95))
    assert len(jumped) == 1
    assert "Deadline warning: 90% of budget used" in jumped[0].content
    assert "Deadline warning: 70% of budget used" not in jumped[0].content

    later = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(96))
    assert later == []


def test_deadline_custom_fractions(load_time_awareness_tool, tmp_path, monkeypatch):
    """Custom fractions replace the default 70/90 schedule."""
    _set_deadline_env(monkeypatch, fractions="0.5,0.8")

    messages = _trigger_at(monkeypatch, tmp_path, Log(), _at_minutes(85))

    assert len(messages) == 1
    content = messages[0].content
    assert "Deadline warning: 80% of budget used" in content
    assert "Deadline warning: 50% of budget used" not in content
    assert "Stop editing and send a reply now" in content


def test_deadline_keeps_gap_reporting(load_time_awareness_tool, tmp_path, monkeypatch):
    """Resume gaps are still reported in deadline mode."""
    _set_deadline_env(monkeypatch)
    gap_start = _DEADLINE_BASE
    gap_end = _at_minutes(40)
    log = Log(
        [
            Message("user", "before", timestamp=gap_start),
            Message("user", "after", timestamp=gap_end),
        ]
    )

    messages = _trigger_at(monkeypatch, tmp_path, log, _at_minutes(41))

    assert len(messages) == 1
    content = messages[0].content
    assert "Resumed after 40min of inactivity" in content
    assert "Remaining time: 59min" in content
    assert "Deadline warning" not in content


def test_deadline_gap_after_prior_notice_still_reported(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """A gap after the latest notice is not hidden by deadline log scanning."""
    _set_deadline_env(monkeypatch)
    log = Log(
        [
            _deadline_log_notice(_DEADLINE_BASE, 10),
            Message("user", "before", timestamp=_at_minutes(10)),
            Message("user", "after", timestamp=_at_minutes(50)),
        ]
    )

    messages = _trigger_at(monkeypatch, tmp_path, log, _at_minutes(51))

    assert len(messages) == 1
    content = messages[0].content
    assert "Resumed after 40min of inactivity" in content
    assert "Deadline warning" not in content


def test_deadline_changed_budget_not_suppressed(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """A new runner budget must not inherit the old run's 90% suppression."""
    _set_deadline_env(monkeypatch)
    log = Log().append(_deadline_log_notice(_at_minutes(90), 90))

    new_start = _at_minutes(100)
    new_deadline = _at_minutes(200)
    _set_deadline_env(monkeypatch, start=new_start, deadline=new_deadline)
    messages = _trigger_at(monkeypatch, tmp_path, log, _at_minutes(190))

    assert len(messages) == 1
    content = messages[0].content
    assert "Deadline warning: 90% of budget used" in content
    assert "Stop editing and send a reply now" in content


def test_scan_log_default_ignores_pre_notice_gap():
    """Default mode stops at the latest notice, as before deadline support."""
    from gptme.hooks import time_awareness as ta

    log = Log(
        [
            Message("user", "old", timestamp=_at_minutes(0)),
            Message("user", "gap", timestamp=_at_minutes(40)),
            Message(
                "system",
                f"{ta.NOTICE_PREFIX}{_at_minutes(50).isoformat()}. Existing notice.",
                timestamp=_at_minutes(50),
                hide=True,
            ),
            Message("user", "new", timestamp=_at_minutes(60)),
        ]
    )

    scan = ta._scan_log(log)

    assert scan is not None
    assert scan.last_notice == _at_minutes(50)
    assert scan.gap is None
    assert scan.deadline_threshold is None
