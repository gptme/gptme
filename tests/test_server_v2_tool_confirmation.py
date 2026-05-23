"""Tests for the tool confirmation flow in the V2 API."""

import logging
import unittest.mock

import pytest
import requests

from gptme.tools import ToolUse

logger = logging.getLogger(__name__)


@pytest.mark.timeout(60)
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
        [
            "I'll help you list the files. Let me run the command:\n\n"
            + tool.to_output("markdown"),
            "Done, let me know if you need anything else.",
        ]
    )

    # Start generation with mocked response
    with (
        unittest.mock.patch("gptme.server.session_step._stream", mock_stream),
        unittest.mock.patch(
            "gptme.server.session_step._try_auto_name_and_notify", return_value=None
        ),
    ):
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

        # Wait for assistant final response and completion before leaving the
        # mock context; otherwise the background continuation thread may fall
        # through to the real LLM stream.
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "message_added")
        assert wait_for_event(event_listener, "generation_complete")

    # Verify conversation state
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
    )
    assert resp.status_code == 200

    # Check message sequence.
    # Hook-injected hidden context can legitimately add system messages here
    # (token budget, lessons, active context, output-format hints, etc.), so
    # assert the flow invariants instead of a brittle exact message count.
    messages = resp.json()["log"]
    assert len(messages) >= 5, f"Expected at least 5 messages, got {len(messages)}"

    # Verify message content
    assert messages[0]["role"] == "system" and "testing" in messages[0]["content"]

    # Find the user message and verify the assistant/tool flow follows it.
    user_msg_idx = next(
        (i for i, m in enumerate(messages) if m["role"] == "user"), None
    )
    assert user_msg_idx is not None, "No user message found in conversation"
    assert "List files" in messages[user_msg_idx]["content"]

    assistant_tool_idx = next(
        (
            i
            for i, m in enumerate(messages)
            if m["role"] == "assistant" and "ls -la" in m["content"]
        ),
        None,
    )
    assert assistant_tool_idx is not None, "No assistant tool message (ls -la) found"
    assert user_msg_idx < assistant_tool_idx

    # Verify TOKEN_BUDGET message exists
    assert any(
        "token_budget" in m.get("content", "")
        for m in messages
        if m["role"] == "system"
    )

    # Verify final assistant message
    assert messages[-1]["role"] == "assistant"
    assert "Done" in messages[-1]["content"]


@pytest.mark.timeout(60)
def test_tool_confirmation_without_session_id(
    init_, setup_conversation, event_listener, mock_generation, wait_for_event
):
    """Test that tool confirmation works without session_id (server finds tool across sessions)."""
    port, conversation_id, session_id = setup_conversation

    # Add a user message requesting a command
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Echo hello world"},
    )

    # Define the tool we expect to be used
    tool = ToolUse(
        "shell",
        args=[],
        content="echo 'hello world'",
    )

    # Create mock response with the tool
    mock_stream = mock_generation(
        [
            "I'll echo that for you:\n\n" + tool.to_output("markdown"),
            "Done!",
        ]
    )

    # Start generation with mocked response
    with (
        unittest.mock.patch("gptme.server.session_step._stream", mock_stream),
        unittest.mock.patch(
            "gptme.server.session_step._try_auto_name_and_notify", return_value=None
        ),
    ):
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

        # Confirm the tool WITHOUT session_id - server should find it
        resp = requests.post(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/tool/confirm",
            json={"tool_id": tool_id, "action": "confirm"},  # No session_id!
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        # Wait for tool execution
        assert wait_for_event(event_listener, "tool_executing")
        assert wait_for_event(event_listener, "message_added")
        assert wait_for_event(event_listener, "generation_started")
        assert wait_for_event(event_listener, "message_added")
        assert wait_for_event(event_listener, "generation_complete")


@pytest.mark.timeout(10)
def test_tool_confirmation_without_session_id_tool_not_found(init_, setup_conversation):
    """Test that confirming a non-existent tool without session_id returns 404."""
    port, conversation_id, session_id = setup_conversation

    # Try to confirm a non-existent tool without session_id
    resp = requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/tool/confirm",
        json={"tool_id": "non-existent-tool-id", "action": "confirm"},  # No session_id
    )
    assert resp.status_code == 404
    assert "Tool not found in any session" in resp.json()["error"]
