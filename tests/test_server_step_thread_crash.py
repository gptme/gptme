"""A step thread that crashes before step() cleans up must release its reservation."""

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
