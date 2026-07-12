"""Tests for the computer tool."""

import os
import shutil
import subprocess
from typing import Any, cast
from unittest import mock

import pytest

from gptme.tools.computer import (
    COMMON_KEY_MAP,
    IS_MACOS,
    MODIFIER_KEYS,
    _chunks,
    _get_display_resolution,
    _get_macos_display_scale,
    _linux_accessibility_tree,
    _linux_click_accessible_element,
    _linux_scroll,
    _linux_window_focus,
    _macos_accessibility_tree,
    _macos_click_accessible_element,
    _macos_window_focus,
    _parse_key_sequence,
    _run_xdotool,
    _scale_coordinates,
    _ScalingSource,
    computer,
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


# === _chunks() tests ===


def test_chunks_basic():
    """Test basic string chunking."""
    result = _chunks("abcdef", 2)
    assert result == ["ab", "cd", "ef"]


def test_chunks_uneven():
    """Test chunking with remainder."""
    result = _chunks("abcde", 2)
    assert result == ["ab", "cd", "e"]


def test_chunks_empty():
    """Test chunking empty string."""
    result = _chunks("", 5)
    assert result == []


def test_chunks_larger_than_string():
    """Test chunk size larger than input."""
    result = _chunks("abc", 10)
    assert result == ["abc"]


def test_chunks_size_one():
    """Test single-character chunks."""
    result = _chunks("abc", 1)
    assert result == ["a", "b", "c"]


# === _parse_key_sequence() tests ===


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


def test_parse_key_sequence_kp_prefix():
    """Test parsing explicit key press with kp: prefix."""
    operations = _parse_key_sequence("kp:return")
    assert len(operations) == 1
    assert operations[0]["type"] == "key"
    assert operations[0]["key"] == "return"


def test_parse_key_sequence_alias_mapping():
    """Test that key aliases are resolved during parsing."""
    # "enter" should map to "return"
    ops = _parse_key_sequence("enter")
    assert cast(dict[str, Any], ops[0])["key"] == "return"

    # "escape" should map to "esc"
    ops = _parse_key_sequence("escape")
    assert cast(dict[str, Any], ops[0])["key"] == "esc"

    # "command" should map to "cmd" as a modifier
    ops = _parse_key_sequence("command+c")
    assert "cmd" in cast(dict[str, Any], ops[0])["modifiers"]


def test_parse_key_sequence_whitespace_handling():
    """Test that whitespace around semicolons is stripped."""
    ops = _parse_key_sequence("ctrl+c ; t:hello ; return")
    assert len(ops) == 3
    assert ops[0]["type"] == "combo"
    assert cast(dict[str, Any], ops[1])["text"] == "hello"
    assert cast(dict[str, Any], ops[2])["key"] == "return"


# === Key mapping tests ===


def test_key_mapping():
    """Test that key mappings work correctly."""
    assert COMMON_KEY_MAP.get("return") == "return"
    assert COMMON_KEY_MAP.get("enter") == "return"
    assert COMMON_KEY_MAP.get("cmd") == "cmd"
    assert COMMON_KEY_MAP.get("command") == "cmd"

    for modifier in ["ctrl", "alt", "cmd", "shift"]:
        assert modifier in MODIFIER_KEYS


def test_key_mapping_completeness():
    """Test that all expected aliases are present."""
    # Control aliases
    assert COMMON_KEY_MAP["ctrl"] == "ctrl"
    assert COMMON_KEY_MAP["control"] == "ctrl"

    # Alt aliases
    assert COMMON_KEY_MAP["alt"] == "alt"
    assert COMMON_KEY_MAP["option"] == "alt"

    # Super/Cmd aliases
    assert COMMON_KEY_MAP["cmd"] == "cmd"
    assert COMMON_KEY_MAP["command"] == "cmd"
    assert COMMON_KEY_MAP["super"] == "cmd"


# === _scale_coordinates() tests ===


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@display_required
def test_coordinate_scaling(mock_resolution):
    """Test coordinate scaling between API and physical space."""
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


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_coordinate_scaling_out_of_bounds(mock_resolution):
    """Test that out-of-bounds API coordinates raise ValueError."""
    with pytest.raises(ValueError, match="out of bounds"):
        _scale_coordinates(_ScalingSource.API, 1025, 384, 1024, 768)

    with pytest.raises(ValueError, match="out of bounds"):
        _scale_coordinates(_ScalingSource.API, 512, 769, 1024, 768)


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_coordinate_scaling_origin(mock_resolution):
    """Test scaling of origin coordinates."""
    x, y = _scale_coordinates(_ScalingSource.API, 0, 0, 1024, 768)
    assert x == 0
    assert y == 0


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_coordinate_scaling_max_corner(mock_resolution):
    """Test scaling of maximum corner coordinates."""
    x, y = _scale_coordinates(_ScalingSource.API, 1024, 768, 1024, 768)
    assert x == 1920
    assert y == 1080


# === _get_display_resolution() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_get_display_resolution_linux():
    """Test display resolution detection on Linux via xrandr."""
    xrandr_output = """\
Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
XWAYLAND0 connected primary 1920x1080+0+0
   1920x1080     59.96*+
   1440x900      59.89
"""
    with mock.patch("subprocess.check_output", return_value=xrandr_output):
        width, height = _get_display_resolution()
        assert width == 1920
        assert height == 1080


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_get_display_resolution_linux_failure():
    """Test that xrandr failure raises RuntimeError."""
    with (
        mock.patch(
            "subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "xrandr"),
        ),
        pytest.raises(RuntimeError, match="Failed to get display resolution"),
    ):
        _get_display_resolution()


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_get_display_resolution_macos():
    """Test display resolution detection on macOS via system_profiler."""
    profiler_output = """\
Graphics/Displays:
    Apple M1:
      Displays:
        Color LCD:
          Display Type: Built-In Retina LCD
          Resolution: 2560 x 1664 Retina
"""
    with mock.patch("subprocess.check_output", return_value=profiler_output):
        width, height = _get_display_resolution()
        assert width == 2560
        assert height == 1664


# === _get_macos_display_scale() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_get_macos_display_scale_via_appkit():
    """Scale factor is read from AppKit.NSScreen.backingScaleFactor() when available."""
    _get_macos_display_scale.cache_clear()
    mock_screen = mock.MagicMock()
    mock_screen.backingScaleFactor.return_value = 2.0
    mock_appkit = mock.MagicMock()
    mock_appkit.NSScreen.mainScreen.return_value = mock_screen

    with mock.patch.dict("sys.modules", {"AppKit": mock_appkit}):
        scale = _get_macos_display_scale()

    assert scale == 2.0


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_get_macos_display_scale_via_system_profiler():
    """Scale factor is derived from system_profiler when AppKit is unavailable."""
    _get_macos_display_scale.cache_clear()
    profiler_output = """\
Graphics/Displays:
    Apple M1:
      Displays:
        Color LCD:
          Resolution: 2560 x 1664 Retina
          UI Looks like: 1280 x 832 @ 60.00Hz
"""

    with (
        mock.patch.dict("sys.modules", {"AppKit": None}),
        mock.patch("subprocess.check_output", return_value=profiler_output),
    ):
        scale = _get_macos_display_scale()

    assert scale == pytest.approx(2560 / 1280, rel=1e-3)


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_get_macos_display_scale_via_system_profiler_multi_display():
    """Scale factor parsed correctly even when system_profiler has 'Maximum Resolution' before 'Resolution:'."""
    _get_macos_display_scale.cache_clear()
    profiler_output = """\
Graphics/Displays:

    Display with External Monitor:
      Displays:
        Built-In Retina:
          Maximum Resolution: 3840 x 2160
          Resolution: 2560 x 1664 Retina
          UI Looks like: 1280 x 832 @ 60.00Hz
        External Monitor:
          Resolution: 1920 x 1080
          UI Looks like: 1920 x 1080 @ 60.00Hz
"""

    with (
        mock.patch.dict("sys.modules", {"AppKit": None}),
        mock.patch("subprocess.check_output", return_value=profiler_output),
    ):
        scale = _get_macos_display_scale()

    # Must NOT match "Maximum Resolution: 3840 x 2160" — should find actual Resolution: 2560
    # giving 2560 / 1280 = 2.0
    assert scale == pytest.approx(2.0, rel=1e-3)


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_get_macos_display_scale_via_system_profiler_prefers_main_display():
    """Main Display metadata wins when multiple display blocks have valid scale pairs."""
    _get_macos_display_scale.cache_clear()
    profiler_output = """\
Graphics/Displays:
    Apple M1:
      Displays:
        External Monitor:
          Resolution: 1920 x 1080
          UI Looks like: 1920 x 1080 @ 60.00Hz
        Color LCD:
          Main Display: Yes
          Resolution: 2560 x 1664 Retina
          UI Looks like: 1280 x 832 @ 60.00Hz
"""

    with (
        mock.patch.dict("sys.modules", {"AppKit": None}),
        mock.patch("subprocess.check_output", return_value=profiler_output),
    ):
        scale = _get_macos_display_scale()

    assert scale == pytest.approx(2.0, rel=1e-3)


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_get_macos_display_scale_fallback_to_2x():
    """Falls back to 2.0 when both AppKit and system_profiler are unavailable."""
    _get_macos_display_scale.cache_clear()
    with (
        mock.patch.dict("sys.modules", {"AppKit": None}),
        mock.patch(
            "subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "system_profiler"),
        ),
    ):
        scale = _get_macos_display_scale()

    assert scale == 2.0


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(2560, 1664))
@mock.patch("gptme.tools.computer._get_macos_display_scale", return_value=2.0)
@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_coordinate_scaling_macos_2x(mock_scale, mock_res):
    """Coordinate scaling on macOS 2× Retina yields logical (non-physical) coords."""
    # Physical: 2560×1664, logical (after ÷2): 1280×832
    # Clicking at API (640, 416) with API space 1280×832 → physical (1280, 832)
    # But gptme works in logical space, so we expect (640, 416)
    x, y = _scale_coordinates(_ScalingSource.API, 640, 416, 1280, 832)
    assert x == 640
    assert y == 416


