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
* shell-background job completion wakes an idle server conversation through
  ``request_watch_wake`` (so it shares the watch policy above), stays queued
  for STEP_PRE while busy, and is retried at step end.
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


def _run_job(cid: str):
    """Start a real ``printf`` job for ``cid`` and wait for its completion."""
    from gptme.hooks import current_conversation_id
    from gptme.tools.shell_background import start_background_job

    token = current_conversation_id.set(cid)
    try:
        job = start_background_job("printf done")
    finally:
        current_conversation_id.reset(token)
    assert job._reader_thread is not None
    # The reader publishes the completion (and rings the doorbell) last.
    job._reader_thread.join(timeout=10)
    assert not job._reader_thread.is_alive()
    assert job._completion_notified is True
    return job


def _queued(job) -> bool:
    from gptme.tools import shell_background

    with shell_background._completion_queue.mutex:
        return any(q is job for q in shell_background._completion_queue.queue)


def _cleanup_job(cid: str, job) -> None:
    from gptme.tools import shell_background

    shell_background.reset_background_jobs(cid, all_conversations=False)
    if job is not None:
        # Join even a completed job's reader before closing its pipes.
        job.kill()
        for pipe in (job.process.stdin, job.process.stdout, job.process.stderr):
            if pipe is not None:
                pipe.close()


@pytest.mark.skipif(sys.platform == "win32", reason="uses printf")
def test_shell_background_completion_wakes_idle_conversation(logs_dir, armed) -> None:
    """Job exit rings the same doorbell as subagent completion, exactly once."""
    manager, started = armed
    cid = "matrix-shell-idle"
    session = SessionManager.create_session(cid)
    job = None
    try:
        job = _run_job(cid)
        started.assert_called_once()
        appended = [call.args[0].content for call in manager.append.call_args_list]
        assert any(
            f"Background shell job #{job.id} finished" in text for text in appended
        )
        # Claimed by the wake: the woken step's STEP_PRE must not deliver it again.
        assert not _queued(job)
        assert session.generating is True
    finally:
        SessionManager.remove_session(session.id)
        _cleanup_job(cid, job)


@pytest.mark.skipif(sys.platform == "win32", reason="uses printf")
def test_shell_background_completion_while_busy_waits_for_step_end(
    logs_dir, armed
) -> None:
    """Busy: stays queued for STEP_PRE; the step-end retry then wakes once."""
    manager, started = armed
    cid = "matrix-shell-busy"
    session = SessionManager.create_session(cid)
    session.generating = True
    job = None
    try:
        job = _run_job(cid)
        started.assert_not_called()
        manager.append.assert_not_called()
        assert _queued(job)

        session.generating = False
        SessionManager.retry_deferred_watch_wakes(cid)
        started.assert_called_once()
        assert not _queued(job)
    finally:
        SessionManager.remove_session(session.id)
        _cleanup_job(cid, job)


def test_shell_wake_requeues_when_rendering_fails(logs_dir, monkeypatch) -> None:
    """A claimed completion is put back when its message cannot be rendered."""
    from gptme.tools import shell_background as sb

    cid = "matrix-shell-render-fail"
    job = sb.BackgroundJob(
        0, "printf done", unittest.mock.Mock(), 0, conversation_id=cid
    )
    sb._jobs_for(cid)[job.id] = job
    with sb._job_lock, sb._completion_queue.mutex:
        sb._completion_queue.queue.append(job)
    monkeypatch.setattr(
        SessionManager,
        "get_sessions_for_conversation",
        unittest.mock.MagicMock(return_value=[object()]),
    )
    monkeypatch.setattr(
        sb,
        "_completion_message",
        unittest.mock.MagicMock(side_effect=RuntimeError("render failed")),
    )
    try:
        assert sb.wake_server_for_completed_jobs(cid) is False
        assert _queued(job)
    finally:
        sb.reset_background_jobs(cid, all_conversations=False)


