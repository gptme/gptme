"""Tests for auto-stepping and persistence in the V2 API."""

import logging
import unittest.mock

import pytest
import requests
from gptme.tools import ToolUse

logger = logging.getLogger(__name__)


@pytest.mark.timeout(30)
def test_auto_stepping(
    init_, setup_conversation, event_listener, mock_generation, wait_for_event
):
    """Test auto-stepping and auto-confirm functionality with multiple tools in sequence."""
    port, conversation_id, session_id = setup_conversation

    # Add a user message requesting multiple commands
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Create a directory and list its contents"},
    )

    # Define tools that will be used
    tool1 = ToolUse(
        tool="shell",
        args=[],
        content="mkdir -p test_dir",
    )

    tool2 = ToolUse(
        tool="shell",
        args=[],
        content="ls -la test_dir",
    )

    # Create a mock that returns different responses for each call
    mock_stream = mock_generation(
        [
            (
                "I'll help you create a directory and list its contents.\n\nFirst, let's create a directory:\n\n"
                + tool1.to_output("markdown")
            ),
            ("Now, let's list its contents:\n\n" + tool2.to_output("markdown")),
            ("The directory has been created and listed successfully."),
        ]
    )

    # Start generation with auto-confirm and the sequential mock
    with unittest.mock.patch("gptme.server.api_v2._stream", mock_stream):
        requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
            json={
                "session_id": session_id,
                "model": "openai/mock-model",
                "auto_confirm": 2,
            },
        )

        # Wait for first tool execution
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "generation_complete")
        assert wait_for_event(event_listener, "tool_pending")
        assert wait_for_event(event_listener, "tool_executing")
        assert wait_for_event(event_listener, "message_added")

        # requests.post(
        #     f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        #     json={
        #         "session_id": session_id,
        #         "model": "openai/mock-model",
        #         "auto_confirm": True,
        #     },
        # )

        # Wait for second tool execution
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "generation_complete")
        assert wait_for_event(event_listener, "tool_pending")
        assert wait_for_event(event_listener, "tool_executing")
        assert wait_for_event(event_listener, "message_added")

    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}"
    )
    assert resp.status_code == 200

    conversation_data = resp.json()
    messages = conversation_data["log"]

    # We should have 6 messages:
    # 1. System, 2. User, 3. Assistant (mkdir), 4. System output,
    # 5. Assistant (ls), 6. System output
    assert len(messages) == 7, (
        f"Expected 7 messages, got {len(messages)}" + "\n" + str(messages)
    )

    # Check for specific content
    content_text = " ".join([m["content"] for m in messages])
    assert (
        tool1.content and tool1.content in content_text
    ), "First tool content not found"
    assert (
        tool2.content and tool2.content in content_text
    ), "Second tool content not found"
    assert "total " in content_text, "Expected 'total 0' in messages"
