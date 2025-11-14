"""Tests for the token-awareness tool."""

import pytest

from gptme.hooks import HookType, clear_hooks, get_hooks, trigger_hook
from gptme.logmanager import Log
from gptme.message import Message


@pytest.fixture(autouse=True)
def clear_all_hooks():
    """Clear all hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture
def load_token_awareness_tool():
    """Load the token-awareness hook."""
    from gptme.hooks.token_awareness import register

    register()
    yield
    clear_hooks()


def test_token_awareness_hook_exists():
    """Test that the token-awareness hook can be imported."""
    from gptme.hooks import token_awareness

    assert token_awareness.register is not None


def test_token_awareness_hooks_registered(load_token_awareness_tool):
    """Test that token-awareness hooks are registered."""
    session_start_hooks = get_hooks(HookType.SESSION_START)
    tool_post_hooks = get_hooks(HookType.TOOL_POST_EXECUTE)

    # Should have at least one SESSION_START hook (token_budget)
    assert len(session_start_hooks) >= 1
    assert any("token_awareness.token_budget" in h.name for h in session_start_hooks)

    # Should have at least one TOOL_POST_EXECUTE hook (token_usage)
    assert len(tool_post_hooks) >= 1
    assert any("token_awareness.token_usage" in h.name for h in tool_post_hooks)


def test_add_token_budget_hook(load_token_awareness_tool, tmp_path):
    """Test that the token budget hook adds the correct message."""
    from gptme.llm.models import set_default_model

    # Set a known model for testing
    set_default_model("openai/gpt-4")

    initial_msgs = [Message("system", "Test system message")]

    results = list(
        trigger_hook(
            HookType.SESSION_START,
            logdir=tmp_path,
            workspace=tmp_path,
            initial_msgs=initial_msgs,
        )
    )

    # Should have at least one message from the hook
    assert len(results) >= 1

    # Find the budget message
    budget_msg = next(
        (msg for msg in results if "<budget:token_budget>" in msg.content), None
    )
    assert budget_msg is not None
    assert budget_msg.role == "system"
    # Note: hide property may be disabled for testing purposes

    # Check that the budget value is present and reasonable
    # GPT-4 has a context window of 8192 or 128000 depending on version
    assert "128000" in budget_msg.content or "8192" in budget_msg.content


def test_add_token_usage_warning_hook(load_token_awareness_tool, tmp_path):
    """Test that the token usage warning hook adds the correct message."""
    from gptme.llm.models import set_default_model

    # Set a known model for testing
    set_default_model("openai/gpt-4")

    # Create a mock log with some messages
    log = Log()
    log = log.append(Message("system", "System message"))
    log = log.append(Message("user", "Hello"))
    log = log.append(Message("assistant", "Hi there!"))

    # Use workspace=None to trigger fallback behavior (shows warning every time)
    results = list(
        trigger_hook(HookType.TOOL_POST_EXECUTE, log=log, workspace=None, tool_use=None)
    )

    # Should have at least one message from the hook
    assert len(results) >= 1

    # Find the usage warning message
    usage_msg = next(
        (msg for msg in results if "<system_warning>" in msg.content), None
    )
    assert usage_msg is not None
    assert usage_msg.role == "system"
    # Note: hide property may be disabled for debugging/testing purposes

    # Check that the usage information is present
    assert "Token usage:" in usage_msg.content
    assert "remaining" in usage_msg.content

    # Check format: Token usage: X/Y; Z remaining
    content = usage_msg.content
    assert "/" in content
    assert ";" in content


def test_token_calculation_accuracy(load_token_awareness_tool, tmp_path):
    """Test that token calculations are reasonably accurate."""
    from gptme.llm.models import set_default_model
    from gptme.message import len_tokens

    # Set a known model for testing
    set_default_model("openai/gpt-4")

    # Create a log with known content
    log = Log()
    msg1 = Message("user", "Hello world")
    msg2 = Message("assistant", "Hi there!")
    log = log.append(msg1)
    log = log.append(msg2)

    # Calculate expected token count
    expected_tokens = len_tokens(msg1, "gpt-4") + len_tokens(msg2, "gpt-4")

    # Use workspace=None to trigger fallback behavior (shows warning every time)
    results = list(
        trigger_hook(HookType.TOOL_POST_EXECUTE, log=log, workspace=None, tool_use=None)
    )

    # Find the usage warning
    usage_msg = next(
        (msg for msg in results if "<system_warning>" in msg.content), None
    )
    assert usage_msg is not None

    # Extract the token count from the message
    # Format: Token usage: X/Y; Z remaining
    content = usage_msg.content
    used_tokens = int(content.split("Token usage: ")[1].split("/")[0])

    # The used tokens should match our calculation
    assert used_tokens == expected_tokens


def test_no_model_loaded_graceful_handling(
    load_token_awareness_tool, tmp_path, monkeypatch
):
    """Test that hooks handle missing model gracefully."""
    from gptme.llm import models

    # Mock get_default_model to return None
    monkeypatch.setattr(models, "get_default_model", lambda: None)

    # Trigger SESSION_START hook
    results = list(
        trigger_hook(
            HookType.SESSION_START,
            logdir=tmp_path,
            workspace=tmp_path,
            initial_msgs=[],
        )
    )

    # Should not crash, but also should not add any messages
    # (or at least no budget message)
    budget_msgs = [msg for msg in results if "<budget:token_budget>" in msg.content]
    assert len(budget_msgs) == 0


def test_hook_priority(load_token_awareness_tool):
    """Test that the token budget hook has correct priority."""
    session_start_hooks = get_hooks(HookType.SESSION_START)

    # Find the token_budget hook
    token_budget_hook = next(
        h for h in session_start_hooks if "token_awareness.token_budget" in h.name
    )

    # Should have high priority (10) to run early
    assert token_budget_hook.priority == 10


def test_multiple_usage_warnings(load_token_awareness_tool, tmp_path):
    """Test that multiple usage warnings can be generated."""
    from gptme.llm.models import set_default_model

    set_default_model("openai/gpt-4")

    log = Log()
    log = log.append(Message("user", "Hello"))

    # Use workspace=None to trigger fallback behavior (shows warning every time)
    # Trigger hook first time
    results1 = list(
        trigger_hook(HookType.TOOL_POST_EXECUTE, log=log, workspace=None, tool_use=None)
    )
    assert len([m for m in results1 if "<system_warning>" in m.content]) >= 1

    # Add more messages
    log = log.append(Message("assistant", "Hi"))
    log = log.append(Message("user", "How are you?"))

    # Trigger hook second time
    results2 = list(
        trigger_hook(HookType.TOOL_POST_EXECUTE, log=log, workspace=None, tool_use=None)
    )
    assert len([m for m in results2 if "<system_warning>" in m.content]) >= 1

    # Token counts should be different (more tokens in second call)
    usage1 = next(m for m in results1 if "<system_warning>" in m.content).content
    usage2 = next(m for m in results2 if "<system_warning>" in m.content).content

    # Extract token counts
    tokens1 = int(usage1.split("Token usage: ")[1].split("/")[0])
    tokens2 = int(usage2.split("Token usage: ")[1].split("/")[0])

    assert tokens2 > tokens1  # Should have more tokens after adding messages
