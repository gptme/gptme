"""
Tool for computer interaction for X11 or macOS environments, including screen capture, keyboard, and mouse control.

The computer tool provides direct interaction with the desktop environment.
Similar to Anthropic's computer use demo, but integrated with gptme's architecture.

.. rubric:: Features

- Keyboard input simulation
- Mouse control (movement, clicks, dragging)
- Screen capture with automatic scaling
- Cursor position tracking

.. rubric:: Installation

On Linux, requires X11 and xdotool::

    # On Debian/Ubuntu
    sudo apt install xdotool

    # On Arch Linux
    sudo pacman -S xdotool

On macOS, uses native ``screencapture`` and external tool ``cliclick``::

    brew install cliclick

You need to give your terminal both screen recording and accessibility permissions in System Preferences.

.. rubric:: Configuration

The tool uses these environment variables:

- DISPLAY: X11 display to use (default: ":1", Linux only)
- WIDTH: Screen width (default: 1024)
- HEIGHT: Screen height (default: 768)

.. rubric:: Usage

The tool supports these actions:

Keyboard:
    - key: Send key sequence (e.g., "Return", "Control_L+c")
    - type: Type text with realistic delays

Mouse:
    - mouse_move: Move mouse to coordinates
    - left_click: Click left mouse button
    - right_click: Click right mouse button
    - middle_click: Click middle mouse button
    - double_click: Double click left mouse button
    - left_click_drag: Click and drag to coordinates

Screen:
    - screenshot: Take and view a screenshot
    - cursor_position: Get current mouse position
    - wait_for_change: Poll until screen changes, then return one screenshot

Window management:
    - window_focus: Wait for a window matching a name pattern to appear and focus it

Accessibility (Linux/AT-SPI2 only):
    - accessibility_tree: Dump the AT-SPI2 accessibility tree for all apps
    - click_accessible_element: Find and click an element by role and name (text='role:name')

The tool automatically handles screen resolution scaling to ensure optimal performance
with LLM vision capabilities.

.. rubric:: Tips for Complex Operations

For complex operations involving multiple keypresses, you can use semicolon-separated sequences with ``key``:

Examples:
    - Filling a login form: ``t:username;kp:tab;t:password;kp:return``
    - Switching applications: ``cmd+tab`` on macOS, ``alt+Tab`` on Linux
    - (macOS) Opening Spotlight and searching: ``cmd+space;t:firefox;return``

Using a single sequence for complex operations ensures proper timing and recognition of keyboard shortcuts.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypedDict

from ..dirs import get_state_dir
from .base import ToolFunction, ToolSpec, ToolUse
from .computer_transport import get_transport
from .screenshot import screenshot
from .vision import view_image

if TYPE_CHECKING:
    from pathlib import Path

    from ..message import ArtifactDescriptor, Message, MessageMetadata
    from .computer_transport import ComputerTransport

logger = logging.getLogger(__name__)


# Platform detection
IS_MACOS = platform.system() == "Darwin"

# Subdirectory of the gptme state dir where computer-use audit records live.
COMPUTER_AUDIT_SUBDIR = "computer-audit"


def _audit_log_path() -> Path:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return get_state_dir() / COMPUTER_AUDIT_SUBDIR / f"{date}.jsonl"


def _audit_log(
    action: str,
    text: str | None,
    coordinate: tuple[int, int] | None,
    duration: float,
    error: str | None,
) -> None:
    """Append one record of a computer-use action to the audit log.

    Records action metadata (never raw ``text``, since actions like ``type``
    can carry passwords or other sensitive input) so failures and the shape of
    what was done are auditable without leaking typed content into logs.
    """
    path = _audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "text_len": len(text) if text is not None else None,
            "coordinate": coordinate,
            "duration_ms": round(duration * 1000, 1),
            "error": error,
        }
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        # Audit logging must never break the underlying action.
        logger.warning(f"Failed to write computer-use audit log: {e}")


def _make_screenshot_msg(path: Path, tool: str = "computer") -> Message | None:
    """Return view_image message augmented with an artifact descriptor."""
    msg = view_image(path)
    if not msg.files:
        # view_image returns a system error message (no files attached) when path not found
        print("Error: Screenshot failed")
        return None
    descriptor: ArtifactDescriptor = {
        "source_type": "attachment",
        "path": str(path),
        "kind": "image",
        "mime_type": "image/png",
        "tool": tool,
    }
    existing: MessageMetadata = dict(msg.metadata) if msg.metadata else {}  # type: ignore[assignment]
    existing["artifacts"] = [*existing.get("artifacts", []), descriptor]
    return dataclasses.replace(msg, metadata=existing)


def _compute_change_ratio(path1: Path, path2: Path) -> float:
    """Return fraction of pixels that differ between two screenshots (0.0–1.0).

    Uses Pillow's pixel-level comparison after converting to a consistent mode.
    Returns 0.0 if images can't be compared (mismatched sizes, load errors).
    """
    try:
        from PIL import Image, ImageChops

        img1 = Image.open(path1).convert("RGB")
        img2 = Image.open(path2).convert("RGB")
        if img1.size != img2.size:
            return 0.0
        diff = ImageChops.difference(img1, img2)
        total_pixels = img1.width * img1.height
        raw = diff.tobytes()  # 3 bytes per pixel for RGB
        nonzero = sum(
            1 for i in range(0, len(raw), 3) if raw[i] or raw[i + 1] or raw[i + 2]
        )
        return nonzero / total_pixels
    except Exception:
        return 0.0


# Constants from Anthropic's implementation
TYPING_DELAY_MS = 12
TYPING_GROUP_SIZE = 50

Action = Literal[
    "key",
    "type",
    "mouse_move",
    "left_click",
    "left_click_drag",
    "right_click",
    "middle_click",
    "double_click",
    "scroll",
    "screenshot",
    "cursor_position",
    "wait_for_change",
    "window_focus",
    "accessibility_tree",
    "click_accessible_element",
]

ScrollDirection = Literal["up", "down", "left", "right"]


class _Resolution(TypedDict):
    width: int
    height: int


# Recommended maximum resolutions for LLM vision
MAX_SCALING_TARGETS: dict[str, _Resolution] = {
    "XGA": _Resolution(width=1024, height=768),  # 4:3
    "WXGA": _Resolution(width=1280, height=800),  # 16:10
    "FWXGA": _Resolution(width=1366, height=768),  # ~16:9
}


class _ScalingSource(Enum):
    COMPUTER = "computer"
    API = "api"


def _get_api_resolution() -> tuple[int, int]:
    """Return the configured API-space resolution (WIDTH/HEIGHT env or display-ratio defaults)."""
    display_width, display_height = _get_display_resolution()
    display_ratio = display_width / display_height
    default_resolution: _Resolution | None = None
    closest_ratio_diff = float("inf")
    for res in MAX_SCALING_TARGETS.values():
        ratio = res["width"] / res["height"]
        ratio_diff = abs(ratio - display_ratio)
        if ratio_diff < closest_ratio_diff:
            closest_ratio_diff = ratio_diff
            default_resolution = res
    if default_resolution is None:
        default_resolution = MAX_SCALING_TARGETS["XGA"]
    width = int(os.getenv("WIDTH", str(default_resolution["width"])))
    height = int(os.getenv("HEIGHT", str(default_resolution["height"])))
    return width, height


def _chunks(s: str, chunk_size: int) -> list[str]:
    """Split string into chunks for typing simulation."""
    return [s[i : i + chunk_size] for i in range(0, len(s), chunk_size)]


def _get_display_resolution() -> tuple[int, int]:
    """Get the physical display resolution."""
    try:
        if IS_MACOS:
            output = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"], text=True, timeout=10
            )
            for line in output.splitlines():
                if "Resolution" in line:
                    # Parse "Resolution: 2560 x 1664 Retina"
                    parts = line.split(":")[-1].split("x")
                    width = int(parts[0].strip())
                    height = int(parts[1].split()[0].strip())
                    return width, height
        else:
            output = subprocess.check_output(["xrandr"], text=True, timeout=10)
            for line in output.splitlines():
                if "*" in line:  # Current resolution has an asterisk
                    # Parse "2560x1440" from the line
                    resolution = line.split()[0]
                    width, height = map(int, resolution.split("x"))
                    return width, height
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
        IndexError,
    ) as e:
        raise RuntimeError(f"Failed to get display resolution: {e}") from e
    raise RuntimeError("Failed to get display resolution")


def _scale_coordinates(
    source: _ScalingSource, x: int, y: int, api_width: int, api_height: int
) -> tuple[int, int]:
    """Scale coordinates between API space and actual screen resolution."""
    # Get the actual physical resolution
    physical_width, physical_height = _get_display_resolution()

    # Account for macOS display scaling factor
    if IS_MACOS:
        # macOS display scaling factor
        # TODO: retrieve somehow? we could move mouse to the bottom right and then get the position
        # (but it's hacky and confusing to users)
        display_scale = 2560 / 1709

        physical_width = int(physical_width / display_scale)
        physical_height = int(physical_height / display_scale)
        logger.info(
            f"Adjusted physical resolution: {physical_width}x{physical_height} (scale: {display_scale})"
        )

    if source == _ScalingSource.API:
        if x > api_width or y > api_height:
            raise ValueError(f"Coordinates {x}, {y} are out of bounds")

        # Scale up from API coordinates to physical screen coordinates
        x_scale = physical_width / api_width
        y_scale = physical_height / api_height
        scaled_x = round(x * x_scale)
        scaled_y = round(y * y_scale)
        logger.info(f"Scaling from API ({x},{y}) to physical ({scaled_x},{scaled_y})")
        logger.info(f"Scale factors: x={x_scale:.3f}, y={y_scale:.3f}")
        return scaled_x, scaled_y
    # _ScalingSource.COMPUTER
    # Scale down from physical screen coordinates to API coordinates
    x_scale = api_width / physical_width
    y_scale = api_height / physical_height
    return round(x * x_scale), round(y * y_scale)


def _run_xdotool(cmd: str, display: str | None = None) -> str:
    """Run an xdotool command with optional display setting and wait for completion."""
    if IS_MACOS:
        raise RuntimeError("xdotool is not supported on macOS")

    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display
    try:
        # Parse cmd into arguments to avoid shell injection
        cmd_args = shlex.split(cmd)
        result = subprocess.run(
            ["xdotool", *cmd_args],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"xdotool command timed out: {cmd}") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"xdotool command failed: {e.stderr}") from e


def _macos_type(text: str) -> None:
    """
    Type text using cliclick on macOS.

    Security:
        - Uses cliclick for reliable input
        - Text is properly escaped
    """
    safe_text = shlex.quote(text)
    try:
        subprocess.run(
            ["cliclick", "t:" + safe_text],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick type command timed out") from e
    except FileNotFoundError:
        raise RuntimeError(
            "cliclick not found. Install with: brew install cliclick"
        ) from None


def _ensure_cliclick() -> None:
    """Ensure cliclick is installed, raise helpful error if not."""
    if not shutil.which("cliclick"):
        raise RuntimeError("cliclick not found. Install with: brew install cliclick")


def _macos_key(key_sequence: str) -> None:
    """
    Send key sequence using cliclick on macOS.

    Uses unified key sequence parser to handle:
    - t:text - Type text
    - modifier+key - Press key with modifiers
    - key - Press single key

    Multiple operations can be chained with semicolons.

    Examples:
    - "cmd+space;t:firefox;return"
    - "t:Hello, world!;tab;t:More text"

    Security:
        - Input is properly escaped
        - Uses cliclick's built-in key system
    """
    _ensure_cliclick()

    operations = _parse_key_sequence(key_sequence)
    commands = []

    for op in operations:
        if op["type"] == "text":
            commands.append(f"t:{op['text']}")

        elif op["type"] == "key":
            key = COMMON_KEY_MAP.get(op["key"].lower(), op["key"]).lower()
            if len(key) == 1:
                # For single characters, use type
                commands.append(f"t:{key}")
            else:
                # For special keys, use key press
                commands.append(f"kp:{key}")

        elif op["type"] == "combo":
            modifiers = op["modifiers"]
            key = op["key"]

            if modifiers:
                # Press modifiers
                commands.append(f"kd:{','.join(modifiers)}")

            # Press the main key
            key = COMMON_KEY_MAP.get(key.lower(), key).lower()
            if len(key) == 1:
                commands.append(f"t:{key}")
            else:
                commands.append(f"kp:{key}")

            if modifiers:
                # Release modifiers
                commands.append(f"ku:{','.join(modifiers)}")

    try:
        # Use list form to avoid shell injection - cliclick accepts commands as args
        cmd_list = ["cliclick", *commands]
        logger.info(f"Running: {' '.join(cmd_list)}")
        subprocess.run(cmd_list, check=True, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick key sequence timed out") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to send key sequence: {e.stderr}") from e


def _macos_mouse_move(x: int, y: int) -> None:
    """
    Move mouse using cliclick on macOS.

    Security:
        - Coordinates are validated as integers
        - Uses cliclick for reliable input
    """
    try:
        logger.info(f"Moving mouse to {x},{y}")
        subprocess.run(
            ["cliclick", f"m:{x},{y}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick mouse move timed out") from e
    except FileNotFoundError:
        raise RuntimeError(
            "cliclick not found. Install with: brew install cliclick"
        ) from None


def _linux_handle_key_sequence(key_sequence: str, display: str) -> None:
    """
    Handle complex key sequences for Linux using xdotool.

    Uses unified key sequence parser to handle:
    - t:text - Type text
    - modifier+key - Press key with modifiers
    - key - Press single key

    Multiple operations can be chained with semicolons.

    Examples:
    - "ctrl+l;t:firefox;Return"
    - "alt+Tab;alt+Tab"

    Args:
        key_sequence: The key sequence to send
        display: The X11 display to use
    """
    # Map common keys to xdotool-specific keys
    xdotool_key_map = {
        "return": "Return",
        "ctrl": "ctrl",
        "alt": "alt",
        "cmd": "super",
        "shift": "shift",
        "esc": "Escape",
        "space": "space",
        "tab": "Tab",
    }

    operations = _parse_key_sequence(key_sequence)

    for op in operations:
        if op["type"] == "text":
            _linux_type(op["text"], display)

        elif op["type"] == "key":
            key = xdotool_key_map.get(op["key"].lower(), op["key"])
            _run_xdotool(f"key {shlex.quote(key)}", display)

        elif op["type"] == "combo":
            xdotool_keys = []

            # Add modifiers
            for mod in op["modifiers"]:
                mapped_mod = xdotool_key_map.get(mod.lower(), mod)
                xdotool_keys.append(shlex.quote(mapped_mod))

            # Add main key
            if op["key"]:
                mapped_key = xdotool_key_map.get(op["key"].lower(), op["key"])
                xdotool_keys.append(shlex.quote(mapped_key))

            # Execute as a key sequence
            xdotool_key_seq = " ".join(xdotool_keys)
            _run_xdotool(f"key {xdotool_key_seq}", display)


def _linux_type(text: str, display: str) -> None:
    for chunk in _chunks(text, TYPING_GROUP_SIZE):
        _run_xdotool(
            f"type --delay {TYPING_DELAY_MS} -- {shlex.quote(chunk)}",
            display,
        )


def _linux_scroll(
    x: int, y: int, direction: str, display: str, amount: int = 3
) -> None:
    """Scroll in a direction at (x, y) using xdotool on Linux/X11.

    Button mapping: 4=up, 5=down, 6=left, 7=right.
    """
    button_map = {"up": "4", "down": "5", "left": "6", "right": "7"}
    button = button_map.get(direction)
    if button is None:
        raise ValueError(f"Invalid scroll direction: {direction!r}")
    _run_xdotool(f"mousemove --sync {x} {y}", display)
    _run_xdotool(f"click --repeat {amount} {button}", display)


def _macos_scroll(x: int, y: int, direction: str, amount: int = 3) -> None:
    """Scroll in a direction at (x, y) on macOS using Quartz scroll wheel events."""
    try:
        from Quartz import (  # type: ignore[import-not-found]
            CGEventCreateScrollWheelEvent,
            CGEventPost,
            CGEventSetLocation,
            kCGHIDEventTap,
            kCGScrollEventUnitLine,
        )
        from Quartz.CoreGraphics import CGPoint  # type: ignore[import-not-found]
    except ImportError:
        raise RuntimeError(
            "pyobjc-framework-Quartz is required for scroll on macOS. "
            "Install with: pip install pyobjc-framework-Quartz"
        ) from None

    _macos_mouse_move(x, y)

    delta_y = 0
    delta_x = 0
    if direction == "up":
        delta_y = amount
    elif direction == "down":
        delta_y = -amount
    elif direction == "left":
        delta_x = amount
    elif direction == "right":
        delta_x = -amount
    else:
        raise ValueError(f"Invalid scroll direction: {direction!r}")

    event = CGEventCreateScrollWheelEvent(
        None, kCGScrollEventUnitLine, 2, delta_y, delta_x
    )
    CGEventSetLocation(event, CGPoint(x, y))
    CGEventPost(kCGHIDEventTap, event)


def _linux_window_focus(pattern: str, display: str, timeout: float = 10.0) -> None:
    """Wait for a window matching the name pattern to appear and focus it.

    Uses xdotool's ``--sync`` flag so the call blocks until the window exists,
    then focuses it.  This avoids the screenshot-polling workaround previously
    needed when opening new terminal windows in X11 environments.

    Args:
        pattern: Substring matched against WM_NAME (window title).
        display: X11 display string (e.g. ":1").
        timeout: Seconds to wait for the window to appear (default 10).
    """
    env = os.environ.copy()
    env["DISPLAY"] = display
    try:
        subprocess.run(
            [
                "xdotool",
                "search",
                "--sync",
                "--limit",
                "1",
                "--name",
                pattern,
                "windowfocus",
                "--sync",
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout + 2,  # extra headroom beyond the xdotool sync wait
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"No window matching {pattern!r} appeared within {timeout:.0f}s"
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"xdotool search/focus failed for pattern {pattern!r}: {e.stderr}"
        ) from e


def _linux_accessibility_tree(display: str, max_depth: int = 8) -> str:
    """Return a text dump of the AT-SPI2 accessibility tree for all desktop apps.

    Requires the optional ``pyatspi`` package (``pip install pyatspi``).
    The tree lists every accessible object with its role, name, and state,
    indented by depth.  Use the output to identify elements for
    ``click_accessible_element`` without needing pixel coordinates.

    Args:
        display: X11 display string (e.g. ":1").
        max_depth: Maximum recursion depth (default 8).

    Returns:
        Multi-line text representation of the accessibility tree.

    Raises:
        RuntimeError: If pyatspi is not installed or the desktop is not accessible.
    """
    try:
        import pyatspi  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        raise RuntimeError(
            "pyatspi not installed. Install with: pip install pyatspi\n"
            "(requires AT-SPI2 accessibility stack: apt install python3-pyatspi)"
        ) from None

    lines: list[str] = []

    def _walk(obj: object, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            role = obj.getRoleName()  # type: ignore[attr-defined]
            name = obj.name or ""  # type: ignore[attr-defined]
        except Exception:
            role, name = "unknown", ""

        indent = "  " * depth
        label = role if not name else f"{role}: {name}"
        lines.append(f"{indent}{label}")

        try:
            child_count = obj.childCount  # type: ignore[attr-defined]
        except Exception:
            child_count = 0
        for i in range(child_count):
            try:
                child = obj[i]  # type: ignore[index]
                _walk(child, depth + 1)
            except Exception:
                pass

    _old_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    try:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception as e:
            raise RuntimeError(f"Could not connect to AT-SPI desktop: {e}") from e

        lines.append(f"Desktop ({desktop.childCount} apps)")
        for i in range(desktop.childCount):
            try:
                app = desktop[i]
                _walk(app, 1)
            except Exception:
                pass
    finally:
        if _old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = _old_display

    return "\n".join(lines) if lines else "(empty accessibility tree)"


def _linux_click_accessible_element(
    role_name: str, element_name: str, display: str
) -> tuple[int, int]:
    """Find an accessible element by role and name and return its center coordinates.

    Looks up the element via AT-SPI2, computes its bounding box, and returns the
    center (x, y) in screen coordinates.  The caller should then use
    ``xdotool mousemove --sync x y click 1`` or a transport-layer click to
    actually interact with it.

    Args:
        role_name: AT-SPI role name, e.g. "push button", "entry", "check box".
        element_name: Accessible name of the element (case-insensitive substring match).
        display: X11 display string.

    Returns:
        (x, y) center of the first matching element in screen coordinates.

    Raises:
        RuntimeError: If pyatspi is not installed, the element is not found, or has
            no geometry.
    """
    try:
        import pyatspi  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        raise RuntimeError(
            "pyatspi not installed. Install with: pip install pyatspi\n"
            "(requires AT-SPI2 accessibility stack: apt install python3-pyatspi)"
        ) from None

    name_lower = element_name.lower()

    def _find(obj: object, depth: int) -> object | None:
        if depth > 20:
            return None
        try:
            role = obj.getRoleName()  # type: ignore[attr-defined]
            name = (obj.name or "").lower()  # type: ignore[attr-defined]
            if role == role_name and name_lower in name:
                return obj
        except Exception:
            pass
        try:
            child_count = obj.childCount  # type: ignore[attr-defined]
        except Exception:
            return None
        for i in range(child_count):
            try:
                result = _find(obj[i], depth + 1)  # type: ignore[index]
                if result is not None:
                    return result
            except Exception:
                pass
        return None

    _old_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    try:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception as e:
            raise RuntimeError(f"Could not connect to AT-SPI desktop: {e}") from e

        found = None
        for i in range(desktop.childCount):
            try:
                found = _find(desktop[i], 0)
                if found is not None:
                    break
            except Exception:
                pass

        if found is None:
            raise RuntimeError(
                f"No accessible element with role={role_name!r} and name containing "
                f"{element_name!r} found in the accessibility tree. "
                "Run computer('accessibility_tree') to see available elements."
            )

        try:
            component = found.queryComponent()  # type: ignore[attr-defined]
            bbox = component.getExtents(pyatspi.DESKTOP_COORDS)
            x = bbox.x + bbox.width // 2
            y = bbox.y + bbox.height // 2
        except Exception as e:
            raise RuntimeError(
                f"Found element {role_name!r}: {element_name!r} but could not get its "
                f"screen position: {e}"
            ) from e
    finally:
        if _old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = _old_display

    return x, y


def _macos_window_focus(pattern: str, timeout: float = 10.0) -> None:
    """Focus the frontmost application whose name contains pattern on macOS.

    Uses AppleScript via ``osascript`` with a Python-level retry loop so the
    call blocks until a matching window appears or the timeout expires — matching
    the blocking semantics of the Linux xdotool path.

    Args:
        pattern: Substring matched against application/process name.
        timeout: Seconds to wait for the window to appear (default 10).
    """
    script = (
        "on run argv\n"
        "  set needle to item 1 of argv\n"
        '  tell application "System Events"\n'
        "    set found to false\n"
        "    repeat with p in (every process whose background only is false)\n"
        "      if name of p contains needle then\n"
        "        set frontmost of p to true\n"
        "        set found to true\n"
        "        exit repeat\n"
        "      end if\n"
        "    end repeat\n"
        "    if found then\n"
        '      return "found"\n'
        "    else\n"
        '      return "not_found"\n'
        "    end if\n"
        "  end tell\n"
        "end run"
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = subprocess.run(
                ["osascript", "-e", script, pattern],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"window_focus timed out for pattern {pattern!r}") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to focus window matching {pattern!r}: {e.stderr}"
            ) from e

        if result.stdout.strip() == "found":
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"No window matching {pattern!r} appeared within {timeout:.0f}s"
            )
        time.sleep(0.5)


def _macos_click(button: int) -> None:
    """
    Click mouse button using cliclick on macOS.

    Security:
        - Button number is validated as integer
        - Only allows valid button numbers
        - Uses cliclick for reliable input
    """
    _ensure_cliclick()

    if button not in (1, 2, 3):
        raise ValueError("Invalid button number")

    # Get current position
    try:
        result = subprocess.run(
            ["cliclick", "p"], check=True, capture_output=True, text=True, timeout=10
        )
        pos = result.stdout.strip()
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick cursor position query timed out") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get cursor position: {e.stderr}") from e

    # Map buttons to cliclick commands
    button_map = {1: "c", 2: "m", 3: "rc"}
    cmd = f"{button_map[button]}:{pos}"

    try:
        result = subprocess.run(
            ["cliclick", cmd], check=True, capture_output=True, text=True, timeout=10
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick click command timed out") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to click: {e.stderr}") from e


def _macos_drag(x: int, y: int) -> None:
    """Drag from current mouse position to (x, y) using cliclick on macOS."""
    _ensure_cliclick()

    # Get current position as drag start
    try:
        result = subprocess.run(
            ["cliclick", "p"], check=True, capture_output=True, text=True, timeout=10
        )
        start_pos = result.stdout.strip()
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick cursor position query timed out") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get cursor position: {e.stderr}") from e

    # mousedown at start, mouseup at destination
    try:
        subprocess.run(
            ["cliclick", f"dd:{start_pos}", f"du:{x},{y}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("cliclick drag command timed out") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to drag: {e.stderr}") from e


def _dispatch_transport(
    transport: ComputerTransport,
    action: Action,
    text: str | None = None,
    coordinate: tuple[int, int] | None = None,
) -> Message | None:
    """Route a computer action through the transport layer."""
    if action == "key":
        if not text:
            raise ValueError("text is required for key")
        transport.key(text)
        print(f"Sent key sequence: {text}")
        return None

    if action == "type":
        if not text:
            raise ValueError("text is required for type")
        transport.type_text(text)
        print(f"Typed text: {text}")
        return None

    if action in ("mouse_move", "left_click_drag"):
        if not coordinate:
            raise ValueError(f"coordinate is required for {action}")
        x, y = coordinate
        if action == "mouse_move":
            transport.mouse_move(x, y)
            print(f"Moved mouse to {x},{y}")
        else:
            transport.left_click_drag(x, y)
            print(f"Dragged to {x},{y}")
        return None

    click_actions = {"left_click", "right_click", "middle_click", "double_click"}
    if action in click_actions:
        if coordinate:
            x, y = coordinate
            transport.mouse_move(x, y)
        click_fn = {
            "left_click": transport.left_click,
            "right_click": transport.right_click,
            "middle_click": transport.middle_click,
            "double_click": transport.double_click,
        }[action]
        click_fn()
        print(f"Performed {action}")
        return None

    if action == "scroll":
        if not coordinate:
            raise ValueError("coordinate is required for scroll")
        if not text:
            raise ValueError(
                "text (direction: up/down/left/right) is required for scroll"
            )
        x, y = coordinate
        direction = text.lower()
        if direction not in ("up", "down", "left", "right"):
            raise ValueError(
                f"Invalid scroll direction: {direction!r}. Must be up/down/left/right"
            )
        transport.scroll(x, y, direction)
        print(f"Scrolled {direction} at {x},{y}")
        return None

    if action == "screenshot":
        path = transport.screenshot()
        return _make_screenshot_msg(path)

    if action == "cursor_position":
        x, y = transport.cursor_position()
        print(f"Cursor position: X={x},Y={y}")
        return None

    if action == "wait_for_change":
        timeout = float(text) if text else 10.0
        # Start polling at 50ms, cap at 500ms — catches fast UI updates without
        # burning CPU on long waits.
        poll_interval = 0.05
        max_poll_interval = 0.5
        change_threshold = 0.01
        baseline = transport.screenshot()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            current = transport.screenshot()
            ratio = _compute_change_ratio(baseline, current)
            if ratio >= change_threshold:
                print(f"Screen changed ({ratio:.1%} pixels differ)")
                return _make_screenshot_msg(current)
            # Back off poll interval up to the cap
            poll_interval = min(poll_interval * 2, max_poll_interval)
        print(
            f"No screen change detected after {timeout:.0f}s — returning current screenshot"
        )
        return _make_screenshot_msg(transport.screenshot())

    if action == "window_focus":
        if not text:
            raise ValueError("text (window name pattern) is required for window_focus")
        transport.window_focus(text)
        print(f"Focused window matching: {text!r}")
        return None

    if action == "accessibility_tree":
        display = os.getenv("DISPLAY", ":1")
        tree = _linux_accessibility_tree(display)
        print(tree)
        return None

    if action == "click_accessible_element":
        if not text:
            raise ValueError(
                "text='role_name:element_name' is required for click_accessible_element"
            )
        if ":" not in text:
            raise ValueError(
                "text must be 'role_name:element_name', e.g. 'push button:Submit'"
            )
        role_name, _, element_name = text.partition(":")
        display = os.getenv("DISPLAY", ":1")
        x, y = _linux_click_accessible_element(
            role_name.strip(), element_name.strip(), display
        )
        transport.mouse_move(x, y)
        transport.left_click()
        print(
            f"Clicked accessible element {role_name!r}: {element_name!r} at ({x}, {y})"
        )
        return None

    raise ValueError(f"Invalid action: {action}")


def _computer_impl(
    action: Action, text: str | None = None, coordinate: tuple[int, int] | None = None
) -> Message | None:
    """
    Perform computer interactions in X11 or macOS environments.

    Args:
        action: The type of action to perform
        text: Text to type or key sequence to send
        coordinate: X,Y coordinates for mouse actions
    """
    # Optional transport-layer dispatch (env: GPTME_COMPUTER_TRANSPORT)
    transport = get_transport()
    if transport:
        return _dispatch_transport(transport, action, text, coordinate)

    display = os.getenv("DISPLAY", ":1")
    # Default API space resolution
    # Get actual display resolution and calculate aspect ratio
    display_width, display_height = _get_display_resolution()
    display_ratio = display_width / display_height
    logger.info(
        f"Physical display resolution: {display_width}x{display_height} (ratio: {display_ratio:.3f})"
    )

    # Choose default resolution based on display ratio
    default_resolution = None
    closest_ratio_diff = float("inf")
    for name, res in MAX_SCALING_TARGETS.items():
        ratio = res["width"] / res["height"]
        ratio_diff = abs(ratio - display_ratio)
        if ratio_diff < closest_ratio_diff:
            closest_ratio_diff = ratio_diff
            default_resolution = res
            logger.info(
                f"Selected {name} as closest match: {res['width']}x{res['height']} (ratio diff: {ratio_diff:.3f})"
            )

    # Use environment variables if set, otherwise use chosen defaults
    # Fallback to XGA (4:3) if no resolution matched (shouldn't happen)
    if default_resolution is None:
        default_resolution = MAX_SCALING_TARGETS["XGA"]
        logger.info("Fallback to XGA resolution")

    _width_str = os.getenv("WIDTH", str(default_resolution["width"]))
    _height_str = os.getenv("HEIGHT", str(default_resolution["height"]))
    try:
        width = int(_width_str)
    except ValueError as e:
        raise ValueError(
            f"Invalid WIDTH env var: must be an integer, got {_width_str!r}"
        ) from e
    try:
        height = int(_height_str)
    except ValueError as e:
        raise ValueError(
            f"Invalid HEIGHT env var: must be an integer, got {_height_str!r}"
        ) from e
    logger.info(f"Using API space resolution: {width}x{height}")

    if action in ("mouse_move", "left_click_drag"):
        if not coordinate:
            raise ValueError(f"coordinate is required for {action}")
        x, y = _scale_coordinates(
            _ScalingSource.API, coordinate[0], coordinate[1], width, height
        )

        if IS_MACOS:
            if action == "mouse_move":
                _macos_mouse_move(x, y)
            else:  # left_click_drag
                _macos_drag(x, y)
        else:
            if action == "mouse_move":
                _run_xdotool(f"mousemove --sync {x} {y}", display)
            else:  # left_click_drag
                _run_xdotool(f"mousedown 1 mousemove --sync {x} {y} mouseup 1", display)

        # Show the API space coordinates in the output, not the physical ones
        print(f"Moved mouse to {coordinate[0]},{coordinate[1]}")
        return None
    if action in ("key", "type"):
        if not text:
            raise ValueError(f"text is required for {action}")

        if IS_MACOS:
            if action == "key":
                _macos_key(text)
                print(f"Sent key sequence: {text}")
            else:  # type
                for chunk in _chunks(text, TYPING_GROUP_SIZE):
                    _macos_type(chunk)
                print(f"Typed text: {text}")
        else:
            if action == "key":
                _linux_handle_key_sequence(text, display)
                print(f"Sent key sequence: {text}")
            else:  # type
                _linux_type(text, display)
                print(f"Typed text: {text}")
        return None
    if action == "double_click":
        if IS_MACOS:
            # Get current position and double-click using cliclick's dc command
            try:
                result = subprocess.run(
                    ["cliclick", "p"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                pos = result.stdout.strip()
                subprocess.run(
                    ["cliclick", f"dc:{pos}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError("cliclick double-click timed out") from e
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to double-click: {e.stderr}") from e
        else:
            _run_xdotool("click --repeat 2 --delay 100 1", display)
        print("Performed double_click")
        return None
    if action in ("left_click", "right_click", "middle_click"):
        click_map = {
            "left_click": 1,
            "right_click": 3,
            "middle_click": 2,
        }

        if IS_MACOS:
            button = click_map[action]
            _macos_click(button)
        else:
            click_arg = {
                "left_click": "1",
                "right_click": "3",
                "middle_click": "2",
                "double_click": "--repeat 2 --delay 500 1",
            }[action]
            _run_xdotool(f"click {click_arg}", display)

        print(f"Performed {action}")
        return None
    if action == "scroll":
        if not coordinate:
            raise ValueError("coordinate is required for scroll")
        if not text:
            raise ValueError(
                "text (direction: up/down/left/right) is required for scroll"
            )
        direction = text.lower()
        if direction not in ("up", "down", "left", "right"):
            raise ValueError(
                f"Invalid scroll direction: {direction!r}. Must be up/down/left/right"
            )
        sx, sy = _scale_coordinates(
            _ScalingSource.API, coordinate[0], coordinate[1], width, height
        )
        if IS_MACOS:
            _macos_scroll(sx, sy, direction)
        else:
            _linux_scroll(sx, sy, direction, display)
        print(f"Scrolled {direction} at {coordinate[0]},{coordinate[1]}")
        return None
    if action == "screenshot":
        path = screenshot()  # Use existing screenshot function

        # Resize screenshot from physical resolution to API dimensions
        if path.exists():
            try:
                subprocess.run(
                    ["convert", str(path), "-resize", f"{width}x{height}!", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError("Image resize timed out") from e
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Image resize failed: {e.stderr}") from e
        return _make_screenshot_msg(path)
    if action == "cursor_position":
        if IS_MACOS:
            try:
                output = subprocess.run(
                    ["cliclick", "p"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.strip()
                # cliclick outputs format: "x,y"
                x, y = map(int, output.split(","))
            except subprocess.TimeoutExpired as e:
                raise RuntimeError("cliclick cursor position query timed out") from e
            except FileNotFoundError:
                raise RuntimeError(
                    "cliclick not found. Install with: brew install cliclick"
                ) from None
            except (subprocess.CalledProcessError, ValueError) as e:
                raise RuntimeError(f"Failed to get cursor position: {e}") from e
        else:
            output = _run_xdotool("getmouselocation --shell", display)
            if "X=" not in output or "Y=" not in output:
                raise RuntimeError(f"Unexpected xdotool output format: {output}")
            x = int(output.split("X=")[1].split("\n")[0])
            y = int(output.split("Y=")[1].split("\n")[0])

        x, y = _scale_coordinates(_ScalingSource.COMPUTER, x, y, width, height)
        print(f"Cursor position: X={x},Y={y}")
        return None
    if action == "wait_for_change":
        # text holds the optional timeout (seconds) as a string; default 10s
        timeout = float(text) if text else 10.0
        # Start polling at 50ms, cap at 500ms — catches fast UI updates without
        # burning CPU on long waits.
        poll_interval = 0.05
        max_poll_interval = 0.5
        change_threshold = 0.01  # 1% of pixels must differ
        baseline = screenshot()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            current = screenshot()
            ratio = _compute_change_ratio(baseline, current)
            if ratio >= change_threshold:
                print(f"Screen changed ({ratio:.1%} pixels differ)")
                path = current
                if path.exists():
                    try:
                        subprocess.run(
                            [
                                "convert",
                                str(path),
                                "-resize",
                                f"{width}x{height}!",
                                str(path),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                    except (
                        subprocess.CalledProcessError,
                        subprocess.TimeoutExpired,
                        FileNotFoundError,
                    ):
                        pass
                return _make_screenshot_msg(path)
            # Back off poll interval up to the cap
            poll_interval = min(poll_interval * 2, max_poll_interval)
        print(
            f"No screen change detected after {timeout:.0f}s — returning current screenshot"
        )
        path = screenshot()
        if path.exists():
            try:
                subprocess.run(
                    ["convert", str(path), "-resize", f"{width}x{height}!", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
            ):
                pass
        return _make_screenshot_msg(path)
    if action == "window_focus":
        if not text:
            raise ValueError("text (window name pattern) is required for window_focus")
        if IS_MACOS:
            _macos_window_focus(text)
        else:
            _linux_window_focus(text, display)
        print(f"Focused window matching: {text!r}")
        return None

    if action == "accessibility_tree":
        if IS_MACOS:
            raise RuntimeError(
                "accessibility_tree is only supported on Linux (AT-SPI2). "
                "On macOS, use the browser tool's snapshot_url() for web content."
            )
        tree = _linux_accessibility_tree(display)
        print(tree)
        return None

    if action == "click_accessible_element":
        if IS_MACOS:
            raise RuntimeError(
                "click_accessible_element is only supported on Linux (AT-SPI2). "
                "On macOS, use the browser tool's click_element() for web content."
            )
        if not text:
            raise ValueError(
                "text='role_name:element_name' is required for click_accessible_element"
            )
        if ":" not in text:
            raise ValueError(
                "text must be 'role_name:element_name', e.g. 'push button:Submit'"
            )
        role_name, _, element_name = text.partition(":")
        x, y = _linux_click_accessible_element(
            role_name.strip(), element_name.strip(), display
        )
        # AT-SPI2 DESKTOP_COORDS are already physical screen pixels — no scaling needed.
        _run_xdotool(f"mousemove --sync {x} {y} click 1", display)
        print(
            f"Clicked accessible element {role_name!r}: {element_name!r} at ({x}, {y})"
        )
        return None

    raise ValueError(f"Invalid action: {action}")


def computer(
    action: Action, text: str | None = None, coordinate: tuple[int, int] | None = None
) -> Message | None:
    # perf_counter, not monotonic — several tests feed `time.monotonic` a
    # finite side_effect sequence timed to `_computer_impl`'s internal polling
    # loop, and an extra consumer here would desync those mocks.
    start = time.perf_counter()
    error: str | None = None
    try:
        return _computer_impl(action, text, coordinate)
    except Exception as e:
        error = str(e)
        raise
    finally:
        _audit_log(action, text, coordinate, time.perf_counter() - start, error)


computer.__doc__ = _computer_impl.__doc__


# Common key mappings for both platforms
# Output is directly compatible with cliclick
COMMON_KEY_MAP = {
    "return": "return",
    "enter": "return",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "cmd": "cmd",
    "command": "cmd",
    "super": "cmd",
    "shift": "shift",
    "esc": "esc",
    "escape": "esc",
    "space": "space",
    "tab": "tab",
    # Add more mappings as needed
}

# List of recognized modifier keys
MODIFIER_KEYS = ["ctrl", "alt", "cmd", "shift"]


class TextOperation(TypedDict):
    type: Literal["text"]
    text: str


class KeyOperation(TypedDict):
    type: Literal["key"]
    key: str


class ComboOperation(TypedDict):
    type: Literal["combo"]
    modifiers: list[str]
    key: str


KeySequenceOperation = (
    TextOperation | KeyOperation | ComboOperation
)  # Using | syntax instead of Union


def _parse_key_sequence(key_sequence: str) -> list[KeySequenceOperation]:
    """
    Parse a key sequence into a list of operations.

    Supports:
    - "t:text" for typing text
    - "kp:key" for key press (for backwards compatibility)
    - "modifier+key" for key combinations
    - "key" for single key presses

    Returns a list of operations, each a dict with 'type' and relevant data.
    """
    operations: list[KeySequenceOperation] = []

    # Split by semicolons for sequences of operations
    if ";" in key_sequence:
        steps = key_sequence.split(";")
    else:
        steps = [key_sequence]

    for step in steps:
        step = step.strip()

        # Handle text input: t:text
        if step.startswith("t:"):
            text_op: KeySequenceOperation = {"type": "text", "text": step[2:]}
            operations.append(text_op)

        # Handle explicit key press: kp:key (for backwards compatibility)
        elif step.startswith("kp:"):
            key = step[3:]
            mapped_key = COMMON_KEY_MAP.get(key.lower(), key)
            key_op: KeySequenceOperation = {"type": "key", "key": mapped_key}
            operations.append(key_op)

        # Handle modifier+key combinations: mod+key
        elif "+" in step:
            parts = step.split("+")
            modifiers: list[str] = []
            main_key: str = ""  # Empty string instead of None for type safety

            for part in parts:
                mapped = COMMON_KEY_MAP.get(part.lower(), part)
                if mapped.lower() in MODIFIER_KEYS:
                    modifiers.append(mapped.lower())
                else:
                    main_key = mapped

            combo_op: KeySequenceOperation = {
                "type": "combo",
                "modifiers": modifiers,
                "key": main_key or "",  # Ensure it's not None
            }
            operations.append(combo_op)

        # Handle single key press
        else:
            mapped_key = COMMON_KEY_MAP.get(step.lower(), step)
            single_key_op: KeySequenceOperation = {"type": "key", "key": mapped_key}
            operations.append(single_key_op)

    return operations


instructions = """
You can interact with the computer through the `computer` Python function.
Works on both Linux (X11) and macOS.

