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
        threads = []
        for w in list(_watches.values()):
            w.cancelled = True
            w.cancel_event.set()
            _kill_proc(w.proc)
            if w.thread is not None:
                threads.append(w.thread)
        _watches.clear()
        for t in threads:
            t.join(timeout=2.0)

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


def test_coalesce_resets_window_so_trickle_resumes():
    # After a burst coalesces, the burst's timestamps must not keep the window
    # above the threshold — otherwise every later event re-coalesces for the
    # rest of the window, losing individual event text.
    w = Watch(id="w3", kind="stream", description="test", created=time.time())
    for i in range(25):
        _record_event(w, f"burst {i}")
    assert any("coalesced" in e for e in w.events)
    assert _record_event(w, "after burst") is True
    assert w.events[-1] == "after burst"
    # event_times was reset, not left to grow unbounded behind the marker.
    assert len(w.event_times) < 20


def test_record_event_storm_auto_cancel():
    w = Watch(id="w2", kind="stream", description="test", created=time.time())
    for i in range(300):
        _record_event(w, f"line {i}")  # coalesced events still count toward cancel
        if w.cancelled:
            break
    assert w.cancelled


def test_lifetime_event_cap_auto_cancels(monkeypatch):
    # Coalesced events must still count toward the lifetime cap. Disable the
    # window/coalesce guards so only `_MAX_EVENTS` can trip.
    monkeypatch.setattr(_watch_mod, "_MAX_EVENTS", 5)
    monkeypatch.setattr(_watch_mod, "_CANCEL_COUNT", 10_000)
    monkeypatch.setattr(_watch_mod, "_COALESCE_COUNT", 10_000)
    w = Watch(id="w-cap", kind="stream", description="test", created=time.time())
    for i in range(20):
        _record_event(w, f"line {i}")
        if w.cancelled:
            break
    assert w.cancelled
    assert w.seen > 5


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


def test_parse_opts_run_keeps_pattern_flag():
    # --pattern is a tmux option; run/stream must leave it on the command.
    opts, rest = _parse_opts(
        ["pytest", "--pattern", "smoke", "--timeout", "5s"],
        names=frozenset({"timeout"}),
    )
    assert opts == {"timeout": "5s"}
    assert rest == ["pytest", "--pattern", "smoke"]


def test_parse_opts_run_keeps_bare_timeout_flag():
    # pytest --timeout 30 (no unit) is the command's flag, not a watch deadline.
    opts, rest = _parse_opts(
        ["pytest", "--timeout", "30", "--timeout=60"],
        names=frozenset({"timeout"}),
    )
    assert opts == {}
    assert rest == ["pytest", "--timeout", "30", "--timeout=60"]


def test_parse_opts_run_consumes_unit_timeout_keeps_bare():
    opts, rest = _parse_opts(
        ["pytest", "--timeout", "30", "--timeout", "5s"],
        names=frozenset({"timeout"}),
    )
    assert opts == {"timeout": "5s"}
    assert rest == ["pytest", "--timeout", "30"]


