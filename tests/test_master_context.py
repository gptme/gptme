"""
Tests for master context utilities.

Tests byte-range indexing, recovery, and edge cases for master context system.
"""

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


def test_build_master_context_index_basic():
    """Test basic index building with valid JSONL."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"role": "user", "content": "Hello"}\n')
        f.write('{"role": "assistant", "content": "Hi there!"}\n')
        f.write('{"role": "user", "content": "How are you?"}\n')
        f.flush()
        path = Path(f.name)

    try:
        index = build_master_context_index(path)
        assert len(index) == 3
        assert index[0].message_idx == 0
        assert index[1].message_idx == 1
        assert index[2].message_idx == 2
        # Verify byte ranges don't overlap
        for i in range(len(index) - 1):
            assert index[i].byte_end == index[i + 1].byte_start
    finally:
        path.unlink()


def test_build_master_context_index_file_not_found():
    """Test that missing file returns empty index."""
    path = Path("/nonexistent/path/to/file.jsonl")
    index = build_master_context_index(path)
    assert index == []


def test_recover_from_master_context_basic():
    """Test basic recovery of message content."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        msg1 = '{"role": "user", "content": "Hello world"}\n'
        msg2 = '{"role": "assistant", "content": "Hi there!"}\n'
        f.write(msg1)
        f.write(msg2)
        f.flush()
        path = Path(f.name)

    try:
        # Recover first message
        byte_range = MessageByteRange(
            message_idx=0,
            byte_start=0,
            byte_end=len(msg1.encode("utf-8")),
        )
        content = recover_from_master_context(path, byte_range)
        assert content == "Hello world"

        # Recover second message
        byte_range2 = MessageByteRange(
            message_idx=1,
            byte_start=len(msg1.encode("utf-8")),
            byte_end=len(msg1.encode("utf-8")) + len(msg2.encode("utf-8")),
        )
        content2 = recover_from_master_context(path, byte_range2)
        assert content2 == "Hi there!"
    finally:
        path.unlink()


def test_recover_from_master_context_file_not_found():
    """Test that FileNotFoundError is raised for missing file."""
    path = Path("/nonexistent/path/to/file.jsonl")
    byte_range = MessageByteRange(message_idx=0, byte_start=0, byte_end=100)

    with pytest.raises(FileNotFoundError) as excinfo:
        recover_from_master_context(path, byte_range)
    assert "Master log not found" in str(excinfo.value)


def test_recover_from_master_context_invalid_json():
    """Test that ValueError is raised for corrupted JSON."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
        f.write(b"not valid json at all\n")
        f.flush()
        path = Path(f.name)

    try:
        byte_range = MessageByteRange(message_idx=0, byte_start=0, byte_end=22)
        with pytest.raises(ValueError, match="Invalid JSON at byte range") as excinfo:
            recover_from_master_context(path, byte_range)
        assert "Invalid JSON at byte range" in str(excinfo.value)
    finally:
        path.unlink()


def test_recover_from_master_context_missing_content():
    """Test that ValueError is raised when content field is missing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"role": "user", "data": "no content field"}\n')
        f.flush()
        path = Path(f.name)

    try:
        # Calculate exact byte range
        with open(path, "rb") as rf:
            data = rf.read()
        byte_range = MessageByteRange(message_idx=0, byte_start=0, byte_end=len(data))

        with pytest.raises(ValueError, match="has no 'content' field") as excinfo:
            recover_from_master_context(path, byte_range)
        assert "has no 'content' field" in str(excinfo.value)
    finally:
        path.unlink()


def test_recover_from_master_context_invalid_utf8():
    """Test that invalid UTF-8 is handled with replacement characters."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
        # Write JSON with invalid UTF-8 sequence in a way that's still valid JSON
        # The \xff byte is invalid UTF-8
        content = b'{"role": "user", "content": "Hello \xffWorld"}\n'
        f.write(content)
        f.flush()
        path = Path(f.name)

    try:
        byte_range = MessageByteRange(
            message_idx=0, byte_start=0, byte_end=len(content)
        )
        # Should not raise - invalid UTF-8 replaced with replacement character
        result = recover_from_master_context(path, byte_range)
        assert "Hello" in result
        assert "World" in result
        # The \xff should be replaced with replacement character
        assert "\ufffd" in result
    finally:
        path.unlink()


def test_create_master_context_reference():
    """Test creation of master context reference strings."""
    path = Path("/home/user/.cache/gptme/logs/test/conversation.jsonl")
    byte_range = MessageByteRange(message_idx=5, byte_start=1000, byte_end=2000)

    ref = create_master_context_reference(
        logfile=path,
        byte_range=byte_range,
        original_tokens=500,
        preview="This is the start of the message...",
    )

    assert "[Content truncated - 500 tokens]" in ref
    assert "bytes 1000-2000" in ref
    assert "conversation.jsonl" in ref
    assert "This is the start of the message" in ref


def test_create_master_context_reference_no_preview():
    """Test reference creation without preview."""
    path = Path("/path/to/conversation.jsonl")
    byte_range = MessageByteRange(message_idx=0, byte_start=0, byte_end=100)

    ref = create_master_context_reference(
        logfile=path,
        byte_range=byte_range,
        original_tokens=100,
    )

    assert "[Content truncated - 100 tokens]" in ref
    assert "bytes 0-100" in ref
    assert "Preview:" not in ref


def test_build_index_and_recover_roundtrip():
    """Test full roundtrip: build index, then recover each message."""
    messages = [
        {"role": "user", "content": "First message"},
        {"role": "assistant", "content": "Second message with more text"},
        {"role": "user", "content": "Third message"},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
        f.flush()
        path = Path(f.name)

    try:
        # Build index
        index = build_master_context_index(path)
        assert len(index) == 3

        # Recover each message and verify content
        for i, byte_range in enumerate(index):
            content = recover_from_master_context(path, byte_range)
            assert content == messages[i]["content"]
    finally:
        path.unlink()