### When to use the computer tool

Use computer for GUI interactions that cannot be done through the shell: clicking
elements in running applications, typing into GUI windows, taking screenshots to
verify visual state, and keyboard shortcuts in desktop apps. Prefer the shell or
tmux over computer for anything that has a CLI equivalent. Use computer when the
task requires direct screen interaction — for example, operating a browser UI,
a desktop app, or an interactive installer that has no headless mode.

The key input syntax works consistently across platforms with:

Available actions:
- key: Send key sequence using a unified syntax:
  - Type text: "t:Hello World"
  - Press key: "return", "esc", "tab"
  - Key combination: "ctrl+c", "cmd+space"
  - Chain commands: "cmd+space;t:firefox;return"
- type: Type text with realistic delays (legacy method)
- mouse_move: Move mouse to coordinates
- left_click, right_click, middle_click, double_click: Mouse clicks
- left_click_drag: Click and drag to coordinates
- scroll: Scroll the mouse wheel at coordinates (text="up"/"down"/"left"/"right")
- screenshot: Take and view a screenshot
- cursor_position: Get current mouse position
- wait_for_change: Wait until the screen changes, then return a single screenshot.
  Loops internally until ≥1% of pixels differ from the initial capture, or the
  timeout (text="<seconds>", default 10) elapses. Returns one screenshot regardless
  of how many internal polls were needed — avoids stacking redundant screenshots in
  the conversation context. Use after triggering an action that produces a visual
  response (page load, dialog open, animation finish).
