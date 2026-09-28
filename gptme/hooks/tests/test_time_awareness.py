"""Tests for time_awareness hook."""

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gptme.hooks.time_awareness import (
    _conversation_start_times_var,
    _get_next_milestone,
    _shown_milestones_var,
    add_time_message,
)
from gptme.hooks.types import ToolExecutePostData
from gptme.logmanager import Log
from gptme.message import Message
from gptme.util import clock
from gptme.util.clock import format_duration, format_timestamp


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture(autouse=True)
def reset_contextvars():
    """Reset context vars between tests."""
    tok1 = _conversation_start_times_var.set(None)
    tok2 = _shown_milestones_var.set(None)
    yield
    _conversation_start_times_var.reset(tok1)
    _shown_milestones_var.reset(tok2)


def _call_add_time_message(
    workspace: Path | None = None,
) -> list[Message]:
    """Helper to call add_time_message with a dummy log, returning only Messages."""
    # The hook doesn't use the log parameter, so we pass a sentinel
    results = list(
        add_time_message(
            ToolExecutePostData(log=None, workspace=workspace, tool_use=None)
        )
    )
    return [r for r in results if isinstance(r, Message)]


class TestGetNextMilestone:
    """Tests for _get_next_milestone helper."""

    def test_under_one_minute(self) -> None:
        assert _get_next_milestone(0) is None

    def test_one_minute(self) -> None:
        assert _get_next_milestone(1) == 1
        assert _get_next_milestone(4) == 1

    def test_five_minutes(self) -> None:
        assert _get_next_milestone(5) == 5
        assert _get_next_milestone(9) == 5

    def test_ten_minutes(self) -> None:
        assert _get_next_milestone(10) == 10
        assert _get_next_milestone(14) == 10

    def test_fifteen_minutes(self) -> None:
        assert _get_next_milestone(15) == 15
        assert _get_next_milestone(19) == 15

    def test_twenty_minutes(self) -> None:
        assert _get_next_milestone(20) == 20
        assert _get_next_milestone(29) == 20

    def test_every_ten_after_twenty(self) -> None:
        assert _get_next_milestone(30) == 30
        assert _get_next_milestone(35) == 30
        assert _get_next_milestone(40) == 40
        assert _get_next_milestone(59) == 50
        assert _get_next_milestone(60) == 60


class TestAddTimeMessage:
    """Tests for add_time_message hook."""

    def test_no_workspace_returns_nothing(self) -> None:
        msgs = _call_add_time_message(workspace=None)
        assert len(msgs) == 0

    def test_first_call_initializes_no_message(self, workspace: Path) -> None:
        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 0

        # Verify state was initialized
        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        assert str(workspace) in start_times

    def test_message_at_one_minute(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        # Fast-forward time by 2 minutes
        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=2
        )
        _conversation_start_times_var.set(start_times)

        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 1
        assert msgs[0].role == "system"
        assert "Time elapsed" in msgs[0].content
        assert "2min" in msgs[0].content

    def test_no_duplicate_milestone(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=2
        )
        _conversation_start_times_var.set(start_times)

        # First call at ~2min shows the 1-min milestone
        msgs1 = _call_add_time_message(workspace=workspace)
        assert len(msgs1) == 1

        # Second call (still in same range) should NOT repeat
        msgs2 = _call_add_time_message(workspace=workspace)
        assert len(msgs2) == 0

    def test_shows_hours_format(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=65
        )
        _conversation_start_times_var.set(start_times)

        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 1
        assert "1h 5min" in msgs[0].content

    def test_message_is_hidden(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=6
        )
        _conversation_start_times_var.set(start_times)

        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 1
        assert msgs[0].hide is True


# --- Clock consistency: one timezone-aware clock, full date + tz in notices ---

UTC = timezone.utc


