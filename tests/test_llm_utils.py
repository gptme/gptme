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