- window_focus: Wait for a window whose title contains text=<pattern> to appear,
  then focus it. On Linux/X11 this uses xdotool --sync so no screenshot polling
  is needed. Use after opening a new application to avoid guessing where to click.
- accessibility_tree: (Linux/AT-SPI2 only) Dump the accessibility tree for all
  open applications as structured text. Each node shows role, name, and state.
  Use this to discover element names and roles before using click_accessible_element.
  Requires: pip install pyatspi (and AT-SPI2 accessibility stack).
- click_accessible_element: (Linux/AT-SPI2 only) Find and click an element by
  role and name without needing screen coordinates. Use text='role:name' where
  role is the AT-SPI role name (e.g. 'push button', 'entry', 'check box') and
  name is a substring of the element's accessible name. Example:
  computer('click_accessible_element', text='push button:Submit')

### Accessibility-first for native apps (Linux)

Prefer click_accessible_element over coordinate-based clicks for native Linux apps:

  computer("accessibility_tree")                               # inspect available elements
  computer("click_accessible_element", text="entry:Username")  # fill username field
  computer("type", text="user@example.com")
  computer("click_accessible_element", text="push button:Log In")

This is more robust than coordinate guessing: element names don't shift when
window size or position changes. Use coordinate-based clicks only when the app
lacks accessibility support (e.g. electron apps, games, canvas-based UIs).

