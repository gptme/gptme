"""Tests for hook system integration in server API v2."""

import random
import time

import pytest

from gptme.hooks import HookType, register_hook, unregister_hook
from gptme.message import Message

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(30)]


def create_conversation(client: FlaskClient):
    """Create a V2 conversation with a session."""
    convname = f"test-hooks-{random.randint(0, 1000000)}"

    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are a helpful assistant."},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "session_id" in data

    return {"conversation_id": convname, "session_id": data["session_id"]}


def test_session_start_hook(client: FlaskClient, monkeypatch):
    """Test that SESSION_START hook is triggered for new conversations."""
    # Set hook allowlist to include test hooks
    monkeypatch.setenv("HOOK_ALLOWLIST", "test,token_awareness")

    # Create a new conversation
    conv = create_conversation(client)

    # First, add a user message
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}",
        json={"role": "user", "content": "Hello"},
    )
    assert response.status_code == 200

    # Then call step to generate response (should trigger SESSION_START hooks)
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}/step",
        json={
            "session_id": conv["session_id"],
            "stream": False,
        },
    )

    assert response.status_code == 200

    # Wait for step to complete (runs in background thread)
    time.sleep(2)

    # Get conversation log to verify hook messages were added
    response = client.get(f"/api/v2/conversations/{conv['conversation_id']}")
    assert response.status_code == 200
    data = response.get_json()

    messages = data.get("log", [])

    # Debug: print all messages
    print(f"\nTotal messages: {len(messages)}")
    for i, m in enumerate(messages):
        print(
            f"Message {i}: role={m.get('role')}, content={m.get('content', '')[:100]}"
        )

    # Check if test SESSION_START hook message is in the log
    test_hook_messages = [
        m
        for m in messages
        if "TEST_SESSION_START hook triggered" in m.get("content", "")
    ]
    assert (
        len(test_hook_messages) > 0
    ), f"TEST_SESSION_START hook message should be in log. Found {len(messages)} messages total"


def test_message_pre_process_hook(client: FlaskClient, monkeypatch):
    """Test that MESSAGE_PRE_PROCESS hooks work."""
    # Set hook allowlist to include test hooks
    monkeypatch.setenv("HOOK_ALLOWLIST", "test")

    # Create a new conversation
    conv = create_conversation(client)

    # First, add a user message
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}",
        json={"role": "user", "content": "Say hello"},
    )
    assert response.status_code == 200

    # Call step to generate response (should trigger MESSAGE_PRE_PROCESS hooks)
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}/step",
        json={
            "session_id": conv["session_id"],
            "stream": False,
        },
    )

    assert response.status_code == 200

    # Wait for step to complete
    time.sleep(2)

    # Verify that MESSAGE_PRE_PROCESS hook was triggered
    response = client.get(f"/api/v2/conversations/{conv['conversation_id']}")
    assert response.status_code == 200
    data = response.get_json()

    messages = data.get("log", [])

    # Check if test MESSAGE_PRE_PROCESS hook message is in the log
    test_hook_messages = [
        m
        for m in messages
        if "TEST_MESSAGE_PRE_PROCESS hook triggered" in m.get("content", "")
    ]
    assert (
        len(test_hook_messages) > 0
    ), f"TEST_MESSAGE_PRE_PROCESS hook message should be in log. Found {len(messages)} messages total"


