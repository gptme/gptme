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
        from gptme.server.api_v2_sessions import _resolve_hook_elicitation

        request = ElicitationRequest(type="text", prompt="Enter value:")
        pending = register_pending("resolve-1", request)

        _resolve_hook_elicitation("resolve-1", "accept", value="hello")
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.value == "hello"
        assert not pending.result.cancelled
        remove_pending("resolve-1")

    def test_resolve_accept_with_values(self):
        from gptme.server.api_v2_sessions import _resolve_hook_elicitation

        request = ElicitationRequest(
            type="multi_choice", prompt="Pick:", options=["a", "b", "c"]
        )
        pending = register_pending("resolve-2", request)

        _resolve_hook_elicitation("resolve-2", "accept", values=["a", "c"])
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.values == ["a", "c"]
        remove_pending("resolve-2")

    def test_resolve_cancel(self):
        from gptme.server.api_v2_sessions import _resolve_hook_elicitation

        request = ElicitationRequest(type="text", prompt="Enter value:")
        pending = register_pending("resolve-3", request)

        _resolve_hook_elicitation("resolve-3", "cancel")
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.cancelled is True
        remove_pending("resolve-3")

    def test_resolve_decline(self):
        from gptme.server.api_v2_sessions import _resolve_hook_elicitation

        request = ElicitationRequest(type="confirmation", prompt="Continue?")
        pending = register_pending("resolve-4", request)

        _resolve_hook_elicitation("resolve-4", "decline")
        assert pending.event.is_set()
        assert pending.result is not None
        assert pending.result.value is None
        assert not pending.result.cancelled
        remove_pending("resolve-4")


@pytest.mark.skipif(not _has_flask, reason="flask not installed (server extra)")
@pytest.mark.timeout(10)
def test_elicit_respond_endpoint_validation(init_, setup_conversation):
    """Test that the elicit respond endpoint validates required fields."""
    port, conversation_id, _ = setup_conversation

    # Missing elicit_id
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={"action": "accept", "value": "hello"},
    )
    assert resp.status_code == 400

    # Missing action
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={"elicit_id": "test-123", "value": "hello"},
    )
    assert resp.status_code == 400

    # Invalid action
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/elicit/respond",
        json={"elicit_id": "test-123", "action": "invalid"},
    )
    assert resp.status_code == 400


@pytest.mark.skipif(not _has_flask, reason="flask not installed (server extra)")
@pytest.mark.timeout(10)
def test_elicit_respond_endpoint_accept(init_, setup_conversation):
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
    )
    assert resp.status_code == 200

    # Verify the pending elicitation was resolved
    assert pending.event.is_set()
    assert pending.result is not None
    assert pending.result.value == "test-value"
    remove_pending("endpoint-test-1")
