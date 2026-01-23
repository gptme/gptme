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

        prompt_queue: list[Message] = []
        collector = VisiblePromptCollector(prompt_queue)

        assert collector is not None
        assert not collector.is_running
        assert collector.get_queue_size() == 0

    def test_init_with_options(self):
        """Test initialization with custom options."""
        from gptme.util.async_input import VisiblePromptCollector

        prompt_queue: list[Message] = []
        callback_called: list[str] = []

        def on_input(text: str) -> None:
            callback_called.append(text)

        collector = VisiblePromptCollector(
            prompt_queue, on_input=on_input, max_queue_size=50
        )

        assert collector is not None
        assert collector._max_queue_size == 50

    def test_start_stop_no_tty(self, monkeypatch):
        """Test that start is a no-op when stdin is not a TTY."""
        import sys

        from gptme.util.async_input import VisiblePromptCollector

        # Mock stdin.isatty() to return False
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        prompt_queue: list[Message] = []
        collector = VisiblePromptCollector(prompt_queue)

        collector.start()
        # Should not actually start because stdin is not a TTY
        assert not collector.is_running

        collector.stop()  # Should be safe to call even when not running

    def test_get_queue_size(self):
        """Test queue size tracking."""
        from gptme.util.async_input import VisiblePromptCollector

        prompt_queue: list[Message] = []
        collector = VisiblePromptCollector(prompt_queue)

        assert collector.get_queue_size() == 0

        # Add a message to the queue manually (simulating what happens on input)
        prompt_queue.append(Message("user", "test"))
        assert collector.get_queue_size() == 1

    def test_stop_when_not_running(self):
        """Test that stop is safe to call when not running."""
        from gptme.util.async_input import VisiblePromptCollector

        prompt_queue: list[Message] = []
        collector = VisiblePromptCollector(prompt_queue)

        # Should not raise
        collector.stop()
        collector.stop()  # Multiple stops should be safe
