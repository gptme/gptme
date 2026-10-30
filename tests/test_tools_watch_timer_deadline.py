"""Timer deadlines must expire rather than report an elapsed duration."""

import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from gptme.tools import watch as watch_module


@pytest.mark.parametrize(
    ("seconds", "deadline", "expected", "finished_at"),
    [
        (5.0, 101.0, "expired", 101.0),
        (1.0, 105.0, "elapsed", 101.0),
        (1.0, None, "elapsed", 101.0),
        (1.0, 101.0, "expired", 101.0),
        (1.0, 99.0, "expired", 100.0),
        (0.0, None, "elapsed", 100.0),
    ],
)
def test_timer_deadline(
    seconds: float,
    deadline: float | None,
    expected: str,
    finished_at: float,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 100.0
    waits: list[float] = []

    def wait(timeout: float | None = None) -> bool:
        nonlocal now
        assert timeout is not None and timeout > 0
        waits.append(timeout)
        now += timeout
        return False

    watch = watch_module.Watch(
        id="w-timer-deadline",
        kind="timer",
        description="deadline test",
        created=now,
        deadline=deadline,
        logdir=tmp_path,
    )
    # Replace only this module's clock, not the process-global time module.
    monkeypatch.setattr(watch_module, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(watch.cancel_event, "wait", wait)
    watch_module._poll_timer(watch, seconds)
    assert watch.fired
    assert len(watch.events) == 1
    expected_message = (
        "timer elapsed" if expected == "elapsed" else "expired before timer elapsed"
    )
    assert watch.events[0] == f"{expected_message} (deadline test)"
    assert now == pytest.approx(finished_at)
    assert all(delay <= watch_module._POLL_INTERVAL for delay in waits)


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [(100.99, "expired"), (101.0, "expired"), (101.01, "timer elapsed")],
)
def test_timer_wait_overshoots_both_boundaries(
    deadline: float,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 100.0
    watch = watch_module.Watch(
        id="w-timer-overshoot",
        kind="timer",
        description="overshoot test",
        created=now,
        deadline=deadline,
        logdir=tmp_path,
    )

    def wait(timeout: float | None = None) -> bool:
        nonlocal now
        assert timeout is not None and timeout > 0
        now = 101.02
        return False

    monkeypatch.setattr(watch_module, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(watch.cancel_event, "wait", wait)
    watch_module._poll_timer(watch, 1.0)
    assert watch.fired
    assert len(watch.events) == 1
    assert watch.events[0].startswith(expected)


def test_timer_cancellation_during_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    watch = watch_module.Watch(
        id="w-timer-cancel",
        kind="timer",
        description="cancel test",
        created=time.time(),
        deadline=time.time() + 10,
        logdir=tmp_path,
    )

    def cancel(timeout: float | None = None) -> bool:
        watch.cancelled = True
        watch.cancel_event.set()
        return True

    monkeypatch.setattr(watch.cancel_event, "wait", cancel)
    watch_module._poll_timer(watch, 5)
    assert not watch.fired
    assert not watch.events


@pytest.mark.parametrize("cancelled", [False, True])
def test_public_timer_timeout(
    tmp_path: Path, cancelled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.logmanager.manager import _current_log_var

    token = _current_log_var.set(None)
    before = set(watch_module._watches)
    release_worker = Event()
    poll_timer = watch_module._poll_timer

    def gated_poll_timer(watch: watch_module.Watch, seconds: float) -> None:
        release_worker.wait()
        poll_timer(watch, seconds)

    if cancelled:
        monkeypatch.setattr(watch_module, "_poll_timer", gated_poll_timer)
    try:
        watch_module._watch("timer 2s --timeout 20ms public-deadline", None, None)
        watches = [w for key, w in watch_module._watches.items() if key not in before]
        assert len(watches) == 1
        watch = watches[0]
        assert watch.thread is not None
        assert watch.deadline is not None
        if cancelled:
            watch.cancelled = True
            watch.cancel_event.set()
            release_worker.set()
        watch.thread.join(timeout=1)
        assert not watch.thread.is_alive()
        if cancelled:
            assert not watch.fired
            assert not watch.events
        else:
            assert watch.fired
            assert list(watch.events) == [
                "expired before timer elapsed (public-deadline)"
            ]
    finally:
        _current_log_var.reset(token)
        release_worker.set()
        for key in set(watch_module._watches) - before:
            watch = watch_module._watches.pop(key)
            watch.cancelled = True
            watch.cancel_event.set()
            if watch.thread is not None:
                watch.thread.join(timeout=3)
        watch_module.take_queued_watch_events(None)
