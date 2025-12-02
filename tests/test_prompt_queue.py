"""Tests for prompt queueing functionality."""
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


class TestInputMonitor:
    """Tests for InputMonitor class."""

    def test_monitor_initialization(self):
        """Test InputMonitor initializes correctly."""
        from gptme.prompt_queue import InputMonitor

        monitor = InputMonitor()
        assert not monitor.has_input()
        assert monitor.get_input() is None

    def test_monitor_not_tty(self):
        """Test monitor doesn't start when stdin is not a TTY."""
        from gptme.prompt_queue import InputMonitor

        monitor = InputMonitor()
        with patch.object(sys.stdin, "isatty", return_value=False):
            monitor.start()
            # Thread should not be created
            assert monitor._thread is None

    def test_monitor_stop(self):
        """Test monitor stops cleanly."""
        from gptme.prompt_queue import InputMonitor

        monitor = InputMonitor()
        monitor._active.set()
        monitor.stop()
        assert not monitor._active.is_set()

    def test_monitor_clear(self):
        """Test monitor clears state correctly."""
        from gptme.prompt_queue import InputMonitor

        monitor = InputMonitor()
        monitor._captured_input = "test"
        monitor._input_ready.set()

        monitor.clear()

        assert monitor._captured_input is None
        assert not monitor._input_ready.is_set()


class TestQueueAction:
    """Tests for QueueAction enum."""

    def test_queue_action_values(self):
        """Test QueueAction has expected values."""
        from gptme.prompt_queue import QueueAction

        assert QueueAction.RUN_NOW.value == "run"
        assert QueueAction.QUEUE.value == "queue"
        assert QueueAction.DISCARD.value == "discard"


class TestPromptQueueDialog:
    """Tests for prompt_queue_dialog function."""

    def test_dialog_run_now(self):
        """Test dialog returns RUN_NOW for 'r' input."""
        from gptme.prompt_queue import QueueAction, prompt_queue_dialog

        with patch("builtins.input", return_value="r"):
            result = prompt_queue_dialog("test input")
            assert result == QueueAction.RUN_NOW

    def test_dialog_queue(self):
        """Test dialog returns QUEUE for 'q' input."""
        from gptme.prompt_queue import QueueAction, prompt_queue_dialog

        with patch("builtins.input", return_value="q"):
            result = prompt_queue_dialog("test input")
            assert result == QueueAction.QUEUE

    def test_dialog_discard(self):
        """Test dialog returns DISCARD for 'd' input."""
        from gptme.prompt_queue import QueueAction, prompt_queue_dialog

        with patch("builtins.input", return_value="d"):
            result = prompt_queue_dialog("test input")
            assert result == QueueAction.DISCARD

    def test_dialog_keyboard_interrupt(self):
        """Test dialog returns DISCARD on keyboard interrupt."""
        from gptme.prompt_queue import QueueAction, prompt_queue_dialog

        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = prompt_queue_dialog("test input")
            assert result == QueueAction.DISCARD


class TestGlobalFunctions:
    """Tests for module-level functions."""

    def test_start_stop_monitoring(self):
        """Test start/stop monitoring functions."""
        from gptme.prompt_queue import (
            start_input_monitoring,
            stop_input_monitoring,
        )

        with patch.object(sys.stdin, "isatty", return_value=False):
            start_input_monitoring()
            stop_input_monitoring()
            # Should not raise

    def test_get_queued_prompt(self):
        """Test get_queued_prompt returns and clears queued prompt."""
        import gptme.prompt_queue as pq

        pq._queued_prompt = "test prompt"

        result = pq.get_queued_prompt()
        assert result == "test prompt"

        # Second call should return None (cleared)
        result = pq.get_queued_prompt()
        assert result is None

    def test_clear_queued_prompt(self):
        """Test clear_queued_prompt clears queued prompt."""
        import gptme.prompt_queue as pq

        pq._queued_prompt = "test prompt"
        pq.clear_queued_prompt()
        assert pq._queued_prompt is None


class TestQueuedInput:
    """Tests for QueuedInput dataclass."""

    def test_queued_input_creation(self):
        """Test QueuedInput dataclass works correctly."""
        from gptme.prompt_queue import QueueAction, QueuedInput

        qi = QueuedInput(text="hello", action=QueueAction.QUEUE)
        assert qi.text == "hello"
        assert qi.action == QueueAction.QUEUE
