"""Test auto-naming functionality for conversations."""

import os

import pytest
import requests

from gptme.config import ChatConfig
from gptme.dirs import get_logs_dir

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


@pytest.mark.timeout(30)
@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    "glm" in os.getenv("MODEL", "").lower(),
    reason="GLM models are too slow for 30s auto-naming timeout",
)
def test_auto_naming_generates_display_name(event_listener, wait_for_event):
    """Test that auto-naming generates a display name after the first assistant response."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]
    session_id = event_listener["session_id"]

    # Add a user message that should trigger meaningful auto-naming
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": "Help me debug a Python script that's not working. Short answer.",
        },
    )

    # Wait for message_added event
    assert wait_for_event(event_listener, "message_added")

    # Trigger generation with a real model
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id},
    )

    # Wait for generation events
    assert wait_for_event(event_listener, "generation_started")

    # Wait for config_changed event (from auto-naming) - this comes before generation_complete
    assert wait_for_event(event_listener, "config_changed")

    print("✅ Successfully received config_changed event!")

    # Verify the display name was saved to config
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.from_logdir(logdir)

    assert chat_config.name is not None
    assert chat_config.name != ""
    assert len(chat_config.name) > 0
    print(f"Auto-generated display name: '{chat_config.name}'")

    # Verify it's a reasonable length (should be concise)
    assert len(chat_config.name) <= 50


@pytest.mark.timeout(30)
@pytest.mark.slow
@pytest.mark.requires_api
def test_auto_naming_only_runs_once(event_listener, wait_for_event):
    """Test that auto-naming doesn't overwrite existing names."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]
    session_id = event_listener["session_id"]

    # Set a predefined name via config update
    requests.patch(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/config",
        json={"chat": {"name": "Predefined Name"}},
    )

    # Add user message and trigger generation
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Hello there. We are testing, just say 'hi'"},
    )

    assert wait_for_event(event_listener, "message_added")

    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id},
    )

    # Wait for generation to complete
    assert wait_for_event(event_listener, "generation_started")

    # Should NOT get a config_changed event from auto-naming since name already exists
    # We'll wait a bit to make sure it doesn't happen
    assert not wait_for_event(event_listener, "config_changed", timeout=3)

    assert wait_for_event(event_listener, "generation_complete")

    # Verify that the config didn't change after generation
    assert not wait_for_event(event_listener, "config_changed", timeout=1)

    # Check that the predefined name wasn't changed
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.from_logdir(logdir)

    assert chat_config.name == "Predefined Name"
    print(f"Display name preserved: '{chat_config.name}'")


@pytest.mark.timeout(30)
@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    "claude-haiku" in os.getenv("MODEL", "").lower()
    or "glm" in os.getenv("MODEL", "").lower(),
    reason="Claude Haiku outputs thinking tags; GLM is too slow for auto-naming timeout",
)
def test_auto_naming_meaningful_content(event_listener, wait_for_event):
    """Test that auto-naming generates contextually relevant names."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]
    session_id = event_listener["session_id"]

    # Use a simple but specific user message to test contextual naming
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": "How do I center a div in CSS? Short answer please.",
        },
    )

    assert wait_for_event(event_listener, "message_added")

    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id},
    )

    assert wait_for_event(event_listener, "generation_started")

    # Wait for config_changed event
    assert wait_for_event(event_listener, "config_changed", timeout=20)

    # Check that the generated name is contextually relevant
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.from_logdir(logdir)

    assert chat_config.name is not None, "Expected auto-generated name but got None"
    name = chat_config.name.lower()
    print(f"Auto-generated display name: '{chat_config.name}'")

    # Should contain relevant keywords (being flexible since LLM might use different terms)
    relevant_keywords = [
        "css",
        "div",
        "center",
        "centering",
        "layout",
        "styling",
        "web",
    ]
    has_relevant_content = any(keyword in name for keyword in relevant_keywords)
    assert has_relevant_content, (
        f"Generated name '{chat_config.name}' doesn't seem contextually relevant. Expected keywords: {relevant_keywords}"
    )


# Unit tests for validation function
def test_minimum_context_threshold():
    """Test that LLM naming returns None when context is too short."""
    from gptme.message import Message
    from gptme.util.auto_naming import _generate_llm_name

    # Very short conversation - should skip LLM naming due to minimum threshold
    short_messages = [
        Message("system", "You are a helpful assistant."),
        Message("user", "hi"),
        Message("assistant", "Hello!"),
    ]

    # This should return None because context is too short (< 50 chars)
    # without even attempting an LLM call
    result = _generate_llm_name(short_messages, "test/model")
    assert result is None, "Expected None for short context"

    # Slightly longer but still under threshold
    borderline_messages = [
        Message("user", "Hello there"),
        Message("assistant", "Hi! How can I help?"),
    ]
    result = _generate_llm_name(borderline_messages, "test/model")
    assert result is None, "Expected None for borderline short context"


def test_invalid_title_detection():
    """Test that error-like title responses are correctly identified."""
    from gptme.util.auto_naming import _is_invalid_title

    # These should be detected as invalid
    invalid_titles = [
        # Edge cases
        "",  # Empty string
        "   ",  # Whitespace only
        "\t\n",  # Tab and newline only
        # Error patterns
        "Conversation content missing",
        "Missing Conversation Details",
        "content missing",
        "Unable to generate title",
        "I cannot determine the topic",
        "I don't have enough information",
        "I'm sorry, but I can't",
        "Sorry, cannot generate",
        "Unfortunately there's not enough context",
        "Not enough information",
        "Insufficient context",
        "No information available",
        "No context provided",
        "Empty conversation",
        "Missing details here",
        "Details missing from context",
        "N/A",
        "Not applicable",
        "Not available",
        "Title: Python Help",  # Model repeating prompt
        "Name: Debug Session",  # Model repeating prompt
        "I think this is about debugging Python code and it seems to be related to web development",  # Too long
        "The conversation appears to be about Python",  # Starts with explanation
        "Based on the context provided",  # Explanation prefix
        "Here is a title for this conversation",  # Explanation prefix
    ]

    for title in invalid_titles:
        assert _is_invalid_title(title), f"Expected '{title}' to be invalid"

    # These should be valid titles
    valid_titles = [
        "Python debugging help",
        "CSS layout issue",
        "API integration guide",
        "Website creation task",
        "Debug script error",
        "Install dependencies",
        "Fix login bug",
        "Update database schema",
        "Exactly eight words to test the boundary here",  # 8 words (boundary case)
    ]

    for title in valid_titles:
        assert not _is_invalid_title(title), f"Expected '{title}' to be valid"


def test_generate_conversation_name_returns_none_on_llm_failure():
    """Test that generate_conversation_name returns None (not random) when LLM strategy fails.

    This allows callers to retry on subsequent turns when more context is available,
    rather than immediately falling back to a random name.
    """
    from gptme.message import Message
    from gptme.util.auto_naming import generate_conversation_name

    # Short conversation that will cause LLM naming to fail
    short_messages = [
        Message("user", "hi"),
        Message("assistant", "Hello!"),
    ]

    # With LLM strategy, should return None (not a random name) when context is insufficient
    result = generate_conversation_name(
        strategy="llm",
        messages=short_messages,
        model="test/model",
    )

    # Should be None to allow retry, not a random name
    assert result is None, (
        f"Expected None when LLM naming fails, got '{result}'. "
        "generate_conversation_name should not fall back to random names when "
        "LLM strategy is explicitly requested."
    )
