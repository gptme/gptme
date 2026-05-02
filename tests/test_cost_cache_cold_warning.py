"""Test Anthropic cache-cold warning hook."""

import time
from unittest.mock import Mock

import pytest

from gptme.hooks.cost_awareness import (
    ANTHROPIC_CACHE_TTL_SECS,
    cache_cold_warning_hook,
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
def reset_state():
    """Reset pending warning and cost tracker before each test."""
    import gptme.hooks.cost_awareness as module

    module._pending_warning_var.set(None)
    CostTracker.reset()
    yield
    module._pending_warning_var.set(None)


def test_no_warning_without_anthropic_entries(mock_manager):
    """Test that no warning is produced when there are no Anthropic entries."""
    CostTracker.start_session("test")
    CostTracker.record(
        CostEntry(
            timestamp=time.time(),
            model="openai/gpt-4",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.01,
        )
    )

    result = list(cache_cold_warning_hook(mock_manager))
    assert len(result) == 0

    import gptme.hooks.cost_awareness as module

    assert module._pending_warning_var.get() is None


def test_no_warning_when_cache_is_warm(mock_manager):
    """Test no warning when last Anthropic call was within TTL."""
    CostTracker.start_session("test")
    CostTracker.record(
        CostEntry(
            timestamp=time.time() - 60,  # 1 min ago - well within TTL
            model="anthropic/claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=800,
            cache_creation_tokens=200,
            cost=0.01,
        )
    )

    result = list(cache_cold_warning_hook(mock_manager))
    assert len(result) == 0

    import gptme.hooks.cost_awareness as module

    assert module._pending_warning_var.get() is None


def test_warning_when_cache_is_cold(mock_manager):
    """Test warning when last Anthropic call was beyond TTL."""
    CostTracker.start_session("test")
    CostTracker.record(
        CostEntry(
            timestamp=time.time() - ANTHROPIC_CACHE_TTL_SECS - 120,  # 7 min ago
            model="anthropic/claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=800,
            cache_creation_tokens=200,
            cost=0.01,
        )
    )

    result = list(cache_cold_warning_hook(mock_manager))
    assert len(result) == 0  # Warning is stored, not yielded

    import gptme.hooks.cost_awareness as module

    pending = module._pending_warning_var.get()
    assert pending is not None
    assert "cache likely cold" in pending
    assert "TTL=5 min" in pending
    assert "min since last turn" in pending


def test_warning_injected_on_next_user_message(mock_manager):
    """Test that cached cold warning is injected with next user message."""
    import gptme.hooks.cost_awareness as module

    module._pending_warning_var.set(
        "<system_warning>Anthropic prompt cache likely cold</system_warning>"
    )

    msgs = [Message("user", "Continue working")]
    result = list(inject_pending_warning(msgs))

    assert len(result) == 1
    warning = result[0]
    assert isinstance(warning, Message)
    assert warning.role == "system"
    assert "cold" in str(warning.content)
    assert warning.hide is True


def test_multiple_anthropic_calls_uses_most_recent(mock_manager):
    """Test that only the most recent Anthropic call is used for TTL check."""
    CostTracker.start_session("test")
    CostTracker.record(
        CostEntry(
            timestamp=time.time() - ANTHROPIC_CACHE_TTL_SECS - 300,  # 10 min ago
            model="anthropic/claude-sonnet-4-6",
            input_tokens=500,
            output_tokens=200,
            cache_read_tokens=0,
            cache_creation_tokens=500,
            cost=0.01,
        )
    )
    CostTracker.record(
        CostEntry(
            timestamp=time.time() - 30,  # 30 sec ago - warm
            model="anthropic/claude-opus-4-7",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=800,
            cache_creation_tokens=200,
            cost=0.02,
        )
    )

    result = list(cache_cold_warning_hook(mock_manager))
    assert len(result) == 0

    import gptme.hooks.cost_awareness as module

    assert module._pending_warning_var.get() is None  # Most recent is warm
