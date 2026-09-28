"""Tests for the watch tool (slice 3: monitors + ScheduleWakeup parity)."""

import threading
import time
from pathlib import Path

import pytest

from gptme.logmanager.manager import _current_log_var
from gptme.message import Message
from gptme.tools.watch import (
    Watch,
    _deliver,
    _kill_proc,
    _new_id,
    _parse_duration,
    _parse_opts,
    _pending_deliveries,
    _pending_lock,
    _record_event,
    _watch_drain_hook,
    requeue_watch_event,
    take_queued_watch_events,
)


class _FakeManager:
    def __init__(self, logdir: Path | None = None):
        self.logdir = logdir


def _watch_cli(args: str, logdir: Path | None = None) -> Message:
    """Call the tool with a fake current manager routing to `logdir`."""
    if logdir is not None:
        fake = _FakeManager(logdir)
        token = _current_log_var.set(fake)  # type: ignore[arg-type]
    else:
        token = _current_log_var.set(None)
    try:
        out = _watch_tool(args, None, None)
    finally:
        _current_log_var.reset(token)
    assert isinstance(out, Message)
    return out


from gptme.tools import watch as _watch_mod

_watch_tool = _watch_mod._watch


@pytest.fixture(autouse=True)
def _clear_watch_state():
    from gptme.tools.watch import _watches

    def _reset() -> None:
        with _pending_lock:
            _pending_deliveries.clear()
        for w in list(_watches.values()):
            w.cancelled = True
            _kill_proc(w.proc)
        _watches.clear()

    _reset()
    yield
    _reset()


def test_parse_duration():
    assert _parse_duration("30s") == 30.0
    assert _parse_duration("5m") == 300.0
    assert _parse_duration("1h") == 3600.0
    assert _parse_duration("500ms") == 0.5
    assert _parse_duration("2") == 2.0
    with pytest.raises(ValueError, match="invalid duration"):
        _parse_duration("soon")


def test_parse_opts():
    opts, rest = _parse_opts(["--every", "30s", "--timeout=5m", "gh", "pr", "checks"])
    assert opts == {"every": "30s", "timeout": "5m"}
    assert rest == ["gh", "pr", "checks"]


def test_record_event_storm_coalesce():
    w = Watch(id="w1", kind="stream", description="test", created=time.time())
    for i in range(30):
        _record_event(w, f"line {i}")  # may return False once coalescing kicks in
    # Coalescing keeps the log bounded and inserts a summary marker
    assert len(w.events) <= 30
    assert any("coalesced" in e for e in w.events)


def test_record_event_storm_auto_cancel():
    w = Watch(id="w2", kind="stream", description="test", created=time.time())
    for i in range(300):
        _record_event(w, f"line {i}")  # coalesced events still count toward cancel
        if w.cancelled:
            break
    assert w.cancelled


