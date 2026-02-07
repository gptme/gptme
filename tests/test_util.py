from datetime import datetime

from gptme.util import (
    epoch_to_age,
    example_to_xml,
    transform_examples_to_chat_directives,
)
from gptme.util.generate_name import generate_name, is_generated_name


def test_generate_name():
    name = generate_name()
    assert is_generated_name(name)


def test_epoch_to_age():
    epoch_today = datetime.now().timestamp()
    assert epoch_to_age(epoch_today) == "just now"
    epoch_yesterday = epoch_today - 24 * 60 * 60
    assert epoch_to_age(epoch_yesterday) == "yesterday"


def test_transform_examples_to_chat_directives():
    src = """
# Example
> User: Hello
> Bot: Hi
"""
    expected = """
Example

.. chat::

   User: Hello
   Bot: Hi
"""

    assert transform_examples_to_chat_directives(src, strict=True) == expected


def test_transform_examples_to_chat_directives_tricky():
    src = """
> User: hello
> Assistant: lol
> Assistant: lol
> Assistant: lol
""".strip()

    expected = """

.. chat::

   User: hello
   Assistant: lol
   Assistant: lol
   Assistant: lol"""

    assert transform_examples_to_chat_directives(src, strict=True) == expected


def test_example_to_xml_basic():
    x1 = example_to_xml(
        """
> User: Hello
How are you?
> Assistant: Hi
"""
    )

    assert (
        x1
        == """
<user>
Hello
How are you?
</user>
<assistant>
Hi
</assistant>
""".strip()
    )


def test_example_to_xml_preserve_header():
    x1 = example_to_xml(
        """
Header1
-------

> User: Hello

Header2
-------

> System: blah
"""
    )

    assert (
        x1
        == """
Header1
-------

<user>
Hello
</user>

Header2
-------

<system>
blah
</system>
""".strip()
    )


"""Tests for gptme.util module."""

import tempfile
from pathlib import Path

from gptme.util import safe_read_text


def test_safe_read_text_normal_file():
    """Test reading a normal text file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Hello, world!")
        f.flush()
        path = Path(f.name)

    try:
        content = safe_read_text(path)
        assert content == "Hello, world!"
    finally:
        path.unlink()


def test_safe_read_text_binary_png():
    """Test that PNG files are detected as binary and return None."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        # Write PNG magic bytes
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(b"\x00" * 100)  # Some binary content
        f.flush()
        path = Path(f.name)

    try:
        content = safe_read_text(path)
        assert content is None
    finally:
        path.unlink()


def test_safe_read_text_binary_jpeg():
    """Test that JPEG files are detected as binary and return None."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        # Write JPEG magic bytes
        f.write(b"\xff\xd8\xff\xe0")
        f.write(b"\x00" * 100)
        f.flush()
        path = Path(f.name)

    try:
        content = safe_read_text(path)
        assert content is None
    finally:
        path.unlink()


def test_safe_read_text_file_with_null_bytes():
    """Test that files with null bytes are detected as binary."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"some\x00binary\x00content")
        f.flush()
        path = Path(f.name)

    try:
        content = safe_read_text(path)
        assert content is None
    finally:
        path.unlink()


def test_safe_read_text_invalid_utf8():
    """Test that invalid UTF-8 is handled with replacement characters."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        # Write text with invalid UTF-8 sequence (but not binary signature)
        f.write(b"Hello \x80\x81 World")  # Invalid UTF-8 bytes
        f.flush()
        path = Path(f.name)

    try:
        content = safe_read_text(path)
        # Should replace invalid bytes with replacement character
        assert content is not None
        assert "Hello" in content
        assert "World" in content
    finally:
        path.unlink()


def test_safe_read_text_nonexistent_file():
    """Test that nonexistent files return None."""
    path = Path("/nonexistent/file/path.txt")
    content = safe_read_text(path)
    assert content is None
