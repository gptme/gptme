"""Tests for the InputCollector class."""

from gptme.message import Message
from gptme.util.input_collector import InputCollector


def test_input_collector_init():
    """Test InputCollector initialization."""
    queue: list[Message] = []
    collector = InputCollector(queue, max_queue_size=10)
    assert collector.prompt_queue is queue
    assert collector.max_queue_size == 10
    assert not collector._running


def test_input_collector_start_stop():
    """Test starting and stopping the collector."""
    queue: list[Message] = []
    collector = InputCollector(queue)

    # Can't really test input collection without stdin
    # but we can test the start/stop lifecycle doesn't crash
    # Note: start() won't actually start a thread if stdin is not a TTY
    collector.start()
    collector.stop()
    assert not collector._running


def test_input_collector_queue_input():
    """Test the _queue_input method directly."""
    queue: list[Message] = []
    collector = InputCollector(queue, max_queue_size=3)

    # Queue some input
    collector._queue_input("hello")
    assert len(queue) == 1
    assert queue[0].content == "hello"
    assert queue[0].role == "user"

    collector._queue_input("world")
    assert len(queue) == 2

    collector._queue_input("test")
    assert len(queue) == 3

    # Queue should be full now
    collector._queue_input("overflow")
    assert len(queue) == 3  # Should not add more


def test_input_collector_callback():
    """Test the on_input_queued callback."""
    queue: list[Message] = []
    callback_counts: list[int] = []

    def on_queued(count: int):
        callback_counts.append(count)

    collector = InputCollector(queue, on_input_queued=on_queued)

    collector._queue_input("first")
    collector._queue_input("second")

    assert callback_counts == [1, 2]


def test_input_collector_get_queue_size():
    """Test getting queue size."""
    queue: list[Message] = []
    collector = InputCollector(queue)

    assert collector.get_queue_size() == 0

    collector._queue_input("test")
    assert collector.get_queue_size() == 1

    collector._queue_input("test2")
    assert collector.get_queue_size() == 2