def test_until_fires_once(tmp_path: Path):
    # Arming returns immediately; `true` can fire before the next line, so do
    # not assert on the pending queue here — that race failed in CI.
    _watch_cli("until true --every 0.1s --timeout 5s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "until")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "condition met" in w.events[-1]


def _record_all() -> list[Watch]:
    from gptme.tools.watch import _watches, _watches_lock

    with _watches_lock:
        return list(_watches.values())


def test_run_notifies_on_exit(tmp_path: Path):
    _watch_cli("run echo hello", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "rc=0" in w.events[-1]
    assert "hello" in w.events[-1]


def test_timer_fires_after_duration(tmp_path: Path):
    _watch_cli("timer 0.3s coffee", tmp_path)
    w = next(w for w in _record_all() if w.kind == "timer")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "timer elapsed" in w.events[-1]


def test_list_and_cancel(tmp_path: Path):
    out = _watch_cli("timer 60s test-list", tmp_path)
    wid = out.content.split()[2]
    listing = _watch_cli("list", tmp_path)
    assert wid in listing.content
    assert "armed" in listing.content
    out = _watch_cli(f"cancel {wid}", tmp_path)
    assert "Cancelled" in out.content
    assert "cancelled" in _watch_cli("list", tmp_path).content


def test_wait_timeout_returns_still_armed(tmp_path: Path):
    out = _watch_cli("timer 60s slow", tmp_path)
    wid = out.content.split()[2]
    result = _watch_cli(f"wait {wid} 0.2s", tmp_path)
    assert "still armed" in result.content


def test_deliver_offline_queues_for_step_pre(tmp_path: Path):
    logdir = tmp_path / "conv-a"
    w = Watch(
        id=_new_id(), kind="timer", description="t", created=time.time(), logdir=logdir
    )
    _deliver(w, "timer elapsed")
    msgs = list(_watch_drain_hook(_FakeManager(logdir)))
    assert len(msgs) == 1
    assert isinstance(msgs[0], Message)
    assert "fired: timer elapsed" in msgs[0].content


def test_deliver_routes_by_logdir(tmp_path: Path):
    logdir_a = tmp_path / "conv-a"
    logdir_b = tmp_path / "conv-b"
    w = Watch(
        id=_new_id(),
        kind="timer",
        description="t",
        created=time.time(),
        logdir=logdir_a,
    )
    _deliver(w, "for a only")
    # A different conversation's drain must not consume it
    msgs = list(_watch_drain_hook(_FakeManager(logdir_b)))
    assert msgs == []
    msgs = list(_watch_drain_hook(_FakeManager(logdir_a)))
    assert len(msgs) == 1


def test_unknown_verb():
    with pytest.raises(ValueError, match="unknown watch verb"):
        _watch_cli("frobnicate x")


def test_concurrent_fire_is_once(tmp_path: Path):
    w = Watch(
        id=_new_id(),
        kind="timer",
        description="t",
        created=time.time(),
        logdir=tmp_path,
    )
    threads = [threading.Thread(target=_fire_once, args=(w,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert w.fired
    assert len(_pending_deliveries) <= 1
    with _pending_lock:
        _pending_deliveries.clear()


def _fire_once(w: Watch) -> None:
    from gptme.tools.watch import _fire

    _fire(w, "once")


def test_parse_opts_preserves_command_flags():
    # `--repo` belongs to the watched command, not the watch options.
    opts, rest = _parse_opts(
        ["gh", "pr", "checks", "42", "--repo", "gptme/gptme", "--every", "60s"]
    )
    assert opts == {"every": "60s"}
    assert rest == ["gh", "pr", "checks", "42", "--repo", "gptme/gptme"]


def test_command_flags_survive_arming(tmp_path: Path):
    out = _watch_cli(
        "until gh pr checks 42 --repo gptme/gptme --every 0.1s --timeout 0.5s",
        tmp_path,
    )
    w = next(w for w in _record_all() if w.kind == "until")
    assert w.description == "gh pr checks 42 --repo gptme/gptme"
    wid = out.content.split()[2]
    _watch_cli(f"cancel {wid}", tmp_path)


def test_stream_records_each_line_once(tmp_path: Path):
    _watch_cli("stream seq 1 3", tmp_path)
    w = next(w for w in _record_all() if w.kind == "stream")
    deadline = time.time() + 5
    while time.time() < deadline:
        if any("stream ended" in e for e in list(w.events)):
            break
        time.sleep(0.05)
    # 3 lines + the stream-ended marker, each recorded exactly once.
    assert w.delivered == 4
    assert len(w.events) == 4


def test_run_honors_timeout(tmp_path: Path):
    _watch_cli("run sleep 30 --timeout 0.3s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "expired" in w.events[-1]


def test_cancel_kills_watched_process(tmp_path: Path):
    out = _watch_cli("run sleep 30", tmp_path)
    wid = out.content.split()[2]
    w = next(w for w in _record_all() if w.kind == "run")
    assert w.proc is not None and w.proc.poll() is None
    _watch_cli(f"cancel {wid}", tmp_path)
    deadline = time.time() + 5
    while w.proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    assert w.proc.poll() is not None


def test_watches_are_scoped_to_conversation(tmp_path: Path):
    logdir_a = tmp_path / "conv-a"
    logdir_b = tmp_path / "conv-b"
    out = _watch_cli("timer 60s private", logdir_a)
    wid = out.content.split()[2]
    # Another conversation sees nothing and cannot address the watch by id.
    assert "No armed watches" in _watch_cli("list", logdir_b).content
    with pytest.raises(ValueError, match="unknown watch id"):
        _watch_cli(f"cancel {wid}", logdir_b)
    with pytest.raises(ValueError, match="unknown watch id"):
        _watch_cli(f"wait {wid} 0.1s", logdir_b)
    # The owner still can.
    assert wid in _watch_cli("list", logdir_a).content


def test_denylisted_command_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Command denied"):
        _watch_cli("run rm -rf / --no-preserve-root", tmp_path)


def test_requeue_preserves_remaining_events(tmp_path: Path):
    logdir = tmp_path / "conv"
    with _pending_lock:
        _pending_deliveries.extend(
            [
                (logdir, "w1", "a"),
                (logdir, "w2", "b"),
                (logdir, "w3", "c"),
            ]
        )
    batch = take_queued_watch_events(logdir)
    assert batch == [("w1", "a"), ("w2", "b"), ("w3", "c")]
    # Mid-loop failure on the second event: put that one and every later
    # event back, preserving order (the previous-review P1).
    for wid, txt in reversed(batch[1:]):
        requeue_watch_event(logdir, wid, txt)
    assert take_queued_watch_events(logdir) == [("w2", "b"), ("w3", "c")]


def test_run_keeps_only_output_tail(tmp_path: Path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 20000)
    _watch_cli(f"run cat {big}", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "rc=0" in w.events[-1]
    # Tail only — the 20k payload must not all land in the event.
    assert len(w.events[-1]) < 2000
    assert "xxx" in w.events[-1]
