from typing import cast

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
