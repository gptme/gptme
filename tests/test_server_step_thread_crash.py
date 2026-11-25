"""A step thread that crashes before step() cleans up must release its reservation."""

import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("flask")

from gptme.server import session_step
from gptme.server.session_models import SessionManager


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_step_thread_crash_releases_generating(client, tmp_path, monkeypatch):
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
    woken: list[str] = []
    monkeypatch.setattr(
        SessionManager,
        "retry_deferred_watch_wakes",
        classmethod(lambda cls, conv_id: woken.append(conv_id)),
    )
    session.checkpoint_needs_continuation = True
    first_event = session.events_count
    try:
        assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
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
        # The conversation accepts new work again instead of answering 409.
        response = client.post(
            f"/api/v2/conversations/{name}",
            json={"role": "user", "content": "still there?"},
        )
        assert response.status_code == 200
    finally:
        SessionManager.remove_session(session.id)


def test_step_thread_raise_after_release_keeps_new_reservation(
    client, tmp_path, monkeypatch
):
    """step() can release, then re-raise (STEP_PRE SessionCompleteException).

    A non-reserved caller (A2A) that reserves in that gap must not have its
    reservation cleared by the first thread's crash handler, which would
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
            # A concurrent A2A request reserves through the non-reserved
            # path before the crash handler takes step_lock.
            assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
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

    monkeypatch.setattr(session_step, "step", fake_step)
    monkeypatch.setattr(threading, "excepthook", excepthook)
    monkeypatch.setattr(
        SessionManager,
        "retry_deferred_watch_wakes",
        classmethod(lambda cls, conv_id: None),
    )
    first_event = session.events_count
    try:
        assert session_step._start_step_thread(name, session, "anthropic", tmp_path)
        assert handler_done.wait(5)

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
        SessionManager.remove_session(session.id)
