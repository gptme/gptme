import time
from pathlib import Path
from typing import Any, cast

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


def _send_message_request(text: str, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": "client-message-1",
            }
        },
    }


def test_a2a_agent_card(client):
    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    data = response.get_json()
    assert data["name"] == "gptme"
    assert data["supportedInterfaces"][0]["url"].endswith("/api/a2a")
    assert data["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert data["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert (
        data["securitySchemes"]["bearerAuth"]["httpAuthSecurityScheme"]["scheme"]
        == "Bearer"
    )
    assert data["skills"][0]["id"] == "gptme-terminal-agent"


def test_a2a_legacy_agent_card_alias(client):
    response = client.get("/.well-known/agent.json")

    assert response.status_code == 200
    assert response.get_json()["supportedInterfaces"][0]["url"].endswith("/api/a2a")


def test_a2a_send_message_and_get_task(client, monkeypatch, tmp_path):
    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.logmanager import LogManager
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def fake_prompt(**kwargs):
        return [Message("system", "test prompt")]

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
        new_user_turn=False,
    ):
        manager = LogManager.load(conversation_id, lock=False)
        msg = Message("assistant", "A2A response")
        manager.append(msg)
        # Emit generation_complete BEFORE clearing generating, matching production
        # order in session_step.py (event fires, then finally block clears flag).
        session_step_module.SessionManager.add_event(
            conversation_id,
            cast(
                EventType,
                {
                    "type": "generation_complete",
                    "message": {"role": "assistant", "content": "A2A response"},
                },
            ),
        )
        session.generating = False

    monkeypatch.setattr(a2a_api_module, "get_prompt", fake_prompt)
    monkeypatch.setattr(
        a2a_api_module,
        "_resolve_model",
        lambda chat_config: "openai/gpt-4o-mini",
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    response = client.post("/api/a2a", json=_send_message_request("hello from A2A"))

    assert response.status_code == 200
    data = response.get_json()
    task = data["result"]["task"]
    assert task["id"].startswith("a2a-")
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "A2A response"

    get_task_response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "GetTask",
            "params": {"id": task["id"], "historyLength": 1},
        },
    )

    assert get_task_response.status_code == 200
    fetched = get_task_response.get_json()["result"]["task"]
    assert fetched["id"] == task["id"]
    assert fetched["status"]["state"] == "TASK_STATE_COMPLETED"
    assert fetched["history"][0]["role"] == "ROLE_AGENT"


def test_a2a_failed_task_persists_after_session_gc(client, monkeypatch, tmp_path):
    """A failed task must still report FAILED via GetTask after session GC.

    The in-memory session holds ``last_error``; once it is garbage-collected,
    GetTask falls back to a ``a2a_failed.json`` sentinel written at failure time
    instead of inferring WORKING/COMPLETED.
    """
    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def fake_prompt(**kwargs):
        return [Message("system", "test prompt")]

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
        new_user_turn=False,
    ):
        # Simulate a step-thread crash: emit an error event, then stop.
        session_step_module.SessionManager.add_event(
            conversation_id,
            cast(EventType, {"type": "error", "error": "step thread crashed"}),
        )
        session.generating = False

    monkeypatch.setattr(a2a_api_module, "get_prompt", fake_prompt)
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    response = client.post("/api/a2a", json=_send_message_request("trigger failure"))

    assert response.status_code == 200
    error = response.get_json()["error"]
    assert error["code"] == -32603
    assert error["data"][0]["reason"] == "TASK_FAILED"
    task_id = error["data"][0]["metadata"]["taskId"]
    assert task_id.startswith("a2a-")
    assert (tmp_path / task_id / "a2a_failed.json").exists()

    # Simulate session GC: no live session remains for the conversation.
    monkeypatch.setattr(
        a2a_api_module.SessionManager,
        "get_sessions_for_conversation",
        staticmethod(lambda conversation_id: []),
    )

    get_task_response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )

    assert get_task_response.status_code == 200
    fetched = get_task_response.get_json()["result"]["task"]
    assert fetched["status"]["state"] == "TASK_STATE_FAILED"
    error_artifacts = [
        a for a in fetched.get("artifacts", []) if a["artifactId"] == "error"
    ]
    assert error_artifacts
    assert "step thread crashed" in error_artifacts[0]["parts"][0]["text"]


