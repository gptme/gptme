"""Tests for the Server-Sent Events (SSE) stream functionality in the V2 API."""

import pytest
import requests

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


@pytest.mark.timeout(20)
def test_event_stream(event_listener, wait_for_event):
    """Test the event stream endpoint."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]

    # Send a message to trigger an event
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        headers={"Authorization": "Bearer test-token-for-server-thread"},
        json={"role": "user", "content": "Test message"},
    )

    # Wait for events
    assert wait_for_event(event_listener, "connected")
    assert wait_for_event(event_listener, "message_added")

    # Verify message content
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        headers={"Authorization": "Bearer test-token-for-server-thread"},
    )
    assert resp.status_code == 200
    messages = resp.json()["log"]

    # Find the user message
    user_messages = [m for m in messages if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "Test message"


@pytest.mark.timeout(20)
@pytest.mark.slow
@pytest.mark.requires_api
def test_event_stream_with_generation(event_listener, wait_for_event):
    """Test that the event stream receives generation events."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]
    session_id = event_listener["session_id"]

    # Add a user message
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        headers={"Authorization": "Bearer test-token-for-server-thread"},
        json={"role": "user", "content": "Say hello"},
    )

    # Use a real model
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        headers={"Authorization": "Bearer test-token-for-server-thread"},
        json={"session_id": session_id},
    )

    # Wait for events
    assert wait_for_event(event_listener, "generation_started")
    assert wait_for_event(event_listener, "generation_progress")
    assert wait_for_event(event_listener, "generation_complete")

    # Verify the response
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        headers={"Authorization": "Bearer test-token-for-server-thread"},
    )
    assert resp.status_code == 200
    messages = resp.json()["log"]

    # Find the assistant's response
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert len(assistant_messages[0]["content"]) > 0
