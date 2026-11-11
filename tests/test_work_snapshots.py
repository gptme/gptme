"""Owner-scoped, non-destructive work snapshots for UI rendering (gptme#4557).

Covers the read model a TUI work view needs: watches and subagents scoped to
the originating conversation/branch, explicit deadline expiry instead of a
"fired" label, and reads that never drain the model-notification queues.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gptme.tools import watch as watch_module
from gptme.tools.subagent import Subagent, subagent_snapshots
from gptme.tools.subagent.types import (
    ReturnType,
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)
from gptme.tools.watch import (
    Watch,
    _pending_deliveries,
    _pending_lock,
    _register,
    format_watch_event,
    take_queued_watch_events,
    watch_snapshots,
)


@pytest.fixture(autouse=True)
def _clean_registries():
    def reset() -> None:
        with watch_module._watches_lock:
            watch_module._watches.clear()
        with _pending_lock:
            _pending_deliveries.clear()
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()
        while not _completion_queue.empty():
            _completion_queue.get_nowait()

    reset()
    yield
    reset()


def _watch(watch_id: str, logdir: Path | None, *, branch: str = "main", **kw) -> Watch:
    w = Watch(
        id=watch_id,
        kind=kw.pop("kind", "timer"),
        description=kw.pop("description", watch_id),
        created=kw.pop("created", 100.0),
        logdir=logdir,
        **kw,
    )
    w.branch = branch
    _register(w)
    return w


def _expire_timer(watch: Watch, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive a timer past its deadline on a controlled clock."""
    now = 100.0

    def wait(timeout: float | None = None) -> bool:
        nonlocal now
        now += timeout or 0.0
        return False

    monkeypatch.setattr(watch_module, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(watch.cancel_event, "wait", wait)
    watch_module._poll_timer(watch, 5.0)


def test_deadline_expiry_is_explicit_and_delivered_once(tmp_path, monkeypatch):
    w = _watch("w1", tmp_path, deadline=101.0)
    _expire_timer(w, monkeypatch)

    assert w.expired and w.fired and not w.cancelled
    assert w.status == "expired"
    assert w.delivered == 1
    queued = take_queued_watch_events(tmp_path)
    assert queued == [("w1", "expired before timer elapsed (w1)", "expired")]
    assert format_watch_event(*queued[0]) == (
        "Watch w1 expired: expired before timer elapsed (w1)"
    )


def test_met_condition_still_reports_fired(tmp_path, monkeypatch):
    w = _watch("w1", tmp_path, deadline=None)
    _expire_timer(w, monkeypatch)
    assert w.status == "fired" and not w.expired
    (event,) = take_queued_watch_events(tmp_path)
    assert format_watch_event(*event).startswith("Watch w1 fired:")


def test_expire_after_cancel_or_fire_is_ignored(tmp_path):
    cancelled = _watch("w1", tmp_path, cancelled=True)
    fired = _watch("w2", tmp_path, fired=True)
    watch_module._expire(cancelled, "expired")
    watch_module._expire(fired, "expired")
    assert cancelled.status == "cancelled"
    assert fired.status == "fired"
    assert take_queued_watch_events(tmp_path) == []


def test_server_event_status_names_expiry(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    events: list = []
    wakes: list[str] = []

    def wake(cid, msg, branch) -> bool:
        wakes.append(msg.content)
        return True

    monkeypatch.setattr(
        SessionManager, "add_event", staticmethod(lambda cid, ev: events.append(ev))
    )
    monkeypatch.setattr(SessionManager, "request_watch_wake", staticmethod(wake))
    w = _watch("w1", tmp_path / "conv", deadline=101.0)
    _expire_timer(w, monkeypatch)
    assert [e["status"] for e in events] == ["expired"]
    assert wakes == ["Watch w1 (timer) expired: expired before timer elapsed (w1)"]


def test_watch_snapshots_are_owner_and_branch_scoped(tmp_path):
    mine, other = tmp_path / "mine", tmp_path / "other"
    _watch("w1", mine, created=1.0)
    _watch("w2", mine, created=2.0, deadline=50.0, expired=True, fired=True)
    _watch("w3", mine, branch="side")
    _watch("w4", other)
    _watch("w5", None)  # no-conversation watch must never leak into a UI

    snaps = watch_snapshots(mine, branch="main")
    assert [(s.id, s.status, s.deadline) for s in snaps] == [
        ("w1", "armed", None),
        ("w2", "expired", 50.0),
    ]
    assert {s.id for s in watch_snapshots(mine)} == {"w1", "w2", "w3"}
    assert [s.id for s in watch_snapshots(other)] == ["w4"]


def test_watch_snapshot_does_not_drain_delivery_queue(tmp_path):
    w = _watch("w1", tmp_path)
    watch_module._fire(w, "ready")
    watch_snapshots(tmp_path)
    watch_snapshots(tmp_path)
    assert take_queued_watch_events(tmp_path) == [("w1", "ready", "fired")]


def _subagent(
    agent_id: str,
    logdir: Path,
    parent: Path | None,
    *,
    branch: str | None = "main",
    thread: threading.Thread | None = None,
    started_at: float = 0.0,
) -> Subagent:
    sa = Subagent(
        agent_id=agent_id,
        prompt=f"task for {agent_id}",
        thread=thread,
        logdir=logdir,
        model=None,
        parent_logdir=parent,
        parent_branch=branch,
        started_at=started_at or time.time(),
    )
    with _subagents_lock:
        _subagents.append(sa)
    return sa


def test_subagent_snapshots_are_owner_and_branch_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gptme.tools.subagent.api._maybe_rehydrate_registry", lambda: None
    )
    parent, stranger = tmp_path / "parent", tmp_path / "stranger"
    gate = threading.Event()
    running = threading.Thread(target=gate.wait, daemon=True)
    running.start()
    try:
        _subagent("a", tmp_path / "a", parent, thread=running, started_at=1.0)
        _subagent("b", tmp_path / "b", parent, started_at=2.0)
        _subagent("c", tmp_path / "c", parent, branch="side")
        _subagent("d", tmp_path / "d", stranger)
        _subagent("e", tmp_path / "e", None)
        with _subagent_results_lock:
            _subagent_results["b"] = ReturnType("success", "done")

        snaps = subagent_snapshots(parent, parent_branch="main")
        assert [(s.agent_id, s.status) for s in snaps] == [
            ("a", "running"),
            ("b", "success"),
        ]
        assert snaps[0].run_id == str((tmp_path / "a").resolve())
        assert {s.agent_id for s in subagent_snapshots(parent)} == {"a", "b", "c"}
        assert [s.agent_id for s in subagent_snapshots(stranger)] == ["d"]
    finally:
        gate.set()
        running.join(timeout=2)


def test_reused_agent_id_does_not_borrow_newer_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gptme.tools.subagent.api._maybe_rehydrate_registry", lambda: None
    )
    parent = tmp_path / "parent"
    _subagent("dup", tmp_path / "run1", parent, started_at=1.0)
    _subagent("dup", tmp_path / "run2", parent, started_at=2.0)
    with _subagent_results_lock:
        _subagent_results["dup"] = ReturnType("failure", "run2 failed")

    snaps = subagent_snapshots(parent)
    assert [(Path(s.run_id).name, s.status) for s in snaps] == [
        ("run1", "unknown"),
        ("run2", "failure"),
    ]