def test_a2a_reports_failure_released_between_read_and_idle_check(
    client, monkeypatch, tmp_path
):
    """A step failure published right after an empty event read is still reported."""
    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
    ):
        session.generating = True
        real_read_events = session.read_events

        def read_then_crash(abs_index):
            events, next_index = real_read_events(abs_index)
            if session.generating:
                # The step thread releases its reservation and publishes the
                # failure just after this (empty) read returns.
                with session.step_lock:
                    session.generating = False
                    session_step_module.SessionManager.add_event(
                        conversation_id,
                        cast(EventType, {"type": "error", "error": "early crash"}),
                    )
                    session_step_module.SessionManager.add_event(
                        conversation_id, cast(EventType, {"type": "step_complete"})
                    )
            return events, next_index

        session.read_events = read_then_crash

    monkeypatch.setattr(
        a2a_api_module, "get_prompt", lambda **kw: [Message("system", "test")]
    )
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    response = client.post("/api/a2a", json=_send_message_request("trigger failure"))

    assert response.status_code == 200
    error = response.get_json()["error"]
    assert error["data"][0]["reason"] == "TASK_FAILED"
    task_id = error["data"][0]["metadata"]["taskId"]
    assert (tmp_path / task_id / "a2a_failed.json").exists()


def test_a2a_final_drain_respects_blocking_timeout(client, monkeypatch, tmp_path):
    """A stalled step_lock holder cannot keep SendMessage past its timeout."""
    import threading
    import time

    import gptme.server.a2a_api as a2a_api_module
    from gptme.message import Message

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_A2A_BLOCKING_TIMEOUT", "0.5")
    holder_has_lock = threading.Event()
    release_holder = threading.Event()
    holders: list[threading.Thread] = []

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
    ):
        # The step is idle, but another holder (a deferred watch wake whose
        # disk write stalls) owns step_lock.
        session.generating = False

        def stalled_holder():
            with session.step_lock:
                holder_has_lock.set()
                release_holder.wait(5)

        holder = threading.Thread(target=stalled_holder)
        holders.append(holder)
        holder.start()
        assert holder_has_lock.wait(5)

    monkeypatch.setattr(
        a2a_api_module, "get_prompt", lambda **kw: [Message("system", "test")]
    )
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    try:
        started = time.monotonic()
        response = client.post("/api/a2a", json=_send_message_request("stall"))
        elapsed = time.monotonic() - started
    finally:
        release_holder.set()
        for holder in holders:
            holder.join(5)

    assert response.status_code == 200
    assert "error" not in response.get_json()
    assert elapsed < 3


def test_a2a_final_drain_timeout_still_reports_posted_error(
    client, monkeypatch, tmp_path
):
    """An error posted while a stalled holder owns step_lock is still reported."""
    import threading

    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_A2A_BLOCKING_TIMEOUT", "0.5")
    holder_has_lock = threading.Event()
    release_holder = threading.Event()
    holders: list[threading.Thread] = []

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
    ):
        session.generating = False
        real_read_events = session.read_events
        posted: list[bool] = []

        def read_then_post_error(abs_index):
            events, next_index = real_read_events(abs_index)
            if not posted:
                # The error lands just after the first (empty) read, while a
                # deferred wake still holds step_lock.
                posted.append(True)
                session_step_module.SessionManager.add_event(
                    conversation_id,
                    cast(EventType, {"type": "error", "error": "late failure"}),
                )
            return events, next_index

        session.read_events = read_then_post_error

        def stalled_holder():
            with session.step_lock:
                holder_has_lock.set()
                release_holder.wait(5)

        holder = threading.Thread(target=stalled_holder)
        holders.append(holder)
        holder.start()
        assert holder_has_lock.wait(5)

    monkeypatch.setattr(
        a2a_api_module, "get_prompt", lambda **kw: [Message("system", "test")]
    )
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    try:
        response = client.post("/api/a2a", json=_send_message_request("stall"))
    finally:
        release_holder.set()
        for holder in holders:
            holder.join(5)

    assert response.status_code == 200
    error = response.get_json()["error"]
    assert error["data"][0]["reason"] == "TASK_FAILED"
    task_id = error["data"][0]["metadata"]["taskId"]
    assert (tmp_path / task_id / "a2a_failed.json").exists()


