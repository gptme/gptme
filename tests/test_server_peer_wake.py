"""Server-side peer wake: idle sessions are woken for durable peer messages.

Peer messages arrive cross-process through the durable prompt queue. A busy
session drains them at STEP_PRE; an idle server session needs an explicit
wake. These tests cover the step reservation, the busy/queued path, metadata
preservation, branch scoping, and one real cross-process publication (a
separate interpreter writes the queue record).
"""

import subprocess
import sys
import unittest.mock
from pathlib import Path

import pytest

import gptme.server.session_step as session_step
from gptme.config import ChatConfig
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.server.session_models import SessionManager

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs)
    return logs


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Mock config/model resolution and the step dispatcher."""
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


def _conversation(logs_dir: Path, conversation_id: str) -> Path:
    logdir = logs_dir / conversation_id
    logdir.mkdir(parents=True, exist_ok=True)
    return logdir


def test_idle_session_reserves_and_appends(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, started = armed
    cid = "peer-wake-idle"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    peer_msg = Message(
        "user",
        "hi from peer",
        metadata={"peer_sender": "other-conv", "peer_message_id": "abc"},
    )
    drain = unittest.mock.MagicMock(return_value=[peer_msg])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid) is True
        manager.append.assert_called_once_with(peer_msg)
        manager.write.assert_called_once_with(sync=True)
        assert session.generating is True
        assert session.step_seq == 1
        started.assert_called_once()
        assert started.call_args.kwargs["reserved"] is True
        assert started.call_args.kwargs["step_seq"] == 1
        assert started.call_args.kwargs["inherit_context"] is False
        assert started.call_args.kwargs["branch"] == "main"
    finally:
        SessionManager.remove_session(session.id)


def test_busy_session_leaves_message_queued(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generating session must not consume the message: it stays durable."""
    manager, started = armed
    cid = "peer-wake-busy"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    drain = unittest.mock.MagicMock(return_value=[Message("user", "hi")])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    session = SessionManager.create_session(cid)
    session.generating = True
    try:
        assert SessionManager.request_peer_wake(cid) is False
        drain.assert_not_called()
        manager.append.assert_not_called()
        started.assert_not_called()
        assert session.step_seq == 0
    finally:
        SessionManager.remove_session(session.id)


def test_no_session_does_not_drain(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server only wakes conversations it owns a live session for."""
    manager, started = armed
    cid = "peer-wake-absent"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    drain = unittest.mock.MagicMock(return_value=[Message("user", "hi")])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    assert SessionManager.request_peer_wake(cid) is False
    drain.assert_not_called()
    started.assert_not_called()


def test_wake_is_branch_scoped(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, started = armed
    cid = "peer-wake-branch"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    drain = unittest.mock.MagicMock(return_value=[Message("user", "hi")])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid, branch="feat") is True
        assert drain.call_args.kwargs["branch"] == "feat"
        assert drain.call_args.args[0].name == cid
        assert started.call_args.kwargs["branch"] == "feat"
    finally:
        SessionManager.remove_session(session.id)


def test_real_cross_process_publication_wakes(logs_dir: Path, armed) -> None:
    """A separate interpreter publishes a real record; the server drains it.

    This exercises the actual durable queue (no patched drain) end to end.
    """
    manager, started = armed
    cid = "peer-wake-real"
    logdir = _conversation(logs_dir, cid)
    script = (
        "from pathlib import Path;"
        "from gptme.prompt_queue import queue_prompt;"
        f"queue_prompt(Path({str(logdir)!r}), 'real peer hello', steer=True,"
        " peer_sender='sender-conv', peer_sender_pid=4242,"
        " peer_message_id='msg-1', peer_sender_logdir='/tmp/sender')"
    )
    subprocess.run([sys.executable, "-c", script], check=True, cwd=REPO_ROOT)
    assert (logdir / "prompt-queue.jsonl").exists()

    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid) is True
        manager.append.assert_called_once()
        appended = manager.append.call_args.args[0]
        assert appended.content == "real peer hello"
        assert appended.metadata is not None
        assert appended.metadata["peer_sender"] == "sender-conv"
        assert appended.metadata["peer_message_id"] == "msg-1"
        assert appended.metadata["peer_sender_pid"] == 4242
        # Drained: the queue file is gone, so it cannot be delivered twice.
        assert not (logdir / "prompt-queue.jsonl").exists()
        started.assert_called_once()
    finally:
        SessionManager.remove_session(session.id)


def test_peer_wake_monitor_iterates_only_active(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poller touches only conversations with a live session."""
    cid = "peer-wake-poll"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    calls: list[tuple[str, str]] = []

    def fake_request(conversation_id: str, *, branch: str = "main") -> bool:
        calls.append((conversation_id, branch))
        return False

    monkeypatch.setattr(SessionManager, "request_peer_wake", fake_request)
    session = SessionManager.create_session(cid)
    try:
        session_step._run_peer_wake_check()
        assert calls == [(cid, "main")]
    finally:
        SessionManager.remove_session(session.id)
    # With no session, the poller no longer sees the conversation.
    calls.clear()
    session_step._run_peer_wake_check()
    assert calls == []


def test_interrupted_session_defers(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversation the user stopped must not be woken behind their back."""
    manager, started = armed
    cid = "peer-wake-interrupted"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    drain = unittest.mock.MagicMock(return_value=[Message("user", "hi")])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    session = SessionManager.create_session(cid)
    session.interrupted = True
    try:
        assert SessionManager.request_peer_wake(cid) is False
        drain.assert_not_called()
        assert session.interrupted is True  # not cleared by the wake path
        assert session.step_seq == 0
    finally:
        SessionManager.remove_session(session.id)


def test_autowake_opt_out_defers(
    logs_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """watch_autowake=False disables automatic peer wakes, like the TUI."""
    config = ChatConfig(
        _logdir=tmp_path, model="local/test", workspace=tmp_path, watch_autowake=False
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

    cid = "peer-wake-optout"
    logdir = _conversation(logs_dir, cid)
    (logdir / "prompt-queue.jsonl").touch()
    drain = unittest.mock.MagicMock(return_value=[Message("user", "hi")])
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)

    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid) is False
        drain.assert_not_called()
        started.assert_not_called()
    finally:
        SessionManager.remove_session(session.id)