### Efficient action-verify loops

Prefer wait_for_change over immediate screenshot after triggering UI changes:

  computer("left_click", coordinate=(760, 540))  # trigger action
  computer("wait_for_change", text="5")           # wait for response, see result once

This prevents the conversation from accumulating multiple nearly-identical
screenshots during transitions. Only call screenshot() directly when you need
the current state without waiting.

### Opening new windows without guessing their position

Prefer window_focus over clicking at a guessed coordinate after launching a window:

  computer("key", text="ctrl+alt+t")         # open terminal
  computer("window_focus", text="Terminal")   # wait for it, then focus it
  computer("type", text="echo hello")         # type into the now-focused window

This avoids the delay/click-at-random pattern that fails when window position
varies across sessions or virtual displays.

Note: Key names are automatically mapped between platforms.
Common modifiers (ctrl, alt, cmd/super, shift) work consistently across platforms.

### Observation helpers (structured-first policy)

Two higher-level helpers are available that implement the structured-first observation policy:

- ``observe_web(url, screenshot_too=False)`` — observe a web page using ARIA snapshots first
  (no vision tokens), with automatic fallback to a browser screenshot, then desktop screenshot.
  Pass ``screenshot_too=True`` to get both an ARIA snapshot AND a screenshot side by side.
- ``observe_desktop()`` — thin wrapper around ``computer('screenshot')`` that signals intent
  clearly for native apps and non-browser surfaces.