def test_a2a_unlimited_blocking_timeout_does_not_fail_idle_step(
    client, monkeypatch, tmp_path
):
    """GPTME_A2A_BLOCKING_TIMEOUT=inf must not overflow the final lock wait."""
    import gptme.server.a2a_api as a2a_api_module
    from gptme.message import Message

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_A2A_BLOCKING_TIMEOUT", "inf")

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
    ):
        session.generating = False

    monkeypatch.setattr(
        a2a_api_module, "get_prompt", lambda **kw: [Message("system", "test")]
    )
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    response = client.post("/api/a2a", json=_send_message_request("idle"))

    assert response.status_code == 200
    assert "error" not in response.get_json()


def test_a2a_resume_clears_failure_sentinel(client, monkeypatch, tmp_path):
    """Resuming a failed task (SendMessage with its id) clears the sentinel."""
    import gptme.server.a2a_api as a2a_api_module

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    # Create a minimal task conversation with a failure sentinel in place.
    task_id = "a2a-resume-test"
    logdir = tmp_path / task_id
    logdir.mkdir(parents=True)
    a2a_api_module._write_failure_sentinel(task_id, RuntimeError("boom"))
    assert a2a_api_module._read_failure_sentinel(task_id) is not None

    a2a_api_module._clear_failure_sentinel(task_id)
    assert a2a_api_module._read_failure_sentinel(task_id) is None
    # Clearing a missing sentinel is a no-op, not an error.
    a2a_api_module._clear_failure_sentinel(task_id)


def test_a2a_get_task_not_found_returns_a2a_error(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "GetTask",
            "params": {"id": "a2a-missing-task"},
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["error"]["code"] == -32001
    assert data["error"]["data"][0]["reason"] == "TASK_NOT_FOUND"


def test_a2a_rejects_non_a2a_conversation(client, monkeypatch, tmp_path):
    """Task IDs are namespace-fenced: a conversation not created via A2A (no
    origin marker) must report TASK_NOT_FOUND for both GetTask and resume,
    instead of leaking arbitrary user conversations through the agent endpoint.
    """
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    # A conversation that exists on disk but was created by another surface
    # (webui/CLI): no a2a_origin.json marker.
    task_id = "a2a-foreign-conversation"
    (tmp_path / task_id).mkdir(parents=True)

    get_task_response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )
    assert get_task_response.status_code == 200
    get_error = get_task_response.get_json()["error"]
    assert get_error["code"] == -32001
    assert get_error["data"][0]["reason"] == "TASK_NOT_FOUND"

    resume_response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "resume foreign conversation"}],
                    "messageId": "client-message-1",
                    "taskId": task_id,
                }
            },
        },
    )
    assert resume_response.status_code == 200
    resume_error = resume_response.get_json()["error"]
    assert resume_error["code"] == -32001
    assert resume_error["data"][0]["reason"] == "TASK_NOT_FOUND"


