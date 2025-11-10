"""Tests for the time-awareness tool."""

from datetime import datetime, timedelta

import pytest

from gptme.hooks import HookType, clear_hooks, get_hooks, trigger_hook
from gptme.logmanager import Log


@pytest.fixture(autouse=True)
def clear_all_hooks():
    """Clear all hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture
def load_time_awareness_tool():
    """Load the time-awareness tool and its hooks."""
    from gptme.tools.time_awareness import tool

    tool.register_hooks()
    return tool


def test_time_awareness_tool_exists():
    """Test that the time-awareness tool can be imported."""
    from gptme.tools.time_awareness import tool

    assert tool.name == "time-awareness"


def test_time_awareness_tool_hooks_registered(load_time_awareness_tool):
    """Test that time-awareness tool hooks are registered."""
    tool_post_hooks = get_hooks(HookType.TOOL_POST_EXECUTE)

    # Should have at least one TOOL_POST_EXECUTE hook (time_message)
    assert len(tool_post_hooks) >= 1
    assert any("time-awareness.time_message" in h.name for h in tool_post_hooks)


def test_time_milestones(load_time_awareness_tool, tmp_path, monkeypatch):
    """Test that time messages appear at correct milestones."""
    from gptme.tools.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    # Set up test conversation
    workspace = tmp_path
    log = Log()

    # Mock conversation start time to 30 minutes ago
    now = datetime.now()
    start_time = now - timedelta(minutes=30)
    conversation_start_times = _conversation_start_times_var.get()
    shown_milestones = _shown_milestones_var.get()
    assert conversation_start_times is not None
    assert shown_milestones is not None
    conversation_start_times[str(workspace)] = start_time
    _conversation_start_times_var.set(conversation_start_times)
    shown_milestones[str(workspace)] = set()
    _shown_milestones_var.set(shown_milestones)

    # Trigger hook at different time points
    test_cases = [
        (1, "1min"),
        (5, "5min"),
        (10, "10min"),
        (15, "15min"),
        (20, "20min"),
        (30, "30min"),
    ]

    for elapsed_minutes, expected_time in test_cases:
        # Clear previous milestones to test each individually
        shown_milestones = _shown_milestones_var.get()
        assert shown_milestones is not None
        shown_milestones[str(workspace)] = set()
        _shown_milestones_var.set(shown_milestones)

        # Set conversation start time
        conversation_start_times = _conversation_start_times_var.get()
        assert conversation_start_times is not None
        conversation_start_times[str(workspace)] = now - timedelta(
            minutes=elapsed_minutes
        )
        _conversation_start_times_var.set(conversation_start_times)

        # Trigger TOOL_POST_EXECUTE hook
        messages = list(
            trigger_hook(
                HookType.TOOL_POST_EXECUTE, log=log, workspace=workspace, tool_use=None
            )
        )

        # Should have exactly one message
        assert len(messages) == 1
        message = messages[0]

        # Check message properties
        assert message.role == "system"
        # Note: hide property may be disabled for testing purposes
        assert f"Time elapsed: {expected_time}" in message.content


def test_milestone_progression(load_time_awareness_tool, tmp_path):
    """Test that milestones are shown in sequence without repetition."""
    from gptme.tools.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    workspace = tmp_path
    log = Log()
    now = datetime.now()

    # Set conversation start 25 minutes ago
    conversation_start_times = _conversation_start_times_var.get()
    shown_milestones = _shown_milestones_var.get()
    assert conversation_start_times is not None
    assert shown_milestones is not None
    conversation_start_times[str(workspace)] = now - timedelta(minutes=25)
    _conversation_start_times_var.set(conversation_start_times)
    shown_milestones[str(workspace)] = set()
    _shown_milestones_var.set(shown_milestones)

    # Trigger multiple times - should only show milestone once
    for i in range(3):
        messages = list(
            trigger_hook(
                HookType.TOOL_POST_EXECUTE, log=log, workspace=workspace, tool_use=None
            )
        )

        # First trigger: should show 20min milestone
        # Subsequent triggers: no new messages (milestone already shown)
        if i == 0:
            assert len(messages) == 1
        else:
            assert len(messages) == 0


def test_no_workspace_graceful_handling(
    load_time_awareness_tool, tmp_path, monkeypatch
):
    """Test that missing workspace is handled gracefully."""
    log = Log()

    # Trigger hook without workspace
    messages = list(
        trigger_hook(HookType.TOOL_POST_EXECUTE, log=log, workspace=None, tool_use=None)
    )

    # Should not crash, should not produce messages
    assert len(messages) == 0


def test_time_format_hours(load_time_awareness_tool, tmp_path):
    """Test time formatting includes hours for long conversations."""
    from gptme.tools.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    workspace = tmp_path
    log = Log()
    now = datetime.now()

    # Set conversation start 125 minutes ago (2h 5min)
    conversation_start_times = _conversation_start_times_var.get()
    shown_milestones = _shown_milestones_var.get()
    assert conversation_start_times is not None
    assert shown_milestones is not None
    conversation_start_times[str(workspace)] = now - timedelta(minutes=125)
    _conversation_start_times_var.set(conversation_start_times)
    shown_milestones[str(workspace)] = set()
    _shown_milestones_var.set(shown_milestones)

    # Trigger hook
    messages = list(
        trigger_hook(
            HookType.TOOL_POST_EXECUTE, log=log, workspace=workspace, tool_use=None
        )
    )

    assert len(messages) == 1
    message = messages[0]

    # Should show "2h 5min" format
    assert "Time elapsed: 2h 5min" in message.content


def test_every_10min_after_20(load_time_awareness_tool, tmp_path):
    """Test that messages appear every 10 minutes after 20min mark."""
    from gptme.tools.time_awareness import (
        _conversation_start_times_var,
        _ensure_locals,
        _shown_milestones_var,
    )

    # Initialize context-local storage
    _ensure_locals()

    workspace = tmp_path
    log = Log()
    now = datetime.now()

    # Test 30, 40, 50 minute marks
    for minutes in [30, 40, 50]:
        shown_milestones = _shown_milestones_var.get()
        assert shown_milestones is not None
        shown_milestones[str(workspace)] = set()
        _shown_milestones_var.set(shown_milestones)

        conversation_start_times = _conversation_start_times_var.get()
        assert conversation_start_times is not None
        conversation_start_times[str(workspace)] = now - timedelta(
            minutes=minutes
        )
        _conversation_start_times_var.set(conversation_start_times)

        messages = list(
            trigger_hook(
                HookType.TOOL_POST_EXECUTE, log=log, workspace=workspace, tool_use=None
            )
        )

        assert len(messages) == 1
        assert f"Time elapsed: {minutes}min" in messages[0].content
