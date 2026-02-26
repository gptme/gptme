"""Tests for the computer tool."""

import shutil
import subprocess
from unittest import mock

import pytest

from gptme.tools.computer import (
    COMMON_KEY_MAP,
    IS_MACOS,
    MODIFIER_KEYS,
    _chunks,
    _get_display_resolution,
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
    assert dict(ops[0])["key"] == "return"  # type: ignore[call-overload]

    # "escape" should map to "esc"
    ops = _parse_key_sequence("escape")
    assert dict(ops[0])["key"] == "esc"  # type: ignore[call-overload]

    # "command" should map to "cmd" as a modifier
    ops = _parse_key_sequence("command+c")
    assert "cmd" in dict(ops[0])["modifiers"]  # type: ignore[call-overload,operator]


def test_parse_key_sequence_whitespace_handling():
    """Test that whitespace around semicolons is stripped."""
    ops = _parse_key_sequence("ctrl+c ; t:hello ; return")
    assert len(ops) == 3
    assert ops[0]["type"] == "combo"
    assert dict(ops[1])["text"] == "hello"  # type: ignore[call-overload]
    assert dict(ops[2])["key"] == "return"  # type: ignore[call-overload]


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


# === computer() action validation tests ===


@mock.patch("gptme.tools.computer._get_display_resolution", return_value=(1920, 1080))
def test_computer_invalid_action(mock_res):
    """Test that invalid actions raise ValueError."""
    with pytest.raises(ValueError, match="Invalid action"):
        computer("invalid_action")  # type: ignore[arg-type]


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

    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_screenshot.return_value = mock_path
    mock_view_image.return_value = "image_message"
    mock_subprocess_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0
    )

    result = computer("screenshot")
    mock_screenshot.assert_called_once()
    mock_view_image.assert_called_once_with(mock_path)
    assert result == "image_message"


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
