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
from typing import cast

import pytest

pytest.importorskip("flask")

import gptme.server.session_step as session_step
from gptme.config import ChatConfig
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.server.api_v2_common import MessageAddedEvent
from gptme.server.session_models import SessionManager

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs)
    monkeypatch.setattr("gptme.logmanager.manager.get_logs_dir", lambda: logs)
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


def test_pending_branches_skips_non_objects(tmp_path: Path) -> None:
    (tmp_path / "prompt-queue.jsonl").write_text(
        'null\n[]\n42\n"text"\n{broken\n{"peer_sender":"peer", "branch":"feature"}\n',
        encoding="utf-8",
    )
    assert session_step._pending_peer_branches(tmp_path) == {"main", "feature"}


def test_scan_failure_does_not_starve_other_conversations(
    logs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["peer-scan-bad", "peer-scan-good"]
    for cid in ids:
        (_conversation(logs_dir, cid) / "prompt-queue.jsonl").touch()
    monkeypatch.setattr(SessionManager, "active_conversation_ids", lambda: ids)

    def scan(logdir: Path) -> set[str]:
        if logdir.name == ids[0]:
            raise UnicodeError("invalid queue encoding")
        return {"main"}

    monkeypatch.setattr(session_step, "_pending_peer_branches", scan)
    request = unittest.mock.MagicMock()
    monkeypatch.setattr(SessionManager, "request_peer_wake", request)
    session_step._run_peer_wake_check()
    request.assert_called_once_with(ids[1], branch="main")


def test_peer_sse_is_durable_and_precedes_dispatch(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, started = armed
    cid = "peer-sse"
    (_conversation(logs_dir, cid) / "prompt-queue.jsonl").touch()
    msg = Message("user", "peer input", metadata={"peer_sender": "source"})
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", lambda *a, **kw: [msg])
    session = SessionManager.create_session(cid)

    def dispatch(*args: object, **kwargs: object) -> bool:
        manager.write.assert_called_once_with(sync=True)
        event = session.events[0]
        assert event["type"] == "message_added"
        event = cast(MessageAddedEvent, event)
        assert event["message"]["content"] == "peer input"
        assert event["message"]["metadata"]["peer_sender"] == "source"
        return True

    started.side_effect = dispatch
    try:
        assert SessionManager.request_peer_wake(cid)
    finally:
        SessionManager.remove_session(session.id)


def _lock_probe(logdir: Path) -> bool:
    """A fresh process must use the production lifetime lock protocol."""
    script = """
import os, sys
from gptme.logmanager import LogManager
os.environ.pop("PYTEST_CURRENT_TEST", None)
try:
    with LogManager.load(sys.argv[1], lock=True):
        pass
except RuntimeError:
    sys.exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(logdir)], cwd=REPO_ROOT, check=False
    )
    assert result.returncode in (0, 23)
    return result.returncode == 0


def test_foreign_writer_refuses_without_drain(
    logs_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = "peer-foreign-owner"
    logdir = _conversation(logs_dir, cid)
    with LogManager.load(logdir, create=True, lock=False) as manager:
        manager.write(sync=True)
    (logdir / "prompt-queue.jsonl").write_text("unchanged", encoding="utf-8")
    ready = tmp_path / "ready"
    script = """
import os, sys
from pathlib import Path
from gptme.logmanager import LogManager
os.environ.pop("PYTEST_CURRENT_TEST", None)
with LogManager.load(sys.argv[1], lock=True):
    Path(sys.argv[2]).touch()
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(logdir), str(ready)],
        stdin=subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    import time

    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    session = SessionManager.create_session(cid)
    config = ChatConfig(_logdir=logdir, model="local/test", workspace=tmp_path)
    monkeypatch.setattr(ChatConfig, "load_or_create", lambda *a, **kw: config)
    drain = unittest.mock.MagicMock()
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)
    try:
        assert ready.exists()
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert not SessionManager.request_peer_wake(cid)
        drain.assert_not_called()
        assert (logdir / "prompt-queue.jsonl").read_text() == "unchanged"
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=10)
        SessionManager.remove_session(session.id)


