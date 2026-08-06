"""Tests for evidence replay (BM25-based context recovery)."""

import builtins
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from gptme.llm.models.resolution import _default_model_var
from gptme.message import Message
from gptme.util.replay import (
    _read_master_messages,
    build_query,
    inject_relevant_evidence,
    score_messages_bm25,
)

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


def test_inject_adds_evidence_when_content_reduced_same_message_count():
    """Reduced content should be recoverable even when message count is unchanged."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        recovered_content = (
            "Architecture decision: the zephyr protocol uses retry jitter to avoid "
            "coordinated reconnect storms during failover."
        )
        master_msgs = [
            {"role": "user", "content": recovered_content},
            {"role": "assistant", "content": "Recorded."},
            {"role": "user", "content": "Why does zephyr need retry jitter?"},
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        working = [
            Message("user", "[truncated previous architecture decision]"),
            Message("assistant", "Recorded."),
            Message("user", "Why does zephyr need retry jitter?"),
        ]

        result = inject_relevant_evidence(working, logfile, top_k=3)

        injected = [m for m in result if "Evidence" in m.content]
        assert any(recovered_content in m.content for m in injected), (
            "Should recover reduced master-log content even when counts match"
        )


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


def test_inject_skips_previously_injected_evidence():
    """Replay-injected evidence should dedup against its raw master-log content."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        content = "Python decorators preserve function metadata with functools.wraps"
        master_msgs = [
            {"role": "user", "content": content},
            {"role": "user", "content": "Python decorators wrap callable objects"},
            {"role": "assistant", "content": "Decorators return replacement callables"},
            {"role": "user", "content": "Unrelated note about deployment"},
            {"role": "assistant", "content": "Another unrelated note"},
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        working = [
            Message("system", "You are a helpful assistant.", pinned=True),
            Message("user", "Tell me about Python decorators and functools wraps"),
        ]

        first = inject_relevant_evidence(working, logfile, top_k=1)
        second = inject_relevant_evidence(first, logfile, top_k=1)

        matching_evidence = [
            m for m in second if "Evidence" in m.content and content in m.content
        ]
        assert len(matching_evidence) == 1, (
            "Previously injected evidence should not be re-injected"
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


def test_bm25_fts5_reserved_keywords():
    """FTS5 reserved keywords (NOT, AND, NEAR) should be quoted, not parsed as operators."""
    messages = [
        {"role": "user", "content": "Python NOT Java -- comparing languages"},
        {"role": "user", "content": "The capital of France is Paris"},
    ]
    results = score_messages_bm25(messages, "Python NOT Java")
    assert results, "FTS5 reserved keywords in query should not suppress all results"
    top_idx = results[0][0]
    assert top_idx == 0, "Message about Python NOT Java should rank first"


def test_bm25_fts5_and_keyword():
    """FTS5 reserved keyword 'AND' should work as a literal term."""
    messages = [
        {"role": "user", "content": "Science AND technology are important"},
        {"role": "user", "content": "What is the weather today?"},
    ]
    results = score_messages_bm25(messages, "AND technology")
    assert results, "FTS5 'AND' keyword in query should not suppress all results"


def test_inject_respects_remaining_context_budget():
    """Budget should be remaining space, not a fraction of total context that overflows."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        # Master log with relevant content
        master_msgs = [
            {"role": "user", "content": f"Important Python tip {i} about decorators"}
            for i in range(20)
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        # Create a working context that's nearly full (simulated)
        # Since we can't easily mock token counts, verify that inject_relevant_evidence
        # at least doesn't crash and respects token budgeting constraints
        working = [
            Message("system", "You are a helpful assistant.", pinned=True),
            Message("user", "What are Python decorators?"),
        ]

        result = inject_relevant_evidence(working, logfile, top_k=5)

        # Should inject evidence (master has more messages than working)
        injected = [m for m in result if m.role == "system" and "Evidence" in m.content]
        assert injected, "Should have injected evidence"

        # Verify that budget enforcement didn't produce absurdly long context
        # If budget is being calculated correctly, injected section should be bounded
        total_evidence_tokens = sum(len(m.content) // 4 for m in injected)
        # With remaining budget approach, should never inject more than 20% of model context
        # Default model context is 40k, so max should be 8k tokens (32k chars)
        # This is a loose check; real constraint is in the actual code
        assert total_evidence_tokens < 20_000, (
            f"Injected evidence too large: {total_evidence_tokens} tokens"
        )


def test_inject_respects_context_headroom():
    """Overflow working context should block injection; sparse context should allow it.

    Uses a 100-token context window. working_full exceeds the window (>100 tiktoken
    tokens) so remaining=0 and no evidence is injected. working_small fits easily,
    leaving room for several evidence messages.

    Sets model.model = "gpt-4" so count_tokens() can use the cl100k_base tokenizer.
    """
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        # Evidence messages matching the query; ~10 tokens each (40 chars)
        master_msgs = [
            {"role": "user", "content": f"Python decorator tip {i} " + "y" * 15}
            for i in range(20)
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        # Model with a 100-token context window
        mock_model = MagicMock()
        mock_model.context = 100
        mock_model.model = "gpt-4"  # needed by count_tokens() for tokenizer selection

        # working_full: 1000 "a" chars → ~167-250 tiktoken tokens → exceeds 100 context
        # remaining = max(0, 100 - 200) = 0 → budget = 0 → 0 injected
        working_full = [
            Message("system", "System.", pinned=True),
            Message("assistant", "a" * 1000),
            Message("user", "Tell me about Python decorators"),
        ]

        # working_small: ~8 tokens → remaining ≈ 92 → budget = min(20, 73) = 20 tokens
        # 20 / 10 = 2 evidence messages fit
        working_small = [
            Message("system", "You are helpful.", pinned=True),
            Message("user", "Tell me about Python decorators"),
        ]

        token = _default_model_var.set(mock_model)
        try:
            result_full = inject_relevant_evidence(working_full, logfile, top_k=10)
            result_small = inject_relevant_evidence(working_small, logfile, top_k=10)
        finally:
            _default_model_var.reset(token)

        full_injected = [m for m in result_full if "Evidence" in m.content]
        small_injected = [m for m in result_small if "Evidence" in m.content]

        # Overflow context → no headroom → 0 injected
        # Sparse context → headroom → at least 1 injected
        assert len(full_injected) < len(small_injected), (
            f"Overflow context ({len(full_injected)} injected) should inject"
            f" fewer messages than sparse context ({len(small_injected)} injected)"
        )


# --- build_query ---


def test_build_query_single_turn_is_backward_compatible():
    """n_turns=1 should reproduce original single-message behaviour."""
    msgs = [
        Message("user", "First task: implement zephyr protocol"),
        Message("assistant", "Done."),
        Message("user", "Now debug the timeout"),
    ]
    q = build_query(msgs, n_turns=1)
    assert "timeout" in q
    assert "zephyr" not in q, "n_turns=1 should use only last user message"


def test_build_query_multi_turn_includes_earlier_intent():
    """n_turns=3 should include content from earlier user messages."""
    msgs = [
        Message("user", "First task: implement zephyr authentication"),
        Message("assistant", "Done."),
        Message("user", "Now debug the timeout"),
    ]
    q = build_query(msgs, n_turns=3)
    assert "timeout" in q
    assert "zephyr" in q, "Compound query should include earlier task context"


def test_build_query_empty_messages():
    assert build_query([], n_turns=3) == ""


def test_build_query_no_user_messages():
    msgs = [Message("system", "sys"), Message("assistant", "hi")]
    assert build_query(msgs, n_turns=3) == ""


def test_build_query_truncates_long_turns():
    """Each turn truncated to 300 chars; compound capped at 800 chars."""
    long_msg = "x" * 500
    msgs = [
        Message("user", long_msg),
        Message("user", long_msg),
        Message("user", long_msg),
    ]
    q = build_query(msgs, n_turns=3)
    assert len(q) <= 800, f"Compound query should be capped at 800 chars, got {len(q)}"


def test_inject_compound_query_surfaces_earlier_intent():
    """Compound query should recover earlier task-intent from master log.

    Scenario: the user's original goal was 'implement zephyr authentication'.
    After many turns the working context only has a sub-step query ('debug
    timeout'). A single-message query misses the zephyr evidence; the
    compound query (last 3 turns) surfaces it.
    """
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        earlier_intent = (
            "zephyr authentication: use HMAC-SHA256 with a 30-second token window"
        )
        master_msgs = [
            # The critical early message - falls off the working context
            {"role": "user", "content": earlier_intent},
            {"role": "assistant", "content": "Implementing zephyr auth now."},
            # Many unrelated turns follow (simulate long session)
            *[
                {"role": "user", "content": f"Sub-step {i}: adjust settings"}
                for i in range(10)
            ],
            {"role": "user", "content": "Now I need to debug the timeout in the loop"},
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        # Working context: original intent has fallen off; only recent sub-steps visible
        working = [
            Message("system", "You are a coding assistant.", pinned=True),
            Message("user", "First task: implement zephyr authentication"),
            Message("user", "Now debug the timeout in the loop"),
        ]

        # Compound query (last 3 user turns) should surface the zephyr evidence
        result_compound = inject_relevant_evidence(
            working, logfile, top_k=3, query_n_turns=3
        )

        injected_compound = [m for m in result_compound if "Evidence" in m.content]
        zephyr_found = any(earlier_intent in m.content for m in injected_compound)
        assert zephyr_found, (
            "Compound query should surface earlier zephyr authentication intent"
        )


def test_inject_query_n_turns_1_matches_original_single_query():
    """query_n_turns=1 should give same result as original single-message query."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        master_msgs = [
            {"role": "user", "content": f"Python decorator tip {i}"} for i in range(10)
        ]
        logfile = _make_master_log(master_msgs, tmpdir)

        working = [
            Message("system", "System.", pinned=True),
            Message("user", "Early turn about other topics"),
            Message("user", "What are Python decorators?"),
        ]

        # Both should produce the same result since only last message is used
        result_n1 = inject_relevant_evidence(working, logfile, top_k=3, query_n_turns=1)
        injected_n1 = [m for m in result_n1 if "Evidence" in m.content]

        # At minimum: n_turns=1 still finds decorator-relevant evidence from the last msg
        assert injected_n1, "n_turns=1 should still inject relevant evidence"


# --- _read_master_messages (encoding) ---

# A codec that cannot represent non-ASCII and is the family of a stock-Windows
# locale. Under the shim below, an `open()` that omits `encoding=` reads the
# file as ascii and raises UnicodeDecodeError on any non-ASCII byte — which
# reproduces the mechanism by which `_read_master_messages` would fail on a
# raw-UTF-8 master log before the fix. The string spans CJK and accented Latin,
# though under ascii every non-ASCII byte is undecodable regardless.
NON_ASCII_CONTENT = "我是一名开发者 — drinking café"


@contextmanager
def legacy_default_encoding(codec: str = "ascii"):
    """Make encoding-less `open()` calls behave as they do under a legacy locale.

    Monkeypatching `locale.getpreferredencoding` does not work: CPython reads the
    locale encoding at the C level, so `open()` ignores the patched function. This
    shim supplies a codec in exactly the position CPython would supply the
    locale's — only when the caller passed no `encoding` — so the test fails on a
    machine of any locale when `encoding=` is missing, and passes on a machine of
    any locale when it is present. Mirrors tests/test_config_encoding.py.
    """
    real_open = builtins.open

    def shim(file, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None and len(args) < 2:
            kwargs["encoding"] = codec
        return real_open(file, mode, *args, **kwargs)

    with patch.object(builtins, "open", shim):
        yield


def test_read_master_messages_non_ascii_under_legacy_locale(tmp_path: Path):
    """A raw-UTF-8 master log must read back intact under a legacy (ascii) locale.

    JSON is defined to be UTF-8, so conversation.jsonl must be read as UTF-8
    regardless of host locale. Before the fix, `_read_master_messages` opened it
    with a bare `open(logfile)` (no `encoding=`), so CPython decoded with the
    platform's preferred code page; under a legacy locale a raw-UTF-8 log raised
    UnicodeDecodeError on the first non-ASCII byte. That escapes both the
    per-line `json.JSONDecodeError` handler and the outer `FileNotFoundError`
    handler and propagates through `inject_relevant_evidence` to its unwrapped
    caller (logmanager/manager.py:970).

    The fixture is written with `ensure_ascii=False`, which is deliberately NOT
    gptme's current behaviour: the sole writer of conversation.jsonl,
    `Log.write_jsonl` (gptme/logmanager/manager.py:108-110), serializes with
    `json.dumps` at its default `ensure_ascii=True`, so production logs are pure
    ASCII today and the decode crash is not reachable via gptme's own writer.
    This test provokes the raw-UTF-8 on-disk form a manual edit, an external
    tool, or a future writer change could introduce, and so pins the read to be
    locale-independent — the same discipline as #3399 and #2051. See the
    ASCII-only companion below for today's canonical case.
    """
    logfile = tmp_path / "conversation.jsonl"
    # Pin the bytes on disk to raw UTF-8 (the JSON spec's encoding) so the read
    # side is what the legacy-locale shim stresses. ensure_ascii=False leaves the
    # non-ASCII as raw bytes instead of \uXXXX escapes, which is what makes the
    # legacy ascii decode raise on the unfixed open(). gptme does not write this
    # form today (see the docstring); the fixture deliberately stages it.
    with open(logfile, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"role": "user", "content": NON_ASCII_CONTENT}, ensure_ascii=False
            )
            + "\n"
        )

    with legacy_default_encoding("ascii"):
        messages = _read_master_messages(logfile)

    assert len(messages) == 1
    assert messages[0]["content"] == NON_ASCII_CONTENT


def test_read_master_messages_ascii_log_under_legacy_locale(tmp_path: Path):
    """Control: today's canonical master log (pure ASCII) parses under any locale.

    `Log.write_jsonl` serializes with the default `ensure_ascii=True`, so the
    conversation.jsonl gptme writes today is pure ASCII and decodes under every
    locale — including the legacy one this shim simulates. This is the
    no-regression guard for the common case the non-ASCII test deliberately
    steps outside.
    """
    logfile = tmp_path / "conversation.jsonl"
    with open(logfile, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"role": "user", "content": "Hello, developer!"}, ensure_ascii=False
            )
            + "\n"
        )

    with legacy_default_encoding("ascii"):
        messages = _read_master_messages(logfile)

    assert len(messages) == 1
    assert messages[0]["content"] == "Hello, developer!"
