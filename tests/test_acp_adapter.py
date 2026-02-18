"""Tests for ACP adapter module.

Tests bidirectional conversion between gptme and ACP types.
"""

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
