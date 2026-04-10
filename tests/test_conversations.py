"""Tests for conversation metadata, including last message preview."""

import json
from pathlib import Path

import pytest

from gptme.logmanager.conversations import ConversationMeta, get_conversations


def _make_conversation(
    tmp_path: Path, conv_id: str, messages: list[dict[str, object]]
) -> Path:
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


def test_detail_false_matches_detail_true(logs_dir):
    """detail=False should return same preview/model/messages as detail=True."""
    messages: list[dict[str, object]] = [
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
            "metadata": {"model": "test-model", "cost": 0.01},
        },
    ]
    _make_conversation(logs_dir, "test-detail", messages)

    full = list(get_conversations(detail=True))
    fast = list(get_conversations(detail=False))
    assert len(full) == len(fast) == 1

    # Preview, role, model, and message count should match
    assert fast[0].last_message_role == full[0].last_message_role
    assert fast[0].last_message_preview == full[0].last_message_preview
    assert fast[0].messages == full[0].messages
    assert fast[0].model == full[0].model
    assert fast[0].id == full[0].id
    assert fast[0].name == full[0].name


def test_detail_false_large_conversation(logs_dir):
    """detail=False uses tail scan for large files but still gets correct preview."""
    # Create a conversation large enough to trigger tail scan (>8KB)
    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": "System prompt",
            "timestamp": "2025-01-01T00:00:00Z",
        },
    ]
    # Add enough messages to exceed _TAIL_BYTES (8192)
    messages.extend(
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"Message {i}: {'x' * 100}",
            "timestamp": f"2025-01-01T00:{i:02d}:00Z",
        }
        for i in range(100)
    )
    # Add metadata at the end
    messages.append(
        {
            "role": "assistant",
            "content": "Final answer with details",
            "timestamp": "2025-01-01T01:40:00Z",
            "metadata": {"model": "test-model-v2", "cost": 0.05},
        }
    )
    _make_conversation(logs_dir, "test-large", messages)

    # Verify the file is larger than tail threshold
    conv_file = logs_dir / "test-large" / "conversation.jsonl"
    assert conv_file.stat().st_size > 8192

    full = list(get_conversations(detail=True))
    fast = list(get_conversations(detail=False))
    assert len(full) == len(fast) == 1

    # Core fields must match
    assert fast[0].last_message_role == full[0].last_message_role
    assert fast[0].last_message_preview == full[0].last_message_preview
    assert fast[0].messages == full[0].messages
    assert fast[0].model == full[0].model

    # In fast mode, cost/token fields are zeroed
    assert fast[0].total_cost == 0.0
    assert fast[0].total_input_tokens == 0

    # Full mode has actual cost
    assert full[0].total_cost == 0.05


def test_detail_false_multi_model_consistency(logs_dir):
    """Both scan modes should return the same model for multi-model conversations."""
    # Create a large conversation that switches models mid-way
    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": "System prompt",
            "timestamp": "2025-01-01T00:00:00Z",
        },
        {
            "role": "assistant",
            "content": "Early response",
            "timestamp": "2025-01-01T00:00:01Z",
            "metadata": {"model": "model-early", "cost": 0.01},
        },
    ]
    # Pad to exceed _TAIL_BYTES (8192)
    messages.extend(
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"Filler message {i}: {'y' * 100}",
            "timestamp": f"2025-01-01T00:{i:02d}:00Z",
        }
        for i in range(100)
    )
    # Switch to a different model at the end
    messages.append(
        {
            "role": "assistant",
            "content": "Late response with new model",
            "timestamp": "2025-01-01T01:41:00Z",
            "metadata": {"model": "model-late", "cost": 0.02},
        }
    )
    _make_conversation(logs_dir, "test-multi-model", messages)

    conv_file = logs_dir / "test-multi-model" / "conversation.jsonl"
    assert conv_file.stat().st_size > 8192

    full = list(get_conversations(detail=True))
    fast = list(get_conversations(detail=False))
    assert len(full) == len(fast) == 1

    # Both modes must return the same (most recent) model
    assert full[0].model == "model-late"
    assert fast[0].model == "model-late"
    assert fast[0].model == full[0].model