def test_a2a_send_message_writes_origin_marker(client, monkeypatch, tmp_path):
    """A2A-created conversations get an origin marker so their task IDs stay
    reachable via GetTask/resume while foreign conversations do not."""
    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.logmanager import LogManager
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def fake_prompt(**kwargs):
        return [Message("system", "test prompt")]

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
        new_user_turn=False,
    ):
        manager = LogManager.load(conversation_id, lock=False)
        manager.append(Message("assistant", "A2A response"))
        session_step_module.SessionManager.add_event(
            conversation_id,
            cast(
                EventType,
                {
                    "type": "generation_complete",
                    "message": {"role": "assistant", "content": "A2A response"},
                },
            ),
        )
        session.generating = False

    monkeypatch.setattr(a2a_api_module, "get_prompt", fake_prompt)
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    response = client.post("/api/a2a", json=_send_message_request("hello"))
    task_id = response.get_json()["result"]["task"]["id"]
    assert (tmp_path / task_id / "a2a_origin.json").exists()


def test_a2a_unknown_method_returns_jsonrpc_error(client):
    response = client.post(
        "/api/a2a",
        json={"jsonrpc": "2.0", "id": 4, "method": "Nope", "params": {}},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["error"]["code"] == -32601


def test_a2a_malformed_json_returns_parse_error(client):
    response = client.post("/api/a2a", data="{", content_type="application/json")

    assert response.status_code == 200
    data = response.get_json()
    assert data["error"]["code"] == -32700


def test_a2a_rpc_requires_auth_on_network_binding(monkeypatch):
    from gptme.server.app import create_app
    from gptme.server.auth import init_auth, set_server_token

    app = create_app(host="0.0.0.0")
    set_server_token("a2a-test-token")
    init_auth(host="0.0.0.0", display=False)

    with app.test_client() as test_client:
        missing = test_client.post("/api/a2a", json=_send_message_request("hello"))
        ok = test_client.post(
            "/api/a2a",
            json={"jsonrpc": "2.0", "id": 5, "method": "Nope", "params": {}},
            headers={"Authorization": "Bearer a2a-test-token"},
        )

    assert missing.status_code == 401
    assert ok.status_code == 200
    assert ok.get_json()["error"]["code"] == -32601


def test_a2a_step_complete_on_first_read_returns_completed(
    client, monkeypatch, tmp_path
):
    """A step_complete seen on the initial read returns before the idle check."""
    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.logmanager import LogManager
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
    ):
        manager = LogManager.load(conversation_id, lock=False)
        manager.append(Message("assistant", "first-read response"))
        session_step_module.SessionManager.add_event(
            conversation_id, cast(EventType, {"type": "step_complete"})
        )
        session.generating = False

    monkeypatch.setattr(
        a2a_api_module, "get_prompt", lambda **kw: [Message("system", "test")]
    )
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    # SendMessage must consume the step_complete on its first read. If it
    # ignored it, the idle path would still return a completed task, so count
    # the reads instead of trusting the returned state.
    reads: list[int] = []
    original_get_sessions = a2a_api_module.SessionManager.get_sessions_for_conversation
    wrapped: list[bool] = []

    def counting_get_sessions(conversation_id):
        sessions = original_get_sessions(conversation_id)
        for session in sessions:
            if wrapped:
                break
            real_read_events = session.read_events
            wrapped.append(True)

            def read_events(abs_index, _real=real_read_events):
                reads.append(abs_index)
                return _real(abs_index)

            cast(Any, session).read_events = read_events
        return sessions

    monkeypatch.setattr(
        a2a_api_module.SessionManager,
        "get_sessions_for_conversation",
        counting_get_sessions,
    )

    response = client.post("/api/a2a", json=_send_message_request("first read"))

    assert response.status_code == 200
    data = response.get_json()
    assert "error" not in data
    task = data["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "first-read response"
    assert len(reads) == 1, reads


def test_a2a_final_drain_timeout_still_reports_posted_step_complete(
    client, monkeypatch, tmp_path, caplog
):
    """A step_complete posted while a stalled holder owns step_lock completes."""
    import threading

    caplog.set_level("INFO", logger="gptme.server.a2a_api")

    import gptme.server.a2a_api as a2a_api_module
    import gptme.server.session_step as session_step_module
    from gptme.logmanager import LogManager
    from gptme.message import Message
    from gptme.server.api_v2_common import EventType

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_A2A_BLOCKING_TIMEOUT", "0.5")
    holder_has_lock = threading.Event()
    release_holder = threading.Event()
    holders: list[threading.Thread] = []

    def fake_start_step_thread(
        conversation_id,
        session,
        model,
        workspace,
        branch="main",
        auto_confirm=False,
        stream=True,
    ):
        manager = LogManager.load(conversation_id, lock=False)
        manager.append(Message("assistant", "late response"))
        session.generating = False
        real_read_events = session.read_events
        posted: list[bool] = []

        def read_then_post_complete(abs_index):
            events, next_index = real_read_events(abs_index)
            if not posted:
                # step_complete lands just after the first (empty) read, while
                # a deferred wake still holds step_lock.
                posted.append(True)
                session_step_module.SessionManager.add_event(
                    conversation_id, cast(EventType, {"type": "step_complete"})
                )
            return events, next_index

        session.read_events = read_then_post_complete

        def stalled_holder():
            with session.step_lock:
                holder_has_lock.set()
                release_holder.wait(5)

        holder = threading.Thread(target=stalled_holder)
        holders.append(holder)
        holder.start()
        assert holder_has_lock.wait(5)

    monkeypatch.setattr(
        a2a_api_module, "get_prompt", lambda **kw: [Message("system", "test")]
    )
    monkeypatch.setattr(
        a2a_api_module, "_resolve_model", lambda chat_config: "openai/gpt-4o-mini"
    )
    monkeypatch.setattr(a2a_api_module, "_start_step_thread", fake_start_step_thread)

    try:
        response = client.post("/api/a2a", json=_send_message_request("stall"))
    finally:
        release_holder.set()
        for holder in holders:
            holder.join(5)

    assert response.status_code == 200
    data = response.get_json()
    assert "error" not in data
    task = data["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "late response"
    # A completed task must not also log the blocking timeout: if the final
    # read ignored step_complete the loop would fall through to it.
    assert "A2A SendMessage timed out" not in caplog.text


def test_a2a_resume_serializes_with_error_persistence(client, monkeypatch, tmp_path):
    """A resume cannot be overwritten by a crash handler's older log copy."""
    import threading
    from contextlib import contextmanager
    from uuid import uuid4

    from gptme.logmanager import LogManager
    from gptme.message import Message
    from gptme.server import a2a_api
    from gptme.server.session_models import SessionManager

    name = f"test-resume-lock-{uuid4().hex}"
    result = client.put(
        f"/api/v2/conversations/{name}",
        json={"prompt": "test", "config": {"chat": {"workspace": str(tmp_path)}}},
    )
    session = SessionManager.get_session(result.get_json()["session_id"])
    assert session is not None
    contended = threading.Event()
    loaded = threading.Event()
    errors: list[BaseException] = []
    real_load = LogManager.load
    real_lock = SessionManager.conversation_lock

    @contextmanager
    def observed_lock(conversation_id):
        lock = real_lock(conversation_id)
        if not lock.acquire(blocking=False):
            contended.set()
            lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def observed_load(*args, **kwargs):
        loaded.set()
        return real_load(*args, **kwargs)

    def resume():
        try:
            a2a_api._append_task_message(name, "follow-up must survive")
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=resume)
    try:
        with real_lock(name):
            # Simulate the crash handler's load/persist critical section.
            old = real_load(name, lock=False)
            monkeypatch.setattr(LogManager, "load", observed_load)
            monkeypatch.setattr(SessionManager, "conversation_lock", observed_lock)
            worker.start()
            # Prove the worker reached and contended on the actual lock;
            # a descheduled worker must not make this test pass vacuously.
            assert contended.wait(5), "resume never contended on conversation_lock"
            assert not loaded.is_set(), "resume loaded before acquiring the lock"
            old.append(Message("system", "Error: setup failed"))
            old.write()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
        messages = real_load(name, lock=False).log.messages
        assert any(m.content == "follow-up must survive" for m in messages)
        assert loaded.is_set()
    finally:
        worker.join(5)
        SessionManager.remove_session(session.id)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a2a_crash_failure_survives_gc_while_transcript_write_stalls(
    client, monkeypatch, tmp_path
):
    import threading

    from gptme.message import Message
    from gptme.server import a2a_api, session_step

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    monkeypatch.setenv("GPTME_A2A_BLOCKING_TIMEOUT", "0.2")
    monkeypatch.setattr(a2a_api, "get_prompt", lambda **kw: [Message("system", "test")])
    monkeypatch.setattr(a2a_api, "_resolve_model", lambda config: "anthropic")
    persisting = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    workers: list[threading.Thread] = []
    real_start = a2a_api._start_step_thread

    def crash(*args, **kwargs):
        raise ValueError("setup failed")

    def slow_persist(*args, **kwargs):
        persisting.set()
        assert release.wait(5)

    def start_and_wait(*args, **kwargs):
        seen = set(threading.enumerate())
        result = real_start(*args, **kwargs)
        assert persisting.wait(5)
        workers.extend(t for t in threading.enumerate() if t not in seen)
        return result

    monkeypatch.setattr(session_step, "step", crash)
    monkeypatch.setattr(session_step, "_persist_generation_error", slow_persist)
    monkeypatch.setattr(a2a_api, "_start_step_thread", start_and_wait)
    monkeypatch.setattr(threading, "excepthook", lambda args: finished.set())
    try:
        response = client.post("/api/a2a", json=_send_message_request("crash"))
        error = response.get_json()["error"]
        assert error["data"][0]["reason"] == "TASK_FAILED"
        task_id = error["data"][0]["metadata"]["taskId"]
        assert (tmp_path / task_id / "a2a_failed.json").exists()
        monkeypatch.setattr(
            a2a_api.SessionManager,
            "get_sessions_for_conversation",
            staticmethod(lambda conversation_id: []),
        )
        task = a2a_api._task_from_conversation(task_id)
        assert task["status"]["state"] == "TASK_STATE_FAILED"
    finally:
        release.set()
        assert finished.wait(5)
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
def _guarded_fake_step(session, seen: list[bool]):
    """Mirror step()'s entry guard: an interrupted step returns without releasing."""

    def fake_step(*args, **kwargs):
        seen.append(session.interrupted)
        with session.step_lock:
            if session.interrupted:
                return
            session.generating = False

    return fake_step


@pytest.fixture
def v2_conv(client):
    convname = f"test-a2a-interrupt-{time.monotonic_ns()}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are an AI assistant for testing."},
    )
    assert response.status_code == 200
    return {
        "conversation_id": convname,
        "session_id": response.get_json()["session_id"],
    }


