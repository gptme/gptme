"""Tests for auto-stepping and persistence in the V2 API."""

import logging
import os
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

    test_dir = "/tmp/test_dir"

    # Add a user message requesting multiple commands
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": f"Create a directory named {test_dir} and list its contents",
        },
    )

    # Define tools that will be used
    tool1 = ToolUse(
        tool="shell",
        args=[],
        content=f"mkdir -p {test_dir}",
    )

    tool2 = ToolUse(
        tool="shell",
        args=[],
        content=f"ls -la {test_dir}",
    )

    # Create mock response with the tools
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

    # Start generation with auto-confirm=2 for automatic stepping
    with unittest.mock.patch("gptme.server.api_v2_sessions._stream", mock_stream):
        requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
            json={
                "session_id": session_id,
                "model": "openai/mock-model",
                "auto_confirm": 2,
            },
        )

        # Wait for first tool execution and verify directory creation
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "generation_complete")
        assert wait_for_event(event_listener, "tool_pending")
        assert wait_for_event(event_listener, "tool_executing")
        assert wait_for_event(event_listener, "message_added")
        assert os.path.exists(test_dir), f"Directory {test_dir} was not created"

        # Wait for second tool execution
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "generation_complete")
        assert wait_for_event(event_listener, "tool_pending")
        assert wait_for_event(event_listener, "tool_executing")
        assert wait_for_event(event_listener, "message_added")

        # Wait for final assistant message
        assert wait_for_event(event_listener, "message_added")

    # Verify conversation state
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
    )
    assert resp.status_code == 200

    messages = resp.json()["log"]

    # Verify message sequence
    # Note: Message order can vary depending on hook execution order
    # Message count varies: 8 (CI, no lessons) or 9 (local, with lessons)
    # Expect: system prompt, user message, TOKEN_BUDGET, possibly lessons, then assistant/system messages
    assert len(messages) in [8, 9], f"Expected 8 or 9 messages, got {len(messages)}"
    assert messages[0]["role"] == "system" and "testing" in messages[0]["content"]

    # Find the user message (should be early in sequence)
    user_msg_idx = next(i for i, m in enumerate(messages) if m["role"] == "user")
    assert user_msg_idx <= 2, "User message should be within first 3 messages"

    # Verify TOKEN_BUDGET message exists
    assert any(
        "token_budget" in m.get("content", "")
        for m in messages
        if m["role"] == "system"
    )
    assert messages[4]["role"] == "assistant"
    assert messages[5]["role"] == "system"
    assert messages[6]["role"] == "assistant"
    assert messages[7]["role"] == "system"
    assert messages[8]["role"] == "assistant"
