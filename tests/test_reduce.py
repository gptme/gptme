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
htmlindex = root / "gptme" / "util" / "export_templates" / "index.html"

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


def test_limit_log_partial_call_id_results_dropped():
    """limit_log must drop assistant when only SOME of its call_id results survive.

    Scenario (Responses-API format):
    - assistant has 2 function calls: call_id_A and call_id_B
    - context limit causes the OLDER tool result (call_id_A) to be the
      message that pushed the budget over, so it gets popped
    - call_id_B result AND the assistant both survive the initial cut
    - _is_orphaned misses this: call_id_B's anchor (assistant) is still present
    - Without the fix: API receives function_call(A)+function_call(B) but only
      function_call_output(B) → 400 "No tool output found for call_A"
    - With the fix: _drop_orphaned_tool_pairs detects that assistant has a
      call_id_A result missing from the result → drops assistant + remaining result

    This reproduces the gptme-gpt-5.5 44% infra-failure-rate root cause
    (session 96d3, 2026-07-17).
    """
    from gptme.llm.models.resolution import _default_model_var

    original_model = _default_model_var.get()
    try:
        # Context=11: fits system prompt (2 tok) + call_B result (1 tok)
        # + assistant (7 tok) = 10 tok; adding call_A result (1 tok) = 11 => over.
        # So the oldest message in msgs (call_A result, added last in reverse) is popped.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=11)
        set_default_model(tiny_model)

        # Assistant message with @tool(call_id): format (Responses API)
        # Two function calls with distinct call_ids.
        assistant_content = "@shell(call_A): {}\n@shell(call_B): {}"
        msgs = [
            Message("system", "system prompt"),  # 2 tok — initial, always kept
            Message("assistant", assistant_content),  # ~7 tok — tool use with 2 calls
            Message("system", "result_A", call_id="call_A"),  # 1 tok — result for A
            Message("system", "result_B", call_id="call_B"),  # 1 tok — result for B
        ]

        result = limit_log(msgs)

        result_contents = [m.content for m in result]
        # Neither partial result should survive without the assistant.
        # The assistant must also be absent (it has an incomplete pair).
        assert "result_A" not in result_contents, (
            "Orphaned call_id_A result should be dropped"
        )
        assert "result_B" not in result_contents, (
            "call_id_B result must be dropped too — its assistant was dropped"
        )
        assert assistant_content not in result_contents, (
            "Assistant with incomplete call_id pair must be dropped"
        )
        assert any(m.content == "system prompt" for m in result)
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_limit_log_preserves_pinned_head():
    """limit_log must not drop pinned messages even when they are oldest and non-system.

    Scenario: auto_compact_log marks the first N messages as pinned=True to protect
    task context. If the compacted log still exceeds the model context, prepare_messages
    calls limit_log, which builds tail-first and would normally drop the oldest
    non-system messages — exactly the pinned head messages.

    With the fix: pinned messages are always included (like initial system messages),
    and the remaining budget is filled from the newest non-pinned messages.
    """
    from gptme.llm.models.resolution import _default_model_var

    original_model = _default_model_var.get()
    try:
        # Token counts (gpt-4 tokenizer):
        #   "system" = 1 tok, "task" = 1 tok, "older reply" = 2 tok, "newest message" = 3 tok.
        # Context=5: system(1) + task(1) + newest(3) = 5, fits.
        # With old code: system + newest alone = 4, pinned "task" dropped.
        # With fix: task is always included; tail_budget = 3, newest(3) fits, older(2)+newest(3)=5>3 → only newest.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=5)
        set_default_model(tiny_model)

        msgs = [
            Message("system", "system"),  # 1 tok — initial system, always kept
            Message("user", "task", pinned=True),  # 1 tok — pinned head
            Message("assistant", "older reply"),  # 2 tok — old, non-pinned
            Message("user", "newest message"),  # 2 tok — new, non-pinned
        ]

        result = limit_log(msgs)
        result_contents = [m.content for m in result]

        # The pinned "task" message must survive despite being older than "newest".
        assert "task" in result_contents, "Pinned head message must be preserved"
        assert "system" in result_contents, "Initial system message must be preserved"
        # "older reply" should be dropped in favour of pinned head + newest.
        assert "older reply" not in result_contents, (
            "Non-pinned older message should be dropped when budget is tight"
        )
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_limit_log_preserves_pinned_tool_call_pair():
    """limit_log must not drop a pinned assistant tool-call when its result is clipped.

    Scenario: keep_head pins an assistant message that contains a tool call, but the
    immediately following tool result is not pinned and falls outside the tail budget.
    _drop_orphaned_tool_pairs would remove the orphaned assistant — but the result
    should also be included in the always-reserved set to maintain pair atomicity.

    With the fix: extra_pinned is extended to include immediately following call_id
    results of pinned assistant messages, so the pair is always preserved together.
    """
    from gptme.llm.models.resolution import _default_model_var

    original_model = _default_model_var.get()
    try:
        # Budget: context=5 tokens.
        # Messages: system(1) + assistant-with-call(2) + system-result(1) + newest(3)
        # Without fix: system+newest=4 fits; pinned assistant-call kept but its result
        # excluded from tail → _drop_orphaned_tool_pairs removes assistant.
        # With fix: assistant-call + its result both in extra_pinned (reserved budget).
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=7)
        set_default_model(tiny_model)

        tool_call_msg = Message("assistant", "tool call", pinned=True)
        tool_result_msg = Message("system", "result", call_id="abc123")
        msgs = [
            Message("system", "system"),  # 1 tok — initial system
            tool_call_msg,  # 2 tok — pinned tool call
            tool_result_msg,  # 1 tok — tool result (not pinned, but must be kept)
            Message("user", "newest message"),  # 2 tok — recent context
        ]

        result = limit_log(msgs)
        result_contents = [m.content for m in result]

        assert "tool call" in result_contents, (
            "Pinned assistant tool call must be preserved"
        )
        assert "result" in result_contents, (
            "Tool result of pinned call must be preserved (atomicity)"
        )
        assert "system" in result_contents, "Initial system message must be preserved"
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_limit_log_preserves_pinned_markdown_tool_result():
    """limit_log must also protect non-call_id (markdown) tool results of pinned assistant.

    Scenario: a pinned assistant message contains a tool call, and the immediately
    following system message is a gptme-native markdown result (no call_id).  The
    old code only added call_id system messages to extra_pinned_ids, leaving the
    markdown result eligible for budget exclusion.  When excluded, _is_orphaned
    does NOT catch it (no call_id), so the pinned assistant survives with no result
    — producing an incoherent log.

    With the fix: ALL consecutive system messages after a pinned assistant tool-use
    are added to extra_pinned_ids, so the markdown result is always reserved.
    """
    from gptme.llm.models.resolution import _default_model_var

    original_model = _default_model_var.get()
    try:
        # Budget: context=5 tokens.
        # Messages: system(1) + pinned-assistant-tool(2) + markdown-result(1) + newest(3)
        # Without fix: system+newest=4 fits; pinned assistant kept but markdown result
        # excluded from tail → log has dangling tool call with no result.
        # With fix: markdown result also in extra_pinned; always reserved.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=6)
        set_default_model(tiny_model)

        # Markdown tool result: system message with no call_id
        tool_call_msg = Message("assistant", "tool call", pinned=True)
        markdown_result_msg = Message("system", "markdown result")  # no call_id

        msgs = [
            Message("system", "system"),  # 1 tok — initial system
            tool_call_msg,  # 2 tok — pinned tool call
            markdown_result_msg,  # 2 tok — markdown result (no call_id)
            Message("user", "newest"),  # 1 tok — recent context
        ]

        result = limit_log(msgs)
        result_contents = [m.content for m in result]

        assert "tool call" in result_contents, (
            "Pinned assistant tool call must be preserved"
        )
        assert "markdown result" in result_contents, (
            "Markdown tool result of pinned call must be preserved (no call_id path)"
        )
        assert "system" in result_contents, "Initial system message must be preserved"
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_limit_log_pinned_system_not_orphaned():
    """_is_orphaned must not drop a pinned system message even when its anchor is excluded.

    Scenario: a pinned system message's preceding non-system message is NOT in the
    tail budget selection.  The old _is_orphaned would walk back to that absent anchor
    and return True, removing the pinned message — defeating the pin guarantee.

    With the fix: _is_orphaned skips messages whose id is in extra_pinned_ids.
    """
    from gptme.llm.models.resolution import _default_model_var

    original_model = _default_model_var.get()
    try:
        # Budget: context=4 tokens.
        # Messages: system(1) + assistant-anchor(2) + pinned-result(1) + user(1)
        # always = system(1) + pinned-result(1) = 2; tail_budget=2; newest is user(1).
        # assistant-anchor(2) > remaining(1) so it's dropped.
        # Old _is_orphaned: pinned-result walks back to absent assistant → orphaned → dropped.
        # New _is_orphaned: pinned-result is in extra_pinned_ids → exempt → kept.
        tiny_model = ModelMeta(provider="unknown", model="gpt-4", context=4)
        set_default_model(tiny_model)

        anchor_msg = Message("assistant", "anchor msg")  # 2 tok — non-pinned anchor
        pinned_result = Message("system", "pinned result", pinned=True)  # 1 tok

        msgs = [
            Message("system", "system"),  # 1 tok — initial system
            anchor_msg,  # 2 tok — anchor (will be budget-excluded)
            pinned_result,  # 1 tok — pinned system result
            Message("user", "newest"),  # 1 tok — newest
        ]

        result = limit_log(msgs)
        result_contents = [m.content for m in result]

        assert "pinned result" in result_contents, (
            "Pinned system message must not be dropped by _is_orphaned even when anchor is excluded"
        )
        assert "system" in result_contents, "Initial system message must be preserved"
    finally:
        set_default_model(original_model) if original_model else _default_model_var.set(
            None
        )


def test_drop_orphaned_tool_pairs_preserves_pinned_tool_result():
    """_drop_orphaned_tool_pairs must not drop a pinned system tool result.

    When a system message with call_id is pinned, its anchor assistant may have
    been excluded from the pruned set by limit_log. Previously _drop_orphaned_tool_pairs
    would detect the missing anchor and remove the pinned result too. The fix: skip
    Case 1 removal if the system message has pinned=True.

    Regression test for: _drop_orphaned_tool_pairs ignoring pinned flag.
    """
    from gptme.util.reduce import _drop_orphaned_tool_pairs

    anchor = Message("assistant", "fn call", call_id="call_abc")
    pinned_result = Message("system", "fn result", pinned=True, call_id="call_abc")
    user = Message("user", "follow up")

    original = [anchor, pinned_result, user]

    # Pruned: anchor was excluded (budget or index), only result + user remain
    pruned = [pinned_result, user]

    result = _drop_orphaned_tool_pairs(original, pruned)
    result_contents = [m.content for m in result]

    assert "fn result" in result_contents, (
        "Pinned tool result must survive _drop_orphaned_tool_pairs even when anchor is absent"
    )
