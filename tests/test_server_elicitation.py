"""Tests for the server-based elicitation hook."""

import threading
from importlib.util import find_spec

import pytest
import requests

from gptme.hooks.elicitation import ElicitationRequest, ElicitationResponse
from gptme.hooks.server_elicit import (
    PendingElicitation,
    get_pending,
    register_pending,
    remove_pending,
    resolve_pending,
)

_has_flask = find_spec("flask") is not None


class TestPendingElicitationRegistry:
    """Test the pending elicitation registry (register/resolve/remove)."""

    def test_register_and_get(self):
        request = ElicitationRequest(type="text", prompt="What is your name?")
        pending = register_pending("test-1", request)
        assert isinstance(pending, PendingElicitation)
        assert pending.request is request
        assert pending.result is None

        got = get_pending("test-1")
        assert got is pending

        # Cleanup
        remove_pending("test-1")

    def test_resolve_signals_event(self):
        request = ElicitationRequest(type="text", prompt="Enter value:")
        pending = register_pending("test-2", request)

        response = ElicitationResponse.text("hello")
        assert resolve_pending("test-2", response) is True
        assert pending.result is response
        assert pending.event.is_set()

        remove_pending("test-2")

    def test_resolve_nonexistent_returns_false(self):
        assert resolve_pending("nonexistent", ElicitationResponse.cancel()) is False

    def test_remove_cleans_up(self):
        request = ElicitationRequest(type="choice", prompt="Pick:", options=["a", "b"])
        register_pending("test-3", request)
        assert get_pending("test-3") is not None

        remove_pending("test-3")
        assert get_pending("test-3") is None

    def test_concurrent_register_resolve(self):
        """Test thread safety of register/resolve."""
        request = ElicitationRequest(type="text", prompt="Value?")
        pending = register_pending("test-concurrent", request)
        result = ElicitationResponse.text("concurrent-value")

        # Resolve from another thread
        def resolver():
            resolve_pending("test-concurrent", result)

        t = threading.Thread(target=resolver)
        t.start()
        pending.event.wait(timeout=5)
        t.join()

        assert pending.result is result
        remove_pending("test-concurrent")


class TestServerElicitHook:
    """Test the server_elicit_hook function behavior."""

    def test_returns_none_without_session_context(self):
        """Hook should return None (fall through) when not in server context."""
        from gptme.hooks.server_elicit import server_elicit_hook

        request = ElicitationRequest(type="text", prompt="Enter name:")
        result = server_elicit_hook(request)
        # Should fall through (no session context)
        assert result is None


@pytest.mark.skipif(not _has_flask, reason="flask not installed (server extra)")
class TestResolveHookElicitation:
    """Test the HTTP-to-hook resolution function."""

    def test_resolve_accept_with_value(self):
        from gptme.server.session_step import resolve_hook_elicitation

        request = ElicitationRequest(type="text", prompt="Enter value:")
        pending = register_pending("resolve-1", request)

        resolve_hook_elicitation("resolve-1", "accept", value="hello")
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.value == "hello"
        assert not pending.result.cancelled
        remove_pending("resolve-1")

    def test_resolve_accept_with_values(self):
        from gptme.server.session_step import resolve_hook_elicitation

        request = ElicitationRequest(
            type="multi_choice", prompt="Pick:", options=["a", "b", "c"]
        )
        pending = register_pending("resolve-2", request)

        resolve_hook_elicitation("resolve-2", "accept", values=["a", "c"])
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.values == ["a", "c"]
        remove_pending("resolve-2")

    def test_resolve_cancel(self):
        from gptme.server.session_step import resolve_hook_elicitation

        request = ElicitationRequest(type="text", prompt="Enter value:")
        pending = register_pending("resolve-3", request)

        resolve_hook_elicitation("resolve-3", "cancel")
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.cancelled is True
        remove_pending("resolve-3")

    def test_resolve_decline(self):
        from gptme.server.session_step import resolve_hook_elicitation

        request = ElicitationRequest(type="confirmation", prompt="Continue?")
        pending = register_pending("resolve-4", request)

        resolve_hook_elicitation("resolve-4", "decline")
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.value is None
        assert not pending.result.cancelled
        remove_pending("resolve-4")


