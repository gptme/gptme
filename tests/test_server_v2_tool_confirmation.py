"""Tests for the tool confirmation flow in the V2 API."""

import logging
import unittest.mock

import pytest
import requests
from gptme.tools import ToolUse

logger = logging.getLogger(__name__)


@pytest.mark.timeout(20)
def test_tool_confirmation_flow(
    init_, setup_conversation, event_listener, mock_generation, wait_for_event
):
    """Test the tool confirmation flow."""
    port, conversation_id, session_id = setup_conversation

    # Add a user message requesting a command
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "List files in the current directory"},
    )

    # Define the tool we expect to be used
    tool = ToolUse(
        "shell",
        args=[],
        content="ls -la",
    )

    # Create mock response with the tool
    mock_stream = mock_generation(
        "I'll help you list the files. Let me run the command:\n\n"
        + tool.to_output("markdown")
    )

    # Start generation with mocked response
    with unittest.mock.patch("gptme.server.api_v2._stream", mock_stream):
        # Request a step
        requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
            json={"session_id": session_id, "model": "openai/mock-model"},
        )

        # Wait for tool to be detected
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "generation_complete")
        assert wait_for_event(event_listener, "tool_pending")
        tool_id = event_listener["get_tool_id"]()

        # Confirm the tool execution
        resp = requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/tool/confirm",
            json={"session_id": session_id, "tool_id": tool_id, "action": "confirm"},
        )
        assert resp.status_code == 200

        # Wait for tool execution and output
        assert wait_for_event(event_listener, "tool_executing")
        assert wait_for_event(event_listener, "message_added")

    # Verify conversation state
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}"
    )
    assert resp.status_code == 200

    # Check message sequence
    messages = resp.json()["log"]
    assert len(messages) == 4, f"Expected 4 messages, got {len(messages)}"

    # Verify message content
    assert messages[0]["role"] == "system" and "testing" in messages[0]["content"]
    assert messages[1]["role"] == "user" and "List files" in messages[1]["content"]
    assert messages[2]["role"] == "assistant" and "ls -la" in messages[2]["content"]
    assert messages[3]["role"] == "system" and "total" in messages[3]["content"]
