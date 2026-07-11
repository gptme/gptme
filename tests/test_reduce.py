from pathlib import Path

import pytest

from gptme.llm.models.resolution import set_default_model
from gptme.llm.models.types import ModelMeta
from gptme.message import Message, len_tokens
from gptme.util import reduce as reduce_mod
from gptme.util.reduce import (
    _truncate_details_blocks,
    limit_log,
    reduce_log,
    truncate_msg,
)

# Project root
root = Path(__file__).parent.parent

# Some large files
readme = root / "README.md"
cli = root / "gptme" / "cli" / "main.py"
htmlindex = root / "gptme" / "server" / "static" / "index.html"

long_msg = Message(
    "system",
    content="\n\n".join(
        f"```{fn.name}\n{fn.read_text()}\n```" for fn in [cli, htmlindex]
    ),
)


def test_truncate_msg():
    len_pre = len_tokens(long_msg, "gpt-4")
    truncated = truncate_msg(long_msg)
    assert truncated is not None
    len_post = len_tokens(truncated, "gpt-4")
    assert len_pre > len_post
    assert "[...]" in truncated.content
    assert "```main.py" in truncated.content
    assert "```index.html" in truncated.content


def test_truncate_details_block():
    """Test that long <details> blocks are truncated."""
    # Generate a long details block with 50 lines
    body_lines = [f"line {i}: some log output here" for i in range(50)]
    body = "\n".join(body_lines)
    content = f"Some context.\n<details>\n<summary>CI logs</summary>\n{body}\n</details>\nMore context."

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    assert "[...]" in truncated.content
    # summary is preserved
    assert "<summary>CI logs</summary>" in truncated.content
    # opening and closing tags preserved
    assert "<details>" in truncated.content
    assert "</details>" in truncated.content
    # surrounding context preserved
    assert "Some context." in truncated.content
    assert "More context." in truncated.content
    # first and last lines preserved
    assert "line 0:" in truncated.content
    assert "line 49:" in truncated.content
    # middle lines removed
    assert "line 25:" not in truncated.content


def test_truncate_details_short():
    """Short <details> blocks should not be truncated."""
    body = "\n".join(f"line {i}" for i in range(5))
    content = f"<details>\n<summary>Short</summary>\n{body}\n</details>"

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    # No truncation needed, should return None
    assert truncated is None


def test_truncate_details_no_summary():
    """<details> without <summary> should still be truncated."""
    body_lines = [f"log line {i}" for i in range(50)]
    body = "\n".join(body_lines)
    content = f"<details>\n{body}\n</details>"

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    assert "[...]" in truncated.content
    assert "log line 0" in truncated.content
    assert "log line 49" in truncated.content