@pytest.mark.skipif(not _has_flask, reason="flask not installed (server extra)")
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
@pytest.mark.parametrize("tool", ["choice", "form"])
def test_tools_through_server_hook(
    monkeypatch: pytest.MonkeyPatch, tool: str, action: str
) -> None:
    """Tools emit real server requests and consume the HTTP resolver's responses."""
    from types import SimpleNamespace

    from gptme.hooks.server_confirm import current_conversation_id, current_session_id
    from gptme.hooks.server_elicit import server_elicit_hook
    from gptme.server.api_v2_sessions import SessionManager
    from gptme.server.session_step import resolve_hook_elicitation
    from gptme.tools.choice import execute_choice
    from gptme.tools.form import execute_form

    hook = SimpleNamespace(name="server_elicit", enabled=True, func=server_elicit_hook)
    monkeypatch.setattr("gptme.hooks.get_hooks", lambda *a, **k: [hook])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    events = []
    answer = "Beta" if tool == "choice" else '{"count": 12, "ok": false}'

    def respond(conversation_id, event):
        assert conversation_id == "tool-contract"
        events.append(event)
        resolve_hook_elicitation(event["elicit_id"], action, value=answer)

    monkeypatch.setattr(SessionManager, "add_event", respond)
    conversation_token = current_conversation_id.set("tool-contract")
    session_token = current_session_id.set("tool-session")
    try:
        if tool == "choice":
            messages = list(execute_choice("Pick one?\nAlpha\nBeta", None, None))
            expected = (
                "User selected: Beta" if action == "accept" else "Selection cancelled"
            )
        else:
            messages = list(
                execute_form(
                    "count: Count? (number)\nok: Proceed? [yes/no]", None, None
                )
            )
            expected = (
                'Form submitted:\n```json\n{\n  "count": 12,\n  "ok": false\n}\n```'
                if action == "accept"
                else "Form cancelled"
            )
    finally:
        current_session_id.reset(session_token)
        current_conversation_id.reset(conversation_token)

    assert [message.content for message in messages] == [expected]
    (event,) = events
    assert event["type"] == "elicit_pending"
    assert event["elicit_type"] == tool
    assert get_pending(event["elicit_id"]) is None
    if tool == "choice":
        assert event["options"] == ["Alpha", "Beta"]
    else:
        assert [(f["name"], f["type"]) for f in event["fields"]] == [
            ("count", "number"),
            ("ok", "boolean"),
        ]
        assert event["fields"][1]["default"] is False


@pytest.mark.skipif(not _has_flask, reason="flask not installed (server extra)")
@pytest.mark.timeout(10)
def test_elicit_respond_endpoint_validation(init_, setup_conversation, auth_headers):
    """Test that the elicit respond endpoint validates required fields."""
    port, conversation_id, _ = setup_conversation

    # Missing elicit_id
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={"action": "accept", "value": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    # Missing action
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={"elicit_id": "test-123", "value": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    # Invalid action
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={"elicit_id": "test-123", "action": "invalid"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.skipif(not _has_flask, reason="flask not installed (server extra)")
@pytest.mark.timeout(10)
def test_elicit_respond_endpoint_accept(init_, setup_conversation, auth_headers):
    """Test that the elicit respond endpoint resolves a pending elicitation."""
    port, conversation_id, _ = setup_conversation

    # Register a pending elicitation
    request = ElicitationRequest(type="text", prompt="Enter value:")
    pending = register_pending("endpoint-test-1", request)

    # Respond via HTTP
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={
            "elicit_id": "endpoint-test-1",
            "action": "accept",
            "value": "test-value",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Verify the pending elicitation was resolved
    assert pending.event.is_set()
    assert pending.result is not None
    assert pending.result.value == "test-value"
    remove_pending("endpoint-test-1")