def test_quoted_args_survive_arming(tmp_path: Path):
    # shlex.split + " ".join would turn grep 'foo bar' into grep foo bar.
    out = _watch_cli("run grep 'foo bar' file --timeout 0.5s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    assert w.description == "grep 'foo bar' file"
    _watch_cli(f"cancel {out.content.split()[2]}", tmp_path)


def test_run_keeps_bare_command_timeout(tmp_path: Path):
    # A bare `--timeout 30` belongs to the command (pytest-style); the
    # unit-suffixed `--timeout 0.5s` is the watch deadline.
    out = _watch_cli("run true --timeout 30 --timeout 0.5s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    assert w.description == "true --timeout 30"
    assert w.deadline is not None
    _watch_cli(f"cancel {out.content.split()[2]}", tmp_path)


def test_command_flags_survive_arming(tmp_path: Path):
    out = _watch_cli(
        "until gh pr checks 42 --repo gptme/gptme --every 0.1s --timeout 0.5s",
        tmp_path,
    )
    w = next(w for w in _record_all() if w.kind == "until")
    assert w.description == "gh pr checks 42 --repo gptme/gptme"
    wid = out.content.split()[2]
    _watch_cli(f"cancel {wid}", tmp_path)


def test_run_keeps_pattern_flag_on_command(tmp_path: Path):
    out = _watch_cli("run echo --pattern smoke --timeout 0.5s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    assert w.description == "echo --pattern smoke"
    _watch_cli(f"cancel {out.content.split()[2]}", tmp_path)


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


def test_stream_honors_timeout(tmp_path: Path):
    # A quiet stream that reaches its deadline must still deliver its wake;
    # signalling the reader to stop must not mark the watch cancelled.
    _watch_cli("stream sleep 30 --timeout 0.3s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "stream")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert not w.cancelled
    assert "expired" in w.events[-1]
    assert w.delivered == 1


def test_run_honors_timeout(tmp_path: Path):
    _watch_cli("run sleep 30 --timeout 0.3s", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "expired" in w.events[-1]


def test_run_reaps_child_after_sigkill_on_timeout(tmp_path: Path):
    """A child ignoring SIGTERM is reaped on explicit timeout, not on EOF."""
    import os
    import signal
    import subprocess as sp

    ready = tmp_path / "ready"
    proc = sp.Popen(
        [
            "python3",
            "-c",
            (
                "import os, pathlib, signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "os.close(1); os.close(2); "
                "pathlib.Path(sys.argv[1]).touch(); time.sleep(60)"
            ),
            str(ready),
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
        start_new_session=True,
    )
    w = Watch(
        id="w-rc",
        kind="run",
        description="slow-exit",
        created=time.time(),
        proc=proc,
        logdir=tmp_path,
    )
    t = threading.Thread(target=_watch_mod._poll_run, args=(w, proc), daemon=True)
    t.start()
    try:
        limit = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < limit:
            time.sleep(0.01)
        assert ready.exists(), "child did not install its signal handler"
        w.deadline = time.time() - 1
        t.join(timeout=10)
        assert not t.is_alive()
        assert w.fired
        assert "expired without exit" in w.events[-1]
        assert proc.returncode == -signal.SIGKILL
        assert proc.stdout is not None and proc.stdout.closed
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        w.cancelled = True
        w.cancel_event.set()
        _kill_proc(proc)
        t.join(timeout=3)


@pytest.mark.parametrize("returncode", [0, 7])
def test_run_waits_for_exit_after_stdout_eof(tmp_path: Path, returncode: int):
    """EOF is not permission to terminate quiet work after two seconds."""
    import subprocess as sp
    import sys

    marker = tmp_path / "completed"
    proc = sp.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, pathlib, sys, time; os.close(1); os.close(2); "
                "time.sleep(2.8); pathlib.Path(sys.argv[1]).touch(); "
                "sys.exit(int(sys.argv[2]))"
            ),
            str(marker),
            str(returncode),
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
    )
    w = Watch(
        id="w-eof",
        kind="run",
        description="closed-output",
        created=time.time(),
        proc=proc,
        logdir=tmp_path,
    )
    try:
        _watch_mod._poll_run(w, proc)
        assert marker.exists(), "watch killed the child before its work completed"
        assert proc.returncode == returncode
        assert list(w.events) == [f"process exited rc={returncode}"]
        assert proc.stdout is not None and proc.stdout.closed
    finally:
        _kill_proc(proc)
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.mark.parametrize("returncode", [0, 7])
def test_run_exit_between_polls_wins_over_deadline(
    tmp_path: Path, returncode: int, monkeypatch: pytest.MonkeyPatch
):
    """An exit during the poll wait must not turn into a timeout wake."""
    import subprocess as sp
    import sys

    release = tmp_path / "release"
    proc = sp.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys, time; p = pathlib.Path(sys.argv[1]); "
                "exec('while not p.exists(): time.sleep(0.01)'); "
                "print('done', flush=True); sys.exit(int(sys.argv[2]))"
            ),
            str(release),
            str(returncode),
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
    )
    w = Watch(
        id="w-exit-deadline",
        kind="run",
        description="exit-between-polls",
        created=time.time(),
        deadline=time.time() + 60,
        proc=proc,
        logdir=tmp_path,
    )
    wait = w.cancel_event.wait
    released = False

    def wait_for_exit(timeout: float | None = None) -> bool:
        nonlocal released
        if released:
            return wait(timeout)
        released = True
        release.touch()
        assert proc.wait(timeout=5) == returncode
        # Resume the observer after the deadline with the real child reaped,
        # rather than rely on scheduler timing to reproduce the race.
        w.deadline = time.time() - 1
        return False

    monkeypatch.setattr(w.cancel_event, "wait", wait_for_exit)
    try:
        _watch_mod._poll_run(w, proc)
        assert list(w.events) == [f"process exited rc={returncode} done"]
        assert proc.stdout is not None and proc.stdout.closed
    finally:
        _kill_proc(proc)
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.mark.parametrize("finish", ["deadline", "cancel"])
def test_run_stops_live_child_after_stdout_eof(tmp_path: Path, finish: str):
    import subprocess as sp
    import sys

    closed = tmp_path / "closed"
    proc = sp.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, pathlib, sys, time; os.close(1); os.close(2); "
                "pathlib.Path(sys.argv[1]).touch(); time.sleep(60)"
            ),
            str(closed),
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
    )
    w = Watch(
        id="w-eof-stop",
        kind="run",
        description="closed-output",
        created=time.time(),
        proc=proc,
        logdir=tmp_path,
    )
    worker = threading.Thread(target=_watch_mod._poll_run, args=(w, proc), daemon=True)
    worker.start()
    try:
        limit = time.monotonic() + 5
        while not closed.exists() and time.monotonic() < limit:
            time.sleep(0.01)
        assert closed.exists()
        if finish == "deadline":
            w.deadline = time.time() - 1
        else:
            w.cancelled = True
            w.cancel_event.set()
        worker.join(timeout=1.5)
        assert not worker.is_alive()
        assert proc.poll() is not None
        assert proc.stdout is not None and proc.stdout.closed
        if finish == "deadline":
            assert w.fired and "expired without exit" in w.events[-1]
        else:
            assert not w.fired
    finally:
        w.cancelled = True
        w.cancel_event.set()
        _kill_proc(proc)
        worker.join(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()


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


def test_deferred_watch_wakes_deliver_batch_in_one_wake(tmp_path, monkeypatch):
    """A queued batch wakes once with every event, not one event per turn.

    `request_watch_wake` sets `generating=True` before it returns, so a
    per-event loop would deliver only the first event and requeue the rest.
    """
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    logdir = (tmp_path / "conv").resolve()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    with _pending_lock:
        _pending_deliveries.extend([(logdir, "w1", "a"), (logdir, "w2", "b")])

    calls: list[str] = []

    def _fake_wake(cls, conversation_id, message, *, branch="main"):
        calls.append(message.content)
        return True

    monkeypatch.setattr(SessionManager, "request_watch_wake", classmethod(_fake_wake))
    SessionManager.retry_deferred_watch_wakes("conv")

    assert len(calls) == 1
    assert "Watch w1 fired: a" in calls[0]
    assert "Watch w2 fired: b" in calls[0]
    assert take_queued_watch_events(logdir) == []


def test_deferred_watch_wakes_requeue_whole_batch_when_busy(tmp_path, monkeypatch):
    """A busy conversation keeps the whole batch, in order."""
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    logdir = (tmp_path / "conv").resolve()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    with _pending_lock:
        _pending_deliveries.extend([(logdir, "w1", "a"), (logdir, "w2", "b")])

    monkeypatch.setattr(
        SessionManager,
        "request_watch_wake",
        classmethod(lambda cls, cid, msg, *, branch="main": False),
    )
    SessionManager.retry_deferred_watch_wakes("conv")

    assert take_queued_watch_events(logdir) == [("w1", "a"), ("w2", "b")]


@pytest.mark.parametrize("wake_succeeds", [False, True])
def test_deferred_watch_wakes_preserve_branches(tmp_path, monkeypatch, wake_succeeds):
    """Retry must neither send a foreign branch to main nor relabel it on failure."""
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    logdir = (tmp_path / "conv").resolve()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    with _pending_lock:
        _pending_deliveries.extend(
            [(logdir, "w1", "a", "other"), (logdir, "w2", "b", "other")]
        )
    calls = []

    def wake(cls, conversation_id, message, *, branch="main"):
        calls.append((branch, message.content))
        return wake_succeeds

    monkeypatch.setattr(SessionManager, "request_watch_wake", classmethod(wake))
    SessionManager.retry_deferred_watch_wakes("conv")
    assert len(calls) == 1
    assert calls[0][0] == "other"
    assert "Watch w1 fired: a" in calls[0][1]
    assert "Watch w2 fired: b" in calls[0][1]
    assert take_queued_watch_events(logdir, branch="main") == []
    assert take_queued_watch_events(logdir, branch="other") == (
        [] if wake_succeeds else [("w1", "a"), ("w2", "b")]
    )


def test_deferred_watch_wakes_keep_mixed_branch_order(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    logdir = (tmp_path / "conv").resolve()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    events: list[tuple[Path | None, str, str] | tuple[Path | None, str, str, str]] = [
        (logdir, "w1", "a", "other"),
        (logdir, "w2", "b", "main"),
        (logdir, "w3", "c", "other"),
        (logdir, "w4", "d"),  # legacy entries still belong to main
    ]
    with _pending_lock:
        _pending_deliveries.extend(events)
    calls = []

    def wake(cls, conversation_id, message, *, branch="main"):
        calls.append((branch, message.content))
        return False

    monkeypatch.setattr(SessionManager, "request_watch_wake", classmethod(wake))
    SessionManager.retry_deferred_watch_wakes("conv")
    assert calls == [
        ("other", "Watch w1 fired: a\nWatch w3 fired: c"),
        ("main", "Watch w2 fired: b\nWatch w4 fired: d"),
    ]
    assert take_queued_watch_events(logdir) == [
        ("w1", "a"),
        ("w2", "b"),
        ("w3", "c"),
        ("w4", "d"),
    ]


def test_deliver_live_wake_preserves_branch(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    calls = []
    monkeypatch.setattr(SessionManager, "add_event", lambda *args: None)

    def wake(cls, conversation_id, message, *, branch="main"):
        calls.append(branch)
        return True

    monkeypatch.setattr(SessionManager, "request_watch_wake", classmethod(wake))
    watch = Watch(
        id="branch-watch",
        kind="timer",
        description="test",
        created=time.time(),
        logdir=tmp_path / "conv",
        branch="other",
    )
    _deliver(watch, "ready")
    assert calls == ["other"]
    assert take_queued_watch_events(watch.logdir) == []


def test_command_from_watch_content():
    from gptme.tools.watch import _command_from_watch_content

    assert (
        _command_from_watch_content(
            "until gh pr checks 42 --repo gptme/gptme --every 60s"
        )
        == "gh pr checks 42 --repo gptme/gptme"
    )
    assert (
        _command_from_watch_content("run grep 'foo bar' file") == "grep 'foo bar' file"
    )
    assert _command_from_watch_content("run echo hello") == "echo hello"
    assert (
        _command_from_watch_content("run pytest --pattern smoke --timeout 5s")
        == "pytest --pattern smoke"
    )
    assert (
        _command_from_watch_content("stream tail -f app.log --pattern ERROR")
        == "tail -f app.log --pattern ERROR"
    )
    assert _command_from_watch_content("timer 10m coffee") is None
    assert _command_from_watch_content("list") is None


def test_execute_watch_structured_content(tmp_path):
    """A structured ``content`` kwarg (no code block) must reach every verb.

    Regression: ``execute_watch`` resolved ``content`` from kwargs but passed the
    original ``code=None`` to ``_watch``, so ``{"content": "list"}`` raised the
    usage error instead of listing.
    """
    from gptme.tools import watch as watch_mod

    def _run(content: str) -> list[Message]:
        token = _current_log_var.set(_FakeManager(tmp_path))  # type: ignore[arg-type]
        try:
            return list(watch_mod.execute_watch(None, None, {"content": content}))
        finally:
            _current_log_var.reset(token)

    listing = _run("list")
    assert len(listing) == 1
    assert "No armed watches" in listing[0].content

    armed = _run("timer 10m brew")
    assert len(armed) == 1
    assert "Armed watch" in armed[0].content
    wid = armed[0].content.split()[2]

    cancelled = _run(f"cancel {wid}")
    assert len(cancelled) == 1
    assert "Cancelled watch" in cancelled[0].content


def test_watch_allowlist_hook_auto_confirms_allowlisted():
    from gptme.hooks.confirm import ConfirmAction
    from gptme.tools.base import ToolUse
    from gptme.tools.watch import watch_allowlist_hook

    tool_use = ToolUse(tool="watch", args=[], content="until ls --every 30s")
    result = watch_allowlist_hook(tool_use)
    assert result is not None
    assert result.action == ConfirmAction.CONFIRM


def test_watch_allowlist_hook_falls_through_non_allowlisted():
    from gptme.tools.base import ToolUse
    from gptme.tools.watch import watch_allowlist_hook

    tool_use = ToolUse(tool="watch", args=[], content="run python script.py")
    assert watch_allowlist_hook(tool_use) is None


def test_watch_allowlist_hook_ignores_timer_and_other_tools():
    from gptme.tools.base import ToolUse
    from gptme.tools.watch import watch_allowlist_hook

    timer = ToolUse(tool="watch", args=[], content="timer 10m")
    assert watch_allowlist_hook(timer) is None
    assert watch_allowlist_hook(ToolUse(tool="shell", args=[], content="ls")) is None


@pytest.mark.parametrize(
    ("command", "check"),
    [
        ("sleep 30; gh pr checks 42", "gh pr checks 42"),
        ("sleep 1m && make test", "make test"),
        ("gh pr checks 1; sleep 30", "gh pr checks 1"),
        ("while ! curl -s localhost:5700; do sleep 5; done", "curl -s localhost:5700"),
        ("until grep -q done f.log; do sleep 10; done", "grep -q done f.log"),
        ("for i in 1 2 3; do sleep 10; tail -5 x.log; done", "tail -5 x.log"),
    ],
)
def test_find_sleep_poll_detects_chains(command: str, check: str):
    from gptme.tools.watch import _find_sleep_poll

    found = _find_sleep_poll(command)
    assert found is not None
    assert found[1] == check


@pytest.mark.parametrize(
    "command",
    [
        "sleep 30",  # bare delay: nothing to poll
        "sleep 2; ls",  # short settle delay
        "echo sleep 30; ls",  # sleep is an argument, not a command
        'git commit -m "sleep 30; fix"',
        "pytest tests/",
    ],
)
def test_find_sleep_poll_allows_non_polls(command: str):
    from gptme.tools.watch import _find_sleep_poll

    assert _find_sleep_poll(command) is None


def test_sleep_poll_guard_hook_refuses_with_watch_suggestion():
    from gptme.hooks.confirm import ConfirmAction
    from gptme.tools.base import ToolUse
    from gptme.tools.watch import sleep_poll_guard_hook

    tool_use = ToolUse(tool="shell", args=[], content="sleep 30; gh pr checks 42")
    result = sleep_poll_guard_hook(tool_use)
    assert result is not None
    assert result.action == ConfirmAction.SKIP
    assert "until gh pr checks 42 --every 30s" in (result.message or "")


def test_sleep_poll_guard_hook_ignores_other_tools_and_plain_commands():
    from gptme.tools.base import ToolUse
    from gptme.tools.watch import sleep_poll_guard_hook

    watch = ToolUse(tool="watch", args=[], content="until ls --every 30s")
    assert sleep_poll_guard_hook(watch) is None
    assert sleep_poll_guard_hook(ToolUse(tool="shell", args=[], content="ls")) is None


def test_execute_watch_skip_does_not_arm(tmp_path: Path):
    from gptme.hooks import HookType, register_hook, unregister_hook
    from gptme.hooks.confirm import ConfirmationResult
    from gptme.tools.base import ToolUse, using_current_tool_use
    from gptme.tools.watch import execute_watch

    def skip_hook(tool_use, preview=None, workspace=None):
        return ConfirmationResult.skip("skipped by test")

    register_hook("watch-skip-test", HookType.TOOL_CONFIRM, skip_hook, 100)
    try:
        tool_use = ToolUse(tool="watch", args=[], content="run echo should-not-run")
        with using_current_tool_use(tool_use):
            msgs = list(execute_watch("run echo should-not-run", None, None))
        text = " ".join(m.content.lower() for m in msgs)
        assert "skipped" in text or "aborted" in text
        assert _record_all() == []
    finally:
        unregister_hook("watch-skip-test", HookType.TOOL_CONFIRM)


def test_execute_watch_run_arms_when_confirmed(tmp_path: Path):
    from gptme.logmanager.manager import _current_log_var
    from gptme.tools.base import ToolUse, using_current_tool_use
    from gptme.tools.watch import execute_watch

    tool_use = ToolUse(tool="watch", args=[], content="run echo hello")
    fake = _FakeManager(tmp_path)
    token = _current_log_var.set(fake)  # type: ignore[arg-type]
    try:
        with using_current_tool_use(tool_use):
            msgs = list(execute_watch("run echo hello", None, None))
        assert any("Armed watch" in m.content for m in msgs)
        armed = [w for w in _record_all() if w.kind == "run"]
        assert armed
        list(execute_watch(f"cancel {armed[0].id}", None, None))
    finally:
        _current_log_var.reset(token)


def test_run_replaces_undecodable_bytes(tmp_path: Path):
    # Keep quoting so printf emits 0xff 0xfe, not the digits 377376.
    # errors="replace" must not treat those bytes as EOF.
    _watch_cli(r"run printf '\377\376'", tmp_path)
    w = next(w for w in _record_all() if w.kind == "run")
    deadline = time.time() + 5
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "rc=0" in w.events[-1]
    assert "errored" not in w.events[-1]
    assert "\ufffd" in w.events[-1]


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


def test_run_rejects_shell_operators(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError, match="shell operators"):
        _watch_cli("run echo ok > status.txt", tmp_path)
    with pytest.raises(ValueError, match="shell operators"):
        _watch_cli("until echo ok | grep x", tmp_path)


def test_tmux_requires_pattern_or_stable(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError, match="--pattern"):
        _watch_cli("tmux mysession", tmp_path)
    # pattern-only passes validation and arms (capture failure fires as event).
    out = _watch_cli("tmux mysession --pattern foo", tmp_path)
    assert "Armed watch" in out.content


def test_quoted_operator_argument_is_allowed():
    """A quoted `|` is a literal argument, not a shell operator.

    The operator check runs on the raw text (quote-aware), not on
    ``shlex.split`` output which strips the quotes.
    """
    from gptme.tools.watch import _reject_unquoted_operators

    _reject_unquoted_operators("grep '|' file")  # must not raise
    _reject_unquoted_operators('echo "a > b"')  # must not raise
    _reject_unquoted_operators(r"echo \|")  # escaped operator is a literal
    import pytest

    with pytest.raises(ValueError, match="shell operators"):
        _reject_unquoted_operators("echo ok > status.txt")
    with pytest.raises(ValueError, match="shell operators"):
        _reject_unquoted_operators("echo a | grep b")
    with pytest.raises(ValueError, match="shell operators"):
        # escaped backslash, then an unquoted pipe
        _reject_unquoted_operators(r"echo \\| grep")


def test_until_slow_command_outlives_every(tmp_path: Path):
    """A probe slower than every*4 must still be able to fire.

    Regression: per-invocation timeout was ``every * 4``, so a 0.8s
    command with ``--every 0.1s`` was killed at 0.4s on every attempt
    and never reached exit 0.
    """
    _watch_cli(
        "until python3 -c 'import time; time.sleep(0.8)' --every 0.1s --timeout 5s",
        tmp_path,
    )
    w = next(w for w in _record_all() if w.kind == "until")
    deadline = time.time() + 6
    while not w.fired and time.time() < deadline:
        time.sleep(0.05)
    assert w.fired
    assert "condition met" in w.events[-1]


def test_tmux_invalid_pattern_is_valueerror(tmp_path: Path):
    with pytest.raises(ValueError, match="invalid --pattern regex"):
        _watch_cli("tmux sess --pattern '['", tmp_path)


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="requires fork")
@pytest.mark.parametrize("with_deadline", [False, True])
def test_run_exits_when_grandchild_holds_pipe(tmp_path: Path, with_deadline: bool):
    """A settled child must not wait forever for an inherited stdout handle."""
    import os
    import signal
    import subprocess as sp
    import sys

    proc = sp.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, time; pid = os.fork(); "
                "time.sleep(60) if pid == 0 else print('parent done', flush=True)"
            ),
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
        start_new_session=True,
    )
    w = Watch(
        id="w-inherited",
        kind="run",
        description="inherited-pipe",
        created=time.time(),
        proc=proc,
        logdir=tmp_path,
    )
    worker = threading.Thread(target=_watch_mod._poll_run, args=(w, proc), daemon=True)
    try:
        assert proc.wait(timeout=5) == 0
        if with_deadline:
            # Direct-child work is complete; its deadline must not expire the
            # subsequent bounded drain of a grandchild's inherited pipe.
            w.deadline = time.time() + 0.5
        worker.start()
        worker.join(timeout=6)
        assert not worker.is_alive()
        assert proc.returncode == 0
        assert list(w.events) == ["process exited rc=0 parent done"]
        assert proc.stdout is not None and proc.stdout.closed
    finally:
        w.cancelled = True
        w.cancel_event.set()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        _kill_proc(proc)
        worker.join(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()


def test_run_timeout_closes_stdout_when_grandchild_holds_pipe(tmp_path: Path):
    """Closing stdout unblocks the reader if a grandchild inherited the pipe."""
    import os
    import signal
    import subprocess as sp

    proc = sp.Popen(
        [
            "python3",
            "-c",
            "import os, time; os.fork() or time.sleep(60); time.sleep(60)",
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
        start_new_session=True,
    )
    w = Watch(
        id="w-pipe",
        kind="run",
        description="grandchild-pipe",
        created=time.time(),
        deadline=time.time() + 0.3,
        proc=proc,
        logdir=tmp_path,
    )
    t = threading.Thread(target=_watch_mod._poll_run, args=(w, proc), daemon=True)
    t.start()
    try:
        t.join(timeout=5)
        assert not t.is_alive()
        assert w.fired
        assert "expired" in w.events[-1]
        assert proc.stdout is None or proc.stdout.closed
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        _kill_proc(proc)


def test_stream_ends_when_parent_exits_even_if_grandchild_holds_pipe(
    tmp_path: Path,
):
    """Parent exit ends the stream even if a grandchild inherited stdout."""
    import os
    import signal
    import subprocess as sp

    proc = sp.Popen(
        [
            "python3",
            "-c",
            (
                "import os, sys, time; os.fork() or time.sleep(60); "
                "print('hi', flush=True)"
            ),
        ],
        stdout=sp.PIPE,
        stderr=sp.DEVNULL,
        text=True,
        start_new_session=True,
        bufsize=1,
    )
    w = Watch(
        id="w-stream-pipe",
        kind="stream",
        description="grandchild-pipe-stream",
        created=time.time(),
        proc=proc,
        logdir=tmp_path,
    )
    t = threading.Thread(target=_watch_mod._poll_stream, args=(w, proc), daemon=True)
    t.start()
    try:
        t.join(timeout=5)
        assert not t.is_alive()
        assert w.fired
        assert any("stream ended" in e for e in w.events)
        assert any(e.strip() == "hi" for e in w.events)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        _kill_proc(proc)


@pytest.mark.parametrize("branch", ["main", "feature"])
@pytest.mark.parametrize("execution", ["in-process", "acp"])
def test_server_step_drains_only_its_watch_branch(
    tmp_path, monkeypatch, branch, execution
):
    import asyncio
    from unittest.mock import MagicMock

    pytest.importorskip("flask")
    from gptme.config import ChatConfig
    from gptme.hooks import HookType
    from gptme.llm.models import ModelMeta
    from gptme.logmanager import LogManager
    from gptme.server import session_step
    from gptme.server.session_models import SessionManager
    from gptme.tools.watch import take_queued_watch_events_with_branches

    monkeypatch.chdir(tmp_path)
    manager = LogManager(
        [Message("user", "watch work")] if execution == "in-process" else [],
        logdir=tmp_path / "parent",
        lock=False,
    )
    manager.branch(branch)
    session = SessionManager.create_session("parent")
    config = ChatConfig(_logdir=manager.logdir, workspace=tmp_path)
    monkeypatch.setattr(
        session_step, "prepare_execution_environment", lambda **kw: None
    )
    monkeypatch.setattr(session_step.LogManager, "load", lambda *a, **kw: manager)
    monkeypatch.setattr(session_step.ChatConfig, "load_or_create", lambda *a: config)
    monkeypatch.setattr(
        "gptme.llm.models.get_model", lambda *a: ModelMeta("local", "test", 4096)
    )
    # Stop before inference, after the real STEP_PRE watch drain has run.
    monkeypatch.setattr(session_step, "prepare_messages", lambda *a, **kw: [])

    def hooks(hook_type, **kwargs):
        if hook_type == HookType.STEP_PRE:
            return list(_watch_drain_hook(**kwargs))
        return []

    monkeypatch.setattr(session_step, "trigger_hook", hooks)
    for origin in ("main", "feature"):
        requeue_watch_event(manager.logdir, origin, f"ready-{origin}", branch=origin)
    try:
        if execution == "acp":
            session.acp_runtime = MagicMock()
            asyncio.run(session_step._acp_step("parent", session, tmp_path))
        else:
            session_step.step("parent", session, "local/test", tmp_path, branch=branch)
        delivered = [m.content for m in manager.log if m.content.startswith("Watch ")]
        assert delivered == [f"Watch {branch} fired: ready-{branch}"]
        other = "feature" if branch == "main" else "main"
        assert take_queued_watch_events_with_branches(manager.logdir) == [
            (other, f"ready-{other}", other)
        ]
    finally:
        session.acp_runtime = None
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("newline", [False, True])
def test_stream_bounds_oversized_lines(
    newline: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real pipe reads must bound events before retention and delivery."""
    import subprocess
    import sys

    limit = 64 * 1024
    ending = b"\nshort\n" if newline else b""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.stdout.buffer.write("
                f"b'x' * {2 * 1024 * 1024} + {ending!r})"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    w = Watch(
        id="bounded",
        kind="stream",
        description="test",
        created=time.time(),
        deadline=time.time() + 10,
        proc=proc,
    )
    delivered = []
    monkeypatch.setattr(_watch_mod, "_deliver", lambda _, text: delivered.append(text))
    try:
        _watch_mod._poll_stream(w, proc)
        assert proc.returncode == 0
        assert not w.cancelled
        assert delivered == list(w.events)
        assert max(len(text.encode("utf-8")) for text in delivered) <= limit
        assert delivered[0].startswith("x" * 1024)
        assert "truncated" in delivered[0]
        assert delivered[0].count("truncated") == 1
        if newline:
            assert delivered[1] == "short"
        assert delivered[-1] == "stream ended (rc=0)"
    finally:
        _kill_proc(proc)
        proc.wait(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.mark.parametrize("size", [65535, 65536, 65537])
@pytest.mark.parametrize("ending", [b"\n", b""])
def test_stream_line_limit_boundaries(size: int, ending: bytes) -> None:
    bounded = _watch_mod._StreamLines()
    payload = b"a" * size + ending
    events: list[str] = []
    for offset in range(0, len(payload), 4096):
        events.extend(bounded.feed(payload[offset : offset + 4096]))
        assert len(bounded.buffer) <= 65536
    events.extend(bounded.feed(b"", final=True))
    assert len(events) == 1
    assert len(events[0].encode("utf-8")) <= 65536
    if size <= 65536:
        assert events == ["a" * size]
    else:
        assert events[0].endswith(_watch_mod._STREAM_TRUNCATED)


def test_stream_carry_and_discard_are_bounded() -> None:
    bounded = _watch_mod._StreamLines()
    events: list[str] = []
    for _ in range(2048):
        events.extend(bounded.feed(b"x" * 4096))
        assert len(bounded.buffer) <= 65536
        assert len(events) <= 1
    assert bounded.discarding
    assert not bounded.buffer
    events.extend(bounded.feed(b"\nnext\nlast"))
    events.extend(bounded.feed(b"", final=True))
    assert len(events[0].encode("utf-8")) == 65536
    assert events[0].endswith(_watch_mod._STREAM_TRUNCATED)
    assert events[1:] == ["next", "last"]
    assert not bounded.buffer


@pytest.mark.parametrize("chunk_size", [1, 7, 4096])
def test_stream_utf8_split_and_truncation(chunk_size: int) -> None:
    bounded = _watch_mod._StreamLines()
    ordinary = "café 日本語 😀"
    payload = (ordinary + "\n" + "😀" * 20000 + "\n" + ordinary).encode()
    events: list[str] = []
    for offset in range(0, len(payload), chunk_size):
        events.extend(bounded.feed(payload[offset : offset + chunk_size]))
        assert len(bounded.buffer) <= 65536
    events.extend(bounded.feed(b"", final=True))
    assert events[0] == events[2] == ordinary
    assert events[1].endswith(_watch_mod._STREAM_TRUNCATED)
    assert "\ufffd" not in "".join(events)
    assert all(len(event.encode()) <= 65536 for event in events)


def test_stream_incomplete_utf8_eof_is_replaced() -> None:
    bounded = _watch_mod._StreamLines()
    assert list(bounded.feed(b"hello \xf0\x9f")) == []
    assert list(bounded.feed(b"", final=True)) == ["hello \ufffd"]


def test_stream_real_pipe_split_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    # Force read boundaries inside multibyte codepoints, independently of
    # OS pipe scheduling. All bytes still come from a real child process.
    real_read = _watch_mod.os.read
    monkeypatch.setattr(_watch_mod.os, "read", lambda fd, n: real_read(fd, min(n, 1)))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write('café 😀\\nfin'.encode())",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    w = Watch(
        id="utf8",
        kind="stream",
        description="test",
        created=time.time(),
        deadline=time.time() + 10,
        proc=proc,
    )
    monkeypatch.setattr(_watch_mod, "_deliver", lambda *_: None)
    try:
        _watch_mod._poll_stream(w, proc)
        assert list(w.events) == ["café 😀", "fin", "stream ended (rc=0)"]
    finally:
        _kill_proc(proc)
        proc.wait(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.mark.parametrize("stop_kind", ["deadline", "cancel"])
def test_stream_oversized_live_child_cleanup(
    stop_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; os.write(1, b'x' * (2 * 1024 * 1024)); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    delivered = threading.Event()
    w = Watch(
        id="cleanup", kind="stream", description="test", created=time.time(), proc=proc
    )

    def deliver(_: Watch, text: str) -> None:
        if "truncated" in text:
            delivered.set()

    monkeypatch.setattr(_watch_mod, "_deliver", deliver)
    worker = threading.Thread(
        target=_watch_mod._poll_stream, args=(w, proc), daemon=True
    )
    worker.start()
    try:
        assert delivered.wait(timeout=5), (
            "oversized unterminated line must signal loss promptly"
        )
        assert proc.poll() is None
        if stop_kind == "deadline":
            w.deadline = time.time() - 1
        else:
            w.cancelled = True
            w.cancel_event.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert proc.poll() is not None
        assert proc.stdout is not None and proc.stdout.closed
        assert all(len(text.encode()) <= 65536 for text in w.events)
        if stop_kind == "deadline":
            assert not w.cancelled
            assert "expired without event" in w.events[-1]
        else:
            assert len(w.events) == 1
    finally:
        _kill_proc(proc)
        worker.join(timeout=3)
        proc.wait(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.mark.parametrize("guard", ["queue", "storm"])
def test_stream_firehose_guards_still_stop_child(
    guard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import queue
    import subprocess
    import sys

    # Use the real bounded queue and child I/O. The queue case deliberately
    # stalls the first delivery until overflow; the storm case allows enough
    # queue headroom that the consumer's event-count guard trips first.
    monkeypatch.setattr(
        _watch_mod, "_STREAM_LINE_QUEUE", 1 if guard == "queue" else 512
    )
    queued_sizes: list[int] = []

    class MeasuredQueue(queue.Queue[str | None]):
        def put(
            self, item: str | None, block: bool = True, timeout: float | None = None
        ) -> None:
            if item is not None:
                queued_sizes.append(len(item.encode("utf-8")))
            super().put(item, block=block, timeout=timeout)

    monkeypatch.setattr(_watch_mod.queue, "Queue", MeasuredQueue)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; os.write(1, b'short\\n' * 300); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    w = Watch(
        id="firehose",
        kind="stream",
        description="test",
        created=time.time(),
        deadline=time.time() + 10,
        proc=proc,
    )

    def deliver(_: Watch, text: str) -> None:
        if guard == "queue":
            assert w.cancel_event.wait(timeout=3), (
                "reader must cancel on queue overflow"
            )

    monkeypatch.setattr(_watch_mod, "_deliver", deliver)
    try:
        _watch_mod._poll_stream(w, proc)
        assert w.cancelled
        assert proc.poll() is not None
        assert proc.stdout is not None and proc.stdout.closed
        assert queued_sizes and max(queued_sizes) <= 65536
        if guard == "storm":
            assert w.seen > _watch_mod._CANCEL_COUNT
        else:
            assert w.seen == 1
    finally:
        _kill_proc(proc)
        proc.wait(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()