These helpers are preferred over calling ``computer("screenshot")`` directly when observing
web pages, because ARIA snapshots avoid costly vision tokens and give a DOM-addressable tree.
"""


def observe_web(url: str, screenshot_too: bool = False) -> list[Message]:
    """Observe a web page: structured ARIA snapshot first, screenshot as fallback.

    Implements the structured-first observation policy: prefer accessibility snapshots
    for web targets — they avoid vision-token cost and give a DOM-addressable tree.
    Use ``screenshot_too=True`` when you need pixel-level visual confirmation alongside
    the structured snapshot (e.g. to verify layout or canvas content).

    Falls back to a browser screenshot, then to a desktop screenshot, if Playwright is
    not available.

    Args:
        url: Page URL to observe.
        screenshot_too: If True, also take a screenshot even when a snapshot succeeded.

    Returns:
        List of :class:`~gptme.message.Message` objects (snapshot and/or screenshots).
        Empty only if all observation paths fail.

    Example (from IPython in a computer-use session)::

        msgs = observe_web("https://news.ycombinator.com")
        # Returns one Message containing the ARIA snapshot text.

        msgs = observe_web("https://example.com", screenshot_too=True)
        # Returns snapshot Message + screenshot Message side-by-side.
    """
    messages: list[Message] = []

    snapshot_text: str | None = None
    try:
        from gptme.tools.browser import has_playwright, snapshot_url

        if has_playwright():
            snapshot_text = snapshot_url(url)
    except Exception:
        pass

    if snapshot_text is not None:
        from gptme.message import Message

        messages.append(Message("system", snapshot_text))
        if screenshot_too:
            # Playwright is available (snapshot succeeded), use browser screenshot.
            # Wrapped in try/except so a page-load failure degrades gracefully
            # instead of discarding the snapshot already in messages.
            try:
                from gptme.tools.browser import screenshot_url

                path = screenshot_url(url)
                msg = _make_screenshot_msg(path, tool="computer")
                if msg is not None:
                    messages.append(msg)
            except Exception:
                pass
    else:
        # Fallback: browser screenshot, then desktop screenshot
        try:
            from gptme.tools.browser import has_playwright, screenshot_url

            if has_playwright():
                path = screenshot_url(url)
                msg = _make_screenshot_msg(path, tool="computer")
                if msg is not None:
                    messages.append(msg)
        except Exception:
            pass

        if not messages:
            msg = computer("screenshot")
            if msg is not None:
                messages.append(msg)

    return messages


def observe_desktop() -> Message | None:
    """Observe the current desktop state via screenshot.

    Thin wrapper around ``computer('screenshot')`` that makes the
    structured-first / screenshot-fallback policy explicit: call this when
    there is no URL to snapshot (native apps, the raw desktop, or any
    non-browser surface).

    Returns:
        Screenshot :class:`~gptme.message.Message`, or ``None`` if capture failed.

    Example (from IPython in a computer-use session)::

        msg = observe_desktop()
        # Equivalent to computer("screenshot"), but signals intent clearly.
    """
    return computer("screenshot")


def examples(tool_format):
    system = platform.system()
    is_macos = system == "Darwin"

    # Common examples for all platforms
    common_examples = f"""