def test_writer_lock_survives_async_worker_and_session_removal(
    logs_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from gptme.prompt_queue import queue_prompt

    cid = "peer-async-owner"
    logdir = _conversation(logs_dir, cid)
    with LogManager.load(logdir, create=True, lock=False) as manager:
        manager.write(sync=True)
    original = LogManager.get_current_log()
    queue_prompt(logdir, "hello", steer=True, peer_sender="source")
    config = ChatConfig(_logdir=logdir, model="local/test", workspace=tmp_path)
    monkeypatch.setattr(ChatConfig, "load_or_create", lambda *a, **kw: config)
    entered, finish, exited = threading.Event(), threading.Event(), threading.Event()
    session = SessionManager.create_session(cid)

    def fake_step(**kwargs: object) -> None:
        entered.set()
        assert finish.wait(10)
        exited.set()

    monkeypatch.setattr(session_step, "step", fake_step)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    try:
        assert SessionManager.request_peer_wake(cid)
        assert LogManager.get_current_log() is original
        assert entered.wait(10)
        assert not _lock_probe(logdir)
        SessionManager.remove_session(session.id)
        assert not _lock_probe(logdir)
        finish.set()
        assert exited.wait(10)
        import time

        deadline = time.monotonic() + 10
        while session.writer_manager is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session.writer_manager is None
        assert _lock_probe(logdir)
    finally:
        finish.set()
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("dispatch", [False, RuntimeError("cannot launch")])
def test_dispatch_failure_releases_writer(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch, dispatch: object
) -> None:
    manager, started = armed
    cid = "peer-launch-fail"
    (_conversation(logs_dir, cid) / "prompt-queue.jsonl").touch()
    monkeypatch.setattr(
        "gptme.prompt_queue.drain_peer_prompts",
        lambda *a, **kw: [Message("user", "hi")],
    )
    if isinstance(dispatch, Exception):
        started.side_effect = dispatch
    else:
        started.return_value = dispatch
    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid)
        assert session.writer_manager is None
        assert not session.generating
        assert session._writer_workers == 0
        assert session.last_error is not None
        assert "Start a new step" in session.last_error
        assert any(event["type"] == "error" for event in session.events)
        manager.append.assert_called_once()
        manager.__exit__.assert_called_once()
    finally:
        SessionManager.remove_session(session.id)


