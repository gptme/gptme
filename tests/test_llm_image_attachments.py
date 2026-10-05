"""Case-insensitive image attachments in provider request conversion."""

import base64
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image

from gptme.llm.llm_anthropic import (
    _prepare_messages_for_api as prepare_anthropic,
)
from gptme.llm.llm_openai import _prepare_messages_for_api as prepare_openai
from gptme.llm.utils import process_image_file
from gptme.message import Message
from gptme.tools import get_tool, init_tools


@pytest.mark.parametrize(
    ("suffix", "image_format", "media_type"),
    [
        ("JPG", "JPEG", "image/jpeg"),
        ("JpEg", "JPEG", "image/jpeg"),
        ("PNG", "PNG", "image/png"),
        ("GiF", "GIF", "image/gif"),
        ("WEBP", "WEBP", "image/webp"),
    ],
)
def test_process_image_file_mixed_case_suffix(
    tmp_path: Path, suffix: str, image_format: str, media_type: str
) -> None:
    image_path = tmp_path / f"photo.{suffix}"
    Image.new("RGB", (1, 1)).save(image_path, format=image_format)
    content: list[dict] = []

    result = process_image_file(image_path, content)

    assert result is not None
    data, actual_media_type = result
    assert actual_media_type == media_type
    assert base64.b64decode(data) == image_path.read_bytes()
    assert content == [
        {"type": "text", "text": f"![{image_path.name}]({image_path.name}):"}
    ]


@pytest.mark.parametrize("suffix", ["TXT", "SVG"])
def test_unsupported_uppercase_suffix(tmp_path: Path, suffix: str) -> None:
    image_path = tmp_path / f"photo.{suffix}"
    image_path.write_bytes(b"not a supported image")
    content: list[dict] = []

    assert process_image_file(image_path, content) is None
    assert content == []


def test_uppercase_image_keeps_vision_and_size_checks(tmp_path: Path) -> None:
    image_path = tmp_path / "photo.PNG"
    Image.new("RGB", (1, 1)).save(image_path, format="PNG")
    content: list[dict] = []

    assert (
        process_image_file(image_path, content, check_vision_support=lambda: False)
        is None
    )
    assert content == []
    assert process_image_file(image_path, content, max_size_mb=0) is None
    assert content
    assert "Image size exceeds" in content[-1]["text"]


@pytest.mark.parametrize("suffix", ["jpg", "JPG", "JpEg"])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("tool_result", [False, True])
def test_provider_preserves_image_attachment(
    tmp_path: Path, suffix: str, provider: str, tool_result: bool
) -> None:
    image_path = tmp_path / f"photo.{suffix}"
    Image.new("RGB", (1, 1)).save(image_path, format="JPEG")
    init_tools(allowlist=["save"])
    tool = get_tool("save")
    assert tool is not None
    messages = (
        [
            Message(role="user", content="View the image"),
            Message(
                role="assistant", content='@save(view1): {"path": "x", "content": "x"}'
            ),
            Message(
                role="system",
                content="Image result",
                call_id="view1",
                files=[image_path],
            ),
        ]
        if tool_result
        else [Message(role="user", content="Describe this image", files=[image_path])]
    )
    messages.insert(0, Message(role="system", content="You are helpful."))

    if provider == "openai":
        converted_openai, _ = prepare_openai(messages, "openai/gpt-4o", [tool])
        converted = cast(list[dict[str, Any]], converted_openai)
        image_parts = [
            part
            for msg in converted
            for part in msg.get("content", [])
            if part["type"] == "image_url"
        ]
        assert len(image_parts) == 1
        assert image_parts[0]["image_url"]["url"] == (
            "data:image/jpeg;base64,"
            + base64.b64encode(image_path.read_bytes()).decode()
        )
        if tool_result:
            assert converted[-1]["role"] == "user"
            assert all(part["type"] == "text" for part in converted[-2]["content"])
    else:
        converted_anthropic, _, _ = prepare_anthropic(messages, [tool])
        parts = [
            part
            for msg in cast(list[dict[str, Any]], converted_anthropic)
            for part in msg["content"]
        ]
        image_parts = [part for part in parts if part["type"] == "image"]
        if tool_result:
            image_parts = [
                part
                for block in parts
                if block["type"] == "tool_result"
                for part in block["content"]
                if part["type"] == "image"
            ]
        assert len(image_parts) == 1
        assert image_parts[0]["source"]["media_type"] == "image/jpeg"
        assert (
            base64.b64decode(image_parts[0]["source"]["data"])
            == image_path.read_bytes()
        )

    assert messages[-1].files == [image_path]
