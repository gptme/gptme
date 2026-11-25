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


def test_tool_chain_does_not_steal_a_newer_epoch_tool(v2_conv, monkeypatch):
    """An older worker must not chain to a tool a newer generation queued.

    If it did, it would re-tag the tool to its own epoch and the newer turn
    would reply before that tool's result was saved.
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
    session.interrupted = False
    session.generating = False

    def execute(code, args, kwargs):
        # The newer generation starts while this older tool runs.
        session.step_seq = 6
        return Message("system", "old tool output")

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

    old_tool = ToolExecution(
        tool_id="old-tool",
        tooluse=ToolUse("test_tool", [], None),
        auto_confirm=True,
        step_seq=5,
    )
    new_tool = ToolExecution(
        tool_id="new-tool",
        tooluse=ToolUse("test_tool", [], None),
        auto_confirm=True,
        step_seq=6,
    )
    session.pending_tools[old_tool.tool_id] = old_tool
    session.pending_tools[new_tool.tool_id] = new_tool

    thread = start_tool_execution(
        conversation_id,
        session,
        old_tool.tool_id,
        None,
        "openai/mock-model",
        ChatConfig(),
    )
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert session.step_seq == 6  # the fake tool really ran
    assert new_tool.tool_id in session.pending_tools
    assert session.pending_tools[new_tool.tool_id].step_seq == 6
    assert continuation.call_count == 0


def _skip_tool(client, v2_conv, tool_id: str, monkeypatch, dispatches: list[dict]):
    """POST the real skip route with continuation dispatch captured."""
    import gptme.server.api_v2_sessions as sessions_mod

    def fake_start_step_thread(*args, **kwargs):
        dispatches.append(kwargs)
        return True

    monkeypatch.setattr(sessions_mod, "_start_step_thread", fake_start_step_thread)
    return client.post(
        f"/api/v2/conversations/{v2_conv['conversation_id']}/tool/confirm",
        json={
            "session_id": v2_conv["session_id"],
            "tool_id": tool_id,
            "action": "skip",
        },
    )


def test_skip_does_not_reply_while_a_batch_sibling_is_pending(
    v2_conv, client, monkeypatch
):
    """Skipping one call must not answer the turn while a sibling still waits.

    The skip resolves a single tool call of the current assistant turn; it is
    not a new generation. The turn's tool phase is complete only once no tool
    of that epoch is pending or executing. Advancing the epoch here made a
    later-approved sibling start a second reply for the same turn.
    """
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.tools import ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.generating = False
    session.interrupted = False
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo skipped"), step_seq=5
    )
    session.pending_tools["keeper"] = ToolExecution(
        tool_id="keeper", tooluse=ToolUse("shell", [], "echo kept"), step_seq=5
    )

    dispatches: list[dict] = []
    response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, dispatches)

    assert response.status_code == 200
    assert "skip-me" not in session.pending_tools
    assert "keeper" in session.pending_tools
    assert session.step_seq == 5, "a skip is a tool decision, not a new epoch"
    assert dispatches == [], "the pending sibling still owns the turn"
    assert not session.generating


def test_skip_does_not_strand_an_executing_sibling_worker(v2_conv, client, monkeypatch):
    """A skip must leave a running same-epoch worker able to deliver its result.

    Re-tagging the batch to a new epoch (an earlier attempt) moved tools away
    from workers already dispatched for them: the last worker could not start a
    continuation (epoch mismatch) and its result never got a model turn.
    """
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.tools import ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.generating = False
    session.interrupted = False
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo skipped"), step_seq=5
    )
    session._executing_tools.add("running-sibling")
    session._executing_tool_epochs["running-sibling"] = 5
    try:
        dispatches: list[dict] = []
        response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, dispatches)

        assert response.status_code == 200
        assert session.step_seq == 5, "the running sibling still owns epoch 5"
        assert dispatches == [], "the running worker will elect the reply itself"
        assert session._executing_tool_epochs["running-sibling"] == 5
    finally:
        session._executing_tools.discard("running-sibling")
        session._executing_tool_epochs.pop("running-sibling", None)


def test_skip_advances_epoch_when_it_elects_the_reply(v2_conv, client, monkeypatch):
    """The skip that starts the turn's reply must revoke older workers.

    A worker can save its result and leave ``_executing_tool_epochs`` before it
    elects its continuation. If the skip elected under the same epoch, both it
    and that worker could start a reply for one turn. Advancing the epoch here
    (safe: no same-epoch tool remains) makes the stale worker's election fail.
    """
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.tools import ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.generating = False
    session.interrupted = False
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo skipped"), step_seq=5
    )

    dispatches: list[dict] = []
    response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, dispatches)

    assert response.status_code == 200
    assert session.step_seq == 6, "electing the reply is a generation boundary"
    assert len(dispatches) == 1
    assert dispatches[0]["step_seq"] == 6
    assert dispatches[0]["reserved"] is True
    assert response.get_json()["continuation_started"] is True


def test_skip_clears_interrupt_before_dispatching_the_reply(
    v2_conv, client, monkeypatch
):
    """A skip that elects the reply must not leave a stale interrupt flag.

    ``/rerun`` with only manual tools registers pending calls but does not
    clear ``interrupted``. Skipping the last one then reserved and dispatched
    ``step()``, whose entry guard returned before the release ``finally``, so
    the session stayed generating until the stuck sweep.
    """
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.tools import ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.generating = False
    session.interrupted = True
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo skipped"), step_seq=5
    )

    dispatches: list[dict] = []
    response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, dispatches)

    assert response.status_code == 200
    assert session.interrupted is False
    assert session.generating is True
    assert len(dispatches) == 1


def test_skip_reports_deferred_reply_and_remaining_approvals(
    v2_conv, client, monkeypatch
):
    """A deferred skip must tell the client no reply started and what remains.

    The page clears its approval card and marks the chat busy when it sends the
    skip. If the server defers the reply to a sibling, a bare ``Tool skipped``
    leaves the page waiting on a generation that has not started, with the next
    approval hidden.
    """
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.tools import ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.generating = False
    session.interrupted = False
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo skipped"), step_seq=5
    )
    session.pending_tools["approved"] = ToolExecution(
        tool_id="approved", tooluse=ToolUse("shell", [], "echo approved"), step_seq=5
    )
    session.remember_handled_tool("approved")
    session.pending_tools["automatic"] = ToolExecution(
        tool_id="automatic",
        tooluse=ToolUse("shell", [], "echo auto"),
        step_seq=5,
        auto_confirm=True,
    )
    session.pending_tools["keeper"] = ToolExecution(
        tool_id="keeper", tooluse=ToolUse("shell", [], "echo kept"), step_seq=5
    )

    dispatches: list[dict] = []
    response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, dispatches)

    body = response.get_json()
    assert response.status_code == 200
    assert dispatches == []
    assert body["continuation_started"] is False
    assert [tool["tool_id"] for tool in body["pending_tools"]] == ["keeper"]


def test_approving_a_sibling_after_a_skip_yields_one_reply(
    v2_conv, client, monkeypatch
):
    """The batch produces exactly one reply, and it includes the sibling result.

    End-to-end: skip one call, then approve the other. The approval worker is
    the last owner of the epoch, so it must start the turn's single
    continuation; the skip must not have started one first.
    """
    import unittest.mock

    from gptme.config import ChatConfig
    from gptme.message import Message
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution
    from gptme.tools.base import ToolSpec, ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    session.step_seq = 5
    session.generating = False
    session.interrupted = False
    session.pending_tools["skip-me"] = ToolExecution(
        tool_id="skip-me", tooluse=ToolUse("shell", [], "echo skipped"), step_seq=5
    )
    keeper = ToolExecution(
        tool_id="keeper", tooluse=ToolUse("shell", [], "echo kept"), step_seq=5
    )
    session.pending_tools["keeper"] = keeper

    skip_dispatches: list[dict] = []
    response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, skip_dispatches)
    assert response.status_code == 200
    assert skip_dispatches == []

    def execute(code, args, kwargs):
        return Message("system", "kept tool output")

    monkeypatch.setattr(
        "gptme.tools.get_tool",
        lambda name: ToolSpec(name="test_tool", desc="test", execute=execute),
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda hook_type, data: [])
    monkeypatch.setattr(
        "gptme.server.session_step.prepare_execution_environment", lambda **kw: None
    )
    import gptme.server.session_step as step_mod

    continuation = unittest.mock.Mock(return_value=True)
    monkeypatch.setattr(step_mod, "_start_step_thread", continuation)

    thread = start_tool_execution(
        v2_conv["conversation_id"],
        session,
        "keeper",
        None,
        "openai/mock-model",
        ChatConfig(),
    )
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert continuation.call_count == 1, "exactly one reply for the whole batch"
    assert continuation.call_args.kwargs["step_seq"] == 6
    assert session.pending_tools == {}
    assert session.step_seq == 6


def test_deferred_skip_snapshots_approvals_before_worker_can_claim(
    v2_conv, client, monkeypatch
):
    """A worker cannot mutate the approvals dict while skip serializes it."""
    import threading

    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.tools import ToolUse

    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    for tool_id in ("skip-me", "keeper"):
        session.pending_tools[tool_id] = ToolExecution(
            tool_id=tool_id, tooluse=ToolUse("shell", [], "echo test")
        )

    original = ToolExecution.to_pending_dict
    claims: list[bool] = []

    def serialize(tool):
        def claim():
            lock = SessionManager.conversation_lock(v2_conv["conversation_id"])
            acquired = lock.acquire(blocking=False)
            claims.append(acquired)
            if acquired:
                try:
                    session.pending_tools.pop(tool.tool_id)
                finally:
                    lock.release()

        worker = threading.Thread(target=claim)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        return original(tool)

    monkeypatch.setattr(ToolExecution, "to_pending_dict", serialize)
    response = _skip_tool(client, v2_conv, "skip-me", monkeypatch, [])
    assert response.status_code == 200
    # The deferred skip serializes twice now: once for its HTTP response and
    # once for the snapshot it publishes to every other viewer. Neither pass
    # may be claimable by a worker.
    assert claims and not any(claims), "a worker claimed during serialization"
    assert [t["tool_id"] for t in response.json["pending_tools"]] == ["keeper"]


@pytest.mark.parametrize("skip_while_running", [False, True])
def test_manual_rerun_after_interrupt_continues_once(
    v2_conv, client, monkeypatch, skip_while_running
):
    """Approving a rerun after interrupt must resume regardless of skip order."""
    import threading
    from unittest.mock import Mock

    from gptme.message import Message
    from gptme.server.session_models import SessionManager
    from gptme.server.session_step import start_tool_execution
    from gptme.tools.base import ToolSpec

    conversation_id = v2_conv["conversation_id"]
    session = SessionManager.get_session(v2_conv["session_id"])
    assert session is not None
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={
            "role": "assistant",
            "content": "```shell\necho first\n```\n```shell\necho second\n```",
        },
    )
    assert response.status_code == 200
    session.generating = True
    response = client.post(
        f"/api/v2/conversations/{conversation_id}/interrupt",
        json={"session_id": v2_conv["session_id"]},
    )
    assert response.status_code == 200
    assert session.interrupted
    interrupted_seq = session.step_seq
    response = client.post(
        f"/api/v2/conversations/{conversation_id}/rerun",
        json={"session_id": v2_conv["session_id"]},
    )
    assert response.status_code == 200
    first_id, second_id = response.json["tool_ids"]
    started = threading.Event()
    finish = threading.Event()
    workers = []

    def execute(code, args, kwargs):
        started.set()
        assert finish.wait(timeout=5)
        return Message("system", "rerun output")

    def start(*args, **kwargs):
        thread = start_tool_execution(*args, **kwargs)
        workers.append(thread)
        return thread

    monkeypatch.setattr(
        "gptme.tools.get_tool",
        lambda name: ToolSpec(name="test_tool", desc="test", execute=execute),
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda hook_type, data: [])
    monkeypatch.setattr(
        "gptme.server.session_step.prepare_execution_environment", lambda **kw: None
    )
    monkeypatch.setattr("gptme.server.api_v2_sessions.start_tool_execution", start)
    continuation = Mock(return_value=True)
    monkeypatch.setattr("gptme.server.session_step._start_step_thread", continuation)
    skip_dispatches: list[dict] = []
    try:
        if not skip_while_running:
            response = _skip_tool(
                client, v2_conv, second_id, monkeypatch, skip_dispatches
            )
            assert response.status_code == 200
            assert response.json["continuation_started"] is False
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/tool/confirm",
            json={
                "session_id": v2_conv["session_id"],
                "tool_id": first_id,
                "action": "confirm",
            },
        )
        assert response.status_code == 200
        assert started.wait(timeout=5)
        if skip_while_running:
            response = _skip_tool(
                client, v2_conv, second_id, monkeypatch, skip_dispatches
            )
            assert response.status_code == 200
            assert response.json["continuation_started"] is False
    finally:
        finish.set()
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive()

    assert skip_dispatches == []
    assert continuation.call_count == 1
    assert not session.interrupted
    assert session.step_seq == interrupted_seq + 2  # rerun, then reply
    assert not session.pending_tools