def test_tool_worker_pin_outlives_generation_and_session_removal(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Async tools keep ownership after the originating generation returns."""
    manager, _ = armed
    cid = "peer-tool-pin"
    (_conversation(logs_dir, cid) / "prompt-queue.jsonl").touch()
    monkeypatch.setattr(
        "gptme.prompt_queue.drain_peer_prompts",
        lambda *a, **kw: [Message("user", "hi")],
    )
    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid)
        generation, release_generation = session_step._pin_writer_worker(
            session, lambda: None
        )
        tool, release_tool = session_step._pin_writer_worker(session, lambda: None)
        generation()
        release_generation()  # rollback is safe after a target already exited
        assert session._writer_workers == 1
        # SESSION_END opens its own short-lived manager, not the pinned one.
        monkeypatch.setattr(LogManager, "load", unittest.mock.MagicMock())
        SessionManager.remove_session(session.id)
        manager.__exit__.assert_not_called()
        tool()
        release_tool()
        assert session._writer_workers == 0
        assert session.writer_manager is None
        manager.__exit__.assert_called_once()
    finally:
        SessionManager.remove_session(session.id)


def test_acp_peer_wake_defers_without_consuming(
    logs_dir: Path, armed, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, started = armed
    cid = "peer-acp-deferred"
    queue = _conversation(logs_dir, cid) / "prompt-queue.jsonl"
    queue.write_text("pending peer input", encoding="utf-8")
    drain = unittest.mock.MagicMock()
    monkeypatch.setattr("gptme.prompt_queue.drain_peer_prompts", drain)
    session = SessionManager.create_session(cid)
    session.use_acp = True
    session.acp_last_user_msg_index = 7
    try:
        assert not SessionManager.request_peer_wake(cid)
        drain.assert_not_called()
        started.assert_not_called()
        manager.append.assert_not_called()
        assert session.acp_last_user_msg_index == 7
        assert queue.read_text() == "pending peer input"
    finally:
        SessionManager.remove_session(session.id)


def test_removal_between_peer_reservation_and_dispatch_keeps_real_lock(
    logs_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove deterministically in the former zero-pin dispatch gap."""
    import threading

    from gptme.prompt_queue import queue_prompt

    cid = "peer-dispatch-gap"
    logdir = _conversation(logs_dir, cid)
    with LogManager.load(logdir, create=True, lock=False) as manager:
        manager.write(sync=True)
    queue_prompt(logdir, "hello", steer=True, peer_sender="source")
    config = ChatConfig(_logdir=logdir, model="local/test", workspace=tmp_path)
    monkeypatch.setattr(ChatConfig, "load_or_create", lambda *a, **kw: config)
    session = SessionManager.create_session(cid)
    entered, finish = threading.Event(), threading.Event()
    start = session_step._start_step_thread

    def remove_then_dispatch(*args: object, **kwargs: object) -> bool:
        assert session._writer_workers == 1
        SessionManager.remove_session(session.id)
        assert session.writer_manager is not None
        assert not _lock_probe(logdir)
        # Match the real peer dispatcher arguments; no mocked worker pinning.
        return start(
            cid,
            session,
            "local/test",
            tmp_path,
            reserved=True,
            step_seq=session.step_seq,
            inherit_context=False,
        )

    def fake_step(**kwargs: object) -> None:
        entered.set()
        assert finish.wait(10)

    monkeypatch.setattr(session_step, "_start_step_thread", remove_then_dispatch)
    monkeypatch.setattr(session_step, "step", fake_step)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    try:
        assert SessionManager.request_peer_wake(cid)
        assert entered.wait(10)
        assert session._writer_workers == 1
        assert not _lock_probe(logdir)
    finally:
        finish.set()
        import time

        deadline = time.monotonic() + 10
        while session.writer_manager is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        SessionManager.remove_session(session.id)
    assert session.writer_manager is None
    assert _lock_probe(logdir)


def test_real_skip_route_dispatch_does_not_reenter_step_lock(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import uuid

    from gptme.server.session_models import ToolExecution
    from gptme.tools import ToolUse

    cid = f"test-real-skip-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{cid}", json={"prompt": "Test skip dispatch"}
    )
    assert response.status_code == 200
    sid = response.get_json()["session_id"]
    session = SessionManager.get_session(sid)
    assert session is not None
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo hi")
    )
    generated = threading.Event()
    monkeypatch.setattr(session_step, "step", lambda **kwargs: generated.set())
    responses: list[int] = []
    errors: list[Exception] = []

    def request_skip() -> None:
        try:
            # Flask's fixture client preserves context in the main thread;
            # requests on a new thread need their own client/context stack.
            with client.application.test_client() as thread_client:
                result = thread_client.post(
                    f"/api/v2/conversations/{cid}/tool/confirm",
                    json={"session_id": sid, "tool_id": "skip-me", "action": "skip"},
                )
            responses.append(result.status_code)
        except Exception as error:
            errors.append(error)

    request_thread = threading.Thread(target=request_skip, daemon=True)
    request_thread.start()
    request_thread.join(timeout=5)
    assert not request_thread.is_alive(), "skip deadlocked while pinning the worker"
    assert not errors
    assert responses == [200]
    assert generated.wait(5)
    assert "skip-me" not in session.pending_tools
    SessionManager.remove_session(sid)


def test_failed_peer_dispatch_is_visible_without_requeue_or_duplicate(
    logs_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.prompt_queue import queue_prompt

    cid = "peer-failed-visible"
    logdir = _conversation(logs_dir, cid)
    with LogManager.load(logdir, create=True, lock=False) as manager:
        manager.write(sync=True)
    queue_prompt(logdir, "saved once", steer=True, peer_sender="source")
    config = ChatConfig(_logdir=logdir, model="local/test", workspace=tmp_path)
    monkeypatch.setattr(ChatConfig, "load_or_create", lambda *a, **kw: config)
    started = unittest.mock.MagicMock(side_effect=RuntimeError("cannot launch"))
    monkeypatch.setattr(session_step, "_start_step_thread", started)
    session = SessionManager.create_session(cid)
    try:
        assert SessionManager.request_peer_wake(cid)
        assert session.last_error is not None
        assert any(
            event["type"] == "error" and event.get("error") == session.last_error
            for event in session.events
        )
        assert not (logdir / "prompt-queue.jsonl").exists()
        assert not SessionManager.request_peer_wake(cid)
        started.assert_called_once()
        with LogManager.load(logdir, lock=False) as saved:
            assert [msg.content for msg in saved.log.messages] == ["saved once"]
    finally:
        SessionManager.remove_session(session.id)
