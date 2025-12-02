"""Tests for the interactive generation module."""

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from gptme.util.interactive_gen import (
    InputAction,
    check_stdin_has_data,
    get_interrupt_content,
    get_queued_input,
    has_interrupt_content,
    has_queued_input,
    queue_input,
    read_stdin_nonblocking,
    set_interrupt_content,
    show_input_dialog,
)


class TestQueuedInput:
    """Tests for queued input functionality."""

    def test_queue_and_retrieve(self):
        """Test that input can be queued and retrieved."""
        # Clear any existing state
        get_queued_input()

        assert not has_queued_input()
        queue_input("test input")
        assert has_queued_input()
        assert get_queued_input() == "test input"
        assert not has_queued_input()
        assert get_queued_input() is None

    def test_queue_overwrites(self):
        """Test that queueing new input overwrites old input."""
        get_queued_input()  # Clear state

        queue_input("first")
        queue_input("second")
        assert get_queued_input() == "second"


class TestInterruptContent:
    """Tests for interrupt content functionality."""

    def test_set_and_retrieve(self):
        """Test that interrupt content can be set and retrieved."""
        # Clear any existing state
        get_interrupt_content()

        assert not has_interrupt_content()
        set_interrupt_content("interrupt text")
        assert has_interrupt_content()
        assert get_interrupt_content() == "interrupt text"
        assert not has_interrupt_content()
        assert get_interrupt_content() is None


class TestStdinChecks:
    """Tests for stdin checking functionality."""

    def test_check_stdin_non_tty(self):
        """Test that check_stdin_has_data returns False for non-TTY stdin."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            assert not check_stdin_has_data()

    def test_read_stdin_non_tty(self):
        """Test that read_stdin_nonblocking returns None for non-TTY stdin."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            assert read_stdin_nonblocking() is None


class TestInputDialog:
    """Tests for the input dialog functionality."""

    def test_dialog_interrupt(self):
        """Test that dialog returns INTERRUPT for 'i' input."""
        with patch("builtins.input", return_value="i"):
            action, text = show_input_dialog("test input")
            assert action == InputAction.INTERRUPT
            assert text == "test input"

    def test_dialog_queue(self):
        """Test that dialog returns QUEUE for 'q' input."""
        with patch("builtins.input", return_value="q"):
            action, text = show_input_dialog("test input")
            assert action == InputAction.QUEUE
            assert text == "test input"

    def test_dialog_cancel(self):
        """Test that dialog returns CANCEL for 'c' input."""
        with patch("builtins.input", return_value="c"):
            action, text = show_input_dialog("test input")
            assert action == InputAction.CANCEL
            assert text == "test input"

    def test_dialog_cancel_on_eof(self):
        """Test that dialog returns CANCEL on EOFError."""
        with patch("builtins.input", side_effect=EOFError):
            action, text = show_input_dialog("test input")
            assert action == InputAction.CANCEL

    def test_dialog_cancel_on_interrupt(self):
        """Test that dialog returns CANCEL on KeyboardInterrupt."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            action, text = show_input_dialog("test input")
            assert action == InputAction.CANCEL