def test_a2a_step_after_interrupt_releases_reservation(v2_conv, monkeypatch):
    """A new A2A request after an interrupt must not leave the session stuck generating.

    Exercises the real ``step()`` (only the provider call is stubbed) so the
    assertion covers the actual release path: a fake ``step()`` that clears
    ``generating`` itself would pass even if the worker left the session busy.
    """
    import gptme.server.session_step as step_mod
    from gptme.server import a2a_api
    from gptme.server.session_models import SessionManager

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.interrupted = True
    calls: list[int] = []

    def fake_chat_complete(*args, **kwargs):
        calls.append(1)
        return "A2A response", None

    monkeypatch.setattr(step_mod, "_chat_complete", fake_chat_complete)
    monkeypatch.setattr(a2a_api, "_resolve_model", lambda cfg: "openai/mock-model")
    monkeypatch.setenv("GPTME_A2A_BLOCKING_TIMEOUT", "10")

    a2a_api._run_task_blocking(v2_conv["conversation_id"], session)

    assert calls == [1]
    assert not session.generating
    assert not session.interrupted


def test_tool_continuation_reservation_keeps_interrupt(v2_conv, monkeypatch):
    """Only a new user turn clears an interrupt; a continuation must still stop."""
    import gptme.server.session_step as step_mod
    from gptme.server.session_models import SessionManager

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.interrupted = True
    seen: list[bool] = []
    monkeypatch.setattr(step_mod, "step", _guarded_fake_step(session, seen))

    assert step_mod._start_step_thread(
        conversation_id=v2_conv["conversation_id"],
        session=session,
        model="openai/mock-model",
        workspace=Path.cwd(),
    )
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)

    assert seen == [True]
    assert session.interrupted


