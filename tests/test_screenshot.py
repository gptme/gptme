"""Tests for screenshot tool security."""

from pathlib import Path

import pytest

from gptme.tools.screenshot import OUTPUT_DIR, _validate_screenshot_path


def test_screenshot_path_validation():
    """Test that path traversal attempts are blocked."""
    # Valid paths within OUTPUT_DIR should work
    valid_path = OUTPUT_DIR / "test.png"
    result = _validate_screenshot_path(valid_path)
    assert result == valid_path.resolve()

    # Subdirectory within OUTPUT_DIR should work
    subdir_path = OUTPUT_DIR / "subdir" / "test.png"
    result = _validate_screenshot_path(subdir_path)
    # Note: parent may not exist yet, but the path should still validate

    # Path traversal attempts should be blocked
    with pytest.raises(ValueError, match="must be within"):
        _validate_screenshot_path(Path("/etc/passwd"))

    with pytest.raises(ValueError, match="must be within"):
        _validate_screenshot_path(OUTPUT_DIR / ".." / "etc" / "passwd")

    with pytest.raises(ValueError, match="must be within"):
        _validate_screenshot_path(Path("/tmp/outputs/../evil.png"))