# === _run_xdotool() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_run_xdotool_basic():
    """Test basic xdotool command execution."""
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["xdotool", "key", "Return"],
            returncode=0,
            stdout="",
            stderr="",
        )
        _run_xdotool("key Return")
        mock_run.assert_called_once()
        # Verify xdotool is first arg
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "xdotool"
        assert "key" in call_args
        assert "Return" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_run_xdotool_with_display():
    """Test xdotool uses DISPLAY env var when provided."""
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        _run_xdotool("key Return", display=":99")
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["DISPLAY"] == ":99"


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_run_xdotool_failure():
    """Test xdotool command failure raises RuntimeError."""
    with mock.patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "xdotool", stderr="error"
        )
        with pytest.raises(RuntimeError, match="xdotool command failed"):
            _run_xdotool("invalid_cmd")


@mock.patch("gptme.tools.computer.IS_MACOS", True)
def test_run_xdotool_macos_raises():
    """Test that xdotool raises on macOS."""
    with pytest.raises(RuntimeError, match="not supported on macOS"):
        _run_xdotool("key Return")


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_run_xdotool_timeout():
    """Test that xdotool timeout raises RuntimeError."""
    with mock.patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("xdotool", 10)
        with pytest.raises(RuntimeError, match="xdotool command timed out"):
            _run_xdotool("key Return")


@mock.patch("gptme.tools.computer.IS_MACOS", False)
def test_get_display_resolution_timeout():
    """Test that xrandr timeout raises RuntimeError."""
    with (
        mock.patch(
            "subprocess.check_output",
            side_effect=subprocess.TimeoutExpired("xrandr", 10),
        ),
        pytest.raises(RuntimeError, match="Failed to get display resolution"),
    ):
        _get_display_resolution()


# === computer() action validation tests ===


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_invalid_action(mock_res):
    """Test that invalid actions raise ValueError."""
    with pytest.raises(ValueError, match="Invalid action"):
        computer(cast(Any, "invalid_action"))


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_mouse_move_requires_coordinate(mock_res):
    """Test that mouse_move without coordinate raises ValueError."""
    with pytest.raises(ValueError, match="coordinate is required"):
        computer("mouse_move")


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_drag_requires_coordinate(mock_res):
    """Test that left_click_drag without coordinate raises ValueError."""
    with pytest.raises(ValueError, match="coordinate is required"):
        computer("left_click_drag")


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_key_requires_text(mock_res):
    """Test that key action without text raises ValueError."""
    with pytest.raises(ValueError, match="text is required"):
        computer("key")


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_type_requires_text(mock_res):
    """Test that type action without text raises ValueError."""
    with pytest.raises(ValueError, match="text is required"):
        computer("type")


# === computer() action execution tests (mocked) ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_mouse_move_linux(mock_xdotool, mock_res):
    """Test mouse_move calls xdotool with scaled coordinates."""
    result = computer("mouse_move", coordinate=(512, 384))
    assert result is None
    mock_xdotool.assert_called_once()
    call_args = mock_xdotool.call_args[0][0]
    assert "mousemove" in call_args
    assert "--sync" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_left_click_linux(mock_xdotool, mock_res):
    """Test left_click calls xdotool click."""
    result = computer("left_click")
    assert result is None
    mock_xdotool.assert_called_once()
    call_args = mock_xdotool.call_args[0][0]
    assert "click" in call_args
    assert "1" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_right_click_linux(mock_xdotool, mock_res):
    """Test right_click calls xdotool with button 3."""
    computer("right_click")
    call_args = mock_xdotool.call_args[0][0]
    assert "click" in call_args
    assert "3" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_middle_click_linux(mock_xdotool, mock_res):
    """Test middle_click calls xdotool with button 2."""
    computer("middle_click")
    call_args = mock_xdotool.call_args[0][0]
    assert "click" in call_args
    assert "2" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_double_click_linux(mock_xdotool, mock_res):
    """Test double_click calls xdotool with repeat flag."""
    computer("double_click")
    call_args = mock_xdotool.call_args[0][0]
    assert "click" in call_args
    assert "--repeat" in call_args
    assert "2" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._linux_handle_key_sequence")