def test_stale_tool_worker_does_not_start_continuation(v2_conv, monkeypatch):
    """A tool queued before an interrupt must not resume after a newer turn cleared it.

    The A2A turn clears ``session.interrupted``. A tool worker that was queued
    before the interrupt still holds the old epoch, so it must not elect a
    continuation on top of the newer turn: that would emit an unsolicited model
    turn after the client already saw TASK_STATE_COMPLETED (and make the session
    busy again).
    """
    import unittest.mock

    from gptme.config import ChatConfig
    from gptme.message import Message
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution
    from gptme.tools.base import ToolSpec, ToolUse

    conversation_id = v2_conv["conversation_id"]
    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.interrupted = False  # a newer A2A turn already cleared it
    session.generating = False

    def execute(code, args, kwargs):
        # The interrupt and the newer turn both land while this tool runs.
        session.step_seq += 1
        session.interrupted = False
        return Message("system", "late tool output")

    monkeypatch.setattr(
        "gptme.tools.get_tool",
        lambda name: ToolSpec(name="test_tool", desc="test", execute=execute),
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda hook_type, data: [])
    monkeypatch.setattr(
        "gptme.server.session_step.prepare_execution_environment", lambda **kw: None
    )
    continuation = unittest.mock.Mock()
    monkeypatch.setattr("gptme.server.session_step._start_step_thread", continuation)

    tool_exec = ToolExecution(
        tool_id="tool-stale", tooluse=ToolUse("test_tool", [], None)
    )
    session.pending_tools[tool_exec.tool_id] = tool_exec

    thread = start_tool_execution(
        conversation_id,
        session,
        tool_exec.tool_id,
        None,
        "openai/mock-model",
        ChatConfig(),
    )
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert session.step_seq == 6  # the fake tool really ran
    assert continuation.call_count == 0
    assert not session.generating


