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


def test_run_reports_killed_returncode(tmp_path: Path):
    """A stopped child is reaped before its exit wake reports `rc`.

    Regression: the SIGKILL escalation path did not reap, so the exit wake
    reported ``rc=None`` for a process that had been stopped.
    """
    import os
    import signal
    import subprocess as sp

    proc = sp.Popen(
        [
            "python3",
            "-c",
            "import os, signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "os.close(1); os.close(2); time.sleep(5)",
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
        deadline=None,
        proc=proc,
        logdir=tmp_path,
    )
    t = threading.Thread(target=_watch_mod._poll_run, args=(w, proc), daemon=True)
    t.start()
    try:
        t.join(timeout=10)
        assert not t.is_alive()
        assert w.fired
        assert "process exited rc=" in w.events[-1]
        assert "rc=None" not in w.events[-1]
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        _kill_proc(proc)


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
            "import os, sys, time; os.fork() or time.sleep(60); "
            "print('hi', flush=True)",
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
