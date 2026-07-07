"""Tests for evidence replay (BM25-based context recovery)."""

import json
import tempfile
from pathlib import Path

from gptme.message import Message
from gptme.util.replay import inject_relevant_evidence, score_messages_bm25

# --- score_messages_bm25 ---


def test_bm25_returns_relevant_first():
    messages = [
        {"role": "user", "content": "Tell me about pandas dataframes"},
        {
            "role": "assistant",
            "content": "Pandas is a Python library for data analysis",
        },
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris"},
    ]
    results = score_messages_bm25(messages, "pandas dataframe library")
    assert results, "Expected at least one result"
    top_idx = results[0][0]
    # The pandas messages should score highest
    assert top_idx in (0, 1), (
        f"Expected pandas-related message at top, got idx={top_idx}"
    )


def test_bm25_empty_inputs():
    assert score_messages_bm25([], "query") == []
    assert score_messages_bm25([{"role": "user", "content": "hi"}], "") == []


def test_bm25_no_match():
    messages = [{"role": "user", "content": "hello world"}]
    results = score_messages_bm25(messages, "xylophone umbrella")
    # No matches = empty list
    assert results == []


def test_bm25_scores_positive():
    messages = [
        {"role": "user", "content": "Python programming language"},
        {"role": "user", "content": "JavaScript frontend development"},
    ]
    results = score_messages_bm25(messages, "Python")
    for _idx, score in results:
        assert score > 0, "All returned scores should be positive"


# --- inject_relevant_evidence ---


def _make_master_log(messages: list[dict], tmpdir: Path) -> Path:
    logfile = tmpdir / "conversation.jsonl"
    with open(logfile, "w") as f:
        f.writelines(json.dumps(msg) + "\n" for msg in messages)
    return logfile


def test_inject_adds_evidence_when_compacted():
    """Evidence should be injected when master log has more messages than working context."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        # Master log has 10 messages
        master_msgs = [
            {"role": "user", "content": f"Question {i} about Python decorators"}
            for i in range(10)
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        # Working context is compacted to just 2 messages
        working = [
            Message("system", "You are a helpful assistant.", pinned=True),
            Message("user", "Tell me about Python decorators"),
        ]

        result = inject_relevant_evidence(working, logfile, top_k=3)

        # Should have more messages than the compacted working context
        assert len(result) > len(working), "Evidence should have been injected"
        # Injected messages should be system messages
        injected = [m for m in result if m.role == "system" and "Evidence" in m.content]
        assert injected, "Should have injected evidence messages"


def test_inject_no_evidence_when_context_not_compacted():
    """When master log has same or fewer messages than working context, skip injection."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        logfile = _make_master_log(msgs, tmpdir)

        working = [
            Message("user", "Hello"),
            Message("assistant", "Hi there"),
        ]

        result = inject_relevant_evidence(working, logfile, top_k=3)
        assert result == working, "Should return unchanged when no compaction occurred"


def test_inject_skips_already_present_content():
    """Evidence already visible in working context should not be re-injected."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        content = "This is about Python decorators"
        master_msgs = [
            {"role": "user", "content": content},
            {"role": "user", "content": "Other content about something else"},
            {"role": "user", "content": "More different content"},
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        # Working context already has the python decorators message
        working = [
            Message("user", content),
            Message("user", "Tell me more about decorators"),
        ]

        result = inject_relevant_evidence(working, logfile, top_k=5)

        # Count how many times the exact content appears
        matching = [
            m for m in result if content in m.content and "Evidence" not in m.content
        ]
        assert len(matching) == 1, (
            "Should not duplicate content already in working context"
        )


def test_inject_no_user_message():
    """No injection when there's no user message to derive the query from."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        logfile = _make_master_log(
            [{"role": "assistant", "content": "hello"} for _ in range(5)], tmpdir
        )
        working = [Message("system", "system msg")]
        result = inject_relevant_evidence(working, logfile)
        assert result == working


def test_inject_missing_logfile():
    """Gracefully handles a missing master log."""
    working = [Message("user", "test")]
    result = inject_relevant_evidence(working, Path("/nonexistent/path.jsonl"))
    assert result == working


def test_inject_inserts_after_pinned_system():
    """Injected evidence should appear after initial pinned system messages."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        master_msgs = [
            {
                "role": "user",
                "content": f"Python tip {i} about decorators and functions",
            }
            for i in range(8)
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        pinned_sys = Message("system", "You are a coding assistant.", pinned=True)
        working = [
            pinned_sys,
            Message("user", "What are Python decorators?"),
        ]

        result = inject_relevant_evidence(working, logfile, top_k=2)

        if len(result) > len(working):
            # First message should still be the pinned system message
            assert result[0].role == "system" and result[0].pinned, (
                "Pinned system message should remain first"
            )
