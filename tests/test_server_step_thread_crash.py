"""A step thread that crashes before step() cleans up must release its reservation."""

import threading
import time
from typing import Any, cast
from uuid import uuid4

import pytest

pytest.importorskip("flask")

from gptme.logmanager import LogManager
from gptme.server import session_step
from gptme.server.session_models import SessionManager


def _capture_new_threads(seen: set[threading.Thread]) -> list[threading.Thread]:
    """Return (and remember) the worker threads spawned since ``seen``."""
    new = [t for t in threading.enumerate() if t not in seen]
    seen.update(new)
    return new


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.parametrize("persistence_fails", [False, True])
def test_step_thread_crash_releases_generating(
    client, tmp_path, monkeypatch, persistence_fails
):
    name = f"test-step-crash-{uuid4().hex}"
    result = client.put(
        f"/api/v2/conversations/{name}",
        json={
            "prompt": "Be helpful.",
            "config": {"chat": {"workspace": str(tmp_path)}},
        },
    )
    assert result.status_code == 200
    session = SessionManager.get_session(result.get_json()["session_id"])
    assert session is not None

    def crash(*args, **kwargs):
        # e.g. record_runtime_selection() on a bare provider name, which runs
        # before step()'s own try/finally
        raise ValueError("not enough values to unpack (expected 2, got 1)")

    monkeypatch.setattr(session_step, "step", crash)
    if persistence_fails:

        def fail_persistence(*args, **kwargs):
            raise OSError("conversation storage unavailable")

        monkeypatch.setattr(session_step, "_persist_generation_error", fail_persistence)
    woken: list[str] = []
    monkeypatch.setattr(
        SessionManager,
        "retry_deferred_watch_wakes",
        classmethod(lambda cls, conv_id: woken.append(conv_id)),
    )
    session.checkpoint_needs_continuation = True
    first_event = session.events_count
    seen = set(threading.enumerate())
    workers: list[threading.Thread] = []
    try:
        assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
        workers.extend(_capture_new_threads(seen))
        deadline = time.monotonic() + 5
        while not woken and time.monotonic() < deadline:
            time.sleep(0.02)

        # The release happens in one step_lock section; read under it.
        with session.step_lock:
            assert not session.generating
            assert session.generating_since is None
            assert session.last_error and "unpack" in session.last_error
            assert session.checkpoint_needs_continuation is False
            events, _ = session.read_events(first_event)
        types = [e["type"] for e in events if e["type"] in ("error", "step_complete")]
        assert types == ["error", "step_complete"]
        assert woken == [name]
        reloaded = LogManager.load(name, lock=False)
        persisted_error = any(
            message.role == "system"
            and message.content
            == "Error: not enough values to unpack (expected 2, got 1)"
            for message in reloaded.log.messages
        )
        assert persisted_error is not persistence_fails
        # The conversation accepts new work again instead of answering 409.
        response = client.post(
            f"/api/v2/conversations/{name}",
            json={"role": "user", "content": "still there?"},
        )
        assert response.status_code == 200
    finally:
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("replacement_backend", ["inprocess", "acp"])
def test_step_thread_raise_after_release_keeps_new_reservation(
    client, tmp_path, monkeypatch, replacement_backend
):
    """step() can release, then re-raise (STEP_PRE SessionCompleteException).

    A non-reserved caller (in-process or ACP) that reserves in that gap must
    not have its reservation cleared by the first thread's crash handler, which would
    report a clean stop as a failure and admit a second concurrent step.
    """
    from gptme.tools.complete import SessionCompleteException

    name = f"test-step-reraise-{uuid4().hex}"
    result = client.put(
        f"/api/v2/conversations/{name}",
        json={
            "prompt": "Be helpful.",
            "config": {"chat": {"workspace": str(tmp_path)}},
        },
    )
    assert result.status_code == 200
    session = SessionManager.get_session(result.get_json()["session_id"])
    assert session is not None

    second_started = threading.Event()
    finish_second = threading.Event()
    handler_done = threading.Event()
    calls: list[int | None] = []

    def fake_step(*args, step_seq=None, **kwargs):
        calls.append(step_seq)
        if len(calls) == 1:
            # Mirror step()'s STEP_PRE SessionCompleteException branch:
            # release under step_lock, emit step_complete, then re-raise.
            with session.step_lock:
                session.generating = False
                session.generating_since = None
                SessionManager.add_event(name, {"type": "step_complete"})
            # A replacement worker reserves through either backend's
            # non-reserved path before the crash handler takes step_lock.
            if replacement_backend == "acp":
                assert session_step._start_acp_step_thread(name, session, tmp_path)
            else:
                assert session_step._start_step_thread(
                    name, session, "anthropic", tmp_path
                )
            assert second_started.wait(5)
            raise SessionCompleteException("budget exhausted")
        second_started.set()
        finish_second.wait(5)
        with session.step_lock:
            if session.step_seq == step_seq:
                session.generating = False
                session.generating_since = None
                SessionManager.add_event(name, {"type": "step_complete"})

    def excepthook(args):
        if isinstance(args.exc_value, SessionCompleteException):
            handler_done.set()

    async def fake_acp_step(*args, **kwargs):
        fake_step(*args, **kwargs)

    monkeypatch.setattr(session_step, "step", fake_step)
    monkeypatch.setattr(session_step, "_acp_step", fake_acp_step)
    monkeypatch.setattr(threading, "excepthook", excepthook)
    monkeypatch.setattr(
        SessionManager,
        "retry_deferred_watch_wakes",
        classmethod(lambda cls, conv_id: None),
    )
    first_event = session.events_count
    seen = set(threading.enumerate())
    workers: list[threading.Thread] = []
    try:
        assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
        workers.extend(_capture_new_threads(seen))
        assert handler_done.wait(5)
        # The second worker (spawned by the first, from inside fake_step) is
        # still running: it blocks on finish_second.
        workers.extend(_capture_new_threads(seen))

        with session.step_lock:
            # The second step's reservation survives the first thread's crash.
            assert session.generating
            assert session.last_error is None
            events, _ = session.read_events(first_event)
        types = [e["type"] for e in events if e["type"] in ("error", "step_complete")]
        assert types == ["step_complete"]
        # The two reservations hold distinct epochs.
        assert len(calls) == 2 and calls[0] != calls[1]
    finally:
        finish_second.set()
        # remove_session() does not join the workers; GPTME_STRICT_THREAD_LEAKS=1
        # would otherwise fail the test after its assertions.
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
        SessionManager.remove_session(session.id)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_step_thread_crash_empty_error_reports_failed(client, tmp_path, monkeypatch):
    """A bare ``RuntimeError()`` stringifies to "".

    The release path must fall back to a non-empty message so ``last_error``
    stays truthy (A2A reports FAILED, not COMPLETED/SUBMITTED) and webui SSE
    clients receive a non-empty error event.
    """
    from gptme.server import a2a_api

    name = f"test-step-crash-empty-{uuid4().hex}"
    result = client.put(
        f"/api/v2/conversations/{name}",
        json={
            "prompt": "Be helpful.",
            "config": {"chat": {"workspace": str(tmp_path)}},
        },
    )
    assert result.status_code == 200
    session = SessionManager.get_session(result.get_json()["session_id"])
    assert session is not None

    def crash(*args, **kwargs):
        raise RuntimeError  # bare: str(exc) == ""

    monkeypatch.setattr(session_step, "step", crash)
    woken: list[str] = []
    monkeypatch.setattr(
        SessionManager,
        "retry_deferred_watch_wakes",
        classmethod(lambda cls, conv_id: woken.append(conv_id)),
    )
    # Mark the conversation as A2A-created so _task_from_conversation can read it.
    a2a_api._write_origin_marker(name)
    first_event = session.events_count
    seen = set(threading.enumerate())
    workers: list[threading.Thread] = []
    try:
        assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
        workers.extend(_capture_new_threads(seen))
        deadline = time.monotonic() + 5
        while not woken and time.monotonic() < deadline:
            time.sleep(0.02)

        with session.step_lock:
            assert not session.generating
            assert session.last_error == "Generation failed"
            events, _ = session.read_events(first_event)
        error_events = [e for e in events if e.get("type") == "error"]
        assert error_events
        # read_events() is typed as a union of event TypedDicts; the error
        # event's payload is not expressible in that union.
        assert cast("dict[str, Any]", error_events[-1])["error"] == "Generation failed"

        task = a2a_api._task_from_conversation(name)
        assert task["status"]["state"] == "TASK_STATE_FAILED"
    finally:
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
        SessionManager.remove_session(session.id)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_crash_publishes_terminal_events_before_persistence(
    client, tmp_path, monkeypatch
):
    """Slow transcript storage must not hide the failure past an A2A deadline."""
    name = f"test-crash-slow-storage-{uuid4().hex}"
    result = client.put(
        f"/api/v2/conversations/{name}",
        json={"prompt": "test", "config": {"chat": {"workspace": str(tmp_path)}}},
    )
    session = SessionManager.get_session(result.get_json()["session_id"])
    assert session is not None
    persisting = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def crash(*args, **kwargs):
        raise ValueError("setup failed")

    def slow_persist(*args, **kwargs):
        persisting.set()
        assert release.wait(5)

    monkeypatch.setattr(session_step, "step", crash)
    monkeypatch.setattr(session_step, "_persist_generation_error", slow_persist)
    monkeypatch.setattr(threading, "excepthook", lambda args: finished.set())
    first_event = session.events_count
    seen = set(threading.enumerate())
    workers: list[threading.Thread] = []
    try:
        assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
        assert persisting.wait(5)
        workers.extend(_capture_new_threads(seen))
        events, _ = session.read_events(first_event)
        assert [
            e["type"] for e in events if e["type"] in ("error", "step_complete")
        ] == ["error", "step_complete"]
        assert not session.generating
    finally:
        release.set()
        assert finished.wait(5)
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
        SessionManager.remove_session(session.id)