User: Take a screenshot of the desktop
Assistant: I'll capture the screen using the screenshot tool.
{ToolUse("ipython", [], 'computer("screenshot")').to_output(tool_format)}
System: Viewing image...

User: Type "Hello, World!" into the active window
Assistant: I'll type the text with realistic delays.
{ToolUse("ipython", [], 'computer("type", text="Hello, World!")').to_output(tool_format)}
System: Typed text: Hello, World!

User: Move the mouse to coordinates (100, 200) and click
Assistant: I'll move the mouse and perform a left click.
{ToolUse("ipython", [], 'computer("mouse_move", coordinate=(100, 200))').to_output(tool_format)}
System: Moved mouse to 100,200
{ToolUse("ipython", [], 'computer("left_click")').to_output(tool_format)}
System: Performed left_click

User: Get the current mouse position
Assistant: I'll get the cursor position.
{ToolUse("ipython", [], 'computer("cursor_position")').to_output(tool_format)}
System: Cursor position: X=512,Y=384

User: Double-click at current position
Assistant: I'll perform a double-click.
{ToolUse("ipython", [], 'computer("double_click")').to_output(tool_format)}
System: Performed double_click

User: Scroll down in the page at (512, 400)
Assistant: I'll scroll down at those coordinates.
{ToolUse("ipython", [], 'computer("scroll", coordinate=(512, 400), text="down")').to_output(tool_format)}
System: Scrolled down at 512,400

