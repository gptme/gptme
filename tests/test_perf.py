"""Performance benchmarks for gptme core latency, tracked over time.

Covers the core areas from issue #4373:
- ToolUse.iter_from_content parsing at 1k / 10k / 50k chars
- LogManager.append/write on growing logs
- prepare_messages on a 500-message conversation
- len_tokens (token counting) at different content sizes

TUI-specific benchmarks live in test_perf_tui.py (requires textual extra).

Each benchmark runs ITERS iterations and records the median wall-clock time.
Thresholds are generous to survive CI noise; the real value is the stored JSON
trend, not the pass/fail gate alone.

Run manually for absolute numbers on a quiet machine:
    pytest tests/test_perf.py tests/test_perf_tui.py -v -m perf
    pytest tests/test_perf.py -v -m "perf and not slow"
"""

from __future__ import annotations

import math
import statistics
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ITERS = 10  # iterations per benchmark; enough for a stable median


def _median_ms(fn: Callable[[], object], n: int = ITERS) -> float:
    """Run fn n times and return median elapsed milliseconds."""
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def _p95_ms(fn: Callable[[], object], n: int = ITERS) -> float:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    idx = math.ceil(n * 0.95) - 1
    return samples[idx]


def _make_long_content(chars: int) -> str:
    """Deterministic prose without any tool-call syntax."""
    word = "lorem "
    return (word * (chars // len(word) + 1))[:chars]


def _make_tool_content(chars: int, n_tools: int = 3) -> str:
    """Content with n_tools shell blocks interspersed in prose."""
    block = "```shell\necho hello\n```\n\n"
    prose_each = max(10, (chars - len(block) * n_tools) // (n_tools + 1))
    parts = []
    for _ in range(n_tools):
        parts.append(_make_long_content(prose_each))
        parts.append(block)
    parts.append(_make_long_content(prose_each))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Tool parsing benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestToolParsing:
    """ToolUse.iter_from_content parsing latency at different message sizes."""

    @pytest.fixture(autouse=True)
    def _init(self):
        from gptme.message import set_output_format
        from gptme.tools import init_tools

        init_tools(allowlist=["shell"])
        set_output_format("quiet")

    @pytest.mark.parametrize("chars", [1_000, 10_000, 50_000])
    def test_parse_markdown_no_tools(self, chars: int):
        """Parsing a large prose message that contains no tool calls."""
        from gptme.tools.base import ToolUse

        content = _make_long_content(chars)
        median = _median_ms(lambda: list(ToolUse.iter_from_content(content)))
        # Parse should stay well under 500ms even for 50k chars
        assert median < 500, f"iter_from_content({chars}ch) median {median:.1f}ms"

    @pytest.mark.parametrize("chars", [1_000, 10_000, 50_000])
    def test_parse_markdown_with_tools(self, chars: int):
        """Parsing a message with embedded shell tool calls."""
        from gptme.tools.base import ToolUse

        content = _make_tool_content(chars)
        median = _median_ms(lambda: list(ToolUse.iter_from_content(content)))
        assert median < 500, (
            f"iter_from_content({chars}ch w/ tools) median {median:.1f}ms"
        )

    def test_parse_streaming_incremental(self):
        """Incremental streaming parse: simulate one-word-at-a-time delivery."""
        from gptme.tools.base import ToolUse

        full = _make_tool_content(5_000)
        words = full.split()
        accumulated = ""
        t0 = time.perf_counter()
        for word in words:
            accumulated += word + " "
            list(ToolUse.iter_from_content(accumulated, streaming=True))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # Total cost of streaming parse across all words: <2s
        assert elapsed_ms < 2000, (
            f"streaming parse of {len(words)} words took {elapsed_ms:.1f}ms"
        )


# ---------------------------------------------------------------------------
# LogManager append/write benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestLogManagerPerf:
    """LogManager.append and write latency as the log grows."""

    def test_append_latency_small_log(self, tmp_path: Path):
        """append() on a fresh (empty) log."""
        from gptme.logmanager import LogManager
        from gptme.message import Message

        manager = LogManager([], logdir=tmp_path / "small", lock=False)
        msg = Message("user", "hello world")
        median = _median_ms(lambda: manager.append(msg))
        assert median < 200, f"append on empty log median {median:.1f}ms"

    @pytest.mark.slow
    def test_append_latency_large_log(self, tmp_path: Path):
        """append() on a log with 500 messages (write() rewrites the whole file)."""
        from gptme.logmanager import LogManager
        from gptme.message import Message

        msgs = [
            Message("user" if i % 2 == 0 else "assistant", f"msg {i}")
            for i in range(500)
        ]
        manager = LogManager(msgs, logdir=tmp_path / "large", lock=False)
        msg = Message("user", "one more")
        median = _median_ms(lambda: manager.append(msg), n=5)
        # write() rewrites the JSONL; 500-message log must not take >1s
        assert median < 1000, f"append on 500-msg log median {median:.1f}ms"

    def test_write_jsonl_latency(self, tmp_path: Path):
        """Log.write_jsonl latency for a 200-message log."""
        from gptme.logmanager import Log
        from gptme.message import Message

        msgs = [
            Message(
                "user" if i % 2 == 0 else "assistant",
                f"message {i} " + "x" * 100,
            )
            for i in range(200)
        ]
        log = Log(msgs)
        out = tmp_path / "bench.jsonl"
        median = _median_ms(lambda: log.write_jsonl(out))
        assert median < 500, f"write_jsonl(200 msgs) median {median:.1f}ms"


# ---------------------------------------------------------------------------
# Token counting benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestTokenCounting:
    """len_tokens latency at different content sizes."""

    @pytest.mark.parametrize("chars", [1_000, 10_000, 50_000])
    def test_len_tokens_single_message(self, chars: int):
        from gptme.message import Message
        from gptme.util.tokens import len_tokens

        msg = Message("user", _make_long_content(chars))
        model = "openai/gpt-4o"
        median = _median_ms(lambda: len_tokens(msg, model=model))
        assert median < 500, f"len_tokens({chars}ch) median {median:.1f}ms"

    def test_len_tokens_conversation(self):
        """Token counting for a 100-message conversation."""
        from gptme.message import Message
        from gptme.util.tokens import len_tokens

        msgs = [
            Message(
                "user" if i % 2 == 0 else "assistant",
                f"message {i} " + "w " * 50,
            )
            for i in range(100)
        ]
        model = "openai/gpt-4o"
        median = _median_ms(lambda: len_tokens(msgs, model=model))
        assert median < 500, f"len_tokens(100 msgs) median {median:.1f}ms"


# ---------------------------------------------------------------------------
# Shell tool round-trip benchmark
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestShellToolPerf:
    """Shell tool execute latency for a trivial command."""

    @pytest.fixture(autouse=True)
    def _init(self):
        from gptme.message import set_output_format
        from gptme.tools import init_tools

        init_tools(allowlist=["shell"])
        set_output_format("quiet")

    def test_shell_trivial_roundtrip(self):
        """Round-trip latency for a no-op shell command."""
        from gptme.message import Message
        from gptme.tools import execute_msg

        msg = Message("assistant", "```shell\necho hello\n```")
        median = _median_ms(lambda: list(execute_msg(msg)))
        # Shell round-trip (fork + exec + read) should be under 500ms
        assert median < 500, f"shell trivial round-trip median {median:.1f}ms"


# ---------------------------------------------------------------------------
# prepare_messages benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestPrepareMessages:
    """prepare_messages latency on conversations of different sizes."""

    @pytest.fixture(autouse=True)
    def _init(self):
        from gptme.init import init
        from gptme.tools import init_tools

        init(
            "mock/echo",
            interactive=False,
            tool_allowlist=["shell"],
            tool_format="markdown",
            no_confirm=True,
        )
        init_tools(allowlist=["shell"])

    @pytest.mark.parametrize("n_msgs", [50, 200, 500])
    def test_prepare_messages(self, n_msgs: int, tmp_path: Path):
        from gptme.logmanager import prepare_messages
        from gptme.message import Message

        msgs = [
            Message(
                "user" if i % 2 == 0 else "assistant",
                f"msg {i} " + "w " * 20,
            )
            for i in range(n_msgs)
        ]
        median = _median_ms(lambda: prepare_messages(msgs, workspace=tmp_path))
        # prepare_messages on 500 messages must stay under 2s
        assert median < 2000, f"prepare_messages({n_msgs} msgs) median {median:.1f}ms"