@pytest.mark.skipif(sys.platform == "win32", reason="uses printf")
def test_shell_background_completion_without_server_session_stays_queued(
    logs_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No server session (CLI, thread subagent): no wake, STEP_PRE delivers it."""
    wake = unittest.mock.MagicMock(return_value=True)
    monkeypatch.setattr(SessionManager, "request_watch_wake", wake)
    cid = "matrix-shell-nosession"
    job = None
    try:
        job = _run_job(cid)
        wake.assert_not_called()
        assert _queued(job)
    finally:
        _cleanup_job(cid, job)


def _queue_branch_jobs(sb, cid: str):
    """Queue one completion on ``main`` and one on ``feature`` for ``cid``."""
    jobs = []
    for job_id, branch in enumerate(["main", "feature"]):
        job = sb.BackgroundJob(
            job_id,
            f"printf {branch}",
            unittest.mock.Mock(),
            0,
            conversation_id=cid,
            branch=branch,
        )
        sb._jobs_for(cid)[job.id] = job
        jobs.append(job)
    with sb._job_lock, sb._completion_queue.mutex:
        sb._completion_queue.queue.extend(jobs)
    return jobs


def _render_by_branch(job) -> Message:
    return Message("system", f"job #{job.id} on {job.branch}")


def test_server_step_pre_claims_only_its_branch(logs_dir, armed, monkeypatch) -> None:
    """A main step must not swallow a feature completion; the step-end retry
    then wakes the feature branch with it."""
    from gptme.tools import shell_background as sb

    cid = "matrix-shell-branches"
    monkeypatch.setattr(sb, "_completion_message", _render_by_branch)
    wake = unittest.mock.MagicMock(wraps=SessionManager.request_watch_wake)
    monkeypatch.setattr(SessionManager, "request_watch_wake", wake)
    session = SessionManager.create_session(cid)
    session.generating = True  # a main-branch step is running
    try:
        main_job, feature_job = _queue_branch_jobs(sb, cid)
        step_manager = unittest.mock.Mock(chat_id=cid, current_branch="main")
        delivered = [m.content for m in sb.background_job_completion_hook(step_manager)]
        assert delivered == ["job #0 on main"]
        assert not _queued(main_job)
        assert _queued(feature_job)

        session.generating = False
        SessionManager.retry_deferred_watch_wakes(cid)
        wake.assert_called_once()
        assert wake.call_args.kwargs["branch"] == "feature"
        assert "job #1 on feature" in wake.call_args.args[1].content
        assert not _queued(feature_job)
    finally:
        SessionManager.remove_session(session.id)
        sb.reset_background_jobs(cid, all_conversations=False)


def test_cli_step_pre_claims_every_branch(logs_dir, monkeypatch) -> None:
    """Without a server session nothing would wake the other branch later,
    so STEP_PRE keeps delivering every queued completion."""
    from gptme.tools import shell_background as sb

    cid = "matrix-shell-branches-cli"
    monkeypatch.setattr(sb, "_completion_message", _render_by_branch)
    try:
        main_job, feature_job = _queue_branch_jobs(sb, cid)
        step_manager = unittest.mock.Mock(chat_id=cid, current_branch="main")
        delivered = [m.content for m in sb.background_job_completion_hook(step_manager)]
        assert delivered == ["job #0 on main", "job #1 on feature"]
        assert not _queued(main_job) and not _queued(feature_job)
    finally:
        sb.reset_background_jobs(cid, all_conversations=False)


@pytest.mark.skipif(sys.platform == "win32", reason="uses printf")
def test_late_kill_notice_wakes_idle_conversation(logs_dir, armed) -> None:
    """A kill after completion delivery is a new event, not an idle queue leak."""
    from gptme.tools import shell_background as sb

    manager, started = armed
    cid = "matrix-shell-late-kill"
    session = SessionManager.create_session(cid)
    job = None
    try:
        job = _run_job(cid)
        started.assert_called_once()
        assert not _queued(job)
        # The completion's step finished before the memory guard killed leftovers.
        session.generating = False
        manager.append.reset_mock()
        started.reset_mock()

        sb._record_kill_reason(job, "Processes left over were killed.")

        started.assert_called_once()
        (message,) = [call.args[0] for call in manager.append.call_args_list]
        assert "Processes left over were killed." in message.content
        assert not _queued(job)
        assert session.generating is True
    finally:
        SessionManager.remove_session(session.id)
        _cleanup_job(cid, job)


@pytest.mark.skipif(sys.platform == "win32", reason="uses printf")
def test_kill_during_failed_wake_keeps_the_completion(
    logs_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill recorded while a wake holds the claimed completion must not
    replace that completion with only the late notice when the wake fails."""
    from gptme.tools import shell_background as sb

    cid = "matrix-shell-kill-window"
    job = None
    try:
        job = _run_job(cid)  # no server session yet: stays queued
        assert _queued(job)

        killed = False

        def kill_then_refuse(*args, **kwargs) -> bool:
            nonlocal killed
            # The guard kills once. Recording the notice rings a second wake;
            # that retry must not simulate another kill recursively.
            if not killed:
                killed = True
                sb._record_kill_reason(job, "Processes left over were killed.")
            return False

        monkeypatch.setattr(SessionManager, "request_watch_wake", kill_then_refuse)
        monkeypatch.setattr(
            SessionManager,
            "get_sessions_for_conversation",
            unittest.mock.MagicMock(return_value=[object()]),
        )
        assert sb.wake_server_for_completed_jobs(cid) is False

        step_manager = unittest.mock.Mock(chat_id=cid, current_branch="main")
        (message,) = list(sb.background_job_completion_hook(step_manager))
        assert f"Background shell job #{job.id} finished" in message.content
        assert "Processes left over were killed." in message.content
        assert not _queued(job)
    finally:
        _cleanup_job(cid, job)
