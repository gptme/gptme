"""Tests for the computer tool."""

import shutil
import subprocess
from unittest import mock

import pytest

from gptme.tools.computer import (
    _parse_key_sequence,
    _scale_coordinates,
    _ScalingSource,
    IS_MACOS,
    COMMON_KEY_MAP,
    MODIFIER_KEYS,
)


def is_display_available():
    """Check if a usable display is available for tests."""
    if IS_MACOS:
        return True

    # Check if xrandr is available and can run successfully
    if not shutil.which("xrandr"):
        return False

    try:
        subprocess.run(["xrandr"], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


# Create a pytest marker for tests that require a display
display_required = pytest.mark.skipif(
    not IS_MACOS and not is_display_available(),
    reason="Test requires a working display environment",
)


# Parser tests (no mocking needed)
def test_parse_key_sequence_text():
    """Test parsing text input operations."""
    operations = _parse_key_sequence("t:Hello World")
    assert len(operations) == 1
    assert operations[0]["type"] == "text"
    assert operations[0]["text"] == "Hello World"


def test_parse_key_sequence_single_key():
    """Test parsing single key operations."""
    operations = _parse_key_sequence("return")
    assert len(operations) == 1
    assert operations[0]["type"] == "key"
    assert operations[0]["key"] == "return"


def test_parse_key_sequence_combination():
    """Test parsing key combination operations."""
    operations = _parse_key_sequence("ctrl+c")
    assert len(operations) == 1
    assert operations[0]["type"] == "combo"
    assert "ctrl" in operations[0]["modifiers"]
    assert operations[0]["key"] == "c"


def test_parse_key_sequence_chained():
    """Test parsing chained operations."""
    operations = _parse_key_sequence("cmd+space;t:firefox;return")
    assert len(operations) == 3

    # First operation: cmd+space
    assert operations[0]["type"] == "combo"
    assert "cmd" in operations[0]["modifiers"]
    assert operations[0]["key"] == "space"

    # Second operation: t:firefox
    assert operations[1]["type"] == "text"
    assert operations[1]["text"] == "firefox"

    # Third operation: return
    assert operations[2]["type"] == "key"
    assert operations[2]["key"] == "return"


def test_parse_key_sequence_multiple_modifiers():
    """Test parsing key combinations with multiple modifiers."""
    operations = _parse_key_sequence("ctrl+alt+delete")
    assert len(operations) == 1
    assert operations[0]["type"] == "combo"
    assert "ctrl" in operations[0]["modifiers"]
    assert "alt" in operations[0]["modifiers"]
    assert operations[0]["key"] == "delete"


def test_key_mapping():
    """Test that key mappings work correctly."""
    # Test some common key mappings
    assert COMMON_KEY_MAP.get("return") == "return"
    assert COMMON_KEY_MAP.get("enter") == "return"  # Aliases to return
    assert COMMON_KEY_MAP.get("cmd") == "cmd"
    assert COMMON_KEY_MAP.get("command") == "cmd"  # Aliases to cmd

    # Test all modifier keys are recognized
    for modifier in ["ctrl", "alt", "cmd", "shift"]:
        assert modifier in MODIFIER_KEYS


# Scale coordinate tests
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@display_required
def test_coordinate_scaling(mock_resolution):
    """Test coordinate scaling between API and physical space."""
    # API space: 1024x768
    api_x, api_y = 512, 384

    # Scale to physical
    phys_x, phys_y = _scale_coordinates(_ScalingSource.API, api_x, api_y, 1024, 768)

    # Scale back to API
    round_x, round_y = _scale_coordinates(
        _ScalingSource.COMPUTER, phys_x, phys_y, 1024, 768
    )

    # Should be very close to original
    assert abs(round_x - api_x) <= 1
    assert abs(round_y - api_y) <= 1


# Minimal platform-specific tests
@pytest.mark.skipif(not IS_MACOS, reason="macOS-only test")
def test_macos_key_generation():
    """Test command generation for macOS key handling."""
    with mock.patch("gptme.tools.computer._macos_key") as mock_key:
        # Import here to avoid loading platform-specific code
        from gptme.tools.computer import computer

        computer("key", text="cmd+c")
        assert mock_key.called
        assert mock_key.call_args[0][0] == "cmd+c"


@pytest.mark.skipif(IS_MACOS, reason="Linux-only test")
@display_required
def test_linux_key_generation():
    """Test command generation for Linux key handling."""
    with mock.patch("gptme.tools.computer._linux_handle_key_sequence") as mock_key:
        # Import here to avoid loading platform-specific code
        from gptme.tools.computer import computer

        computer("key", text="ctrl+c")
        assert mock_key.called
        assert mock_key.call_args[0][0] == "ctrl+c"