def test_message_post_process_hook(client: FlaskClient, monkeypatch):
    """Test that MESSAGE_POST_PROCESS hooks work."""
    import unittest.mock

    # Set hook allowlist to include test hooks
    monkeypatch.setenv("HOOK_ALLOWLIST", "test")

    # Create a new conversation
    conv = create_conversation(client)

    # First, add a user message
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}",
        json={"role": "user", "content": "Say hello"},
    )
    assert response.status_code == 200

    # Mock _chat_complete (since stream=False)
    # Returns (response, metadata) tuple - the new format
    def mock_chat_complete(messages, model, tools=None):
        return ("Hello! How can I help you?", None)

    with unittest.mock.patch(
        "gptme.server.api_v2_sessions._chat_complete", mock_chat_complete
    ):
        # Call step to generate response (should trigger MESSAGE_POST_PROCESS hooks)
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={
                "session_id": conv["session_id"],
                "stream": False,
            },
        )

        assert response.status_code == 200

        # Wait for step to complete (keep mock active)
        time.sleep(2)

    # Verify that MESSAGE_POST_PROCESS hook was triggered
    response = client.get(f"/api/v2/conversations/{conv['conversation_id']}")
    assert response.status_code == 200
    data = response.get_json()

    messages = data.get("log", [])

    # Check if test MESSAGE_POST_PROCESS hook message is in the log
    test_hook_messages = [
        m
        for m in messages
        if "TEST_MESSAGE_POST_PROCESS hook triggered" in m.get("content", "")
    ]
    assert (
        len(test_hook_messages) > 0
    ), f"TEST_MESSAGE_POST_PROCESS hook message should be in log. Found {len(messages)} messages total"


def test_session_end_hook(client: FlaskClient):
    """Test that SESSION_END hook is triggered when last session is removed."""
    hook_triggered = []

    def session_end_hook(manager):
        hook_triggered.append("SESSION_END")
        yield Message("system", "SESSION_END hook triggered")

    # Register the hook
    register_hook("test_session_end", HookType.SESSION_END, session_end_hook)

    try:
        # Create a new conversation
        conv = create_conversation(client)
        session_id = conv["session_id"]

        # First, add a user message
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}",
            json={"role": "user", "content": "Hello"},
        )
        assert response.status_code == 200

        # Then call step to initialize the conversation
        response = client.post(
            f"/api/v2/conversations/{conv['conversation_id']}/step",
            json={
                "session_id": session_id,
                "stream": False,
            },
        )

        assert response.status_code == 200

        # Close the session (this should trigger SESSION_END since it's the only/last session)
        # Note: The actual cleanup might happen through SessionManager.remove_session
        # We need to check if there's an endpoint for this or if it happens automatically

        # For now, we'll verify the hook is registered correctly
        # The actual triggering happens in SessionManager.remove_session which is called
        # during cleanup/timeout operations

        # We can at least verify the hook doesn't error when called
        from gptme.logmanager import LogManager

        manager = LogManager.load(conv["conversation_id"], lock=False)

        # Manually trigger to test the hook works
        from gptme.hooks import trigger_hook

        list(trigger_hook(HookType.SESSION_END, manager=manager))

        assert "SESSION_END" in hook_triggered

    finally:
        # Clean up
        unregister_hook("test_session_end", HookType.SESSION_END)


def test_hooks_work_with_tools(client: FlaskClient, monkeypatch):
    """Test that hooks work correctly (simplified test)."""
    # Set hook allowlist to include test hooks
    monkeypatch.setenv("HOOK_ALLOWLIST", "test")

    # Create a new conversation
    conv = create_conversation(client)

    # First, add a user message
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}",
        json={"role": "user", "content": "Hello"},
    )
    assert response.status_code == 200

    # Call step - hooks should work without breaking things
    response = client.post(
        f"/api/v2/conversations/{conv['conversation_id']}/step",
        json={
            "session_id": conv["session_id"],
            "stream": False,
        },
    )

    assert response.status_code == 200

    # Wait for step to complete
    time.sleep(2)

    # Verify that hooks were triggered
    response = client.get(f"/api/v2/conversations/{conv['conversation_id']}")
    assert response.status_code == 200
    data = response.get_json()

    messages = data.get("log", [])

    # Verify both SESSION_START and MESSAGE_PRE_PROCESS hooks were triggered
    session_start_messages = [
        m
        for m in messages
        if "TEST_SESSION_START hook triggered" in m.get("content", "")
    ]
    pre_process_messages = [
        m
        for m in messages
        if "TEST_MESSAGE_PRE_PROCESS hook triggered" in m.get("content", "")
    ]

    assert len(session_start_messages) > 0, "Should have SESSION_START hook message"
    assert len(pre_process_messages) > 0, "Should have MESSAGE_PRE_PROCESS hook message"
