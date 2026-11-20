"""Conversation-scoped tool approval across clients, without provider calls."""

import json
import uuid
from unittest.mock import patch

import pytest

pytest.importorskip("flask")

from gptme.server.session_models import SessionManager, ToolExecution
from gptme.tools import ToolUse


@pytest.fixture
def approval(client):
    conversation_id = f"approval-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conversation_id}", json={"prompt": "Test"}
    )
    assert response.status_code == 200
    owner = SessionManager.get_session(response.json["session_id"])
    assert owner is not None
    viewer = SessionManager.create_session(conversation_id)
    tool_id = str(uuid.uuid4())
    owner.pending_tools[tool_id] = ToolExecution(
        tool_id=tool_id, tooluse=ToolUse("shell", [], "echo approved")
    )
    yield conversation_id, owner, viewer, tool_id
    owner.pending_tools.clear()
    owner._executing_tools.clear()


def test_viewer_confirms_owners_tool(client, approval):
    conversation_id, owner, viewer, tool_id = approval
    SessionManager.add_event(
        conversation_id,
        {"type": "tool_pending", **owner.pending_tools[tool_id].to_pending_dict()},
    )
    assert viewer.events[-1]["tool_id"] == tool_id
    with patch("gptme.server.api_v2_sessions.start_tool_execution") as start:
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/tool/confirm",
            json={"session_id": viewer.id, "tool_id": tool_id, "action": "confirm"},
        )
    assert response.status_code == 200
    assert start.call_args.args[1] is owner


def test_viewer_reconnect_sees_owners_pending_tool(client, approval):
    conversation_id, owner, viewer, tool_id = approval
    response = client.get(
        f"/api/v2/conversations/{conversation_id}/events?session_id={viewer.id}",
        buffered=False,
    )
    try:
        connected = json.loads(
            next(response.iter_encoded()).decode().removeprefix("data: ")
        )
    finally:
        response.close()
    assert connected["pending_tools"] == [
        owner.pending_tools[tool_id].to_pending_dict()
    ]


@pytest.mark.parametrize("with_session", [False, True])
def test_duplicate_executing_confirmation_is_success(client, approval, with_session):
    conversation_id, owner, viewer, tool_id = approval
    owner.pending_tools.pop(tool_id)
    owner._executing_tools.add(tool_id)
    body = {"tool_id": tool_id, "action": "confirm"}
    if with_session:
        body["session_id"] = viewer.id
    with patch("gptme.server.api_v2_sessions.start_tool_execution") as start:
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/tool/confirm", json=body
        )
    assert response.status_code == 200
    start.assert_not_called()


def test_other_conversation_cannot_resolve_tool(client, approval):
    conversation_id, owner, viewer, tool_id = approval
    other_id = f"other-{uuid.uuid4().hex[:8]}"
    client.put(f"/api/v2/conversations/{other_id}", json={"prompt": "Other"})
    response = client.post(
        f"/api/v2/conversations/{other_id}/tool/confirm",
        json={"tool_id": tool_id, "action": "confirm"},
    )
    assert response.status_code == 404
    response = client.post(
        f"/api/v2/conversations/{other_id}/tool/confirm",
        json={"session_id": viewer.id, "tool_id": tool_id, "action": "confirm"},
    )
    assert response.status_code == 403
    assert tool_id in owner.pending_tools


@pytest.mark.parametrize("action", ["confirm", "edit", "auto"])
def test_duplicate_requests_schedule_once(client, approval, action):
    conversation_id, owner, viewer, tool_id = approval
    body = {
        "session_id": viewer.id,
        "tool_id": tool_id,
        "action": action,
        "content": "echo edited",
        "count": 2,
    }
    with patch("gptme.server.api_v2_sessions.start_tool_execution") as start:
        for _ in range(2):
            response = client.post(
                f"/api/v2/conversations/{conversation_id}/tool/confirm", json=body
            )
            assert response.status_code == 200
        assert start.call_count == 1
        # A response delayed until after execution must still be idempotent.
        owner.pending_tools.pop(tool_id)
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/tool/confirm", json=body
        )
        assert response.status_code == 200
        assert start.call_count == 1
    if action == "auto":
        assert owner.auto_confirm_count == 2
        assert viewer.auto_confirm_count == 0


def test_dispatch_failure_leaves_approval_retryable(client, approval):
    conversation_id, owner, viewer, tool_id = approval
    body = {"session_id": viewer.id, "tool_id": tool_id, "action": "confirm"}
    with patch(
        "gptme.server.api_v2_sessions.start_tool_execution",
        side_effect=RuntimeError("dispatch"),
    ):
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/tool/confirm", json=body
        )
        assert response.status_code == 500
    assert tool_id not in owner.handled_tools
    assert tool_id in owner.pending_tools


