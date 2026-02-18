"""Tests for ACP adapter module.

Tests bidirectional conversion between gptme and ACP types.
"""

import pytest

from gptme.acp.adapter import (
    acp_content_to_gptme_message,
    format_tool_result,
    generate_id,
    gptme_codeblock_to_tool_info,
    gptme_message_to_acp_content,
)
from gptme.codeblock import Codeblock
from gptme.message import Message


class TestGenerateId:
    """Tests for generate_id utility."""

    def test_returns_string(self):
        assert isinstance(generate_id(), str)

    def test_length(self):
        """IDs are truncated UUIDs (first 8 chars)."""
        assert len(generate_id()) == 8

    def test_unique(self):
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100


class TestGptmeMessageToAcpContent:
    """Tests for gptme Message -> ACP Content conversion."""

    def test_simple_text(self):
        msg = Message(role="user", content="Hello, world!")
        result = gptme_message_to_acp_content(msg)
        assert result == [{"type": "text", "text": "Hello, world!"}]

    def test_multiline_text(self):
        msg = Message(role="assistant", content="Line 1\nLine 2\nLine 3")
        result = gptme_message_to_acp_content(msg)
        assert len(result) == 1
        assert result[0]["text"] == "Line 1\nLine 2\nLine 3"

    def test_empty_content(self):
        msg = Message(role="user", content="")
        result = gptme_message_to_acp_content(msg)
        assert result == []

    def test_content_with_codeblock(self):
        """Codeblocks are currently passed as plain text (Phase 1)."""
        msg = Message(
            role="assistant", content="Here is code:\n```python\nprint('hi')\n```"
        )
        result = gptme_message_to_acp_content(msg)
        assert len(result) == 1
        assert "```python" in result[0]["text"]

    def test_system_message(self):
        msg = Message(role="system", content="System prompt")
        result = gptme_message_to_acp_content(msg)
        assert result == [{"type": "text", "text": "System prompt"}]


class TestAcpContentToGptmeMessage:
    """Tests for ACP Content -> gptme Message conversion."""

    def test_pydantic_text_block(self):
        """ACP SDK returns Pydantic TextContentBlock objects, not dicts."""
        acp = pytest.importorskip("acp")
        block = acp.text_block("hello from zed")
        msg = acp_content_to_gptme_message([block], "user")
        assert msg.role == "user"
        assert msg.content == "hello from zed"

    def test_pydantic_multiple_blocks(self):
        """Multiple Pydantic blocks should be joined correctly."""
        acp = pytest.importorskip("acp")
        blocks = [acp.text_block("Part 1"), acp.text_block("Part 2")]
        msg = acp_content_to_gptme_message(blocks, "user")
        assert msg.content == "Part 1\nPart 2"

    def test_single_text_block(self):
        content = [{"type": "text", "text": "Hello!"}]
        msg = acp_content_to_gptme_message(content, "user")
        assert msg.role == "user"
        assert msg.content == "Hello!"

    def test_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "Part 1"},
            {"type": "text", "text": "Part 2"},
        ]
        msg = acp_content_to_gptme_message(content, "assistant")
        assert msg.role == "assistant"
        assert msg.content == "Part 1\nPart 2"

    def test_non_text_blocks_ignored(self):
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "image", "url": "http://example.com/img.png"},
            {"type": "text", "text": "World"},
        ]
        msg = acp_content_to_gptme_message(content, "user")
        assert msg.content == "Hello\nWorld"

    def test_empty_content(self):
        msg = acp_content_to_gptme_message([], "user")
        assert msg.role == "user"
        assert msg.content == ""

    def test_system_role(self):
        content = [{"type": "text", "text": "System instruction"}]
        msg = acp_content_to_gptme_message(content, "system")
        assert msg.role == "system"

    def test_missing_text_key(self):
        """Blocks with type=text but no text key should produce empty string."""
        content = [{"type": "text"}]
        msg = acp_content_to_gptme_message(content, "user")
        assert msg.content == ""


class TestRoundTrip:
    """Tests for round-trip conversion consistency."""

    def test_message_round_trip(self):
        """Converting gptme -> ACP -> gptme should preserve content."""
        original = Message(role="user", content="Test message")
        acp = gptme_message_to_acp_content(original)
        restored = acp_content_to_gptme_message(acp, "user")
        assert restored.content == original.content
        assert restored.role == original.role

    def test_multiline_round_trip(self):
        original = Message(role="assistant", content="Line 1\nLine 2\nLine 3")
        acp = gptme_message_to_acp_content(original)
        restored = acp_content_to_gptme_message(acp, "assistant")
        assert restored.content == original.content