def test_computer_key_linux(mock_key_seq, mock_res):
    """Test key action delegates to _linux_handle_key_sequence."""
    computer("key", text="ctrl+c")
    mock_key_seq.assert_called_once()
    assert mock_key_seq.call_args[0][0] == "ctrl+c"


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._linux_type")
def test_computer_type_linux(mock_type, mock_res):
    """Test type action delegates to _linux_type."""
    computer("type", text="Hello World")
    mock_type.assert_called_once()
    assert mock_type.call_args[0][0] == "Hello World"


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_drag_linux(mock_xdotool, mock_res):
    """Test left_click_drag calls xdotool with mousedown/mouseup sequence."""
    computer("left_click_drag", coordinate=(800, 600))
    call_args = mock_xdotool.call_args[0][0]
    assert "mousedown" in call_args
    assert "mousemove" in call_args
    assert "mouseup" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_cursor_position_linux(mock_xdotool, mock_res):
    """Test cursor_position parses xdotool getmouselocation output."""
    mock_xdotool.return_value = "X=960\nY=540\nSCREEN=0\nWINDOW=12345\n"
    result = computer("cursor_position")
    assert result is None
    call_args = mock_xdotool.call_args[0][0]
    assert "getmouselocation" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer._run_xdotool")
def test_computer_cursor_position_bad_output(mock_xdotool, mock_res):
    """Test cursor_position with unexpected xdotool output."""
    mock_xdotool.return_value = "unexpected output"
    with pytest.raises(RuntimeError, match="Unexpected xdotool output"):
        computer("cursor_position")


# === _linux_handle_key_sequence() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._run_xdotool")
@mock.patch("gptme.tools.computer._linux_type")
def test_linux_key_sequence_text_op(mock_type, mock_xdotool):
    """Test that t:text in key sequence delegates to _linux_type."""
    from gptme.tools.computer import _linux_handle_key_sequence

    _linux_handle_key_sequence("t:hello", ":1")
    mock_type.assert_called_once_with("hello", ":1")
    mock_xdotool.assert_not_called()


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._run_xdotool")
def test_linux_key_sequence_single_key(mock_xdotool):
    """Test single key maps correctly for xdotool."""
    from gptme.tools.computer import _linux_handle_key_sequence

    _linux_handle_key_sequence("return", ":1")
    call_args = mock_xdotool.call_args[0][0]
    assert "key" in call_args
    assert "Return" in call_args  # xdotool uses "Return" not "return"


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._run_xdotool")
def test_linux_key_sequence_combo(mock_xdotool):
    """Test modifier+key combo maps correctly for xdotool."""
    from gptme.tools.computer import _linux_handle_key_sequence

    _linux_handle_key_sequence("ctrl+c", ":1")
    call_args = mock_xdotool.call_args[0][0]
    assert "key" in call_args
    assert "ctrl" in call_args


# === _linux_type() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._run_xdotool")
def test_linux_type_short_text(mock_xdotool):
    """Test typing short text produces single xdotool call."""
    from gptme.tools.computer import _linux_type

    _linux_type("hello", ":1")
    mock_xdotool.assert_called_once()
    call_args = mock_xdotool.call_args[0][0]
    assert "type" in call_args
    assert "--delay" in call_args


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._run_xdotool")
def test_linux_type_long_text_chunked(mock_xdotool):
    """Test typing long text is split into chunks."""
    from gptme.tools.computer import TYPING_GROUP_SIZE, _linux_type

    long_text = "a" * (TYPING_GROUP_SIZE * 3 + 10)
    _linux_type(long_text, ":1")
    assert mock_xdotool.call_count == 4  # 3 full chunks + 1 remainder


