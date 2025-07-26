"""
Shared utilities for LLM implementations.
"""

import base64
import logging
from pathlib import Path

from ..tools import ToolUse
from ..tools.base import Parameter


def extract_tool_uses_from_assistant_message(
    message_content: str | list[dict[str, str]],
) -> tuple[list[dict], list[ToolUse]]:
    """Extract tool uses from assistant message content.

    This shared utility processes message content to extract tool uses,
    handling both list and string content types. It processes text line by line
    to detect tool calls and separates text before tool calls.

    Args:
        message_content: The message content (string or list of content parts)

    Returns:
        tuple: (content_parts, tool_uses) where:
            - content_parts: List of content parts (text and non-text)
            - tool_uses: List of extracted ToolUse objects with call_id
    """
    text = ""
    content_parts = []
    tool_uses = []

    # Some content are text, some are list
    if isinstance(message_content, list):
        message_parts = message_content
    else:
        message_parts = [{"type": "text", "text": message_content}]

    for message_part in message_parts:
        if message_part["type"] != "text":
            content_parts.append(message_part)
            continue

        # For a message part of type `text`` we try to extract the tool_uses
        # We search line by line to stop as soon as we have a tool call
        # It makes it easier to split in multiple parts.
        for line in message_part["text"].split("\n"):
            text += line + "\n"

            tooluses = [
                tooluse
                for tooluse in ToolUse.iter_from_content(text)
                if tooluse.is_runnable
            ]
            if not tooluses:
                continue

            # At that point we should always have exactly one tooluse
            # Because we remove the previous ones as soon as we encounter
            # them so we can't have more.
            assert len(tooluses) == 1
            tooluse = tooluses[0]
            # We only want to add a tool call if we have a call_id which
            # means it is a tool response
            if tooluse.call_id:
                before_tool = text[: tooluse.start]

                if before_tool.strip():
                    content_parts.append({"type": "text", "text": before_tool})

                tool_uses.append(tooluse)
            else:
                content_parts.append({"type": "text", "text": text})
            # The text is emptied to start over with the next lines if any.
            text = ""

    return content_parts, tool_uses


def parameters2dict(parameters: list[Parameter]) -> dict[str, object]:
    """Convert Parameter objects to JSON Schema dictionary format.

    This utility converts a list of Parameter objects into the JSON Schema
    format expected by both OpenAI and Anthropic APIs for tool definitions.

    Args:
        parameters: List of Parameter objects to convert

    Returns:
        JSON Schema dictionary with type, properties, required, and additionalProperties
    """
    required = []
    properties = {}

    for param in parameters:
        if param.required:
            required.append(param.name)
        properties[param.name] = {"type": param.type, "description": param.description}

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def process_image_file(
    file_path,
    content_parts,
    max_size_mb=5,
    expand_user=False,
    check_vision_support=None,
):
    """Process an image file and add appropriate content parts.

    This shared utility handles the common image processing logic used by both
    Anthropic and OpenAI LLM implementations, including file validation,
    base64 encoding, and size checking.

    Args:
        file_path: Path to the image file
        content_parts: List to append content parts to
        max_size_mb: Maximum file size in MB
        expand_user: Whether to expand user path (~)
        check_vision_support: Optional callable to check if vision is supported

    Returns:
        tuple: (data, media_type) if successful processing, None if skipped
    """

    logger = logging.getLogger(__name__)

    ALLOWED_FILE_EXTS = ["jpg", "jpeg", "png", "gif"]

    f = Path(file_path)
    if expand_user:
        f = f.expanduser()

    ext = f.suffix[1:]
    if ext not in ALLOWED_FILE_EXTS:
        logger.warning("Unsupported file type: %s", ext)
        return None

    if check_vision_support and not check_vision_support():
        logger.warning("Model does not support vision")
        return None

    if ext == "jpg":
        ext = "jpeg"
    media_type = f"image/{ext}"

    content_parts.append(
        {
            "type": "text",
            "text": f"![{f.name}]({f.name}):",
        }
    )

    # read file
    data_bytes = f.read_bytes()
    data = base64.b64encode(data_bytes).decode("utf-8")

    # check that the file is not too large
    max_size_bytes = max_size_mb * 1_024 * 1_024
    if len(data) > max_size_bytes:
        content_parts.append(
            {
                "type": "text",
                "text": f"Image size exceeds {max_size_mb}MB. Please upload a smaller image.",
            }
        )
        return None

    return data, media_type