User: Click the Submit button then wait for the result page to load
Assistant: I'll click Submit and wait for the screen to change before returning a screenshot.
{ToolUse("ipython", [], 'computer("left_click", coordinate=(760, 540))').to_output(tool_format)}
System: Performed left_click
{ToolUse("ipython", [], 'computer("wait_for_change", text="10")').to_output(tool_format)}
System: Screen changed (23.4% pixels differ)
Viewing image...

User: Open a terminal and run a command
Assistant: I'll open a terminal with a keyboard shortcut, wait for it to appear and focus it, then type the command.
{ToolUse("ipython", [], 'computer("key", text="ctrl+alt+t")').to_output(tool_format)}
System: Sent key sequence: ctrl+alt+t
{ToolUse("ipython", [], 'computer("window_focus", text="Terminal")').to_output(tool_format)}
System: Focused window matching: 'Terminal'
{ToolUse("ipython", [], 'computer("type", text="ls -la")').to_output(tool_format)}
System: Typed text: ls -la
{ToolUse("ipython", [], 'computer("key", text="return")').to_output(tool_format)}
System: Sent key sequence: return

User: Read the content of https://news.ycombinator.com
Assistant: I'll use observe_web to get a structured ARIA snapshot of the page — faster and cheaper than a screenshot.
{ToolUse("ipython", [], 'observe_web("https://news.ycombinator.com")').to_output(tool_format)}
System: [ARIA snapshot of Hacker News front page...]