# === Screenshot action tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer.view_image")
@mock.patch("subprocess.run")
@mock.patch("gptme.tools.computer.screenshot")
def test_computer_screenshot_success(
    mock_screenshot, mock_subprocess_run, mock_view_image, mock_res
):
    """Test screenshot action calls screenshot tool and scales result."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from gptme.message import Message

    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_screenshot.return_value = mock_path
    base_msg = Message(role="user", content="[image]", files=[mock_path])
    mock_view_image.return_value = base_msg
    mock_subprocess_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0
    )

    # Unset WIDTH/HEIGHT so env vars don't override the mocked display resolution.
    with mock.patch.dict("os.environ", {}, clear=False) as env:
        env.pop("WIDTH", None)
        env.pop("HEIGHT", None)
        result = computer("screenshot")
    mock_screenshot.assert_called_once()
    mock_view_image.assert_called_once_with(mock_path)
    assert isinstance(result, Message)
    assert result.metadata is not None
    artifacts = result.metadata.get("artifacts", [])
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "image"
    assert artifacts[0]["tool"] == "computer"
    # Verify resize uses correct API dimensions, not squared coordinates.
    cmd = mock_subprocess_run.call_args[0][0]
    assert "-resize" in cmd
    resize_dim = cmd[cmd.index("-resize") + 1]
    # With physical 1920x1080 (16:9), FWXGA (1366x768) is the closest API target.
    assert resize_dim == "1366x768!"


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
@mock.patch("gptme.tools.computer.screenshot")
def test_computer_screenshot_failure(mock_screenshot, mock_res):
    """Test screenshot action handles missing file gracefully."""
    from pathlib import Path
    from unittest.mock import MagicMock

    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False
    mock_screenshot.return_value = mock_path

    result = computer("screenshot")
    assert result is None


# === Platform-specific tests ===


@pytest.mark.skipif(not IS_MACOS, reason="macOS-only test")
def test_macos_key_generation():
    """Test command generation for macOS key handling."""
    with mock.patch("gptme.tools.computer._macos_key") as mock_key:
        computer("key", text="cmd+c")
        assert mock_key.called
        assert mock_key.call_args[0][0] == "cmd+c"


@pytest.mark.skipif(IS_MACOS, reason="Linux-only test")
@display_required
def test_linux_key_generation():
    """Test command generation for Linux key handling."""
    with mock.patch("gptme.tools.computer._linux_handle_key_sequence") as mock_key:
        computer("key", text="ctrl+c")
        assert mock_key.called
        assert mock_key.call_args[0][0] == "ctrl+c"


# === _macos_click() validation tests ===


def test_macos_click_invalid_button():
    """Test that invalid button numbers raise ValueError."""
    from gptme.tools.computer import _macos_click

    with mock.patch("gptme.tools.computer._ensure_cliclick"):
        with pytest.raises(ValueError, match="Invalid button number"):
            _macos_click(4)
        with pytest.raises(ValueError, match="Invalid button number"):
            _macos_click(0)


# === _macos_mouse_move() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)  # prevent macOS-only guard
@mock.patch("subprocess.run")
def test_macos_mouse_move_success(mock_run):
    """Test _macos_mouse_move calls cliclick with correct args."""
    from gptme.tools.computer import _macos_mouse_move

    _macos_mouse_move(100, 200)
    mock_run.assert_called_once_with(
        ["cliclick", "m:100,200"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("subprocess.run")
def test_macos_mouse_move_timeout(mock_run):
    """Test _macos_mouse_move raises RuntimeError on cliclick timeout."""
    from gptme.tools.computer import _macos_mouse_move

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="cliclick", timeout=10)
    with pytest.raises(RuntimeError, match="cliclick mouse move timed out"):
        _macos_mouse_move(100, 200)


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("subprocess.run")
def test_macos_mouse_move_file_not_found(mock_run):
    """Test _macos_mouse_move raises RuntimeError when cliclick missing."""
    from gptme.tools.computer import _macos_mouse_move

    mock_run.side_effect = FileNotFoundError()
    with pytest.raises(RuntimeError, match="cliclick not found"):
        _macos_mouse_move(100, 200)


# === _macos_drag() tests ===


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._ensure_cliclick")
@mock.patch("subprocess.run")
def test_macos_drag_success(mock_run, mock_ensure):
    """Test _macos_drag gets position then issues drag commands."""
    from gptme.tools.computer import _macos_drag

    # First subprocess.run returns cursor position, second is the drag
    mock_run.side_effect = [
        mock.MagicMock(stdout="500,300\n"),  # cursor position
        mock.MagicMock(stdout=""),  # drag result
    ]
    _macos_drag(800, 600)

    mock_ensure.assert_called_once()
    # Two calls: position query + drag
    assert mock_run.call_count == 2
    # First call: position
    assert mock_run.call_args_list[0].args[0] == ["cliclick", "p"]
    # Second call: drag from start to dest
    assert mock_run.call_args_list[1].args[0] == [
        "cliclick",
        "dd:500,300",
        "du:800,600",
    ]


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._ensure_cliclick")
@mock.patch("subprocess.run")
def test_macos_drag_position_timeout(mock_run, mock_ensure):
    """Test _macos_drag raises RuntimeError on position query timeout."""
    from gptme.tools.computer import _macos_drag

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="cliclick", timeout=10)
    with pytest.raises(RuntimeError, match="cliclick cursor position query timed out"):
        _macos_drag(800, 600)


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._ensure_cliclick")
@mock.patch("subprocess.run")
def test_macos_drag_position_failure(mock_run, mock_ensure):
    """Test _macos_drag raises RuntimeError on position query failure."""
    from gptme.tools.computer import _macos_drag

    mock_run.side_effect = subprocess.CalledProcessError(
        returncode=1, cmd=["cliclick", "p"], stderr="error"
    )
    with pytest.raises(RuntimeError, match="Failed to get cursor position"):
        _macos_drag(800, 600)


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._ensure_cliclick")
@mock.patch("subprocess.run")
def test_macos_drag_timeout(mock_run, mock_ensure):
    """Test _macos_drag raises RuntimeError on drag command timeout."""
    from gptme.tools.computer import _macos_drag

    # First call succeeds (position query), second call (drag) times out
    mock_run.side_effect = [
        mock.MagicMock(stdout="500,300\n"),
        subprocess.TimeoutExpired(cmd="cliclick", timeout=10),
    ]
    with pytest.raises(RuntimeError, match="cliclick drag command timed out"):
        _macos_drag(800, 600)


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._ensure_cliclick")
@mock.patch("subprocess.run")
def test_macos_drag_failure(mock_run, mock_ensure):
    """Test _macos_drag raises RuntimeError on drag command failure."""
    from gptme.tools.computer import _macos_drag

    mock_run.side_effect = [
        mock.MagicMock(stdout="500,300\n"),
        subprocess.CalledProcessError(returncode=1, cmd=["cliclick"], stderr="err"),
    ]
    with pytest.raises(RuntimeError, match="Failed to drag"):
        _macos_drag(800, 600)


# === macOS cursor_position tests ===

# Helper: create a mini _get_display_resolution mock that returns a standard res
_MOCK_MACOS_RES = (1920, 1080)


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_MACOS_RES
)
@mock.patch("gptme.tools.computer._get_macos_display_scale", return_value=2.0)
@mock.patch("subprocess.run")
def test_computer_cursor_position_macos(mock_run, mock_scale, mock_res):
    """Test cursor_position on macOS parses cliclick output correctly."""
    from gptme.tools.computer import computer

    mock_run.return_value = mock.MagicMock(stdout="960,540\n")
    result = computer("cursor_position")
    assert result is None  # prints to stdout
    mock_run.assert_called_once_with(
        ["cliclick", "p"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_MACOS_RES
)
@mock.patch("subprocess.run")
def test_computer_cursor_position_macos_timeout(mock_run, mock_res):
    """Test cursor_position on macOS raises on cliclick timeout."""
    from gptme.tools.computer import computer

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="cliclick", timeout=10)
    with pytest.raises(RuntimeError, match="cliclick cursor position query timed out"):
        computer("cursor_position")


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_MACOS_RES
)
@mock.patch("subprocess.run")
def test_computer_cursor_position_macos_file_not_found(mock_run, mock_res):
    """Test cursor_position on macOS raises when cliclick is missing."""
    from gptme.tools.computer import computer

    mock_run.side_effect = FileNotFoundError()
    with pytest.raises(RuntimeError, match="cliclick not found"):
        computer("cursor_position")


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_MACOS_RES
)
@mock.patch("subprocess.run")
def test_computer_cursor_position_macos_called_process_error(mock_run, mock_res):
    """Test cursor_position on macOS raises on cliclick error."""
    from gptme.tools.computer import computer

    mock_run.side_effect = subprocess.CalledProcessError(
        returncode=1, cmd=["cliclick", "p"], stderr="cliclick failed"
    )
    with pytest.raises(RuntimeError, match="Failed to get cursor position"):
        computer("cursor_position")


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_MACOS_RES
)
@mock.patch("subprocess.run")
def test_computer_cursor_position_macos_bad_output(mock_run, mock_res):
    """Test cursor_position on macOS raises on unparseable cliclick output."""
    from gptme.tools.computer import computer

    mock_run.return_value = mock.MagicMock(stdout="not,valid,output\n")
    with pytest.raises(RuntimeError, match="Failed to get cursor position"):
        computer("cursor_position")


# === scroll action tests ===

_MOCK_LINUX_RES = (1366, 768)


def test_linux_scroll_down_calls_xdotool():
    """_linux_scroll down issues xdotool click 5 (scroll-down button)."""
    calls = []

    def fake_xdotool(cmd: str, display: str) -> str:
        calls.append(cmd)
        return ""

    with mock.patch("gptme.tools.computer._run_xdotool", side_effect=fake_xdotool):
        _linux_scroll(100, 200, "down", ":1", amount=3)

    # First call should be mousemove, then a single click --repeat 3 5
    assert calls[0].startswith("mousemove")
    click_calls = [c for c in calls if c.startswith("click")]
    assert len(click_calls) == 1
    assert "--repeat 3" in click_calls[0], f"Expected --repeat 3, got: {click_calls}"
    assert "5" in click_calls[0], f"Expected button 5 (scroll down), got: {click_calls}"


def test_linux_scroll_up_calls_xdotool():
    """_linux_scroll up issues xdotool click 4 (scroll-up button)."""
    calls = []

    def fake_xdotool(cmd: str, display: str) -> str:
        calls.append(cmd)
        return ""

    with mock.patch("gptme.tools.computer._run_xdotool", side_effect=fake_xdotool):
        _linux_scroll(100, 200, "up", ":1", amount=2)

    click_calls = [c for c in calls if c.startswith("click")]
    assert len(click_calls) == 1
    assert "--repeat 2" in click_calls[0], f"Expected --repeat 2, got: {click_calls}"
    assert "4" in click_calls[0], f"Expected button 4 (scroll up), got: {click_calls}"


def test_linux_scroll_invalid_direction():
    """_linux_scroll raises ValueError for unknown direction."""
    with pytest.raises(ValueError, match="Invalid scroll direction"):
        _linux_scroll(100, 200, "diagonal", ":1")


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES
)
def test_computer_scroll_linux(mock_res):
    """computer('scroll') on Linux calls _linux_scroll with correct args."""
    scroll_calls = []

    def fake_scroll(x, y, direction, display, amount=3):
        scroll_calls.append((x, y, direction, display, amount))

    with mock.patch("gptme.tools.computer._linux_scroll", side_effect=fake_scroll):
        result = computer("scroll", text="down", coordinate=(512, 400))

    assert result is None
    assert len(scroll_calls) == 1
    _, _, direction, _, _ = scroll_calls[0]
    assert direction == "down"


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES
)
def test_computer_scroll_missing_coordinate(mock_res):
    """computer('scroll') raises ValueError when coordinate is missing."""
    with pytest.raises(ValueError, match="coordinate is required"):
        computer("scroll", text="down")


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES
)
def test_computer_scroll_missing_direction(mock_res):
    """computer('scroll') raises ValueError when direction text is missing."""
    with pytest.raises(ValueError, match="text.*direction.*required"):
        computer("scroll", coordinate=(512, 400))


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES
)
def test_computer_scroll_invalid_direction(mock_res):
    """computer('scroll') raises ValueError for an invalid direction string."""
    with pytest.raises(ValueError, match="Invalid scroll direction"):
        computer("scroll", text="sideways", coordinate=(512, 400))


# ============================================================
# _compute_change_ratio tests
# ============================================================

import tempfile
from pathlib import Path

from gptme.tools.computer import _compute_change_ratio


def _make_png(color: tuple[int, int, int], size: tuple[int, int] = (100, 100)) -> Path:
    """Create a temporary solid-colour PNG and return its path."""
    from PIL import Image

    img = Image.new("RGB", size, color)
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(f.name)
    f.close()
    return Path(f.name)


def test_compute_change_ratio_identical():
    """Two identical images → 0% change."""
    p = _make_png((100, 150, 200))
    try:
        assert _compute_change_ratio(p, p) == 0.0
    finally:
        p.unlink(missing_ok=True)


def test_compute_change_ratio_totally_different():
    """Completely different colours → 100% change."""
    p1 = _make_png((0, 0, 0))
    p2 = _make_png((255, 255, 255))
    try:
        ratio = _compute_change_ratio(p1, p2)
        assert ratio == pytest.approx(1.0)
    finally:
        p1.unlink(missing_ok=True)
        p2.unlink(missing_ok=True)


def test_compute_change_ratio_partial():
    """Half-black / half-white images → ~50% change."""
    from PIL import Image

    size = (100, 100)
    img1 = Image.new("RGB", size, (0, 0, 0))
    img2 = Image.new("RGB", size, (0, 0, 0))
    # Paint the right half of img2 white
    for x in range(50, 100):
        for y in range(100):
            img2.putpixel((x, y), (255, 255, 255))

    f1 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    f2 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img1.save(f1.name)
    img2.save(f2.name)
    f1.close()
    f2.close()
    p1, p2 = Path(f1.name), Path(f2.name)
    try:
        ratio = _compute_change_ratio(p1, p2)
        assert 0.45 < ratio < 0.55
    finally:
        p1.unlink(missing_ok=True)
        p2.unlink(missing_ok=True)


def test_compute_change_ratio_missing_file():
    """A missing file path returns 0.0 (no crash)."""
    p = Path("/nonexistent/path.png")
    assert _compute_change_ratio(p, p) == 0.0


def test_compute_change_ratio_mismatched_sizes():
    """Images with different sizes return 0.0 (uncomparable)."""
    p1 = _make_png((0, 0, 0), size=(100, 100))
    p2 = _make_png((255, 0, 0), size=(200, 200))
    try:
        assert _compute_change_ratio(p1, p2) == 0.0
    finally:
        p1.unlink(missing_ok=True)
        p2.unlink(missing_ok=True)


def test_compute_change_ratio_small_text_change():
    """Simulated terminal text change stays above the 0.2% detection threshold.

    Typing a short command into an xterm on a 1024x768 screen changes roughly
    0.3–0.5% of pixels (22 chars × ~112px each out of 786,432 total).  This
    test verifies that a 0.3% change is detectable with the new threshold (0.002)
    and was NOT detectable with the old threshold (0.01) — catching a regression
    where act_and_observe always reported "No screen change detected" after typing.
    """
    from PIL import Image

    # Simulate a 1024×768 terminal screen: mostly black background
    size = (1024, 768)
    img1 = Image.new("RGB", size, (0, 0, 0))
    img2 = img1.copy()

    # Simulate typing "echo act_and_observe_ok" into an xterm at the bottom of the window.
    # Each character is roughly 7×14 pixels; 22 chars = 22 × 7 × 14 = 2156 pixels.
    # We paint a 154×14 pixel rectangle (22 chars wide) to represent the text.
    char_w, char_h = 7, 14
    num_chars = 22
    text_x, text_y = 100, 700  # near the bottom of the terminal
    for x in range(text_x, text_x + num_chars * char_w):
        for y in range(text_y, text_y + char_h):
            img2.putpixel((x, y), (200, 200, 200))  # light text on dark background

    changed_pixels = num_chars * char_w * char_h  # 2156
    expected_ratio = changed_pixels / (size[0] * size[1])  # ~0.0027

    f1 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    f2 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img1.save(f1.name)
    img2.save(f2.name)
    f1.close()
    f2.close()
    p1, p2 = Path(f1.name), Path(f2.name)
    try:
        ratio = _compute_change_ratio(p1, p2)
        # Should be approximately the expected ratio
        assert abs(ratio - expected_ratio) < 0.001, (
            f"expected ~{expected_ratio:.4f}, got {ratio:.4f}"
        )
        # Must be above the new 0.002 threshold so act_and_observe detects it
        assert ratio >= 0.002, (
            f"ratio {ratio:.4f} is below new threshold 0.002 — small text changes won't be detected"
        )
        # This confirms the old 0.01 threshold was wrong: it was ABOVE the change ratio
        assert ratio < 0.01, (
            f"ratio {ratio:.4f} exceeds old 0.01 threshold — old behaviour would have detected this; test premise is wrong"
        )
    finally:
        p1.unlink(missing_ok=True)
        p2.unlink(missing_ok=True)


# ============================================================
# wait_for_change action tests (unit — mocks screenshot/time)
# ============================================================

_MOCK_LINUX_RES_WFC = (1920, 1080)


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WFC
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_wait_for_change_detects_change(mock_transport, mock_res, tmp_path):
    """wait_for_change returns a message when the screen changes."""
    from PIL import Image

    def _solid(color):
        p = tmp_path / f"{color}.png"
        Image.new("RGB", (100, 100), color).save(p)
        return p

    baseline = _solid((0, 0, 0))
    changed = _solid((255, 255, 255))

    call_count = {"n": 0}

    def _fake_screenshot():
        call_count["n"] += 1
        return baseline if call_count["n"] == 1 else changed

    with (
        mock.patch("gptme.tools.computer.screenshot", side_effect=_fake_screenshot),
        mock.patch("gptme.tools.computer._sleep"),
        mock.patch(
            "gptme.tools.computer._make_screenshot_msg", return_value=mock.sentinel.msg
        ),
    ):
        result = computer("wait_for_change", text="5")

    assert result is mock.sentinel.msg


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WFC
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_wait_for_change_timeout_returns_screenshot(mock_transport, mock_res, tmp_path):
    """wait_for_change returns a screenshot even when timeout is reached without change."""
    from PIL import Image

    static = tmp_path / "static.png"
    Image.new("RGB", (100, 100), (42, 42, 42)).save(static)

    call_count = {"count": 0}

    def monotonic_side_effect():
        call_count["count"] += 1
        if call_count["count"] <= 2:
            return 0.0
        return 100.0

    with (
        mock.patch("gptme.tools.computer.screenshot", return_value=static),
        mock.patch("gptme.tools.computer._sleep"),
        mock.patch(
            "gptme.tools.computer._monotonic", side_effect=monotonic_side_effect
        ),
        mock.patch(
            "gptme.tools.computer._make_screenshot_msg",
            return_value=mock.sentinel.timeout_msg,
        ),
    ):
        result = computer("wait_for_change", text="1")

    assert result is mock.sentinel.timeout_msg


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WFC
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_wait_for_change_polls_with_backoff(mock_transport, mock_res, tmp_path):
    """wait_for_change uses adaptive backoff: starts at 50ms, backs off to 500ms cap."""
    from PIL import Image

    static = tmp_path / "static.png"
    Image.new("RGB", (100, 100), (42, 42, 42)).save(static)

    sleep_calls = []

    def _record_sleep(interval):
        sleep_calls.append(interval)

    def _fake_monotonic():
        # Keep the loop inside the deadline until the third sleep has observed
        # the backoff sequence, then expire it on the next loop check.
        return 0.0 if len(sleep_calls) < 3 else 100.0

    with (
        mock.patch("gptme.tools.computer.screenshot", return_value=static),
        mock.patch("gptme.tools.computer._sleep", side_effect=_record_sleep),
        mock.patch("gptme.tools.computer._monotonic", side_effect=_fake_monotonic),
        mock.patch(
            "gptme.tools.computer._make_screenshot_msg",
            return_value=mock.sentinel.timeout_msg,
        ),
    ):
        result = computer("wait_for_change", text="1")

    assert result is mock.sentinel.timeout_msg
    # First call: 50ms; second: 100ms; third: 200ms — doubling backoff
    assert len(sleep_calls) >= 3
    assert sleep_calls[0] == pytest.approx(0.05)  # 50ms initial interval
    assert sleep_calls[1] == pytest.approx(0.10)  # 100ms after first backoff
    assert sleep_calls[2] == pytest.approx(0.20)  # 200ms after second backoff


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WFC
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_wait_for_change_detects_small_change(mock_transport, mock_res, tmp_path):
    """wait_for_change must detect small pixel changes (~0.3%) typical of terminal output.

    Regression test for issue #216: typing a short command in a terminal changes
    only ~0.3-0.5% of a 1024x768 screen.  The old 1% threshold missed these small
    changes and produced spurious "No screen change detected" messages.  The correct
    0.2% threshold catches them.

    This test uses a 100x100 image where 30 pixels (0.3%) change colour: above the
    0.2% threshold but below the old 1% threshold.
    """
    from PIL import Image

    # 100x100 image; 30 pixels differ = 0.3% change.
    # 0.3% >= 0.2% (new threshold) → must be detected.
    # 0.3% <  1.0% (old threshold) → would NOT have been detected.
    baseline_img = Image.new("RGB", (100, 100), (0, 0, 0))
    changed_img = baseline_img.copy()
    for x in range(30):
        changed_img.putpixel((x, 0), (255, 255, 255))

    baseline = tmp_path / "baseline.png"
    changed = tmp_path / "changed.png"
    baseline_img.save(baseline)
    changed_img.save(changed)

    call_count = {"n": 0}

    def _fake_screenshot():
        call_count["n"] += 1
        return baseline if call_count["n"] == 1 else changed

    with (
        mock.patch("gptme.tools.computer.screenshot", side_effect=_fake_screenshot),
        mock.patch("gptme.tools.computer._sleep"),
        mock.patch(
            "gptme.tools.computer._make_screenshot_msg", return_value=mock.sentinel.msg
        ),
    ):
        result = computer("wait_for_change", text="5")

    assert result is mock.sentinel.msg, (
        "wait_for_change did not detect a 0.3% pixel change — "
        "threshold may be set too high (old 1% threshold caused issue #216 terminal delays)"
    )


# ============================================================
# window_focus action tests
# ============================================================

_MOCK_LINUX_RES_WF = (1920, 1080)


@pytest.mark.skipif(IS_MACOS, reason="Linux-only: xdotool window focus")
@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WF
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_window_focus_linux_success(mock_transport, mock_res, capsys):
    """window_focus on Linux calls xdotool search --sync with the given pattern."""
    with mock.patch("gptme.tools.computer.subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(
            returncode=0, stdout="12345\n", stderr=""
        )
        result = computer("window_focus", text="Terminal")

    assert result is None
    out = capsys.readouterr().out
    assert "Terminal" in out
    # Verify xdotool was called with --sync and the pattern
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "xdotool"
    assert "search" in cmd
    assert "--sync" in cmd
    assert "Terminal" in cmd
    assert "windowfocus" in cmd


@pytest.mark.skipif(IS_MACOS, reason="Linux-only: xdotool window focus")
@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WF
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_window_focus_timeout_raises(mock_transport, mock_res):
    """window_focus raises RuntimeError when the window doesn't appear within timeout."""
    with (
        mock.patch(
            "gptme.tools.computer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["xdotool"], 10.0),
        ),
        pytest.raises(RuntimeError, match="No window matching.*'nonexistent'"),
    ):
        computer("window_focus", text="nonexistent")


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WF
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_window_focus_requires_text(mock_transport, mock_res):
    """window_focus raises ValueError when text is not provided."""
    with pytest.raises(ValueError, match="text.*required for window_focus"):
        computer("window_focus", text=None)