def test_rerun_continuation_ignores_stale_executing_tool(v2_conv, monkeypatch):
    """A rerun must not lose its reply because a tool from the previous turn still runs.

    Only outstanding tools from the current generation epoch block a reply. A
    stale worker cannot start one (``owns_epoch``), so waiting on it would drop
    the rerun's continuation entirely.
    """
    import unittest.mock

    from gptme.config import ChatConfig
    from gptme.message import Message
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution
    from gptme.tools.base import ToolSpec, ToolUse

    conversation_id = v2_conv["conversation_id"]
    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 6
    session.interrupted = False
    session.generating = True  # /rerun reserved this epoch
    session._executing_tools.add("old-tool")
    session._executing_tool_epochs["old-tool"] = 5

    def execute(code, args, kwargs):
        return Message("system", "rerun tool output")

    monkeypatch.setattr(
        "gptme.tools.get_tool",
        lambda name: ToolSpec(name="test_tool", desc="test", execute=execute),
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda hook_type, data: [])
    monkeypatch.setattr(
        "gptme.server.session_step.prepare_execution_environment", lambda **kw: None
    )
    continuation = unittest.mock.Mock()
    monkeypatch.setattr("gptme.server.session_step._start_step_thread", continuation)

    tool_exec = ToolExecution(
        tool_id="rerun-tool",
        tooluse=ToolUse("test_tool", [], None),
        step_seq=6,
    )
    session.pending_tools[tool_exec.tool_id] = tool_exec

    thread = start_tool_execution(
        conversation_id,
        session,
        tool_exec.tool_id,
        None,
        "openai/mock-model",
        ChatConfig(),
        reserved=True,
    )
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert continuation.call_count == 1
    kwargs = continuation.call_args.kwargs
    assert kwargs["reserved"] is True
    assert kwargs["step_seq"] == 7