User: Check what's on my desktop right now
Assistant: I'll capture a screenshot of the desktop.
{ToolUse("ipython", [], "observe_desktop()").to_output(tool_format)}
System: Viewing image...

User: Navigate to https://example.com and verify both the text content and visual layout
Assistant: I'll use observe_web with screenshot_too=True to get both the ARIA snapshot and a screenshot.
{ToolUse("ipython", [], 'observe_web("https://example.com", screenshot_too=True)').to_output(tool_format)}
System: [ARIA snapshot + screenshot of example.com]
"""

    # Platform-specific keyboard shortcut examples
    if is_macos:
        keyboard_examples = f"""
User: Open Spotlight Search and search for "Terminal"
Assistant: I'll open Spotlight Search and type "Terminal".
{ToolUse("ipython", [], 'computer("key", text="cmd+space;t:Terminal;return")').to_output(tool_format)}
System: Sent key sequence: cmd+space;t:Terminal;return

User: Open a new browser tab
Assistant: I'll open a new browser tab on macOS.
{ToolUse("ipython", [], 'computer("key", text="cmd+t")').to_output(tool_format)}
System: Sent key sequence: cmd+t
"""
    else:
        # Linux or other platforms
        keyboard_examples = f"""
User: Open a new browser tab
Assistant: I'll open a new browser tab.
{ToolUse("ipython", [], 'computer("key", text="ctrl+t")').to_output(tool_format)}
System: Sent key sequence: ctrl+t
"""

    return common_examples + keyboard_examples


tool = ToolSpec(
    name="computer",
    desc="Control the computer through X11 (keyboard, mouse, screen)",
    instructions=instructions,
    examples=examples,
    functions=[
        ToolFunction.from_callable(computer),
        ToolFunction.from_callable(observe_web),
        ToolFunction.from_callable(observe_desktop),
    ],
    disabled_by_default=True,
)

__doc__ = tool.get_doc(__doc__)