@pytest.mark.skipif(IS_MACOS, reason="Linux-only: xdotool window focus")
@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch(
    "gptme.tools.computer._get_display_resolution", return_value=_MOCK_LINUX_RES_WF
)
@mock.patch("gptme.tools.computer.get_transport", return_value=None)
def test_window_focus_xdotool_failure_raises(mock_transport, mock_res):
    """window_focus raises RuntimeError when xdotool returns non-zero."""
    err = subprocess.CalledProcessError(1, ["xdotool"], stderr="no window found")
    with (
        mock.patch("gptme.tools.computer.subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match="xdotool search/focus failed"),
    ):
        computer("window_focus", text="NoSuchWindow")


def test_linux_window_focus_unit():
    """_linux_window_focus passes --sync, --limit, pattern, and windowfocus to xdotool."""
    with mock.patch("gptme.tools.computer.subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(returncode=0, stdout="999\n", stderr="")
        _linux_window_focus("MyApp", display=":1", timeout=5.0)

    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "xdotool"
    assert "--sync" in cmd
    assert "--limit" in cmd
    assert "MyApp" in cmd
    assert "windowfocus" in cmd
    # timeout + 2 headroom
    assert call_args[1]["timeout"] == pytest.approx(7.0)


# macOS window_focus tests
# ============================================================


def test_macos_window_focus_success():
    """_macos_window_focus returns when osascript reports 'found'."""
    with mock.patch("gptme.tools.computer.subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(
            returncode=0, stdout="found\n", stderr=""
        )
        _macos_window_focus("Terminal", timeout=5.0)

    assert mock_run.called
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "osascript"
    assert cmd[-1] == "Terminal"


def test_macos_window_focus_no_match_raises():
    """_macos_window_focus raises RuntimeError when no matching process is found within timeout.

    This is the P1 bug: previously the function returned None (false success) when the
    AppleScript found no matching process, causing subsequent keystrokes to go to the
    wrong window. Now it raises RuntimeError after the timeout expires.
    """
    with (
        mock.patch("gptme.tools.computer.subprocess.run") as mock_run,
        mock.patch("gptme.tools.computer._monotonic") as mock_time,
        mock.patch("gptme.tools.computer._sleep"),
        pytest.raises(RuntimeError, match="No window matching.*'MissingApp'"),
    ):
        # First call returns not_found, second call has monotonic past deadline
        mock_run.return_value = mock.MagicMock(
            returncode=0, stdout="not_found\n", stderr=""
        )
        mock_time.side_effect = [0.0, 0.0, 10.1]  # start, after first run, after sleep
        _macos_window_focus("MissingApp", timeout=10.0)


def test_macos_window_focus_retry_then_found():
    """_macos_window_focus retries until the window appears."""
    results = [
        mock.MagicMock(returncode=0, stdout="not_found\n", stderr=""),
        mock.MagicMock(returncode=0, stdout="not_found\n", stderr=""),
        mock.MagicMock(returncode=0, stdout="found\n", stderr=""),
    ]
    with (
        mock.patch("gptme.tools.computer.subprocess.run", side_effect=results),
        mock.patch("gptme.tools.computer._monotonic", side_effect=[0.0, 0.5, 1.0, 1.5]),
        mock.patch("gptme.tools.computer._sleep"),
    ):
        _macos_window_focus("SlowApp", timeout=10.0)


def test_macos_window_focus_osascript_failure_raises():
    """_macos_window_focus raises RuntimeError when osascript exits non-zero."""
    err = subprocess.CalledProcessError(1, ["osascript"], stderr="permission denied")
    with (
        mock.patch("gptme.tools.computer.subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match="Failed to focus window matching.*'MyApp'"),
    ):
        _macos_window_focus("MyApp", timeout=5.0)


def test_macos_window_focus_accepts_double_quotes_in_pattern():
    """Quoted titles should be passed as argv, not interpolated into AppleScript."""
    pattern = '"My App"'
    with mock.patch("gptme.tools.computer.subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(
            returncode=0, stdout="found\n", stderr=""
        )
        _macos_window_focus(pattern, timeout=5.0)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "osascript"
    assert cmd[-1] == pattern
    assert pattern not in cmd[2]


# === Accessibility tree tests ===


def _make_mock_accessible(
    role_name: str, name: str, children: list | None = None, bbox=None
):
    """Build a minimal pyatspi-like mock accessible object."""
    obj = mock.MagicMock()
    obj.getRoleName.return_value = role_name
    obj.name = name
    state_set = mock.MagicMock()
    state_set.contains.return_value = False
    obj.getState.return_value = state_set
    kids = children or []
    obj.childCount = len(kids)
    obj.__getitem__ = lambda self, i: kids[i]
    if bbox is not None:
        component = mock.MagicMock()
        component.getExtents.return_value = mock.MagicMock(
            x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3]
        )
        obj.queryComponent.return_value = component
    return obj


def _make_pyatspi_module(desktop_obj):
    """Return a minimal pyatspi mock module."""
    mod = mock.MagicMock()
    mod.Registry.getDesktop.return_value = desktop_obj
    mod.DESKTOP_COORDS = 0
    return mod


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_accessibility_tree_no_pyatspi_raises():
    """Missing pyatspi raises RuntimeError with install hint."""
    with (
        mock.patch.dict("sys.modules", {"pyatspi": None}),
        pytest.raises(RuntimeError, match="pyatspi not installed"),
    ):
        _linux_accessibility_tree(":1")


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_accessibility_tree_returns_structured_text():
    """accessibility_tree returns indented role:name lines for each accessible object."""
    button = _make_mock_accessible("push button", "OK")
    app = _make_mock_accessible("application", "TestApp", children=[button])
    desktop = _make_mock_accessible("desktop frame", "", children=[app])

    pyatspi_mod = _make_pyatspi_module(desktop)

    with mock.patch.dict("sys.modules", {"pyatspi": pyatspi_mod}):
        result = _linux_accessibility_tree(":1")

    assert "TestApp" in result
    assert "push button" in result
    assert "OK" in result
    # Roles and names must NOT be repr-quoted so agents can copy them directly
    # into click_accessible_element without quote mismatches.
    assert "'push button'" not in result
    assert "'OK'" not in result
    # app should be indented one level, button two levels
    lines = result.splitlines()
    app_line = next(ln for ln in lines if "TestApp" in ln)
    btn_line = next(ln for ln in lines if "OK" in ln)
    assert btn_line.startswith("  " + "  ")  # deeper indent than app
    assert app_line.startswith("  ")
    # The button line should be directly copyable as role_name:element_name
    assert "push button: OK" in result


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_accessibility_tree_empty_desktop():
    """Empty desktop returns a non-empty string indicating zero apps."""
    desktop = _make_mock_accessible("desktop frame", "", children=[])
    pyatspi_mod = _make_pyatspi_module(desktop)

    with mock.patch.dict("sys.modules", {"pyatspi": pyatspi_mod}):
        result = _linux_accessibility_tree(":1")

    assert "Desktop" in result or "empty" in result or result.strip()


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_accessibility_tree_sets_display_env():
    """accessibility_tree sets DISPLAY to the given display before connecting to pyatspi."""
    desktop = _make_mock_accessible("desktop frame", "", children=[])
    captured: list[str] = []

    def capturing_get_desktop(index):
        captured.append(os.environ.get("DISPLAY", ""))
        return desktop

    pyatspi_mod = _make_pyatspi_module(desktop)
    pyatspi_mod.Registry.getDesktop.side_effect = capturing_get_desktop

    with mock.patch.dict("sys.modules", {"pyatspi": pyatspi_mod}):
        _linux_accessibility_tree(":42")

    assert captured == [":42"]


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_click_accessible_element_no_pyatspi_raises():
    """Missing pyatspi raises RuntimeError."""
    with (
        mock.patch.dict("sys.modules", {"pyatspi": None}),
        pytest.raises(RuntimeError, match="pyatspi not installed"),
    ):
        _linux_click_accessible_element("push button", "OK", ":1")


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_click_accessible_element_found_returns_center():
    """click_accessible_element returns the center (x, y) of a matching element."""
    # bbox: x=100, y=200, width=80, height=40 → center (140, 220)
    button = _make_mock_accessible("push button", "Submit", bbox=(100, 200, 80, 40))
    app = _make_mock_accessible("application", "MyApp", children=[button])
    desktop = _make_mock_accessible("desktop frame", "", children=[app])

    pyatspi_mod = _make_pyatspi_module(desktop)

    with mock.patch.dict("sys.modules", {"pyatspi": pyatspi_mod}):
        x, y = _linux_click_accessible_element("push button", "Submit", ":1")

    assert x == 140
    assert y == 220


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_click_accessible_element_case_insensitive():
    """Name matching is case-insensitive (substring)."""
    button = _make_mock_accessible("push button", "SUBMIT FORM", bbox=(0, 0, 100, 50))
    app = _make_mock_accessible("application", "App", children=[button])
    desktop = _make_mock_accessible("desktop frame", "", children=[app])

    pyatspi_mod = _make_pyatspi_module(desktop)

    with mock.patch.dict("sys.modules", {"pyatspi": pyatspi_mod}):
        x, y = _linux_click_accessible_element("push button", "submit", ":1")

    assert x == 50
    assert y == 25


@pytest.mark.skipif(IS_MACOS, reason="AT-SPI2 is Linux-only")
def test_click_accessible_element_not_found_raises():
    """Raises RuntimeError with helpful message when element is not found."""
    desktop = _make_mock_accessible("desktop frame", "", children=[])
    pyatspi_mod = _make_pyatspi_module(desktop)

    with (
        mock.patch.dict("sys.modules", {"pyatspi": pyatspi_mod}),
        pytest.raises(RuntimeError, match="No accessible element"),
    ):
        _linux_click_accessible_element("push button", "Nonexistent", ":1")


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_accessibility_tree_routes_to_macos(mock_res):
    """On macOS, accessibility_tree calls _macos_accessibility_tree (not Linux path)."""
    with mock.patch(
        "gptme.tools.computer._macos_accessibility_tree",
        return_value="Process: Safari\n  Window: Test\n    AXButton: Search",
    ) as mock_tree:
        computer("accessibility_tree")
    mock_tree.assert_called_once()


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_click_accessible_element_missing_text(mock_res):
    """click_accessible_element without text raises ValueError on Linux."""
    with pytest.raises(ValueError, match="text="):
        computer("click_accessible_element")


@mock.patch("gptme.tools.computer.IS_MACOS", False)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_click_accessible_element_missing_colon(mock_res):
    """click_accessible_element with text missing ':' raises ValueError."""
    with pytest.raises(ValueError, match="role_name:element_name"):
        computer("click_accessible_element", text="no-colon-here")


# === macOS accessibility_tree tests ===


def test_macos_accessibility_tree_success():
    """_macos_accessibility_tree parses osascript output correctly."""
    fake_output = "Process: Safari\n  Window: Test\n    AXButton: Search\n      AXTextField: query"
    mock_result = mock.MagicMock()
    mock_result.stdout = fake_output

    with mock.patch("subprocess.run", return_value=mock_result):
        result = _macos_accessibility_tree()

    assert "Process: Safari" in result
    assert "AXButton: Search" in result


def test_macos_accessibility_tree_max_depth_controls_child_walk():
    """_macos_accessibility_tree wires max_depth into the generated AppleScript."""
    mock_result = mock.MagicMock()
    mock_result.stdout = ""

    with mock.patch("subprocess.run", return_value=mock_result) as mock_run:
        _macos_accessibility_tree(max_depth=1)
    script_depth_1 = mock_run.call_args.args[0][2]

    with mock.patch("subprocess.run", return_value=mock_result) as mock_run:
        _macos_accessibility_tree(max_depth=2)
    script_depth_2 = mock_run.call_args.args[0][2]

    assert "every UI element of elem1" not in script_depth_1
    assert "every UI element of elem1" in script_depth_2


def test_macos_accessibility_tree_empty_output():
    """_macos_accessibility_tree returns placeholder for empty output."""
    mock_result = mock.MagicMock()
    mock_result.stdout = ""

    with mock.patch("subprocess.run", return_value=mock_result):
        result = _macos_accessibility_tree()

    assert result == "(empty accessibility tree)"


def test_macos_accessibility_tree_osascript_missing():
    """_macos_accessibility_tree raises RuntimeError if osascript is not found."""
    with (
        mock.patch("subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(RuntimeError, match="osascript not found"),
    ):
        _macos_accessibility_tree()


def test_macos_accessibility_tree_timeout():
    """_macos_accessibility_tree raises RuntimeError on timeout."""
    with (
        mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 20)
        ),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        _macos_accessibility_tree()


def test_macos_accessibility_tree_permission_error():
    """_macos_accessibility_tree raises RuntimeError with helpful message on failure."""
    err = subprocess.CalledProcessError(1, "osascript", stderr="Not authorized")
    with (
        mock.patch("subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match="Accessibility permission"),
    ):
        _macos_accessibility_tree()


# === macOS click_accessible_element tests ===


def test_macos_click_accessible_element_success():
    """_macos_click_accessible_element returns correct (x, y) from osascript output."""
    mock_result = mock.MagicMock()
    mock_result.stdout = "AXButton\x1fSubmit\x1f150\x1f300\n"

    with mock.patch("subprocess.run", return_value=mock_result):
        x, y = _macos_click_accessible_element("AXButton", "Submit")

    assert x == 150
    assert y == 300


def test_macos_click_accessible_element_not_found():
    """_macos_click_accessible_element raises RuntimeError when element not found."""
    mock_result = mock.MagicMock()
    mock_result.stdout = ""

    with (
        mock.patch("subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError, match="No accessible element"),
    ):
        _macos_click_accessible_element("AXButton", "Nonexistent")


def test_macos_click_accessible_element_does_not_inject_inputs():
    """Caller-controlled role/name values are matched in Python, not AppleScript."""
    mock_result = mock.MagicMock()
    mock_result.stdout = ""

    role_name = 'AXButton" then\n    do shell script "touch /tmp/pwned"'
    element_name = 'Submit" then\n    do shell script "touch /tmp/pwned"'

    with (
        mock.patch("subprocess.run", return_value=mock_result) as mock_run,
        pytest.raises(RuntimeError, match="No accessible element"),
    ):
        _macos_click_accessible_element(role_name, element_name)

    script = mock_run.call_args.args[0][2]
    assert role_name not in script
    assert element_name not in script
    assert "do shell script" not in script


def test_macos_click_accessible_element_osascript_missing():
    """_macos_click_accessible_element raises RuntimeError when osascript not found."""
    with (
        mock.patch("subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(RuntimeError, match="osascript not found"),
    ):
        _macos_click_accessible_element("AXButton", "Submit")


def test_macos_click_accessible_element_timeout():
    """_macos_click_accessible_element raises RuntimeError on timeout."""
    with (
        mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 15)
        ),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        _macos_click_accessible_element("AXButton", "Submit")


@mock.patch("gptme.tools.computer.IS_MACOS", True)
@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_click_accessible_element_routes_to_macos(mock_res):
    """On macOS, click_accessible_element calls _macos_click_accessible_element."""
    with (
        mock.patch(
            "gptme.tools.computer._macos_click_accessible_element",
            return_value=(100, 200),
        ) as mock_click,
        mock.patch("gptme.tools.computer._macos_mouse_move"),
        mock.patch("gptme.tools.computer._macos_click"),
    ):
        computer("click_accessible_element", text="AXButton:OK")
    mock_click.assert_called_once_with("AXButton", "OK")