def test_truncate_details_and_codeblocks():
    """Both codeblocks and <details> should be truncated in the same message."""
    code_lines = "\n".join(f"    code line {i}" for i in range(50))
    details_lines = "\n".join(f"detail line {i}" for i in range(50))
    content = (
        f"```python\n{code_lines}\n```\n\n"
        f"<details>\n<summary>Logs</summary>\n{details_lines}\n</details>"
    )

    msg = Message("system", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    # Both should be truncated
    assert truncated.content.count("[...]") == 2


def test_truncate_details_helper():
    """Test the _truncate_details_blocks helper directly."""
    body = "\n".join(f"line {i}" for i in range(30))
    content = f"<details>\n<summary>Test</summary>\n{body}\n</details>"
    result = _truncate_details_blocks(content, lines_pre=5, lines_post=5)
    assert "[...]" in result
    assert "line 0" in result
    assert "line 29" in result
    assert "line 15" not in result


def test_truncate_details_nested():
    """Nested <details> blocks should be handled correctly (only outer truncated)."""
    inner_body = "\n".join(f"inner {i}" for i in range(5))
    outer_lines = [f"outer {i}" for i in range(40)]
    # Insert a nested <details> block in the middle
    outer_lines.insert(
        20,
        f"<details>\n<summary>Inner</summary>\n{inner_body}\n</details>",
    )
    outer_body = "\n".join(outer_lines)
    content = f"<details>\n<summary>Outer</summary>\n{outer_body}\n</details>"

    result = _truncate_details_blocks(content, lines_pre=5, lines_post=5)
    assert "[...]" in result
    # Outer structure preserved
    assert "<summary>Outer</summary>" in result
    # First and last outer lines preserved
    assert "outer 0" in result
    assert "outer 39" in result
    # Middle lines truncated
    assert "outer 15" not in result


def test_reduce_log_all_pinned():
    """reduce_log should not crash when all messages are pinned."""
    msgs = [
        Message("system", content="x " * 5000, pinned=True),
        Message("system", content="y " * 5000, pinned=True),
    ]
    # Should not raise ValueError, just return messages as-is with content preserved
    reduced = list(reduce_log(msgs, limit=100))
    assert len(reduced) == 2
    assert reduced == msgs


def test_reduce_log_all_pinned_logs_completion(monkeypatch):
    """All-pinned reduction should not leave the start message dangling."""
    console_messages: list[str] = []
    monkeypatch.setattr(reduce_mod.console, "log", console_messages.append)
    msgs = [
        Message("system", content="x " * 5000, pinned=True),
        Message("system", content="y " * 5000, pinned=True),
    ]

    reduced = list(reduce_log(msgs, limit=100))

    assert reduced == msgs
    assert len(console_messages) == 2
    assert "Log too long" in console_messages[0]
    assert "Could not reduce log further" in console_messages[1]
    assert "pinned or protected tool calls" in console_messages[1]


def test_truncate_msg_skips_unfindable_codeblock(monkeypatch):
    """If a codeblock's reformatted markdown is not in the content, skip it.

    Regression: before this fix, truncate_msg asserted
    ``full_block in content_staged`` and crashed the entire reduction pass
    when the round-trip reconstruction diverged from the original. The
    session-level symptom was an unhandled AssertionError and exit code 1
    in long-context autonomous runs (Bob 2026-04-24, minimax-m2.7 session).
    """
    real_block_lines = "\n".join(f"real_{i}" for i in range(50))
    truncatable_lines = "\n".join(f"trunc_{i}" for i in range(50))
    # Original content has a codeblock with content `real_*`, and a fully
    # well-formed codeblock with content `trunc_*` that should still be
    # truncated even if the first one cannot be round-tripped.
    content = (
        f"```python\n{real_block_lines}\n```\n\n```python\n{truncatable_lines}\n```"
    )
    msg = Message("assistant", content=content)

    # Fake extra codeblock whose to_markdown() output is not present in content.
    class FakeCodeblock:
        lang = "python"
        content = "x = 1"
        fence = "```"

        def to_markdown(self) -> str:
            return "```python\ndoes-not-appear-in-content\n```"

    real_get_codeblocks = Message.get_codeblocks

    def patched_get_codeblocks(self):
        blocks = real_get_codeblocks(self)
        # Prepend the fake one so the skip path runs before a real truncation.
        return [FakeCodeblock(), *blocks]

    monkeypatch.setattr(Message, "get_codeblocks", patched_get_codeblocks)

    truncated = truncate_msg(msg)
    # Truncation still succeeds via the real codeblock.
    assert truncated is not None
    assert "[...]" in truncated.content
    assert "trunc_0" in truncated.content
    assert "trunc_49" in truncated.content


def test_truncate_msg_quad_fence():
    """Quadruple-backtick codeblocks (e.g. from md_codeblock) must survive truncation.

    Before the fix, truncate_msg would AssertionError because to_markdown()
    reconstructed with triple backticks while the original had quadruple.
    """
    lines = "\n".join(f"line_{i}" for i in range(50))
    content = f"````python\n{lines}\n````"
    msg = Message("assistant", content=content)
    truncated = truncate_msg(msg)
    assert truncated is not None
    assert "[...]" in truncated.content
    # Fence length must be preserved
    assert "````python" in truncated.content
    assert truncated.content.rstrip().endswith("````")
    # Must NOT contain triple-backtick version (that would be the old broken behavior)
    assert "```python" not in truncated.content.replace("````python", "")


def test_truncate_msg_preserves_tool_use_codeblocks():
    """Tool-call messages must stay parseable after reduction."""
    tool_lines = "\n".join(f"echo line_{i}" for i in range(50))
    msg = Message(
        "assistant",
        content=f"Planning.\n```shell\n{tool_lines}\n```\nAfter the tool call.",
    )

    truncated = truncate_msg(msg)

    assert truncated is None


def test_reduce_log_skips_tool_use_messages():
    """reduce_log should compact a different message before touching tool calls."""
    tool_lines = "\n".join(f"echo line_{i}" for i in range(80))
    filler_lines = "\n".join(f"value_{i} = {i}" for i in range(70))
    tool_msg = Message(
        "assistant",
        content=f"Planning.\n```shell\n{tool_lines}\n```\nAfter the tool call.",
    )
    filler_msg = Message("assistant", content=f"```python\n{filler_lines}\n```")
    msgs = [
        Message("system", content="system prompt"),
        tool_msg,
        Message("system", content="command executed successfully"),
        filler_msg,
    ]

    reduced = list(reduce_log(msgs, limit=150))

    assert reduced[1].content == tool_msg.content
    assert "[...]" in reduced[3].content


@pytest.mark.slow
def test_reduce_log():
    msgs = [
        Message("system", content="system prompt"),
        Message("user", content=" ".join(fn.name for fn in [readme, cli, htmlindex])),
        long_msg,
    ]
    len_pre = len_tokens(msgs, "gpt-4")
    print(f"{len_pre=}")

    limit = 1000
    reduced = list(reduce_log(msgs, limit=limit))
    len_post = len_tokens(reduced, "gpt-4")
    print(f"{len_post=}")
    print(f"{reduced[-1].content=}")

    assert len_pre > len_post
    assert len_post < limit


def test_limit_log_tool_pair_atomicity():
    """limit_log should drop orphaned tool results instead of splitting pairs.

    When the context limit causes the assistant tool-use message to be dropped
    but the subsequent system tool-result message fits, the result is an orphaned
    tool result with no preceding call. limit_log should drop it rather than
    return an incoherent log.
    """
    from gptme.llm.models.resolution import _default_model_var

    # Save and restore default model to avoid ContextVar contamination.
    original_model = _default_model_var.get()
    try:
        # Context=10: fits system prompt (2 tok) + tool result (1 tok) but not the
        # assistant tool-use (13 tok) on top of that.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=10)
        set_default_model(tiny_model)

        # assistant message with a shell tool call (13 tokens)
        tool_use_content = "I will run a command.\n```shell\necho hello\n```"
        msgs = [
            Message("system", "system prompt"),  # 2 tok — initial system msg
            Message("assistant", tool_use_content),  # 13 tok — tool use
            Message("system", "hello"),  # 1 tok — tool result
        ]

        result = limit_log(msgs)

        # The orphaned tool result ("hello") must not appear without its tool use.
        result_contents = [m.content for m in result]
        assert "hello" not in result_contents, (
            "Orphaned tool result should be dropped when its tool-use was not included"
        )
        # The initial system prompt must always be kept.
        assert any(m.content == "system prompt" for m in result)
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_limit_log_cascading_orphans():
    """limit_log drops ALL tool results when their shared anchor is dropped.

    When break_on_tooluse=False causes multiple consecutive system messages
    (tool results) after a single assistant message that gets dropped by the
    context limit, ALL of them should be orphaned — not just the first one
    whose immediate predecessor is the dropped assistant.

    Regression: before the anchor-walking fix in limit_log, only the first
    orphaned result was caught; the remaining tool results had their immediate
    predecessor (another system message) present in the result set and survived
    the filter.
    """
    from gptme.llm.models.resolution import _default_model_var

    original_model = _default_model_var.get()
    try:
        # Context=12: fits system prompt (2 tok) + both tool results (1+1 tok)
        # but not the assistant tool-use (~20 tok) on top of that.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=12)
        set_default_model(tiny_model)

        # Assistant message with two tool calls (simulating break_on_tooluse=False)
        tool_use_content = (
            "I will run two commands.\n"
            "```shell\necho hello\n```\n"
            "```shell\necho world\n```"
        )
        msgs = [
            Message("system", "system prompt"),  # 2 tok — initial system msg
            Message("assistant", tool_use_content),  # ~20 tok — tool use (both)
            Message("system", "hello"),  # 1 tok — first tool result
            Message("system", "world"),  # 1 tok — second tool result
        ]

        result = limit_log(msgs)

        # Neither orphaned tool result should survive.
        result_contents = [m.content for m in result]
        assert "hello" not in result_contents, (
            "First orphaned tool result should be dropped"
        )
        assert "world" not in result_contents, (
            "Second orphaned tool result should be dropped (regression: "
            "immediate-predecessor check missed cascading orphans)"
        )
        # The initial system prompt must always be kept.
        assert any(m.content == "system prompt" for m in result)
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


# ---------------------------------------------------------------------------
# Tests for proactive_summarize_log
# ---------------------------------------------------------------------------


def test_proactive_summarize_noop_below_threshold():
    """proactive_summarize_log returns the log unchanged when below threshold."""
    from gptme.llm.models.resolution import _default_model_var
    from gptme.util.reduce import proactive_summarize_log

    original_model = _default_model_var.get()
    try:
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=200_000)
        set_default_model(tiny_model)

        msgs = [
            Message("system", "You are helpful."),
            Message("user", "Hello"),
            Message("assistant", "Hi"),
        ]
        result = proactive_summarize_log(msgs, threshold=0.8)
        assert result is msgs  # identical object — no copy made
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_proactive_summarize_triggered(monkeypatch):
    """proactive_summarize_log summarizes older turns when threshold is exceeded."""
    from gptme.llm.models.resolution import _default_model_var
    from gptme.util.reduce import proactive_summarize_log

    original_model = _default_model_var.get()
    try:
        # Tiny context so a modest log exceeds the 50 % threshold.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=10)
        set_default_model(tiny_model)

        fake_summary = Message(
            "system",
            content="Here's a summary of the conversation:\n- User asked about X\n- Assistant explained X",
        )
        monkeypatch.setattr("gptme.llm.summarize", lambda _msgs: fake_summary)

        msgs = [
            Message("system", "You are helpful."),  # initial system — kept
            Message("user", "word " * 5),  # old — summarized
            Message("assistant", "word " * 5),  # old — summarized
            Message("user", "recent question one"),  # recent — kept
            Message("assistant", "recent answer one"),  # recent — kept
            Message("user", "recent question two"),  # recent — kept
            Message("assistant", "recent answer two"),  # recent — kept
        ]

        result = proactive_summarize_log(msgs, threshold=0.5, recent_keep=4)

        # Initial system message preserved as-is.
        assert result[0].content == "You are helpful."

        # Summary message present somewhere after the system block.
        assert any("summary" in m.content.lower() for m in result)

        # Recent messages intact.
        contents = [m.content for m in result]
        assert "recent question one" in contents
        assert "recent answer one" in contents
        assert "recent question two" in contents
        assert "recent answer two" in contents

        # Old filler messages NOT present verbatim.
        assert not any(m.content == "word " * 5 for m in result)

        # Result is strictly shorter than original (summary replaced middle).
        assert len(result) < len(msgs)
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_proactive_summarize_env_disabled_by_default(monkeypatch):
    """proactive_summarize_log is a no-op when env var is not set."""
    from gptme.llm.models.resolution import _default_model_var
    from gptme.util.reduce import proactive_summarize_log

    original_model = _default_model_var.get()
    try:
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=10)
        set_default_model(tiny_model)

        monkeypatch.delenv("GPTME_AUTO_SUMMARIZE_THRESHOLD", raising=False)

        msgs = [Message("user", "word " * 20)]
        result = proactive_summarize_log(msgs)  # no threshold arg, env var absent
        # Must return original list unchanged.
        assert result is msgs
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_proactive_summarize_env_var(monkeypatch):
    """GPTME_AUTO_SUMMARIZE_THRESHOLD env var triggers summarization."""
    from gptme.llm.models.resolution import _default_model_var
    from gptme.util.reduce import proactive_summarize_log

    original_model = _default_model_var.get()
    try:
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=10)
        set_default_model(tiny_model)

        monkeypatch.setenv("GPTME_AUTO_SUMMARIZE_THRESHOLD", "0.5")
        fake_summary = Message("system", "Summary: all the things")
        monkeypatch.setattr("gptme.llm.summarize", lambda _msgs: fake_summary)

        msgs = [
            Message("system", "System prompt."),
            Message("user", "word " * 5),
            Message("assistant", "word " * 5),
            Message("user", "final question"),
            Message("assistant", "final answer"),
        ]

        result = proactive_summarize_log(msgs, recent_keep=2)

        assert result[0].content == "System prompt."
        assert any("Summary" in m.content for m in result)
        assert any("final question" in m.content for m in result)
        assert any("final answer" in m.content for m in result)
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )
