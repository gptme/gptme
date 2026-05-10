"""Tests for shared LLM utility functions in gptme.llm.utils."""

from typing import Any

from gptme.llm.utils import apply_cache_control, parameters2dict, process_image_file
from gptme.tools.base import Parameter

# --- parameters2dict tests ---


class TestParameters2Dict:
    def test_empty_parameters(self):
        result = parameters2dict([])
        assert result == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def test_single_required_parameter(self):
        params = [
            Parameter(
                name="path", type="string", description="The file path", required=True
            )
        ]
        result = parameters2dict(params)
        assert result == {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path"},
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def test_single_optional_parameter(self):
        params = [
            Parameter(
                name="verbose",
                type="boolean",
                description="Enable verbose output",
                required=False,
            )
        ]
        result: dict[str, Any] = parameters2dict(params)
        assert result["required"] == []
        assert "verbose" in result["properties"]

    def test_mixed_required_and_optional(self):
        params = [
            Parameter(
                name="path", type="string", description="The file path", required=True
            ),
            Parameter(
                name="content", type="string", description="File content", required=True
            ),
            Parameter(
                name="overwrite",
                type="boolean",
                description="Overwrite if exists",
                required=False,
            ),
        ]
        result: dict[str, Any] = parameters2dict(params)
        assert result["required"] == ["path", "content"]
        assert len(result["properties"]) == 3
        assert result["additionalProperties"] is False

    def test_preserves_parameter_types(self):
        params = [
            Parameter(
                name="count",
                type="integer",
                description="Number of items",
                required=True,
            ),
            Parameter(
                name="name", type="string", description="Item name", required=True
            ),
            Parameter(
                name="enabled", type="boolean", description="Is enabled", required=False
            ),
        ]
        result: dict[str, Any] = parameters2dict(params)
        assert result["properties"]["count"]["type"] == "integer"
        assert result["properties"]["name"]["type"] == "string"
        assert result["properties"]["enabled"]["type"] == "boolean"


# --- apply_cache_control tests ---


class TestApplyCacheControl:
    def test_empty_messages(self):
        messages, system = apply_cache_control([], None)
        assert messages == []
        assert system is None

    def test_system_message_in_messages_array(self):
        """OpenAI-style: system message as first message in the array."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result_msgs, result_sys = apply_cache_control(messages, None)

        # System message should have cache_control on its content
        sys_content = result_msgs[0]["content"]
        assert isinstance(sys_content, list)
        assert sys_content[0]["cache_control"] == {"type": "ephemeral"}

        # User message (last user msg) should have cache_control
        user_content = result_msgs[1]["content"]
        assert isinstance(user_content, list)
        assert user_content[0]["cache_control"] == {"type": "ephemeral"}

    def test_separate_system_messages(self):
        """Anthropic-style: system messages as separate list."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        system_messages = [
            {"type": "text", "text": "You are helpful."},
        ]
        result_msgs, result_sys = apply_cache_control(messages, system_messages)

        # System messages should have cache_control
        assert result_sys is not None
        assert result_sys[0]["cache_control"] == {"type": "ephemeral"}

    def test_last_two_user_messages_get_cache_control(self):
        """Cache control should be applied to the last two user messages."""
        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
            {"role": "user", "content": "Third question"},
        ]
        result_msgs, _ = apply_cache_control(messages, None)

        # First user message should NOT have cache_control
        first_user = result_msgs[0]["content"]
        if isinstance(first_user, list):
            assert "cache_control" not in first_user[0]
        else:
            # String content — no cache control applied
            assert isinstance(first_user, str)

        # Second user message (index 2) SHOULD have cache_control
        second_user = result_msgs[2]["content"]
        assert isinstance(second_user, list)
        assert second_user[0]["cache_control"] == {"type": "ephemeral"}

        # Third user message (index 4) SHOULD have cache_control
        third_user = result_msgs[4]["content"]
        assert isinstance(third_user, list)
        assert third_user[0]["cache_control"] == {"type": "ephemeral"}

    def test_does_not_mutate_originals(self):
        """apply_cache_control should not modify the input messages."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        system_messages = [
            {"type": "text", "text": "System prompt"},
        ]
        # Keep copies
        orig_msg = {"role": "user", "content": "Hello"}
        orig_sys = {"type": "text", "text": "System prompt"}

        apply_cache_control(messages, system_messages)

        # Originals should be unchanged
        assert messages[0] == orig_msg
        assert system_messages[0] == orig_sys

    def test_list_content_format(self):
        """Messages with list content should be handled correctly."""
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this image"},
                    {"type": "image", "source": {"data": "base64..."}},
                ],
            },
        ]
        result_msgs, _ = apply_cache_control(messages, None)

        # The last non-empty part should get cache_control
        content = result_msgs[0]["content"]
        assert isinstance(content, list)
        # The image part (last part) should get cache_control
        has_cache_control = any(
            "cache_control" in part for part in content if isinstance(part, dict)
        )
        assert has_cache_control

    def test_single_user_message(self):
        """Single user message should still get cache_control."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        result_msgs, _ = apply_cache_control(messages, None)
        content = result_msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0]["cache_control"] == {"type": "ephemeral"}

    def test_empty_system_message_text(self):
        """System message with empty text should not get cache_control."""
        system_messages = [
            {"type": "text", "text": ""},
        ]
        _, result_sys = apply_cache_control([], system_messages)
        assert result_sys is not None
        assert "cache_control" not in result_sys[0]

    def test_system_in_messages_with_list_content(self):
        """System message in messages array with pre-formatted list content."""
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "System prompt part 1"},
                    {"type": "text", "text": "System prompt part 2"},
                ],
            },
            {"role": "user", "content": "Hello"},
        ]
        result_msgs, _ = apply_cache_control(messages, None)

        # System message's last text part should have cache_control
        sys_content = result_msgs[0]["content"]
        assert isinstance(sys_content, list)
        # Last part should have cache_control
        assert sys_content[-1]["cache_control"] == {"type": "ephemeral"}
        # First part should NOT
        assert "cache_control" not in sys_content[0]


# --- process_image_file tests ---


class TestProcessImageFile:
    def test_valid_png_image(self, tmp_path):
        """Processing a valid PNG file should return base64 data and media type."""
        img_file = tmp_path / "test.png"
        # Write a minimal valid-ish PNG (just needs to be readable bytes)
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts)

        assert result is not None
        data, media_type = result
        assert media_type == "image/png"
        assert len(data) > 0  # base64 encoded
        # Should have added a text label part
        assert len(content_parts) == 1
        assert content_parts[0]["type"] == "text"
        assert "test.png" in content_parts[0]["text"]

    def test_valid_jpg_image(self, tmp_path):
        """JPG should be normalized to jpeg media type."""
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts)

        assert result is not None
        _, media_type = result
        assert media_type == "image/jpeg"

    def test_valid_jpeg_image(self, tmp_path):
        """JPEG extension should also work."""
        img_file = tmp_path / "photo.jpeg"
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts)

        assert result is not None
        _, media_type = result
        assert media_type == "image/jpeg"

    def test_valid_gif_image(self, tmp_path):
        """GIF images should be supported."""
        img_file = tmp_path / "anim.gif"
        img_file.write_bytes(b"GIF89a" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts)

        assert result is not None
        _, media_type = result
        assert media_type == "image/gif"

    def test_valid_webp_image(self, tmp_path):
        """WebP images should be supported."""
        img_file = tmp_path / "photo.webp"
        img_file.write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts)

        assert result is not None
        _, media_type = result
        assert media_type == "image/webp"

    def test_unsupported_file_type(self, tmp_path):
        """Non-image file extensions should be rejected."""
        txt_file = tmp_path / "readme.txt"
        txt_file.write_bytes(b"hello world")

        content_parts: list[dict] = []
        result = process_image_file(str(txt_file), content_parts)

        assert result is None
        assert len(content_parts) == 0

    def test_uri_skipped(self):
        """URIs should be skipped (not treated as local files)."""
        content_parts: list[dict] = []
        result = process_image_file("https://example.com/image.png", content_parts)

        assert result is None
        assert len(content_parts) == 0

    def test_file_too_large(self, tmp_path):
        """Files exceeding max_size_mb should be rejected."""
        img_file = tmp_path / "huge.png"
        # Write 6MB of data (exceeds default 5MB limit)
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (6 * 1024 * 1024))

        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts, max_size_mb=5)

        assert result is None
        # Should have both a label and an error message
        assert len(content_parts) == 2
        assert (
            "exceeds" in content_parts[1]["text"].lower()
            or "5MB" in content_parts[1]["text"]
        )

    def test_nonexistent_file(self, tmp_path):
        """Non-existent file should return None with error message."""
        content_parts: list[dict] = []
        result = process_image_file(str(tmp_path / "nonexistent.png"), content_parts)

        assert result is None
        # Should have label + error
        assert len(content_parts) == 2
        assert "error" in content_parts[1]["text"].lower()

    def test_expand_user(self):
        """expand_user=True should expand ~ in paths."""
        # Create a file with a tilde path won't work in CI, but we can test
        # that the option doesn't crash
        content_parts: list[dict] = []
        result = process_image_file(
            "~/nonexistent.png", content_parts, expand_user=True
        )
        # Should fail gracefully (file doesn't exist)
        assert result is None

    def test_vision_support_check_false(self, tmp_path):
        """When vision support check returns False, should skip."""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(
            str(img_file), content_parts, check_vision_support=lambda: False
        )

        assert result is None

    def test_vision_support_check_true(self, tmp_path):
        """When vision support check returns True, should process normally."""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        content_parts: list[dict] = []
        result = process_image_file(
            str(img_file), content_parts, check_vision_support=lambda: True
        )

        assert result is not None

    def test_custom_max_size(self, tmp_path):
        """Custom max_size_mb should be respected."""
        img_file = tmp_path / "small.png"
        # Write 2MB of data
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024))

        # Should fail with 1MB limit
        content_parts: list[dict] = []
        result = process_image_file(str(img_file), content_parts, max_size_mb=1)
        assert result is None

        # Should succeed with 3MB limit
        content_parts2: list[dict] = []
        result2 = process_image_file(str(img_file), content_parts2, max_size_mb=3)
        assert result2 is not None


def test_reply_stream_on_token_callback(monkeypatch):
    """on_token callback receives complete lines (not individual characters)."""
    from gptme.llm import _reply_stream
    from gptme.message import Message

    # Mock _stream to yield known chunks
    chunks_to_yield = ["Hello", ", ", "world!"]

    def _fake_gen(chunks):
        yield from chunks

    class _FakeStream:
        def __init__(self, chunks):
            self.gen = _fake_gen(chunks)
            self.metadata = {"model": "test/model"}

        def __iter__(self):
            yield from self.gen

    monkeypatch.setattr(
        "gptme.llm._stream",
        lambda *args, **kwargs: _FakeStream(chunks_to_yield),
    )

    collected: list[str] = []

    result = _reply_stream(
        messages=[Message("user", "hi")],
        model="test/model",
        tools=None,
        on_token=collected.append,
    )

    expected_text = "Hello, world!"
    assert result.content == expected_text
    assert "".join(collected) == expected_text
    # on_token receives whole lines (or fragments without newline for the final partial line),
    # not individual characters — fewer calls than chars.
    assert len(collected) < len(expected_text)


def test_reply_stream_on_token_break_on_tooluse(monkeypatch):
    """on_token receives only content up to the break_on_tooluse breakpoint."""
    from gptme.llm import _reply_stream
    from gptme.message import Message
    from gptme.tools import init_tools

    init_tools()

    # Use ``shell`` lang tag (recognized by get_tool_for_langtag).
    # streaming=True requires a blank line after closing ``` to confirm block closure.
    tool_block = "```shell\necho hi\n```\n\n"
    suffix = "This text should not be reached"

    def _fake_gen(chunks):
        yield from chunks

    class _FakeStream:
        def __init__(self, chunks):
            self.gen = _fake_gen(chunks)
            self.metadata = {"model": "test/model"}

        def __iter__(self):
            yield from self.gen

    monkeypatch.setattr(
        "gptme.llm._stream",
        lambda *args, **kwargs: _FakeStream([tool_block, suffix]),
    )

    collected: list[str] = []

    result = _reply_stream(
        messages=[Message("user", "hi")],
        model="test/model",
        tools=None,
        on_token=collected.append,
        break_on_tooluse=True,
    )

    received_text = "".join(collected)
    # on_token should only receive content up to (including) the newline that triggered break
    assert received_text == tool_block
    assert result.content == tool_block
    # suffix was never streamed
    assert suffix not in received_text
    # on_token receives whole lines, so fewer calls than characters
    assert len(collected) < len(tool_block)


def test_reply_stream_on_token_thinking_tag_suppressed(monkeypatch):
    """on_token must not receive thinking-tag lines or their content.

    Reasoning traces appear as ``<think>\\n...\\n</think>`` blocks in the raw
    stream.  ACP clients should receive only the final answer text, not the
    raw reasoning.  Critically, even the opening ``<think>`` tag must be
    suppressed — previous code would forward it to on_token before
    ``are_thinking`` could flip at the trailing newline.
    """
    from gptme.llm import _reply_stream
    from gptme.message import Message

    # Model emits a thinking block followed by the actual answer.
    # Each element in the list is one "chunk" from the provider.
    # Use the *real* Anthropic closing sequence "\n</think>\n\n" (double newline)
    # to exercise the post-close blank-line suppression fix.
    chunks = [
        "<think>\n",
        "some private reasoning\n",
        "\n</think>\n\n",
        "Hello, world!",
    ]
    full_text = "".join(chunks)

    def _fake_gen(c):
        yield from c

    class _FakeStream:
        def __init__(self, c):
            self.gen = _fake_gen(c)
            self.metadata = {"model": "test/model"}

        def __iter__(self):
            yield from self.gen

    monkeypatch.setattr(
        "gptme.llm._stream",
        lambda *args, **kwargs: _FakeStream(chunks),
    )

    collected: list[str] = []

    result = _reply_stream(
        messages=[Message("user", "hi")],
        model="test/model",
        tools=None,
        on_token=collected.append,
    )

    received_text = "".join(collected)

    # The persistent log must contain the full text (including thinking block).
    assert result.content == full_text

    # on_token must only receive the final answer — no thinking content.
    assert received_text == "Hello, world!"

    # Specifically: opening tag must NOT appear (was the root cause of the bug).
    assert "<think>" not in received_text
    assert "</think>" not in received_text
    assert "some private reasoning" not in received_text

    # The trailing blank "\n" from "\n</think>\n\n" must not leak as a leading
    # newline before the answer.  (Without the `and line_buffer` guard, callers
    # would receive "\nHello, world!" instead of "Hello, world!".)
    assert not received_text.startswith("\n")


def test_extract_thinking_content_with_signature():
    """_extract_thinking_content must parse embedded think-sig comments."""
    from gptme.llm.llm_anthropic import _extract_thinking_content

    sig = "abc123XYZ=="
    content = (
        f"<think>\nsome reasoning\n<!-- think-sig: {sig} -->\n</think>\nAnswer here."
    )

    blocks, cleaned = _extract_thinking_content(content)

    assert len(blocks) == 1
    thinking, signature = blocks[0]
    assert thinking == "some reasoning"
    assert "Answer here." in cleaned
    assert "<think>" not in cleaned
    assert signature == sig
    # Signature comment must not appear in thinking content
    assert "think-sig" not in thinking


def test_extract_thinking_content_no_signature():
    """_extract_thinking_content returns empty signature when none embedded."""
    from gptme.llm.llm_anthropic import _extract_thinking_content

    content = "<think>\nsome reasoning\n</think>\nAnswer."

    blocks, cleaned = _extract_thinking_content(content)

    assert len(blocks) == 1
    thinking, signature = blocks[0]
    assert thinking == "some reasoning"
    assert signature == ""


def test_extract_thinking_content_multi_block():
    """_extract_thinking_content preserves per-block signatures for multiple blocks."""
    from gptme.llm.llm_anthropic import _extract_thinking_content

    sig1 = "firstSig=="
    sig2 = "secondSig=="
    content = (
        f"<think>\nfirst reasoning\n<!-- think-sig: {sig1} -->\n</think>\n"
        f"<think>\nsecond reasoning\n<!-- think-sig: {sig2} -->\n</think>\n"
        "Answer here."
    )

    blocks, cleaned = _extract_thinking_content(content)

    assert len(blocks) == 2
    assert blocks[0] == ("first reasoning", sig1)
    assert blocks[1] == ("second reasoning", sig2)
    assert "Answer here." in cleaned
    assert "<think>" not in cleaned


def test_handle_tools_thinking_with_signature():
    """_handle_tools must include thinking block with signature when available."""
    from gptme.llm.llm_anthropic import _handle_tools
    from gptme.tools import init_tools

    init_tools()

    # Use a tool-format tool call so _handle_tools converts content to a list.
    sig = "testSignature=="
    tool_call = '@save(call-1): {"path": "hello.py", "content": "print()"}'
    messages = [
        {
            "role": "assistant",
            "content": f"<think>\nI think...\n<!-- think-sig: {sig} -->\n</think>\n{tool_call}",
        }
    ]

    result = list(_handle_tools(messages))
    assert len(result) == 1
    content = result[0]["content"]
    assert isinstance(content, list)

    thinking_blocks = [b for b in content if b.get("type") == "thinking"]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["thinking"] == "I think..."
    assert thinking_blocks[0]["signature"] == sig


def test_handle_tools_thinking_without_signature_skipped():
    """_handle_tools must skip thinking blocks that lack a signature."""
    from gptme.llm.llm_anthropic import _handle_tools
    from gptme.tools import init_tools

    init_tools()

    tool_call = '@save(call-1): {"path": "hello.py", "content": "print()"}'
    messages = [
        {
            "role": "assistant",
            "content": f"<think>\nI think...\n</think>\n{tool_call}",
        }
    ]

    result = list(_handle_tools(messages))
    assert len(result) == 1
    content = result[0]["content"]
    assert isinstance(content, list)

    thinking_blocks = [b for b in content if b.get("type") == "thinking"]
    assert len(thinking_blocks) == 0
    # Tool use must still be present
    tool_blocks = [b for b in content if b.get("type") == "tool_use"]
    assert len(tool_blocks) >= 1


# --- break_on_tooluse model-dependent default tests ---


class TestBreakOnTooluseDefault:
    """Test that reply() sets break_on_tooluse based on model capabilities.

    PR #1724 made break_on_tooluse model-dependent: models that support
    parallel tool calls (e.g. Sonnet 4.6+) default to False (don't break),
    while others default to True (break after each tool block).
    GPTME_BREAK_ON_TOOLUSE env var overrides the default.
    """

    @staticmethod
    def _capture_break_on_tooluse(monkeypatch):
        """Set up mocks to capture the break_on_tooluse value passed to _reply_stream."""
        from gptme.message import Message

        captured: dict[str, Any] = {}

        def fake_reply_stream(messages, model, tools, break_on_tooluse=None, **kwargs):
            captured["break_on_tooluse"] = break_on_tooluse
            return Message("assistant", "ok")

        monkeypatch.setattr("gptme.llm._reply_stream", fake_reply_stream)
        monkeypatch.setattr("gptme.llm.init_llm", lambda *a, **kw: None)
        # Suppress hooks that try to access workspace state
        monkeypatch.setattr("gptme.hooks.trigger_hook", lambda *a, **kw: iter([]))
        return captured

    def test_parallel_model_defaults_break_off(self, monkeypatch):
        """Models supporting parallel tool calls should default break_on_tooluse=False."""
        from gptme.llm import reply
        from gptme.message import Message

        captured = self._capture_break_on_tooluse(monkeypatch)
        # Remove env var to test model-dependent default
        monkeypatch.delenv("GPTME_BREAK_ON_TOOLUSE", raising=False)

        reply(
            [Message("user", "hi")],
            model="anthropic/claude-sonnet-4-6",
            stream=True,
        )
        assert captured["break_on_tooluse"] is False

    def test_non_parallel_model_defaults_break_on(self, monkeypatch):
        """Models NOT supporting parallel tool calls should default break_on_tooluse=True."""
        from gptme.llm import reply
        from gptme.message import Message

        captured = self._capture_break_on_tooluse(monkeypatch)
        monkeypatch.delenv("GPTME_BREAK_ON_TOOLUSE", raising=False)

        reply(
            [Message("user", "hi")],
            model="anthropic/claude-haiku-4-5-20251001",
            stream=True,
        )
        assert captured["break_on_tooluse"] is True

    def test_env_var_overrides_parallel_model(self, monkeypatch):
        """GPTME_BREAK_ON_TOOLUSE=1 should force break_on_tooluse=True even for parallel-capable models."""
        from gptme.llm import reply
        from gptme.message import Message

        captured = self._capture_break_on_tooluse(monkeypatch)
        monkeypatch.setenv("GPTME_BREAK_ON_TOOLUSE", "1")

        reply(
            [Message("user", "hi")],
            model="anthropic/claude-sonnet-4-6",
            stream=True,
        )
        assert captured["break_on_tooluse"] is True

    def test_env_var_overrides_non_parallel_model(self, monkeypatch):
        """GPTME_BREAK_ON_TOOLUSE=0 should force break_on_tooluse=False even for non-parallel models."""
        from gptme.llm import reply
        from gptme.message import Message

        captured = self._capture_break_on_tooluse(monkeypatch)
        monkeypatch.setenv("GPTME_BREAK_ON_TOOLUSE", "0")

        reply(
            [Message("user", "hi")],
            model="anthropic/claude-haiku-4-5-20251001",
            stream=True,
        )
        assert captured["break_on_tooluse"] is False

    def test_openai_parallel_model_defaults_break_off(self, monkeypatch):
        """Cross-provider check: OpenAI models with parallel support also default to False."""
        from gptme.llm import reply
        from gptme.message import Message

        captured = self._capture_break_on_tooluse(monkeypatch)
        monkeypatch.delenv("GPTME_BREAK_ON_TOOLUSE", raising=False)

        reply(
            [Message("user", "hi")],
            model="openai/gpt-4o",
            stream=True,
        )
        assert captured["break_on_tooluse"] is False

    def test_old_claude_model_defaults_break_on(self, monkeypatch):
        """Older Claude 3.5 models without parallel support should default to True."""
        from gptme.llm import reply
        from gptme.message import Message

        captured = self._capture_break_on_tooluse(monkeypatch)
        monkeypatch.delenv("GPTME_BREAK_ON_TOOLUSE", raising=False)

        reply(
            [Message("user", "hi")],
            model="anthropic/claude-3-5-sonnet-20241022",
            stream=True,
        )
        assert captured["break_on_tooluse"] is True


