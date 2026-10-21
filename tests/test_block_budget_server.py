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
    session = ConversationSession(id="budget-session", generating=True)
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

    def stop_at_step_pre(hook_type: HookType, **kwargs: object) -> list[Message]:
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
    else:
        assert not session.generating
        assert session.generating_since is None
        assert events == [{"type": "step_complete"}]
        assert retried == ["budget-conversation"]
