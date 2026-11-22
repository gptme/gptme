"""Wake-admission matrix for server conversations (research #5060).

Characterization tests, not a policy statement: they run the *real*
``SessionManager`` admission code (only config/model resolution and the step
dispatcher are mocked) and record how each event kind behaves when the
conversation is idle, busy, user-interrupted or ACP-owned. The design note
``docs/design/conversation-workers.md`` (section 5) cites these results; if a
test here changes, the note's wake table needs to change with it.

Findings pinned here:

* peer wake and watch wake share busy deferral and ACP deferral;
* they diverge on user-interrupted conversations: peer wake leaves the
  conversation stopped, watch wake (and therefore subagent completion) restarts
  it and clears ``interrupted``;
* shell-background job completion neither requests a wake nor signals
  ``notification_ready``: an idle server conversation is not woken by it.
"""

import sys
import time
import unittest.mock
from pathlib import Path

import pytest

pytest.importorskip("flask")

import gptme.server.session_step as session_step
from gptme.config import ChatConfig
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.server.session_models import SessionManager


@pytest.fixture
def logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs)
    monkeypatch.setattr("gptme.logmanager.manager.get_logs_dir", lambda: logs)
    return logs


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config = ChatConfig(
        _logdir=tmp_path,
        model="local/test",
        workspace=tmp_path,
        watch_autowake=True,
    )
    manager = unittest.mock.MagicMock()
    started = unittest.mock.MagicMock(return_value=True)
    monkeypatch.setattr(
        ChatConfig, "load_or_create", unittest.mock.MagicMock(return_value=config)
    )
    monkeypatch.setattr(
        LogManager, "load", unittest.mock.MagicMock(return_value=manager)
    )
    monkeypatch.setattr(session_step, "_start_step_thread", started)
    return manager, started


def _peer(monkeypatch: pytest.MonkeyPatch, logs_dir: Path, cid: str):
    logdir = logs_dir / cid
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / "prompt-queue.jsonl").touch()
    drain = unittest.mock.MagicMock(return_value=[Message("user", "peer mail")])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    def wake() -> bool:
        return SessionManager.request_peer_wake(cid)

    return wake


def _watch(cid: str):
    def wake() -> bool:
        return SessionManager.request_watch_wake(cid, Message("system", "child done"))

    return wake


def _make_waker(kind: str, monkeypatch, logs_dir, cid):
    return _peer(monkeypatch, logs_dir, cid) if kind == "peer" else _watch(cid)


@pytest.mark.parametrize("kind", ["peer", "watch"])
def test_idle_wakes_once(kind, logs_dir, armed, monkeypatch) -> None:
    _, started = armed
    cid = f"matrix-idle-{kind}"
    wake = _make_waker(kind, monkeypatch, logs_dir, cid)
    session = SessionManager.create_session(cid)
    try:
        assert wake() is True
        assert session.generating is True
        assert started.call_count == 1
        # Single-writer reservation: a second doorbell while the reserved step
        # runs is a busy deferral, not a second step.
        assert wake() is False
        assert started.call_count == 1
    finally:
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("kind", ["peer", "watch"])
def test_busy_defers_without_starting(kind, logs_dir, armed, monkeypatch) -> None:
    manager, started = armed
    cid = f"matrix-busy-{kind}"
    wake = _make_waker(kind, monkeypatch, logs_dir, cid)
    session = SessionManager.create_session(cid)
    session.generating = True
    try:
        assert wake() is False
        started.assert_not_called()
        manager.append.assert_not_called()
    finally:
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("kind", ["peer", "watch"])
def test_acp_owned_conversation_is_not_woken(
    kind, logs_dir, armed, monkeypatch
) -> None:
    manager, started = armed
    cid = f"matrix-acp-{kind}"
    wake = _make_waker(kind, monkeypatch, logs_dir, cid)
    session = SessionManager.create_session(cid)
    session.use_acp = True
    try:
        assert wake() is False
        started.assert_not_called()
        manager.append.assert_not_called()
    finally:
        SessionManager.remove_session(session.id)


def test_interrupted_peer_wake_leaves_conversation_stopped(
    logs_dir, armed, monkeypatch
) -> None:
    manager, started = armed
    cid = "matrix-interrupted-peer"
    wake = _peer(monkeypatch, logs_dir, cid)
    session = SessionManager.create_session(cid)
    session.interrupted = True
    try:
        assert wake() is False
        started.assert_not_called()
        assert session.interrupted is True
    finally:
        SessionManager.remove_session(session.id)


def test_interrupted_watch_wake_restarts_conversation_today(
    logs_dir, armed, monkeypatch
) -> None:
    """DIVERGENCE: watch/subagent-completion wake ignores user-interrupt.

    ``request_peer_wake`` refuses a user-stopped conversation; ``request_watch_wake``
    does not check ``interrupted`` and clears it. Whether that is intended
    (a finished child is information the user asked for) or drift is a
    maintainer decision; this test pins the current behaviour so a change is
    deliberate. See docs/design/conversation-workers.md section 5.
    """
    _, started = armed
    cid = "matrix-interrupted-watch"
    wake = _watch(cid)
    session = SessionManager.create_session(cid)
    session.interrupted = True
    try:
        assert wake() is True
        started.assert_called_once()
        assert session.interrupted is False
    finally:
        SessionManager.remove_session(session.id)


@pytest.mark.skipif(sys.platform == "win32", reason="uses printf")
def test_shell_background_completion_has_no_idle_wake(
    logs_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job exit enqueues for a later step but never rings either doorbell."""
    from gptme.hooks import current_conversation_id
    from gptme.tools import shell_background
    from gptme.tools.shell_background import start_background_job

    wake = unittest.mock.MagicMock(return_value=True)
    monkeypatch.setattr(SessionManager, "request_watch_wake", wake)
    signal = unittest.mock.MagicMock()
    monkeypatch.setattr("gptme.util.notification_ready.signal", signal)

    token = current_conversation_id.set("matrix-shell-job")
    try:
        job = start_background_job("printf done")
        assert job._reader_thread is not None
        job._reader_thread.join(timeout=10)
        assert not job._reader_thread.is_alive()
    finally:
        current_conversation_id.reset(token)

    try:
        deadline = time.monotonic() + 5
        while not job._completion_notified and time.monotonic() < deadline:
            time.sleep(0.01)
        # The completion is queued for a step that has not been started...
        assert job._completion_notified is True
        assert any(q is job for q in shell_background._completion_queue.queue)
        # ...and nothing asked the server to start one.
        wake.assert_not_called()
        signal.assert_not_called()
    finally:
        shell_background._purge_completion_queue({"matrix-shell-job"})