@pytest.mark.parametrize("auto_confirm", [False, True])
def test_real_worker_executes_once_with_duplicate_and_reconnect(
    client, approval, auto_confirm
):
    import threading

    from gptme.config import ChatConfig
    from gptme.message import Message
    from gptme.server.session_step import start_tool_execution

    conversation_id, owner, viewer, tool_id = approval
    execution_started = threading.Event()
    allow_finish = threading.Event()
    calls = []
    threads = []

    def execute(*args, **kwargs):
        calls.append(True)
        execution_started.set()
        assert allow_finish.wait(5)
        return [Message("system", "approved result")]

    def dispatch(*args, **kwargs):
        thread = start_tool_execution(*args, **kwargs)
        threads.append(thread)
        return thread

    body = {"session_id": viewer.id, "tool_id": tool_id, "action": "confirm"}
    with (
        patch("gptme.server.session_step.prepare_execution_environment"),
        patch("gptme.tools.ToolUse.execute", side_effect=execute),
        patch(
            "gptme.server.session_step._start_step_thread", return_value=True
        ) as continuation,
        patch(
            "gptme.server.api_v2_sessions.start_tool_execution", side_effect=dispatch
        ),
    ):
        try:
            if auto_confirm:
                owner.pending_tools[tool_id].auto_confirm = True
                dispatch(
                    conversation_id, owner, tool_id, None, "local/test", ChatConfig()
                )
            else:
                assert (
                    client.post(
                        f"/api/v2/conversations/{conversation_id}/tool/confirm",
                        json=body,
                    ).status_code
                    == 200
                )
            assert execution_started.wait(5)
            assert (
                client.post(
                    f"/api/v2/conversations/{conversation_id}/tool/confirm", json=body
                ).status_code
                == 200
            )
        finally:
            allow_finish.set()
            for thread in threads:
                thread.join(5)
        assert all(not thread.is_alive() for thread in threads)
        assert calls == [True]
        assert continuation.call_count == 1
        assert (
            client.post(
                f"/api/v2/conversations/{conversation_id}/tool/confirm", json=body
            ).status_code
            == 200
        )
        assert len(threads) == 1
    assert tool_id not in owner.pending_tools
    assert tool_id in owner.handled_tools
    assert any(
        e["type"] == "tool_complete" and e["tool_id"] == tool_id for e in viewer.events
    )
    response = client.get(
        f"/api/v2/conversations/{conversation_id}/events?session_id={viewer.id}",
        buffered=False,
    )
    try:
        connected = json.loads(
            next(response.iter_encoded()).decode().removeprefix("data: ")
        )
    finally:
        response.close()
    assert connected["pending_tools"] == []


def test_reconnect_does_not_lose_execution_during_connected_snapshot(client, approval):
    conversation_id, owner, viewer, tool_id = approval
    response = client.get(
        f"/api/v2/conversations/{conversation_id}/events?session_id={viewer.id}",
        buffered=False,
    )
    try:
        stream = response.iter_encoded()
        connected = json.loads(next(stream).decode().removeprefix("data: "))
        assert connected["pending_tools"][0]["tool_id"] == tool_id
        SessionManager.add_event(
            conversation_id, {"type": "tool_executing", "tool_id": tool_id}
        )
        assert b"ping" in next(stream)
        event = json.loads(next(stream).decode().removeprefix("data: "))
        assert event == {"type": "tool_executing", "tool_id": tool_id}
    finally:
        response.close()


def test_worker_startup_failure_releases_approval_receipt(client, approval):
    from gptme.config import ChatConfig
    from gptme.server.session_step import start_tool_execution

    conversation_id, owner, viewer, tool_id = approval
    owner.remember_handled_tool(tool_id)
    # Run the thread body synchronously to inspect the expected startup error
    # without leaking an unhandled-thread warning into the suite.
    with (
        patch(
            "gptme.server.session_step.prepare_execution_environment",
            side_effect=RuntimeError("startup"),
        ),
        patch(
            "gptme.server.session_step.threading.Thread.start",
            lambda thread: thread.run(),
        ),
        pytest.raises(RuntimeError, match="startup"),
    ):
        start_tool_execution(
            conversation_id, owner, tool_id, None, "local/test", ChatConfig()
        )
    assert tool_id in owner.pending_tools
    assert tool_id not in owner.handled_tools