@pytest.fixture
def stockholm_tz(monkeypatch: pytest.MonkeyPatch):
    """Run with local timezone Europe/Stockholm (UTC+2 in summer), not UTC."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() not available on this platform")
    monkeypatch.setenv("TZ", "Europe/Stockholm")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class FakeClock:
    """Controllable replacement for gptme.util.clock.now."""

    def __init__(self, utc: datetime) -> None:
        self.utc = utc

    def __call__(self) -> datetime:
        return self.utc.astimezone()


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch, stockholm_tz) -> FakeClock:
    fc = FakeClock(datetime(2026, 9, 27, 13, 0, tzinfo=UTC))
    monkeypatch.setattr(clock, "now", fc)
    return fc


def _call_with_log(workspace: Path, log: Log) -> list[Message]:
    results = list(
        add_time_message(
            ToolExecutePostData(log=log, workspace=workspace, tool_use=None)
        )
    )
    return [r for r in results if isinstance(r, Message)]


_NOTICE_TIME_RE = re.compile(
    r"The time is now (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \S+ \(UTC([+-]\d{2}):(\d{2})\)"
)


def _notice_time(msg: Message) -> datetime:
    """Parse the absolute time from a notice, including its UTC offset."""
    m = _NOTICE_TIME_RE.search(msg.content)
    assert m, f"notice lacks full date + timezone: {msg.content!r}"
    offset = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone(offset)
    )


class TestClockConsistency:
    def test_notice_uses_local_time_with_date_and_tz(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """Local tz != UTC: notice shows local wall time, full date, tz name and offset."""
        _call_with_log(workspace, Log())
        fake_clock.utc = datetime(2026, 9, 27, 13, 33, tzinfo=UTC)
        msgs = _call_with_log(workspace, Log())
        assert len(msgs) == 1
        assert "The time is now 2026-09-27 15:33 CEST (UTC+02:00)." in msgs[0].content

    def test_notices_monotonic_across_date_boundary(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """Reproduces the report: 13:33 then 09:35 (next day) looked like time going backwards."""
        fake_clock.utc = datetime(2026, 9, 27, 13, 28, tzinfo=UTC)
        _call_with_log(workspace, Log())
        notices = []
        for t in [
            datetime(2026, 9, 27, 13, 33, tzinfo=UTC),
            datetime(2026, 9, 27, 21, 59, tzinfo=UTC),  # 23:59 CEST
            datetime(2026, 9, 28, 9, 35, tzinfo=UTC),  # 11:35 CEST next day
            datetime(2026, 9, 28, 10, 4, tzinfo=UTC),
        ]:
            fake_clock.utc = t
            msgs = _call_with_log(workspace, Log())
            assert len(msgs) == 1
            notices.append(msgs[0])
            # Each notice states the real instant, not just HH:MM
            assert _notice_time(msgs[0]) == t.replace(second=0)
        times = [_notice_time(m) for m in notices]
        assert times == sorted(times)
        assert "2026-09-28 11:35 CEST (UTC+02:00)" in notices[2].content

    def test_mixed_naive_and_aware_timestamps(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """Naive (local) and aware (UTC) message timestamps in one log don't break the hook."""
        fake_clock.utc = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
        # Naive local 15:00 CEST == 13:00 UTC, i.e. 2h before "now".
        start_naive = datetime(2026, 9, 27, 15, 0)  # noqa: DTZ001
        log = Log(
            [
                Message("system", "prompt", timestamp=start_naive),
                Message(
                    "user",
                    "hi",
                    timestamp=datetime(2026, 9, 27, 14, 50, tzinfo=UTC),
                ),
                Message(
                    "assistant",
                    "ok",
                    timestamp=datetime(2026, 9, 27, 16, 59),  # noqa: DTZ001 (naive local)
                ),
            ]
        )
        msgs = _call_with_log(workspace, log)
        assert len(msgs) == 1
        assert "Time elapsed: 2h since session start at 2026-09-27 15:00 CEST" in (
            msgs[0].content
        )

    def test_resume_uses_real_session_start_and_reports_gap(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """A resumed conversation (fresh process) measures from its real start and says it resumed."""
        start = datetime(2026, 9, 27, 13, 0, tzinfo=UTC)
        last_activity = datetime(2026, 9, 27, 19, 30, tzinfo=UTC)
        resumed = datetime(2026, 9, 28, 9, 30, tzinfo=UTC)
        fake_clock.utc = resumed + timedelta(minutes=1)
        log = Log(
            [
                Message("system", "prompt", timestamp=start),
                Message("assistant", "done for today", timestamp=last_activity),
                Message("user", "continue", timestamp=resumed),
                Message("assistant", "ok", timestamp=resumed + timedelta(seconds=30)),
            ]
        )
        msgs = _call_with_log(workspace, log)
        assert len(msgs) == 1
        content = msgs[0].content
        assert "The time is now 2026-09-28 11:31 CEST (UTC+02:00)." in content
        assert "Time elapsed: 20h 31min since session start at 2026-09-27 15:00" in (
            content
        )
        assert "Resumed after 14h of inactivity" in content
        assert "last activity 2026-09-27 21:30 CEST (UTC+02:00)" in content

        # The notice lands in the log; the gap is reported once, not on every step.
        log = Log([*log.messages, *msgs])
        fake_clock.utc = resumed + timedelta(minutes=2)
        assert _call_with_log(workspace, log) == []

    def test_state_survives_fresh_context(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """gptme-server runs each turn in a copied context: ContextVar state is lost.

        Milestones and gaps must be derived from the log, so a new context
        neither repeats the last milestone nor re-reports an old gap.
        """
        start = datetime(2026, 9, 27, 13, 0, tzinfo=UTC)
        resumed = start + timedelta(hours=5)
        log = Log(
            [
                Message("system", "prompt", timestamp=start),
                Message("user", "continue", timestamp=resumed),
            ]
        )
        fake_clock.utc = resumed + timedelta(minutes=1)
        msgs = _call_with_log(workspace, log)
        assert len(msgs) == 1
        assert "Resumed after 5h" in msgs[0].content
        log = Log([*log.messages, *msgs])

        # New turn in a fresh context, same 10-minute milestone window.
        _conversation_start_times_var.set(None)
        _shown_milestones_var.set(None)
        fake_clock.utc = resumed + timedelta(minutes=3)
        assert _call_with_log(workspace, log) == []

        # Next milestone is still shown.
        fake_clock.utc = resumed + timedelta(minutes=12)
        (notice,) = _call_with_log(workspace, log)
        assert "Resumed" not in notice.content
        assert "Time elapsed: 5h 12min" in notice.content

    def test_delayed_first_tool_call_after_resume(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """A gap is still reported if the first tool call comes long after the resume."""
        start = datetime(2026, 9, 27, 13, 0, tzinfo=UTC)
        resumed = start + timedelta(hours=14)
        log = Log(
            [
                Message("system", "prompt", timestamp=start),
                Message("user", "continue", timestamp=resumed),
                Message(
                    "assistant",
                    "thinking...",
                    timestamp=resumed + timedelta(seconds=30),
                ),
            ]
        )
        # e.g. a long-running tool call finishes 41min after the resume
        fake_clock.utc = resumed + timedelta(minutes=41)
        (notice,) = _call_with_log(workspace, log)
        assert "Resumed after 14h of inactivity" in notice.content

    def test_idle_gap_within_process_reported(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """Long idle (e.g. waiting for user input) within one process is reported too."""
        t0 = datetime(2026, 9, 27, 13, 0, tzinfo=UTC)
        fake_clock.utc = t0
        log = Log([Message("system", "prompt", timestamp=t0)])
        _call_with_log(workspace, log)
        t1 = t0 + timedelta(hours=3)
        log = Log([*log.messages, Message("user", "back", timestamp=t1)])
        fake_clock.utc = t1 + timedelta(minutes=1)
        msgs = _call_with_log(workspace, log)
        assert len(msgs) == 1
        assert "Resumed after 3h of inactivity" in msgs[0].content

    def test_system_prompt_date_agrees_with_notices(
        self, workspace: Path, fake_clock: FakeClock
    ) -> None:
        """Just after local midnight the prompt date must match the notice date, with tz."""
        from gptme.prompts.templates import prompt_timeinfo

        fake_clock.utc = datetime(2026, 9, 27, 22, 30, tzinfo=UTC)  # 00:30 CEST 28th
        (prompt,) = prompt_timeinfo()
        assert "2026-09-28 CEST (UTC+02:00)" in prompt.content
        assert "2026-09-27" not in prompt.content

        _call_with_log(workspace, Log())
        fake_clock.utc += timedelta(minutes=2)
        (notice,) = _call_with_log(workspace, Log())
        assert "2026-09-28" in notice.content


class TestClockFormatting:
    def test_format_duration(self) -> None:
        assert format_duration(timedelta(minutes=5)) == "5min"
        assert format_duration(timedelta(hours=2, minutes=5)) == "2h 5min"
        assert format_duration(timedelta(hours=14)) == "14h"
        assert format_duration(timedelta(days=1, hours=3, minutes=7)) == "1d 3h"
        assert format_duration(timedelta(minutes=-5)) == "0min"

    def test_format_timestamp_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if not hasattr(time, "tzset"):
            pytest.skip("time.tzset() not available on this platform")
        monkeypatch.setenv("TZ", "UTC")
        time.tzset()
        try:
            ts = format_timestamp(datetime(2026, 9, 28, 9, 35, tzinfo=UTC))
            assert ts == "2026-09-28 09:35 UTC+00:00"
        finally:
            monkeypatch.undo()
            time.tzset()
