"""Tests for the Server-Sent Events (SSE) stream functionality in the V2 API."""

import threading
import unittest.mock

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
        json={"role": "user", "content": "Test message"},
    )

    # Wait for events
    assert wait_for_event(event_listener, "connected")
    assert wait_for_event(event_listener, "message_added")

    # Verify message content
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
    )
    assert resp.status_code == 200
    messages = resp.json()["log"]

    # Find the user message
    user_messages = [m for m in messages if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "Test message"


@pytest.mark.xfail(reason="Flaky test")
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
        json={"role": "user", "content": "Say hello"},
    )

    # Use a real model
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id},
    )

    # Wait for events
    assert wait_for_event(event_listener, "generation_started")
    assert wait_for_event(event_listener, "generation_progress")
    assert wait_for_event(event_listener, "generation_complete")

    # Verify the response
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
    )
    assert resp.status_code == 200
    messages = resp.json()["log"]

    # Find the assistant's response
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert len(assistant_messages[0]["content"]) > 0


@pytest.mark.timeout(10)
def test_generation_complete_before_auto_naming(
    setup_conversation, event_listener, mock_generation, wait_for_event
):
    """generation_complete must be emitted before _try_auto_name_and_notify runs.

    Regression test for #2081: auto-naming was called synchronously BEFORE
    generation_complete, blocking SSE delivery for 4+ seconds (LLM naming call).

    This test uses a deadlock to detect the regression:
    - The auto-naming mock blocks until generation_complete is received by the SSE client.
    - If auto-naming runs BEFORE generation_complete (the regression): deadlock → timeout → FAIL.
    - If auto-naming runs AFTER generation_complete (correct order): gate opens → PASS.
    """
    port, conversation_id, session_id = setup_conversation

    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Hello"},
    )

    mock_stream = mock_generation(["Hello!"])
    generation_complete_gate = threading.Event()
    auto_name_finished = threading.Event()

    def gated_auto_name(*args, **kwargs):
        # If called BEFORE generation_complete: blocks here until gate opens,
        # preventing generation_complete from being emitted → wait_for_event
        # times out → assertion fails.
        # If called AFTER generation_complete (correct): gate already open → immediate return.
        try:
            generation_complete_gate.wait(timeout=6)
        finally:
            auto_name_finished.set()

    with (
        unittest.mock.patch("gptme.server.session_step._stream", mock_stream),
        unittest.mock.patch(
            "gptme.server.session_step._try_auto_name_and_notify",
            side_effect=gated_auto_name,
        ) as auto_name_mock,
    ):
        requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
            json={"session_id": session_id, "model": "openai/mock-model"},
        )

        assert wait_for_event(event_listener, "generation_complete", timeout=5), (
            "generation_complete not received within 5s — "
            "_try_auto_name_and_notify may be blocking it (regression of #2081)"
        )
        generation_complete_gate.set()

        # Keep the patch installed until the background step reaches and exits
        # auto-naming. This avoids racing patch teardown against a slow worker.
        assert auto_name_finished.wait(timeout=2), "auto-naming did not finish"
        auto_name_mock.assert_called_once()
