"""Server completion signals must drain deferred wakes after releasing ownership."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gptme.config import ChatConfig
from gptme.hooks import HookType
from gptme.message import Message
from gptme.tools.complete import SessionCompleteException

pytest.importorskip("flask", reason="Server extras not installed")

from gptme.server import session_step  # fmt: skip
from gptme.server.session_models import ConversationSession, SessionManager  # fmt: skip


@pytest.mark.parametrize("backend", ["inprocess", "acp"])
@pytest.mark.parametrize("replaced", [False, True])
def test_step_pre_completion_retries_wakes_only_after_owned_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, replaced: bool
) -> None:
    session = ConversationSession(
        id="budget-session", conversation_id="budget-conversation", generating=True
    )
    session.acp_runtime = MagicMock()
    manager = MagicMock()
    manager.logdir = tmp_path
    manager.log.messages = [
        Message("user", "Do the task"),
        Message("assistant", "Working"),
    ]
    monkeypatch.setattr(session_step, "get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(
        ChatConfig, "load_or_create", lambda *args: ChatConfig(interactive=False)
    )
    monkeypatch.setattr(
        session_step, "prepare_execution_environment", lambda **kw: None
    )
    monkeypatch.setattr(session_step.LogManager, "load", lambda *args, **kw: manager)
    monkeypatch.setattr(session_step.CostTracker, "ensure_session", lambda *args: None)
    monkeypatch.setattr(session, "track_skill_turn", lambda *args: None)
    monkeypatch.chdir(tmp_path)

    post_hooks: list[HookType] = []

    def stop_at_step_pre(hook_type: HookType, **kwargs: object) -> list[Message]:
        if hook_type == HookType.TURN_POST:
            post_hooks.append(hook_type)
            return []
        if hook_type == HookType.STEP_PRE:
            if replaced:
                # Simulate interrupt + a replacement reservation during the hook.
                session.step_seq += 1
            raise SessionCompleteException("policy block budget exhausted: 6 blocks")
        return []

    monkeypatch.setattr(session_step, "trigger_hook", stop_at_step_pre)
    events: list[dict] = []
    monkeypatch.setattr(
        SessionManager, "add_event", lambda conversation_id, event: events.append(event)
    )
    retried: list[str] = []

    def retry_wakes(conversation_id: str) -> None:
        # A wake may take step_lock and reserve another generation. It must see
        # the old reservation released and its completion event already emitted.
        assert session.step_lock.acquire(blocking=False)
        try:
            assert not session.generating
            assert events == [{"type": "step_complete"}]
            retried.append(conversation_id)
        finally:
            session.step_lock.release()

    monkeypatch.setattr(SessionManager, "retry_deferred_watch_wakes", retry_wakes)
    with pytest.raises(SessionCompleteException, match="policy block budget exhausted"):
        if backend == "acp":
            asyncio.run(
                session_step._acp_step("budget-conversation", session, tmp_path)
            )
        else:
            session_step.step(
                "budget-conversation", session, "openai/gpt-4o-mini", tmp_path
            )

    if replaced:
        assert session.generating
        assert events == []
        assert retried == []
        assert post_hooks == []
        manager.write.assert_not_called()
    else:
        assert not session.generating
        assert session.generating_since is None
        assert events == [{"type": "step_complete"}]
        assert retried == ["budget-conversation"]
        assert post_hooks == [HookType.TURN_POST]


@pytest.mark.parametrize("backend", ["inprocess", "acp"])
@pytest.mark.parametrize("post_failure", [None, "load", "hook"])
def test_step_pre_completion_runs_turn_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    post_failure: str | None,
) -> None:
    """Budget STEP_PRE must still run TURN_POST so autocommit sees the last tools.

    Server TURN_POST fires after generation and before tools. The sixth policy
    block is appended during tool execution, so the next STEP_PRE raise is the
    first chance to observe those file changes.
    """
    session = ConversationSession(
        id="budget-session", conversation_id="budget-conversation", generating=True
    )
    session.acp_runtime = MagicMock()
    manager = MagicMock()
    manager.logdir = tmp_path
    manager.log.messages = [
        Message("user", "Do the task"),
        Message("assistant", "Working"),
    ]
    monkeypatch.setattr(session_step, "get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(
        ChatConfig, "load_or_create", lambda *args: ChatConfig(interactive=False)
    )
    monkeypatch.setattr(
        session_step, "prepare_execution_environment", lambda **kw: None
    )
    monkeypatch.setattr(session_step.LogManager, "load", lambda *args, **kw: manager)
    monkeypatch.setattr(session_step.CostTracker, "ensure_session", lambda *args: None)
    monkeypatch.setattr(session, "track_skill_turn", lambda *args: None)
    monkeypatch.chdir(tmp_path)

    # The completion hook must get the current log, not the STEP_PRE snapshot.
    fresh_manager = MagicMock()
    fresh_manager.current_branch = "work-branch"
    manager.current_branch = "work-branch"
    loads: list[str] = []

    def load_manager(conversation_id: str, **kwargs: object) -> MagicMock:
        loads.append(conversation_id)
        if len(loads) == 1:
            return manager
        assert kwargs["branch"] == "work-branch"
        if post_failure == "load":
            raise OSError("completion reload failed")
        return fresh_manager

    monkeypatch.setattr(session_step.LogManager, "load", load_manager)
    seen: list[HookType] = []

    def record_hooks(hook_type: HookType, **kwargs: object) -> list[Message]:
        seen.append(hook_type)
        if hook_type == HookType.STEP_PRE:
            raise SessionCompleteException("policy block budget exhausted: 6 blocks")
        if hook_type == HookType.TURN_POST:
            assert kwargs["manager"] is fresh_manager
            assert kwargs["notification_branch"] == "work-branch"
            # Arbitrary hooks must not run under the non-reentrant step lock.
            assert session.step_lock.acquire(blocking=False)
            session.step_lock.release()
            if post_failure == "hook":
                raise RuntimeError("post-turn hook failed")
            return [Message("system", "turn-post-ran", hide=True)]
        return []

    monkeypatch.setattr(session_step, "trigger_hook", record_hooks)
    posted: list[Message] = []
    monkeypatch.setattr(
        session_step,
        "_append_and_notify",
        lambda mgr, sess, msg: posted.append(msg),
    )
    monkeypatch.setattr(SessionManager, "add_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        SessionManager, "retry_deferred_watch_wakes", lambda conversation_id: None
    )
    with pytest.raises(SessionCompleteException, match="policy block budget exhausted"):
        if backend == "acp":
            asyncio.run(
                session_step._acp_step("budget-conversation", session, tmp_path)
            )
        else:
            session_step.step(
                "budget-conversation", session, "openai/gpt-4o-mini", tmp_path
            )

    assert len(loads) == 2
    assert not session.generating
    manager.write.assert_not_called()
    if post_failure:
        assert posted == []
        fresh_manager.write.assert_not_called()
    else:
        assert seen == [HookType.STEP_PRE, HookType.TURN_POST]
        assert [m.content for m in posted] == ["turn-post-ran"]
        fresh_manager.write.assert_called_once()
