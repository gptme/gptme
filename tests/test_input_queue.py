"""Tests for the input queue module."""

import time

import pytest

from gptme.util.input_queue import (
    clear_queue,
    get_all_queued_inputs,
    get_queued_input,
    is_background_input_active,
    queue_size,
    start_background_input,
    stop_background_input,
)


def test_queue_operations():
    """Test basic queue operations without starting background thread."""
    # Clear any existing items
    clear_queue()
    assert queue_size() == 0

    # Queue should be empty
    assert get_queued_input() is None
    assert get_all_queued_inputs() == []


def test_background_input_lifecycle():
    """Test starting and stopping background input."""
    # Should not be active initially
    assert not is_background_input_active()

    # Start background input
    start_background_input()
    # Give thread time to start
    time.sleep(0.1)
    assert is_background_input_active()

    # Stop background input
    stop_background_input()
    time.sleep(0.1)
    assert not is_background_input_active()


def test_clear_queue():
    """Test clearing the queue."""
    from gptme.util.input_queue import _input_queue

    # Add some items directly to the queue
    _input_queue.put("test1")
    _input_queue.put("test2")
    assert queue_size() == 2

    # Clear and verify
    clear_queue()
    assert queue_size() == 0
    assert get_queued_input() is None


def test_get_all_queued_inputs():
    """Test getting all queued inputs at once."""
    from gptme.util.input_queue import _input_queue

    clear_queue()

    # Add items
    _input_queue.put("first")
    _input_queue.put("second")
    _input_queue.put("third")

    # Get all at once
    inputs = get_all_queued_inputs()
    assert inputs == ["first", "second", "third"]

    # Queue should be empty now
    assert queue_size() == 0
