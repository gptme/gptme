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
* shell-background job completion wakes the originating idle server session
  and leaves delivery to the originating branch's STEP_PRE when blocked.
"""

import sys
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


@pytest.mark.skipif(sys.platform == "win32", reason="requires bash")
@pytest.mark.parametrize("state", ["idle", "busy", "interrupted", "disabled", "acp"])
def test_shell_background_completion_wake(
    state: str, logs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route a real shell completion to the branch that launched it."""
    from gptme.hooks import (
        HookType,
        current_conversation_id,
        current_session_id,
        trigger_hook,
    )
    from gptme.tools import ToolUse, init_tools, shell_background

    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    cid = f"matrix-shell-{state}"
    session = SessionManager.create_session(cid)
    observer = (
        SessionManager.create_session(cid) if state in {"acp", "interrupted"} else None
    )
    session.generating = state == "busy"
    session.interrupted = state == "interrupted"
    session.use_acp = state == "acp"
    started = unittest.mock.Mock(return_value=True)
    monkeypatch.setattr(session_step, "_start_step_thread", started)
    job = None
    try:
        with LogManager.preserve_current():
            main = LogManager.load(cid, create=True, lock=False)
            main.append(Message("user", "Run the shell job"))
            main.branch("feature-x")
            main.write()
            ChatConfig(
                _logdir=logs_dir / cid,
                model="local/test",
                workspace=logs_dir,
                watch_autowake=state != "disabled",
            ).save()
            origin = LogManager.load(cid, branch="feature-x", lock=False)
            init_tools(["shell"], include_mcp=False)
            conversation_token = current_conversation_id.set(cid)
            session_token = current_session_id.set(session.id)
            try:
                (tool_use,) = ToolUse.iter_from_content(
                    '@shell(call-1): {"command": "printf shell-%s result", "background": true}',
                    "tool",
                )
                replies = list(tool_use.execute(log=origin.log, workspace=logs_dir))
                assert any(
                    "Started background shell job" in msg.content for msg in replies
                )
                (job,) = shell_background.list_background_jobs()
            finally:
                current_session_id.reset(session_token)
                current_conversation_id.reset(conversation_token)

            assert job._reader_thread is not None
            job._reader_thread.join(timeout=10)
            assert not job._reader_thread.is_alive()
            assert job._completion_notified is True

            if state == "idle":
                assert started.call_count == 1, (
                    "idle shell completion must reserve a step"
                )
                assert session.generating is True
            else:
                started.assert_not_called()
            assert session.interrupted is (state == "interrupted")

            shell_background._notify_completion(job)
            assert started.call_count == (1 if state == "idle" else 0)
            assert not list(
                trigger_hook(
                    HookType.STEP_PRE, manager=LogManager.load(cid, lock=False)
                )
            )
            (completion,) = trigger_hook(HookType.STEP_PRE, manager=origin)
            assert "finished (exit code 0)" in completion.content
            assert "shell-result" in completion.content
            assert not list(trigger_hook(HookType.STEP_PRE, manager=origin))

            session.generating = False
            shell_background._notify_completion(job)
            assert not list(trigger_hook(HookType.STEP_PRE, manager=origin))
            assert started.call_count == (1 if state == "idle" else 0)
            if state == "idle":
                assert started.call_args.args[:3] == (cid, session, "local/test")
                assert started.call_args.kwargs["branch"] == "feature-x"
                assert started.call_args.kwargs["reserved"] is True
                assert started.call_args.kwargs["step_seq"] == session.step_seq == 1
    finally:
        SessionManager.remove_session(session.id)
        if observer:
            SessionManager.remove_session(observer.id)
        shell_background.reset_background_jobs(cid, all_conversations=False)
        if job is not None:
            job.kill()
            for pipe in (job.process.stdin, job.process.stdout, job.process.stderr):
                if pipe is not None:
                    pipe.close()