class TestMaxTokensEnvOverride:
    def test_chat_complete_uses_env_override(self, monkeypatch):
        from gptme.llm import _chat_complete
        from gptme.message import Message

        captured: dict[str, Any] = {}

        def fake_chat(messages, model, tools, output_schema=None, max_tokens=None):
            captured["max_tokens"] = max_tokens
            return "ok", {"model": model}

        monkeypatch.setenv("GPTME_MAX_TOKENS", "8192")
        monkeypatch.setattr("gptme.llm.llm_openai.chat", fake_chat)

        response, metadata = _chat_complete(
            [Message("user", "hi")],
            "openrouter/anthropic/claude-haiku-4-5",
            None,
        )

        assert response == "ok"
        assert metadata == {"model": "openrouter/anthropic/claude-haiku-4-5"}
        assert captured["max_tokens"] == 8192

    def test_chat_complete_prefers_explicit_max_tokens(self, monkeypatch):
        from gptme.llm import _chat_complete
        from gptme.message import Message

        captured: dict[str, Any] = {}

        def fake_chat(messages, model, tools, output_schema=None, max_tokens=None):
            captured["max_tokens"] = max_tokens
            return "ok", {"model": model}

        monkeypatch.setenv("GPTME_MAX_TOKENS", "8192")
        monkeypatch.setattr("gptme.llm.llm_openai.chat", fake_chat)

        _chat_complete(
            [Message("user", "hi")],
            "openrouter/anthropic/claude-haiku-4-5",
            None,
            max_tokens=2048,
        )

        assert captured["max_tokens"] == 2048

    def test_chat_complete_ignores_env_override_for_openai(self, monkeypatch):
        from gptme.llm import _chat_complete
        from gptme.message import Message

        captured: dict[str, Any] = {}

        def fake_chat(messages, model, tools, output_schema=None, max_tokens=None):
            captured["max_tokens"] = max_tokens
            return "ok", {"model": model}

        monkeypatch.setenv("GPTME_MAX_TOKENS", "8192")
        monkeypatch.setattr("gptme.llm.llm_openai.chat", fake_chat)

        _chat_complete(
            [Message("user", "hi")],
            "openai/gpt-4o-mini",
            None,
        )

        assert captured["max_tokens"] is None

    def test_stream_uses_env_override(self, monkeypatch):
        from gptme.llm import _stream
        from gptme.message import Message

        captured: dict[str, Any] = {}

        def fake_stream(messages, model, tools, output_schema=None, max_tokens=None):
            captured["max_tokens"] = max_tokens
            if False:
                yield ""
            return {"model": model}

        monkeypatch.setenv("GPTME_MAX_TOKENS", "4096")
        monkeypatch.setattr("gptme.llm.llm_openai.stream", fake_stream)

        wrapper = _stream(
            [Message("user", "hi")],
            "openrouter/anthropic/claude-haiku-4-5",
            None,
        )

        assert list(wrapper) == []
        assert wrapper.metadata == {"model": "openrouter/anthropic/claude-haiku-4-5"}
        assert captured["max_tokens"] == 4096

    def test_stream_ignores_env_override_for_openai(self, monkeypatch):
        from gptme.llm import _stream
        from gptme.message import Message

        captured: dict[str, Any] = {}

        def fake_stream(messages, model, tools, output_schema=None, max_tokens=None):
            captured["max_tokens"] = max_tokens
            if False:
                yield ""
            return {"model": model}

        monkeypatch.setenv("GPTME_MAX_TOKENS", "4096")
        monkeypatch.setattr("gptme.llm.llm_openai.stream", fake_stream)

        wrapper = _stream(
            [Message("user", "hi")],
            "openai/gpt-4o-mini",
            None,
        )

        assert list(wrapper) == []
        assert wrapper.metadata == {"model": "openai/gpt-4o-mini"}
        assert captured["max_tokens"] is None