class TestGptmeCodeblockToToolInfo:
    """Tests for Codeblock -> ACP tool info conversion."""

    def test_shell_block(self):
        block = Codeblock(lang="shell", content="ls -la")
        info = gptme_codeblock_to_tool_info(block)
        assert info["kind"] == "terminal"
        assert info["name"] == "shell execution"
        assert info["language"] == "shell"
        assert info["content"] == "ls -la"
        assert "id" in info

    def test_save_block(self):
        block = Codeblock(lang="save", content="/tmp/test.py\nprint('hi')")
        info = gptme_codeblock_to_tool_info(block)
        assert info["kind"] == "edit"
        assert info["name"] == "save execution"

    def test_append_block(self):
        block = Codeblock(lang="append", content="/tmp/test.py\nnew line")
        info = gptme_codeblock_to_tool_info(block)
        assert info["kind"] == "edit"

    def test_patch_block(self):
        block = Codeblock(lang="patch", content="--- a\n+++ b")
        info = gptme_codeblock_to_tool_info(block)
        assert info["kind"] == "edit"

    def test_python_block(self):
        block = Codeblock(lang="python", content="print('hello')")
        info = gptme_codeblock_to_tool_info(block)
        assert info["kind"] == "execute"
        assert info["language"] == "python"

    def test_unknown_language(self):
        block = Codeblock(lang="rust", content="fn main() {}")
        info = gptme_codeblock_to_tool_info(block)
        assert info["kind"] == "execute"

    def test_id_is_unique(self):
        block = Codeblock(lang="shell", content="echo hi")
        id1 = gptme_codeblock_to_tool_info(block)["id"]
        id2 = gptme_codeblock_to_tool_info(block)["id"]
        assert id1 != id2


class TestFormatToolResult:
    """Tests for format_tool_result."""

    def test_success(self):
        result = format_tool_result("Output text", success=True)
        assert result == {"status": "completed", "output": "Output text"}

    def test_failure(self):
        result = format_tool_result("Error message", success=False)
        assert result == {"status": "failed", "output": "Error message"}

    def test_none_result(self):
        result = format_tool_result(None)
        assert result == {"status": "completed", "output": ""}

    def test_default_success(self):
        result = format_tool_result("output")
        assert result["status"] == "completed"


class TestContextVarPropagation:
    """Tests for ContextVar propagation in ACP agent executor threads."""

    def test_contextvars_copy_context_propagates(self):
        """Verify that copy_context().run propagates ContextVars to executor threads.

        This is the pattern used in GptmeAgent.prompt() to ensure model, config,
        and tools ContextVars are available in the run_in_executor thread.
        Regression test for: https://github.com/gptme/gptme/issues/1290
        """
        import asyncio
        import contextvars

        var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_var", default=None
        )

        async def test_propagation():
            var.set("hello")
            loop = asyncio.get_running_loop()

            # Without copy_context — value is lost
            result_without = await loop.run_in_executor(None, var.get)
            assert result_without is None

            # With copy_context — value is propagated (this is our fix)
            ctx = contextvars.copy_context()
            result_with = await loop.run_in_executor(None, ctx.run, var.get)
            assert result_with == "hello"

        asyncio.run(test_propagation())

    def test_contextvars_isolated_across_fresh_contexts(self):
        """Verify that ContextVars set in one context are NOT visible in a fresh context.

        This demonstrates the root cause of the ACP model assertion bug (#1290):
        initialize() sets the model ContextVar, but when the ACP framework
        dispatches prompt() in a fresh context (not inheriting from initialize's
        task), the ContextVar is unset. The fix is to re-set the model at the
        start of prompt() using a stored instance attribute.
        Regression test for: https://github.com/gptme/gptme/issues/1290
        """
        import asyncio
        import contextvars

        var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_var", default=None
        )

        async def test_fresh_context_isolation():
            # Simulate initialize() setting the var in its own context
            var.set("model-value")
            assert var.get() == "model-value"

            # Simulate ACP framework dispatching prompt() in a fresh context
            # (not as a child task, but with an independent context)
            fresh_ctx = contextvars.copy_context()
            # Reset the var in the fresh context to simulate ACP's isolation
            fresh_ctx.run(var.set, None)

            async def prompt_handler():
                return var.get()

            # Without the fix: fresh context has var=None
            result_without_fix = fresh_ctx.run(var.get)
            assert (
                result_without_fix is None
            ), "Fresh context should NOT have the var from initialize()"

            # With the fix: re-set the var (simulating what agent.py does)
            stored_model = "model-value"  # stored as self._model in agent
            fresh_ctx.run(var.set, stored_model)
            result_with_fix = fresh_ctx.run(var.get)
            assert (
                result_with_fix == "model-value"
            ), "After re-setting, the var should be available"

        asyncio.run(test_fresh_context_isolation())
