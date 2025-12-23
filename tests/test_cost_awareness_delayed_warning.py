"""Test delayed cost warning injection."""

import time
from unittest.mock import Mock, patch

import pytest

from gptme.hooks.cost_awareness import (
    cost_warning_hook,
    inject_pending_warning,
)
from gptme.message import Message
from gptme.util.cost_tracker import CostEntry, CostTracker


@pytest.fixture
def mock_manager():
    """Create a mock LogManager."""
    manager = Mock()
    manager.log.messages = []
    return manager


@pytest.fixture(autouse=True)
def reset_pending_warning():
    """Reset pending warning before each test."""
    import gptme.hooks.cost_awareness as module

    module._pending_warning = None
    yield
    module._pending_warning = None


def test_warning_printed_but_not_injected_immediately(mock_manager, capsys):
    """Test that warning is stored but not injected as message immediately."""
    # Setup: Create a session with cost that crosses threshold
    CostTracker.start_session("test_session")
    CostTracker.record(
        CostEntry(
            timestamp=time.time(),
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.15,  # Crosses $0.10 threshold
        )
    )

    # Execute the hook
    messages = list(cost_warning_hook(mock_manager))

    # NO message should be yielded immediately
    assert len(messages) == 0

    # Pending warning should be stored for later injection
    import gptme.hooks.cost_awareness as module

    assert module._pending_warning is not None
    assert "<system_warning>" in module._pending_warning
    assert "Session cost reached $0.15" in module._pending_warning


def test_warning_injected_on_next_user_message(mock_manager):
    """Test that pending warning is injected when user sends next message."""
    # Setup: Store a pending warning
    import gptme.hooks.cost_awareness as module

    module._pending_warning = "<system_warning>Test warning</system_warning>"

    # Create a messages list with a user message
    msgs = [Message("user", "Hello")]

    # Execute the injection hook
    result = list(inject_pending_warning(msgs))

    # Should yield the warning message
    assert len(result) == 1
    warning = result[0]
    assert isinstance(warning, Message)
    assert warning.role == "system"
    assert warning.content == "<system_warning>Test warning</system_warning>"
    assert warning.hide is True

    # Pending warning should be cleared
    assert module._pending_warning is None


def test_warning_not_injected_on_assistant_message(mock_manager):
    """Test that warning is NOT injected for assistant messages."""
    # Setup: Store a pending warning
    import gptme.hooks.cost_awareness as module

    module._pending_warning = "<system_warning>Test warning</system_warning>"

    # Create a messages list with an assistant message
    msgs = [Message("assistant", "Response")]

    # Execute the injection hook
    result = list(inject_pending_warning(msgs))

    # Should NOT yield any messages
    assert len(result) == 0

    # Pending warning should still be there
    assert module._pending_warning is not None


def test_no_warning_when_no_threshold_crossed(mock_manager):
    """Test that no warning is generated when cost is below thresholds."""
    # Setup: Create a session with low cost
    CostTracker.start_session("test_session")
    CostTracker.record(
        CostEntry(
            timestamp=time.time(),
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.05,  # Below $0.10 threshold
        )
    )

    # Execute the hook
    with patch("gptme.util.console") as mock_console:
        messages = list(cost_warning_hook(mock_manager))

    # No warning should be printed
    mock_console.print.assert_not_called()

    # No message should be yielded
    assert len(messages) == 0

    # No pending warning
    import gptme.hooks.cost_awareness as module

    assert module._pending_warning is None


def test_no_duplicate_warnings_for_same_threshold(mock_manager):
    """Test that multiple requests in same threshold range don't create duplicate warnings."""
    import gptme.hooks.cost_awareness as module

    # Setup: Create a session and cross threshold
    CostTracker.start_session("test_session")
    CostTracker.record(
        CostEntry(
            timestamp=time.time(),
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.12,  # Crosses $0.10 threshold
        )
    )

    # First warning should be created
    list(cost_warning_hook(mock_manager))
    assert module._pending_warning is not None

    # Clear the pending warning (simulating it was injected)
    module._pending_warning = None

    # Add another request in same threshold range
    CostTracker.record(
        CostEntry(
            timestamp=time.time(),
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.03,  # Total now $0.15, still in $0.10-$0.50 range
        )
    )

    # Should NOT create another warning (same threshold range)
    list(cost_warning_hook(mock_manager))
    assert module._pending_warning is None  # No new warning stored
