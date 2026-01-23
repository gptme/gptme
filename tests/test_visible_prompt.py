"""Tests for VisiblePromptCollector."""

from gptme.message import Message


class TestVisiblePromptCollector:
    """Test cases for the visible prompt collector."""

    def test_import(self):
        """Test that the module can be imported."""
        from gptme.util.async_input import VisiblePromptCollector

        assert VisiblePromptCollector is not None

    def test_init(self):
        """Test basic initialization."""
        from gptme.util.async_input import VisiblePromptCollector

        collector = VisiblePromptCollector()

        assert collector is not None
        assert not collector.is_running
        assert collector.get_queue_size() == 0

    def test_init_with_options(self):
        """Test initialization with custom options."""
        from gptme.util.async_input import VisiblePromptCollector

        callback_called: list[str] = []

        def on_input(text: str) -> None:
            callback_called.append(text)

        collector = VisiblePromptCollector(on_input=on_input, max_queue_size=50)

        assert collector is not None
        assert collector._max_queue_size == 50

    def test_start_stop_no_tty(self, monkeypatch):
        """Test that start is a no-op when stdin is not a TTY."""
        import sys

        from gptme.util.async_input import VisiblePromptCollector

        # Mock stdin.isatty() to return False
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        collector = VisiblePromptCollector()

        collector.start()
        # Should not actually start because stdin is not a TTY
        assert not collector.is_running

        collector.stop()  # Should be safe to call even when not running

    def test_queue_operations(self):
        """Test queue operations - internal queue with thread-safe methods."""
        from gptme.util.async_input import VisiblePromptCollector

        collector = VisiblePromptCollector()

        assert collector.get_queue_size() == 0
        assert not collector.has_messages()
        assert collector.get_message() is None

        # Directly put a message into the internal queue (simulating async input)
        msg = Message("user", "test message")
        collector._message_queue.put(msg)

        assert collector.get_queue_size() == 1
        assert collector.has_messages()

        # Get the message back
        retrieved = collector.get_message()
        assert retrieved is not None
        assert retrieved.content == "test message"
        assert collector.get_queue_size() == 0

    def test_clear_queue(self):
        """Test clearing the queue."""
        from gptme.util.async_input import VisiblePromptCollector

        collector = VisiblePromptCollector()

        # Add some messages
        for i in range(3):
            collector._message_queue.put(Message("user", f"msg{i}"))

        assert collector.get_queue_size() == 3

        collector.clear_queue()

        assert collector.get_queue_size() == 0
        assert not collector.has_messages()

    def test_stop_when_not_running(self):
        """Test that stop is safe to call when not running."""
        from gptme.util.async_input import VisiblePromptCollector

        collector = VisiblePromptCollector()

        # Should not raise
        collector.stop()
        collector.stop()  # Multiple stops should be safe
