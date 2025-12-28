"""Tests for master context byte-range tracking utilities."""

import json
import tempfile
from pathlib import Path

import pytest

from gptme.util.master_context import (
    MessageByteRange,
    build_master_context_index,
    create_master_context_reference,
    recover_from_master_context,
)


@pytest.fixture
def sample_jsonl_file():
    """Create a sample conversation.jsonl file for testing."""
    messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"},
        {"role": "user", "content": "Can you help me with Python?"},
        {
            "role": "assistant",
            "content": "Of course! What would you like to know about Python?",
        },
        {
            "role": "system",
            "content": "This is a long tool output that might get truncated...\n" * 100,
        },
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    temp_path.unlink(missing_ok=True)


def test_build_master_context_index(sample_jsonl_file):
    """Test building byte offset index from master log."""
    index = build_master_context_index(sample_jsonl_file)

    # Should have 5 entries for 5 messages
    assert len(index) == 5

    # Check first entry starts at 0
    assert index[0].message_idx == 0
    assert index[0].byte_start == 0

    # Check each entry is contiguous
    for i in range(1, len(index)):
        assert index[i].byte_start == index[i - 1].byte_end
        assert index[i].message_idx == i

    # Check last entry ends at file size
    file_size = sample_jsonl_file.stat().st_size
    assert index[-1].byte_end == file_size


def test_build_master_context_index_missing_file():
    """Test building index from non-existent file."""
    index = build_master_context_index(Path("/nonexistent/file.jsonl"))
    assert index == []


def test_create_master_context_reference(sample_jsonl_file):
    """Test creating truncation reference with byte ranges."""
    byte_range = MessageByteRange(
        message_idx=4,
        byte_start=1000,
        byte_end=2000,
    )

    reference = create_master_context_reference(
        logfile=sample_jsonl_file,
        byte_range=byte_range,
        original_tokens=500,
        preview="This is a preview...",
    )

    # Check reference contains key info
    assert "500 tokens" in reference
    assert str(sample_jsonl_file) in reference
    assert "bytes 1000-2000" in reference
    assert "This is a preview..." in reference
    assert "recover" in reference.lower()


def test_create_master_context_reference_no_preview(sample_jsonl_file):
    """Test creating reference without preview."""
    byte_range = MessageByteRange(
        message_idx=0,
        byte_start=0,
        byte_end=100,
    )

    reference = create_master_context_reference(
        logfile=sample_jsonl_file,
        byte_range=byte_range,
        original_tokens=50,
        preview=None,
    )

    assert "50 tokens" in reference
    assert "Preview:" not in reference


def test_recover_from_master_context(sample_jsonl_file):
    """Test recovering truncated content from master log."""
    # Build index to get real byte ranges
    index = build_master_context_index(sample_jsonl_file)

    # Recover the first message
    content = recover_from_master_context(sample_jsonl_file, index[0])
    assert content == "Hello, how are you?"

    # Recover the second message
    content = recover_from_master_context(sample_jsonl_file, index[1])
    assert content == "I'm doing well, thank you!"

    # Recover the last (long) message
    content = recover_from_master_context(sample_jsonl_file, index[4])
    assert "long tool output" in content


def test_byte_range_round_trip(sample_jsonl_file):
    """Test that building index and recovering yields original content."""
    # Read original messages
    with open(sample_jsonl_file) as f:
        original_messages = [json.loads(line) for line in f]

    # Build index and recover each message
    index = build_master_context_index(sample_jsonl_file)

    for i, byte_range in enumerate(index):
        recovered = recover_from_master_context(sample_jsonl_file, byte_range)
        original = original_messages[i]["content"]
        assert recovered == original, f"Message {i} content mismatch"
