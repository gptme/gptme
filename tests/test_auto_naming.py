"""Test auto-naming functionality for conversations."""

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
            "content": "Help me debug a Python script that's not working",
        },
    )

    # Wait for message_added event
    assert wait_for_event(event_listener, "message_added")

    # Trigger generation with a real model
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": "openai/gpt-4o-mini"},
    )

    # Wait for generation events
    assert wait_for_event(event_listener, "generation_started")

    # Wait for config_changed event (from auto-naming) - this comes before generation_complete
    assert wait_for_event(event_listener, "config_changed", timeout=15)

    # Now wait for generation_complete
    assert wait_for_event(event_listener, "generation_complete")

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
        json={"role": "user", "content": "Hello there"},
    )

    assert wait_for_event(event_listener, "message_added")

    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": "openai/gpt-4o-mini"},
    )

    # Wait for generation to complete
    assert wait_for_event(event_listener, "generation_started")
    assert wait_for_event(event_listener, "generation_complete")

    # Should NOT get a config_changed event from auto-naming since name already exists
    # We'll wait a bit to make sure it doesn't happen
    wait_for_event(event_listener, "config_changed", timeout=3)

    # Check that the predefined name wasn't changed
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.from_logdir(logdir)

    assert chat_config.name == "Predefined Name"
    print(f"Display name preserved: '{chat_config.name}'")


@pytest.mark.timeout(30)
@pytest.mark.slow
@pytest.mark.requires_api
def test_auto_naming_meaningful_content(event_listener, wait_for_event):
    """Test that auto-naming generates contextually relevant names."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]
    session_id = event_listener["session_id"]

    # Use a very specific user message to test contextual naming
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": "Create a React component for a todo list with drag and drop functionality",
        },
    )

    assert wait_for_event(event_listener, "message_added")

    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": "openai/gpt-4o-mini"},
    )

    assert wait_for_event(event_listener, "generation_started")

    # Wait for config_changed event first (comes before generation_complete)
    assert wait_for_event(event_listener, "config_changed", timeout=20)

    # Then wait for generation_complete
    assert wait_for_event(event_listener, "generation_complete", timeout=20)

    # Check that the generated name is contextually relevant
    logdir = get_logs_dir() / conversation_id
    chat_config = ChatConfig.from_logdir(logdir)

    assert chat_config.name is not None, "Expected auto-generated name but got None"
    name = chat_config.name.lower()
    print(f"Auto-generated display name: '{chat_config.name}'")

    # Should contain relevant keywords (being flexible since LLM might use different terms)
    relevant_keywords = [
        "react",
        "todo",
        "component",
        "drag",
        "drop",
        "list",
        "task",
        "ui",
        "interface",
    ]
    has_relevant_content = any(keyword in name for keyword in relevant_keywords)
    assert has_relevant_content, f"Generated name '{chat_config.name}' doesn't seem contextually relevant. Expected keywords: {relevant_keywords}"
