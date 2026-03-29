"""Tests for conversation metadata, including last message preview."""

import json
from pathlib import Path

import pytest

from gptme.logmanager.conversations import ConversationMeta, get_conversations


def _make_conversation(tmp_path: Path, conv_id: str, messages: list[dict]) -> Path:
    """Create a minimal conversation directory with JSONL messages."""
    conv_dir = tmp_path / conv_id
    conv_dir.mkdir()
    jsonl = conv_dir / "conversation.jsonl"
    jsonl.write_text("\n".join(json.dumps(msg) for msg in messages) + "\n")
    return conv_dir


@pytest.fixture()
def logs_dir(tmp_path, monkeypatch):
    """Redirect logs directory to tmp_path for isolated testing."""
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)
    return tmp_path


def test_last_message_preview_basic(logs_dir):
    """Last message preview should contain truncated content of last user/assistant msg."""
    _make_conversation(
        logs_dir,
        "test-preview",
        [
            {
                "role": "system",
                "content": "System prompt",
                "timestamp": "2025-01-01T00:00:00Z",
            },
            {
                "role": "user",
                "content": "Hello world",
                "timestamp": "2025-01-01T00:00:01Z",
            },
            {
                "role": "assistant",
                "content": "Hi there!",
                "timestamp": "2025-01-01T00:00:02Z",
            },
        ],
    )
    convs = list(get_conversations())
    assert len(convs) == 1
    conv = convs[0]
    assert conv.last_message_role == "assistant"
    assert conv.last_message_preview == "Hi there!"


def test_last_message_preview_truncation(logs_dir):
    """Long messages should be truncated to 100 chars with ellipsis."""
    long_content = "x" * 200
    _make_conversation(
        logs_dir,
        "test-truncate",
        [
            {
                "role": "user",
                "content": long_content,
                "timestamp": "2025-01-01T00:00:00Z",
            },
        ],
    )
    convs = list(get_conversations())
    assert len(convs) == 1
    conv = convs[0]
    assert conv.last_message_role == "user"
    assert conv.last_message_preview == "x" * 100 + "..."
    assert len(conv.last_message_preview) == 103


def test_last_message_preview_whitespace_collapse(logs_dir):
    """Multiline content should be collapsed to single line."""
    _make_conversation(
        logs_dir,
        "test-whitespace",
        [
            {
                "role": "user",
                "content": "line one\nline two\n  indented",
                "timestamp": "2025-01-01T00:00:00Z",
            },
        ],
    )
    convs = list(get_conversations())
    assert len(convs) == 1
    conv = convs[0]
    assert conv.last_message_preview == "line one line two indented"


def test_last_message_preview_skips_system(logs_dir):
    """System messages should not appear as preview."""
    _make_conversation(
        logs_dir,
        "test-system-skip",
        [
            {
                "role": "user",
                "content": "Hello",
                "timestamp": "2025-01-01T00:00:00Z",
            },
            {
                "role": "system",
                "content": "System update",
                "timestamp": "2025-01-01T00:00:01Z",
            },
        ],
    )
    convs = list(get_conversations())
    assert len(convs) == 1
    conv = convs[0]
    # Should show user message, not system
    assert conv.last_message_role == "user"
    assert conv.last_message_preview == "Hello"


def test_last_message_preview_empty_conversation(logs_dir):
    """Conversations with only system messages should have no preview."""
    _make_conversation(
        logs_dir,
        "test-empty",
        [
            {
                "role": "system",
                "content": "System prompt",
                "timestamp": "2025-01-01T00:00:00Z",
            },
        ],
    )
    convs = list(get_conversations())
    assert len(convs) == 1
    conv = convs[0]
    assert conv.last_message_role is None
    assert conv.last_message_preview is None


def test_conversation_meta_defaults():
    """New fields should default to None for backwards compatibility."""
    meta = ConversationMeta(
        id="test",
        name="test",
        path="/tmp/test",
        created=0.0,
        modified=0.0,
        messages=0,
        branches=1,
        workspace="/tmp",
    )
    assert meta.last_message_role is None
    assert meta.last_message_preview is None