def test_subagent_snapshot_does_not_drain_completion_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gptme.tools.subagent.api._maybe_rehydrate_registry", lambda: None
    )
    parent = tmp_path / "parent"
    _subagent("a", tmp_path / "a", parent)
    with _subagent_results_lock:
        _subagent_results["a"] = ReturnType("success", "done")
    _completion_queue.put((parent.resolve(), "a", "success", "done", "main"))

    subagent_snapshots(parent)
    subagent_snapshots(parent)
    assert _completion_queue.qsize() == 1


@pytest.mark.parametrize("wake_succeeds", [False, True])
def test_stream_event_outcomes_survive_expiry_and_retry(
    tmp_path, monkeypatch, wake_succeeds
):
    pytest.importorskip("flask")
    from gptme.server.session_models import SessionManager

    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    server_events = []
    wakes = []
    monkeypatch.setattr(
        SessionManager,
        "add_event",
        staticmethod(lambda cid, ev: server_events.append(ev)),
    )
    monkeypatch.setattr(
        SessionManager, "request_watch_wake", staticmethod(lambda *a, **kw: False)
    )
    w = _watch("w1", tmp_path / "conv", kind="stream", branch="side")
    watch_module._fire(w, "line arrived", once=False)
    watch_module._expire(w, "deadline reached")
    assert [e["status"] for e in server_events] == ["fired", "expired"]

    def wake(cid, message, *, branch):
        wakes.append((branch, message.content))
        return wake_succeeds

    monkeypatch.setattr(SessionManager, "request_watch_wake", staticmethod(wake))
    SessionManager.retry_deferred_watch_wakes("conv")
    assert wakes == [
        ("side", "Watch w1 fired: line arrived\nWatch w1 expired: deadline reached")
    ]
    # A failed server retry must retain both event outcomes for STEP_PRE.
    drained = list(
        watch_module._watch_drain_hook(
            SimpleNamespace(logdir=w.logdir), notification_branch="side"
        )
    )
    assert [m.content for m in drained] == (
        []
        if wake_succeeds
        else ["Watch w1 fired: line arrived", "Watch w1 expired: deadline reached"]
    )


@pytest.mark.parametrize("outcome", ["cancelled", "timeout"])
def test_subagent_snapshot_terminal_outcome_precedes_live_worker(
    tmp_path, monkeypatch, outcome
):
    monkeypatch.setattr(
        "gptme.tools.subagent.api._maybe_rehydrate_registry", lambda: None
    )
    parent = tmp_path / "parent"
    gate = threading.Event()
    running = threading.Thread(target=gate.wait, daemon=True)
    running.start()
    try:
        _subagent("a", tmp_path / "a", parent, thread=running)
        with _subagent_results_lock:
            _subagent_results["a"] = ReturnType(outcome, "stopped")
        assert running.is_alive()
        assert subagent_snapshots(parent)[0].status == outcome
    finally:
        gate.set()
        running.join(timeout=2)


def test_offline_stream_outcomes_do_not_depend_on_watch_registry(tmp_path):
    w = _watch("w1", None, kind="stream")
    watch_module._fire(w, "line arrived", once=False)
    watch_module._expire(w, "deadline reached")
    # Rendering needs only the event, even if the watch has been removed.
    with watch_module._watches_lock:
        watch_module._watches.clear()
    assert [format_watch_event(*event) for event in take_queued_watch_events(None)] == [
        "Watch w1 fired: line arrived",
        "Watch w1 expired: deadline reached",
    ]
