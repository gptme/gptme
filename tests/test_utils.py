"""Tests for llm/utils.py"""

import base64
from pathlib import Path


def test_image_size_check_uses_raw_bytes(tmp_path: Path) -> None:
    """Test that image size validation uses raw bytes, not base64-encoded size.

    This test verifies the fix for Issue #1030 Finding 3: the size check
    should compare raw file bytes against the limit, not the base64-encoded
    size (which is ~33% larger).
    """
    from gptme.llm.utils import process_image_file

    # Create a test image file that is 0.8MB raw (within 1MB limit)
    # but would be ~1.07MB when base64 encoded (exceeds limit if checked wrong)
    raw_size_bytes = int(0.8 * 1024 * 1024)  # 0.8 MB
    test_image = tmp_path / "test.png"

    # Write raw bytes (just need file of correct size, not valid image)
    # Use PNG magic bytes so it's detected as an image
    png_header = b"\x89PNG\r\n\x1a\n"
    test_image.write_bytes(png_header + b"\x00" * (raw_size_bytes - len(png_header)))

    # With max_size_mb=1.0, a 0.8MB file should pass
    # If the check used base64 size (~1.07MB), it would incorrectly fail
    content_parts: list[dict] = []
    result = process_image_file(
        file_path=str(test_image),
        max_size_mb=1.0,
        content_parts=content_parts,
    )

    # Should succeed - file is within raw size limit
    assert (
        result is not None
    ), f"0.8MB file should pass 1MB limit, but got error: {content_parts}"
    data, media_type = result
    assert media_type == "image/png"
    assert data is not None

    # Verify the returned data is base64 encoded and larger than raw
    decoded = base64.b64decode(data)
    assert len(decoded) == raw_size_bytes
    assert len(data) > raw_size_bytes  # base64 is ~33% larger


def test_image_size_check_rejects_oversized_files(tmp_path: Path) -> None:
    """Test that files exceeding the raw size limit are rejected."""
    from gptme.llm.utils import process_image_file

    # Create a file that is 1.2MB (exceeds 1MB limit)
    raw_size_bytes = int(1.2 * 1024 * 1024)  # 1.2 MB
    test_image = tmp_path / "large.png"
    png_header = b"\x89PNG\r\n\x1a\n"
    test_image.write_bytes(png_header + b"\x00" * (raw_size_bytes - len(png_header)))

    content_parts: list[dict] = []
    result = process_image_file(
        file_path=str(test_image),
        max_size_mb=1.0,
        content_parts=content_parts,
    )

    # Should fail - file exceeds raw size limit
    assert result is None
    assert len(content_parts) == 2
    assert "exceeds 1.0MB" in content_parts[1]["text"]
