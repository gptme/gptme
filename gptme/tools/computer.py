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
- GPTME_COMPUTER_CONFIRM_SENSITIVE: Pre-execution gate for sensitive actions
  (type, key, left_click_drag, fill_element).  Values:
  - unset / "0": gate disabled (default, back-compatible)
  - "1": gate enabled; interactive sessions prompt the user, non-interactive sessions block
  - "auto-allow": gate enabled but approves silently (useful in automated scripts)

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

Accessibility (cross-platform):
    - accessibility_tree: Dump the native accessibility tree for all visible apps.
      On Linux uses AT-SPI2 (role names like "push button", "entry").
      On macOS uses System Events via AppleScript (role names like "AXButton", "AXTextField").
    - click_accessible_element: Find and click an element by role and name (text='role:name').
      Linux example: text='push button:Submit'
      macOS example: text='AXButton:Submit'

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
import functools
import logging
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path
from typing import IO, TYPE_CHECKING, Literal, TypedDict

from ._computer_gate import action_risk_level, sensitive_action_gate
from .base import ToolFunction, ToolSpec, ToolUse
from .computer_transport import get_transport
from .screenshot import screenshot
from .vision import view_image

if TYPE_CHECKING:
    from ..message import ArtifactDescriptor, Message, MessageMetadata
    from .computer_transport import ComputerTransport

logger = logging.getLogger(__name__)

# Patch these aliases in tests instead of mutating the process-wide time module.
_monotonic = time.monotonic
_sleep = time.sleep


# Platform detection
IS_MACOS = platform.system() == "Darwin"


def _read_ffmpeg_stderr(stderr: IO[bytes] | None) -> str:
    if stderr is None:
        return ""
    try:
        stderr.seek(0)
        text = stderr.read().decode(errors="replace").strip()
    except OSError:
        return ""
    return text[-4000:]


def _stream_action_risk(
    action: str,
    *,
    text: str | None = None,
    coordinate: tuple[int, int] | None = None,
) -> None:
    """Emit a live risk-label record to the parent agent's progress queue.

    Called by ``computer()`` after sensitive-action gating succeeds.  When the
    current execution context is a ``computer_task()`` subagent, the record is
    forwarded to the parent agent via ``notify_progress()`` so the parent can
    track each action's risk level in real-time rather than having to wait for
    the subagent to finish and then call ``gptme-util computer audit-log``.

    ``act_and_observe()`` delegates through ``computer()``, so it emits records
    for the requested action and, on the local fallback path, for its internal
    ``computer("wait_for_change")`` polling call.

    Does nothing when:
    - Not running inside a subagent (``get_current_agent_id()`` returns None)
    - Any import or notification call raises an exception (never breaks the action)

    The record format is a JSON object with keys:
    - ``action``    — action name (e.g. ``"left_click"``)
    - ``risk``      — risk level: ``"read"``, ``"write"``, or ``"sensitive"``
    - ``coord``     — ``[x, y]`` for coordinate-based actions, absent otherwise
    - ``text_len``  — byte-length of text for sensitive actions; absent otherwise
                      (content is *never* emitted — only its length)
    """
    try:
        import json as _json

        from .subagent import get_current_agent_id, notify_progress

        agent_id = get_current_agent_id()
        if agent_id is None:
            return

        risk = action_risk_level(action)
        record: dict = {"action": action, "risk": risk}
        if coordinate is not None:
            record["coord"] = list(coordinate)
        if risk == "sensitive" and text is not None:
            record["text_len"] = len(text.encode())

        notify_progress(
            agent_id, f"action:{_json.dumps(record, separators=(',', ':'))}"
        )
    except Exception:
        pass  # never let streaming failures interrupt the action


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


@functools.lru_cache(maxsize=1)
def _get_macos_display_scale() -> float:
    """Detect the macOS HiDPI/Retina backing scale factor.

    Tries in order:
    1. ``AppKit.NSScreen.mainScreen().backingScaleFactor()`` — accurate for any display config
    2. Ratio of physical "Resolution:" to logical "UI Looks like" in ``system_profiler``
    3. Falls back to ``2.0`` (standard Retina)
    """
    # Method 1: AppKit (preferred — works for all display configs including external monitors)
    try:
        import AppKit  # type: ignore[import-not-found,import-untyped]

        screen = AppKit.NSScreen.mainScreen()
        if screen is not None:
            return float(screen.backingScaleFactor())
    except Exception:
        pass

    # Method 2: Parse system_profiler output for "UI Looks like: NNN x NNN"
    try:
        output = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"], text=True, timeout=10
        )
        current_physical_w: int | None = None
        current_logical_w: int | None = None
        current_is_main = False
        fallback_scale: float | None = None

        def record_candidate() -> float | None:
            nonlocal fallback_scale
            if (
                current_physical_w is None
                or current_logical_w is None
                or current_logical_w <= 0
            ):
                return None

            scale = current_physical_w / current_logical_w
            if current_is_main:
                return scale
            if fallback_scale is None or (fallback_scale == 1.0 and scale != 1.0):
                fallback_scale = scale
            return None

        for line in output.splitlines():
            stripped = line.strip()
            if (
                line.startswith("        ")
                and not line.startswith("          ")
                and stripped.endswith(":")
            ):
                scale = record_candidate()
                if scale is not None:
                    return scale
                current_physical_w = None
                current_logical_w = None
                current_is_main = False
                continue

            if stripped.startswith("Resolution:"):
                parts = stripped.split(":")[-1].split("x")
                current_physical_w = int(parts[0].strip())
            elif stripped.startswith("UI Looks like:"):
                # "UI Looks like: 1280 x 832 @ 60.00Hz"
                parts = stripped.split(":")[-1].split("x")
                current_logical_w = int(parts[0].strip())
            elif stripped == "Main Display: Yes":
                current_is_main = True

        scale = record_candidate()
        if scale is not None:
            return scale
        if fallback_scale is not None:
            return fallback_scale
    except Exception:
        pass

    # Method 3: Assume standard 2× Retina
    return 2.0


def _scale_coordinates(
    source: _ScalingSource, x: int, y: int, api_width: int, api_height: int
) -> tuple[int, int]:
    """Scale coordinates between API space and actual screen resolution."""
    # Get the actual physical resolution
    physical_width, physical_height = _get_display_resolution()

    # Account for macOS display scaling factor
    if IS_MACOS:
        display_scale = _get_macos_display_scale()
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


def _macos_accessibility_tree(max_depth: int = 2) -> str:
    """Return a text dump of the macOS accessibility tree for visible apps.

    Uses AppleScript via ``osascript`` to query the System Events accessibility
    API.  On macOS, roles use the AX prefix (``AXButton``, ``AXTextField``, etc.)
    rather than the AT-SPI2 names used on Linux.

    Args:
        max_depth: Levels below each window to walk (default 2). Deeper values
            are slower; most interactive elements appear within 2 levels.

    Returns:
        Multi-line text representation of the accessibility tree.

    Raises:
        RuntimeError: If osascript fails or accessibility is not granted.
    """
    if max_depth < 1:
        max_depth = 1
    if max_depth > 4:
        max_depth = 4  # safety cap — deep trees in complex apps can hang

    def level_script(parent: str, depth: int) -> str:
        elem = f"elem{depth}"
        role = f"role{depth}"
        name = f"name{depth}"
        indent = "  " * (depth + 1)
        child_walk = ""
        if depth < max_depth:
            child_walk = f"""\
                        try
{level_script(elem, depth + 1)}
                        end try
"""
        return f"""\
                        repeat with {elem} in (every UI element of {parent})
                            set {role} to ""
                            set {name} to ""
                            try
                                set {role} to role of {elem}
                            end try
                            try
                                set {name} to title of {elem}
                            end try
                            if {name} is "" then
                                try
                                    set {name} to name of {elem}
                                end try
                            end if
                            set output to output & "{indent}" & {role} & ": " & {name} & linefeed
{child_walk}                        end repeat
"""

    # Generate indented lines for each visible app / window / element
    script = f"""\
tell application "System Events"
    set output to ""
    set procs to (every process whose background only is false)
    repeat with proc in procs
        set procName to name of proc
        set output to output & "Process: " & procName & linefeed
        try
            repeat with win in (every window of proc)
                set winName to ""
                try
                    set winName to name of win
                end try
                set output to output & "  Window: " & winName & linefeed
                try
{level_script("win", 1)}
                end try
            end repeat
        end try
    end repeat
    return output
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = result.stdout.strip()
        return out or "(empty accessibility tree)"
    except FileNotFoundError:
        raise RuntimeError(
            "osascript not found — macOS accessibility tree requires macOS"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "accessibility_tree timed out — try reducing max_depth or targeting fewer apps"
        ) from None
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"accessibility_tree failed: {e.stderr.strip()}\n"
            "Ensure the terminal has Accessibility permission in System Preferences → Privacy & Security."
        ) from e


def _macos_click_accessible_element(
    role_name: str, element_name: str
) -> tuple[int, int]:
    """Find a UI element on macOS by AX role and name and return its center coordinates.

    Searches the frontmost application's window hierarchy up to two levels deep.
    On macOS, role names use the AX prefix, e.g. ``AXButton``, ``AXTextField``,
    ``AXCheckBox``.  Run ``computer('accessibility_tree')`` first to discover
    available roles and names.

    Args:
        role_name: AX role string, e.g. ``"AXButton"``, ``"AXTextField"``.
        element_name: Element title or name (case-insensitive substring match).

    Returns:
        ``(x, y)`` center of the first matching element in screen coordinates.

    Raises:
        RuntimeError: If osascript fails, element is not found, or has no position.
    """
    name_lower = element_name.lower()
    delimiter = "\x1f"

    # Search first level then second level and let Python match caller input.
    # This avoids interpolating LLM/user-controlled text into AppleScript.
    script = """\
tell application "System Events"
    set delimiter to ASCII character 31
    set output to ""
    set frontApp to first application process whose frontmost is true
    set wins to every window of frontApp
    repeat with win in wins
        repeat with elem in (every UI element of win)
            set eRole to ""
            set eName to ""
            try
                set eRole to role of elem
            end try
            try
                set eName to title of elem
            end try
            if eName is "" then
                try
                    set eName to name of elem
                end try
            end if
            try
                set pos to position of elem
                set sz to size of elem
                set cx to (item 1 of pos) + (item 1 of sz) / 2
                set cy to (item 2 of pos) + (item 2 of sz) / 2
                set output to output & eRole & delimiter & eName & delimiter & (cx as integer) as text & delimiter & (cy as integer) as text & linefeed
            end try
            -- second level
            try
                repeat with child in (every UI element of elem)
                    set cRole to ""
                    set cName to ""
                    try
                        set cRole to role of child
                    end try
                    try
                        set cName to title of child
                    end try
                    if cName is "" then
                        try
                            set cName to name of child
                        end try
                    end if
                    try
                        set pos to position of child
                        set sz to size of child
                        set cx to (item 1 of pos) + (item 1 of sz) / 2
                        set cy to (item 2 of pos) + (item 2 of sz) / 2
                        set output to output & cRole & delimiter & cName & delimiter & (cx as integer) as text & delimiter & (cy as integer) as text & linefeed
                    end try
                end repeat
            end try
        end repeat
    end repeat
    return output
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "osascript not found — macOS accessibility requires macOS"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"click_accessible_element timed out searching for {role_name!r}: {element_name!r}"
        ) from None
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"click_accessible_element failed: {e.stderr.strip()}\n"
            "Ensure the terminal has Accessibility permission in System Preferences → Privacy & Security."
        ) from e

    for line in result.stdout.splitlines():
        parts = line.split(delimiter)
        if len(parts) != 4:
            continue
        candidate_role, candidate_name, x_str, y_str = parts
        if candidate_role == role_name and name_lower in candidate_name.lower():
            try:
                return int(x_str.strip()), int(y_str.strip())
            except ValueError:
                raise RuntimeError(
                    f"Unexpected position output from accessibility search: {line!r}"
                ) from None

    raise RuntimeError(
        f"No accessible element with role={role_name!r} containing name {element_name!r} "
        "found in the frontmost app. Run computer('accessibility_tree') to see available elements."
    )


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
    deadline = _monotonic() + timeout
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
        if _monotonic() >= deadline:
            raise RuntimeError(
                f"No window matching {pattern!r} appeared within {timeout:.0f}s"
            )
        _sleep(0.5)


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


def _poll_for_change(
    transport: ComputerTransport,
    baseline: Path,
    timeout: float = 10.0,
    settle_time: float = 0.0,
) -> Message | None:
    """Poll transport screenshots until the screen changes from *baseline*.

    Returns a screenshot Message of the settled screen.  If no change is
    detected within *timeout* seconds, returns a final screenshot anyway so the
    caller always gets visual confirmation of the current state.

    Polling starts at 50 ms and backs off exponentially up to 500 ms — this
    catches fast UI updates without burning CPU on longer waits.

    Extracted so :func:`act_and_observe` can pass a *pre-action* baseline and
    avoid the race where the action changes the screen before the polling loop
    takes its own reference frame.

    Args:
        transport: Transport to take screenshots from.
        baseline: Reference screenshot to detect changes against.
        timeout: Maximum seconds to wait before returning anyway.
        settle_time: If >0, continue polling after the first change is detected
            until the screen stops changing for *settle_time* consecutive seconds.
            This catches multi-phase UI updates (e.g. a terminal window frame
            appearing first, then the shell prompt rendering a moment later) that
            would otherwise cause the next action to race against an unready UI.
            With the default of 0.0 the original behaviour is preserved: return
            on the first detected change.
    """
    poll_interval = 0.05
    max_poll_interval = 0.5
    change_threshold = 0.01
    deadline = _monotonic() + timeout

    changed = False  # Have we seen any change from the original baseline?
    last_change_at: float | None = None  # Monotonic time of the most recent change
    last_changed_frame: Path | None = None  # Screenshot where the last change occurred
    # Slide the comparison baseline forward so we detect *new* changes, not the
    # same change over and over during the settle phase.
    comparison_baseline = baseline

    while _monotonic() < deadline:
        _sleep(poll_interval)
        current = transport.screenshot()
        ratio = _compute_change_ratio(comparison_baseline, current)

        if ratio >= change_threshold:
            if not changed:
                print(f"Screen changed ({ratio:.1%} pixels differ)")
                if settle_time > 0.0:
                    # Reset to fast polling so the settle window is accurate.
                    # Without this, a backed-off poll_interval (up to 0.5 s) would
                    # inflate the effective quiet time beyond settle_time.
                    poll_interval = 0.05
            changed = True
            last_change_at = _monotonic()
            last_changed_frame = current
            comparison_baseline = current  # slide forward for settle detection

            if settle_time <= 0.0:
                # Original behaviour: return on first detected change.
                return _make_screenshot_msg(current)
        elif changed and settle_time > 0.0:
            # Screen changed earlier; check whether it has now settled.
            if (
                last_changed_frame is not None
                and last_change_at is not None
                and (_monotonic() - last_change_at >= settle_time)
            ):
                return _make_screenshot_msg(last_changed_frame)

        poll_interval = min(poll_interval * 2, max_poll_interval)

    # Timeout reached.
    if changed and last_changed_frame is not None:
        # Return the last frame where a change was observed even on timeout —
        # the caller gets the most recent changed state rather than a stale screenshot.
        return _make_screenshot_msg(last_changed_frame)
    print(
        f"No screen change detected after {timeout:.0f}s — returning current screenshot"
    )
    return _make_screenshot_msg(transport.screenshot())


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
        baseline = transport.screenshot()
        return _poll_for_change(transport, baseline, timeout)

    if action == "window_focus":
        if not text:
            raise ValueError("text (window name pattern) is required for window_focus")
        transport.window_focus(text)
        print(f"Focused window matching: {text!r}")
        return None

    if action == "accessibility_tree":
        if IS_MACOS:
            tree = _macos_accessibility_tree()
        else:
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
                "text must be 'role_name:element_name', e.g. 'AXButton:Submit' (macOS) or 'push button:Submit' (Linux)"
            )
        role_name, _, element_name = text.partition(":")
        if IS_MACOS:
            x, y = _macos_click_accessible_element(
                role_name.strip(), element_name.strip()
            )
        else:
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


def computer(
    action: Action, text: str | None = None, coordinate: tuple[int, int] | None = None
) -> Message | None:
    """
    Perform computer interactions in X11 or macOS environments.

    Args:
        action: The type of action to perform
        text: Text to type or key sequence to send
        coordinate: X,Y coordinates for mouse actions
    """
    # Gate check before streaming: ensures blocked sensitive actions never emit
    # a misleading progress record to the parent agent.  No-op for read/write
    # actions; may raise PermissionError for sensitive ones when gating is on.
    sensitive_action_gate(action, text)

    # Emit a live risk-label record to the parent agent when running inside
    # a computer_task() subagent.  This is a no-op in all other contexts.
    _stream_action_risk(action, text=text, coordinate=coordinate)

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
        deadline = _monotonic() + timeout
        while _monotonic() < deadline:
            _sleep(poll_interval)
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
            tree = _macos_accessibility_tree()
        else:
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
                "text must be 'role_name:element_name', e.g. 'AXButton:Submit' (macOS) or 'push button:Submit' (Linux)"
            )
        role_name, _, element_name = text.partition(":")
        if IS_MACOS:
            x, y = _macos_click_accessible_element(
                role_name.strip(), element_name.strip()
            )
            # Use cliclick or native mouse move on macOS — no xdotool
            _macos_mouse_move(x, y)
            _macos_click(1)
        else:
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
- accessibility_tree: Dump the native accessibility tree for all open applications.
  On Linux (AT-SPI2): role names like 'push button', 'entry', 'check box'.
    Requires: pip install pyatspi (and AT-SPI2 accessibility stack).
  On macOS (System Events): role names like 'AXButton', 'AXTextField', 'AXCheckBox'.
    Requires Accessibility permission for the terminal in System Preferences.
  Use this to discover element names and roles before using click_accessible_element.
- click_accessible_element: Find and click an element by role and name without
  needing screen coordinates. Use text='role:name' where role is the platform role
  name and name is a substring of the element's accessible name. Examples:
    Linux:  computer('click_accessible_element', text='push button:Submit')
    macOS:  computer('click_accessible_element', text='AXButton:Submit')

### Accessibility-first for native apps

Prefer click_accessible_element over coordinate-based clicks for native apps:

  computer("accessibility_tree")                               # inspect available elements
  # Linux:
  computer("click_accessible_element", text="entry:Username")  # fill username field
  computer("type", text="user@example.com")
  computer("click_accessible_element", text="push button:Log In")
  # macOS:
  computer("click_accessible_element", text="AXTextField:Username")
  computer("type", text="user@example.com")
  computer("click_accessible_element", text="AXButton:Log In")

This is more robust than coordinate guessing: element names don't shift when
window size or position changes. Use coordinate-based clicks only when the app
lacks accessibility support (e.g. electron apps, games, canvas-based UIs).

### Efficient action-verify loops

Prefer ``act_and_observe()`` over separate ``computer()`` + ``wait_for_change``:

  act_and_observe("left_click", coordinate=(760, 540))  # trigger action, see result

This combines the action and observation into one call, preventing the conversation
from accumulating multiple nearly-identical screenshots during transitions.
Only call ``screenshot()`` directly when you need the current state without waiting.

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

Three higher-level helpers are available that implement the structured-first observation policy:

- ``observe_web(url, screenshot_too=False)`` — observe a web page using ARIA snapshots first
  (no vision tokens), with automatic fallback to a browser screenshot, then desktop screenshot.
  Pass ``screenshot_too=True`` to get both an ARIA snapshot AND a screenshot side by side.
- ``observe_desktop()`` — thin wrapper around ``computer('screenshot')`` that signals intent
  clearly for native apps and non-browser surfaces.
- ``act_and_observe(action, text=None, coordinate=None, timeout=3.0)`` — perform a desktop
  action **and** automatically observe the result. Combines ``computer(action, ...)`` with
  ``wait_for_change`` in one call — the complete "act then look" loop without separate
  screenshot calls. Use this for tight interaction loops where you want to see the screen
  after every click, keypress, or scroll.
- ``computer_task(task, timeout=300, model=None)`` — run a multi-step computer-use task
  in a **context-isolated subagent** and block until done. All screenshots and intermediate
  steps are kept inside the subagent's own context — the caller's context stays lean. Use
  this for long, multi-step automations (filling forms, navigating multi-page flows, running
  GUI apps) where piling dozens of screenshots into the current context would be wasteful.
  Returns a status dict with ``status`` and ``result`` keys.

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
        Always returns at least one message; if all observation paths fail, returns a
        single system message explaining what failed and how to fix it.

    Example (from IPython in a computer-use session)::

        msgs = observe_web("https://news.ycombinator.com")
        # Returns one Message containing the ARIA snapshot text.

        msgs = observe_web("https://example.com", screenshot_too=True)
        # Returns snapshot Message + screenshot Message side-by-side.
    """
    from gptme.message import Message

    messages: list[Message] = []
    _failure_reasons: list[str] = []
    _playwright_missing = False

    snapshot_text: str | None = None
    try:
        from gptme.tools.browser import has_playwright, snapshot_url

        if has_playwright():
            snapshot_text = snapshot_url(url)
        else:
            _playwright_missing = True
            _failure_reasons.append(
                "Playwright not installed — snapshot_url unavailable "
                "(fix: pip install playwright && playwright install chromium)"
            )
    except Exception as e:
        _failure_reasons.append(f"snapshot_url raised: {e}")

    if snapshot_text is not None:
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
            else:
                if not _playwright_missing:
                    _failure_reasons.append(
                        "Playwright not installed — screenshot_url unavailable"
                    )
                    _playwright_missing = True
        except Exception as e:
            _failure_reasons.append(f"screenshot_url raised: {e}")

        if not messages:
            msg = computer("screenshot")
            if msg is not None:
                messages.append(msg)
            else:
                display = os.environ.get("DISPLAY", "unset")
                _failure_reasons.append(
                    f"desktop screenshot failed (DISPLAY={display!r} — no X11 display?)"
                )

    # Surface all failures as an actionable error message so the agent can diagnose.
    if not messages:
        detail = "; ".join(_failure_reasons) if _failure_reasons else "unknown reason"
        messages.append(
            Message(
                "system",
                f"observe_web({url!r}) failed — no observation could be collected.\n"
                f"Reasons: {detail}\n"
                "To enable structured web observation: "
                "pip install playwright && playwright install chromium",
            )
        )

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


class ScreenRecording:
    """Handle for an in-progress screen recording.

    Returned by ``start_recording()``.  Call ``.stop()`` to finish the
    recording and get the output path.  Also usable as a context manager::

        with start_recording("session.mp4") as rec:
            # ... do things on screen ...
            pass  # recording stops here
        print(rec.output_path)  # path to the MP4

    Attributes:
        output_path: Destination file path (set at construction time).
    """

    def __init__(
        self,
        process: subprocess.Popen,
        output_path: Path,
        stderr: IO[bytes] | None = None,
    ) -> None:
        self._process = process
        self._stderr = stderr
        self.output_path = output_path
        self._stop_lock = threading.Lock()
        self._stop_error: str | None = None
        self._stopped = threading.Event()

    def stop(self) -> Path:
        """Stop the recording.  Safe to call more than once.

        Returns:
            Path to the completed video file.
        """
        if self._stopped.is_set():
            if self._stop_error is not None:
                raise RuntimeError(self._stop_error)
            return self.output_path

        with self._stop_lock:
            if self._stopped.is_set():
                if self._stop_error is not None:
                    raise RuntimeError(self._stop_error)
                return self.output_path
            returncode = self._process.poll()
            if returncode is not None:
                diagnostic = _read_ffmpeg_stderr(self._stderr)
                if self._stderr is not None:
                    self._stderr.close()
                if returncode != 0:
                    message = (
                        "ffmpeg exited before recording was stopped "
                        f"(return code {returncode})"
                    )
                    if diagnostic:
                        message += f":\n{diagnostic}"
                    self._stop_error = message
                    self._stopped.set()
                    raise RuntimeError(message)
                self._stopped.set()
                return self.output_path
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            if self._stderr is not None:
                self._stderr.close()
            self._stopped.set()
        return self.output_path

    def __enter__(self) -> ScreenRecording:
        return self

    def __exit__(self, *_) -> None:
        self.stop()


def _ffmpeg_record_cmd(
    output: Path,
    fps: int,
    duration: float | None,
    display: str,
    width: int,
    height: int,
) -> list[str]:
    """Build the ffmpeg command for screen recording (platform-aware)."""
    cmd: list[str] = ["ffmpeg", "-y"]  # -y: overwrite without prompting
    if IS_MACOS:
        # avfoundation: "1" = main display (index 1 = first screen).
        # Users can run `ffmpeg -f avfoundation -list_devices true -i ""` to find the index.
        cmd += ["-f", "avfoundation", "-r", str(fps), "-capture_cursor", "1", "-i", "1"]
    else:
        # x11grab: grab directly from the X11 display buffer.
        cmd += [
            "-f",
            "x11grab",
            "-r",
            str(fps),
            "-s",
            f"{width}x{height}",
            "-i",
            f"{display}",
        ]
    if duration is not None:
        cmd += ["-t", str(duration)]
    # H.264 with fast-start for streaming / review in browser.
    cmd += ["-vcodec", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd.append(str(output))
    return cmd


def start_recording(
    output: str | Path | None = None,
    fps: int = 10,
    display: str | None = None,
) -> ScreenRecording:
    """Start recording the screen to an MP4 file.

    Uses ``ffmpeg`` with ``x11grab`` (Linux) or ``avfoundation`` (macOS).
    Returns a ``ScreenRecording`` handle — call ``.stop()`` to finish or
    use it as a context manager.

    Args:
        output: Destination path for the MP4 file.  Defaults to a timestamped
            file in the system temp directory.
        fps: Frames per second (default 10 — suitable for UI demos; increase
            to 24+ for smooth game recordings).
        display: X11 display string (Linux only).  Defaults to ``$DISPLAY``.

    Returns:
        ``ScreenRecording`` handle.  Call ``.stop()`` when done.

    Raises:
        RuntimeError: If ``ffmpeg`` is not found or recording fails to start.

    Example (from IPython in a computer-use session)::

        rec = start_recording("tweet-demo.mp4")
        # ... interact with the browser ...
        rec.stop()  # saves tweet-demo.mp4

        # Or as a context manager:
        with start_recording("demo.mp4") as rec:
            computer_task("open Firefox and navigate to https://example.com")
        print(rec.output_path)
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found — install it first:\n"
            "  sudo apt install ffmpeg   # Debian/Ubuntu\n"
            "  brew install ffmpeg       # macOS"
        )

    if output is None:
        import tempfile as _tempfile

        fd, tmp = _tempfile.mkstemp(suffix=".mp4", prefix="gptme-screen-")
        os.close(fd)
        output_path = Path(tmp)
    else:
        output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    disp: str = display if display is not None else os.environ.get("DISPLAY", ":1")
    width, height = _get_display_resolution()
    cmd = _ffmpeg_record_cmd(output_path, fps, None, disp, width, height)

    stderr_log = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_log,
        )
    except Exception:
        stderr_log.close()
        raise
    # Give ffmpeg a moment to start; if it exits immediately it failed.
    # Guard BaseException (KeyboardInterrupt, etc.) so proc is never orphaned.
    try:
        _sleep(0.3)
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        stderr_log.close()
        raise
    if proc.poll() is not None:
        diagnostic = _read_ffmpeg_stderr(stderr_log)
        stderr_log.close()
        message = (
            f"ffmpeg exited immediately (return code {proc.returncode}).  "
            "Check that DISPLAY is set and ffmpeg is installed."
        )
        if diagnostic:
            message += f"\n\nffmpeg stderr:\n{diagnostic}"
        raise RuntimeError(message)
    return ScreenRecording(proc, output_path, stderr_log)


def record_screen(
    output: str | Path | None = None,
    duration: float = 10.0,
    fps: int = 10,
    display: str | None = None,
) -> Path:
    """Record the screen for a fixed duration and return the output path.

    Synchronous wrapper around ``start_recording()`` / ``ScreenRecording.stop()``.
    Blocks for *duration* seconds.

    Args:
        output: Destination path for the MP4 file.  Defaults to a timestamped
            file in the system temp directory.
        duration: How many seconds to record (default 10).
        fps: Frames per second (default 10).
        display: X11 display string (Linux only).  Defaults to ``$DISPLAY``.

    Returns:
        :class:`~pathlib.Path` to the finished MP4 file.

    Raises:
        RuntimeError: If ``ffmpeg`` is not found or recording fails to start.

    Example (from IPython in a computer-use session)::

        path = record_screen("tweet-demo.mp4", duration=30)
        print(f"Recording saved to {path}")
    """
    with start_recording(output=output, fps=fps, display=display) as rec:
        _sleep(duration)
        return rec.output_path


# Actions that only read state — no post-action screenshot is useful for these.
_OBSERVATION_ACTIONS: frozenset[str] = frozenset(
    {"screenshot", "cursor_position", "accessibility_tree", "wait_for_change"}
)


def act_and_observe(
    action: Action,
    text: str | None = None,
    coordinate: tuple[int, int] | None = None,
    timeout: float = 3.0,
    settle_time: float = 0.2,
) -> list[Message]:
    """Perform a desktop action then automatically observe the result.

    Implements the "act → look" half of the computer-use loop in one call,
    eliminating the separate ``computer('wait_for_change')`` step after every
    interaction.  The screen is polled until it settles (up to *timeout*
    seconds), then a single screenshot is returned — exactly the same
    behaviour as ``computer('wait_for_change')`` but wired directly after the
    requested action.

    For observation-only actions (``"screenshot"``, ``"cursor_position"``,
    ``"accessibility_tree"``, ``"wait_for_change"``) the call is passed
    through unchanged: no extra screenshot is appended.

    Args:
        action: Desktop action to perform — same values as ``computer()``.
        text: Text to type or key sequence (forwarded to ``computer()``).
        coordinate: Mouse coordinates (forwarded to ``computer()``).
        timeout: Seconds to wait for a screen change after the action (default 3 s).
        settle_time: After detecting the first screen change, keep polling until
            the screen stops changing for *settle_time* consecutive seconds
            (default 0.2 s).  This handles multi-phase UI transitions — e.g.
            a terminal frame appearing first and the shell prompt rendering
            shortly after — so the returned screenshot always shows the final
            settled state rather than a transient intermediate frame.
            Set to 0.0 to get the original behaviour (return on first change).

    Returns:
        List of :class:`~gptme.message.Message` objects:

        - For state-changing actions: zero or one action-output message
          (if the action itself produces output) **plus** a screenshot of the
          settled screen after the change.
        - For observation-only actions: just the output of ``computer()``.

    Example (from IPython in a computer-use session)::

        # Click a button and see the screen update — one call, no polling
        msgs = act_and_observe("left_click", coordinate=(760, 540))

        # Type text and immediately verify what appeared
        msgs = act_and_observe("type", text="hello world")

        # Open a terminal and wait for the shell prompt (multi-phase transition)
        # act_and_observe uses settle_time=0.2 by default: frame appears first,
        # then the shell prompt, then 0.2s of quiet → returned screenshot shows prompt
        msgs = act_and_observe("window_focus", text="Terminal")

        # Observation-only actions are passed through unchanged
        msgs = act_and_observe("screenshot")  # same as [computer("screenshot")]
    """
    msgs: list[Message] = []

    # Forward timeout to wait_for_change in passthrough mode if no explicit text given.
    if action == "wait_for_change" and text is None:
        text = str(timeout)

    # Capture a baseline snapshot BEFORE the action executes.
    # Some actions (notably window_focus) change the screen immediately — if we
    # take the baseline afterwards the polling loop sees no further change and
    # always times out, producing the "delay" symptom reported in #216.
    pre_action_baseline = None
    transport = None
    if action not in _OBSERVATION_ACTIONS:
        transport = get_transport()
        if transport is not None:
            try:
                pre_action_baseline = transport.screenshot()
            except Exception as e:
                print(
                    f"Warning: pre-action baseline screenshot failed ({e!r}); falling back to post-action polling"
                )

    result = computer(action, text=text, coordinate=coordinate)
    if result is not None:
        msgs.append(result)

    # Observation-only actions already carry their output above; no extra screenshot.
    if action in _OBSERVATION_ACTIONS:
        return msgs

    # For any action that modifies desktop state, poll for changes and return
    # one screenshot showing the settled screen.
    if pre_action_baseline is not None and transport is not None:
        # Use the pre-action baseline so changes that happened immediately
        # (e.g. window_focus) are detected rather than missed.
        # settle_time ensures we wait for the screen to stop changing (not just
        # detect the first frame of a multi-phase transition like a terminal
        # appearing frame-by-frame before the shell prompt renders).
        settled = _poll_for_change(
            transport, pre_action_baseline, timeout, settle_time=settle_time
        )
    else:
        settled = computer("wait_for_change", text=str(timeout))
    if settled is not None:
        msgs.append(settled)

    return msgs


def computer_task(
    task: str,
    timeout: int = 300,
    model: str | None = None,
) -> dict:
    """Run a computer-use task in a context-isolated subagent.

    Spawns a child agent with the ``computer-use`` profile and blocks until it
    completes (or times out).  All screenshots and intermediate steps stay inside
    the subagent's own context, so the caller's context remains lean — this is
    the "context-efficient tool-use loop until goal is achieved" pattern described
    in gptme/gptme#216.

    Use this instead of issuing a long chain of ``computer()`` + ``act_and_observe()``
    calls directly when the task has many steps, or when you don't want dozens of
    screenshots piling up in the current context.

    Args:
        task: Natural-language description of what to accomplish.
        timeout: Maximum seconds to wait before giving up (default 300 = 5 min).
        model: Optional model override for the subagent.

    Returns:
        dict: Status mapping with keys:

        - ``status``: ``"success"`` / ``"failure"`` / ``"clarification_needed"`` / ``"timeout"``
        - ``result``: text summary from the subagent
        - ``agent_id``: subagent identifier — pass to ``subagent_read_log()`` for the full transcript
        - ``conversation``: conversation name for the audit CLI (``gptme-util computer audit-log CONVERSATION``)
        - ``logdir``: absolute path to the subagent's conversation directory (str)

        ``"clarification_needed"`` is returned if the subagent needs more
        information before it can complete the task.
        ``"timeout"`` is returned when the wall-clock deadline is reached before
        the subagent finishes. The worker thread may still wind down in the
        background, but callers immediately see the terminal timeout result.

    Example (from IPython in a gptme session)::

        # Compose a tweet without piling screenshots into this context
        result = computer_task(
            "Open Firefox, navigate to https://x.com/compose/tweet, "
            "type 'Hello from gptme!', and click Tweet.",
            timeout=120,
        )
        print(result["status"], result["result"])

        # Audit what the subagent actually did (computer-use actions only)
        import subprocess
        subprocess.run(["gptme-util", "computer", "audit-log", result["conversation"]])

        # Read the full step-by-step transcript
        from gptme.tools.subagent import subagent_read_log
        print(subagent_read_log(result["agent_id"]))
    """
    import uuid as _uuid

    from .subagent import subagent, subagent_wait
    from .subagent.types import _subagents, _subagents_lock

    agent_id = f"computer-task-{_uuid.uuid4().hex[:8]}"
    subagent(
        agent_id=agent_id,
        prompt=task,
        profile="computer-use",
        max_time=timeout,
        model=model,
    )
    result = subagent_wait(agent_id, timeout=timeout)
    result["agent_id"] = agent_id

    # Look up the subagent's logdir so callers can find the audit trail without
    # needing to know that the conversation is stored as "subagent-{agent_id}".
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)
    if sa is not None:
        result["logdir"] = str(sa.logdir)
        result["conversation"] = sa.logdir.name

    return result


# Defined as a module-level constant so it can be embedded inside an f-string
# without using backslash escape sequences (not supported inside f-strings on Python < 3.12).
_COMPUTER_TASK_TWEET_EXAMPLE = (
    "computer_task("
    '"Open Firefox, navigate to https://x.com/compose/tweet, '
    "type 'Hello from gptme!', and click the Tweet button.\""
    ", timeout=120)"
)


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
Assistant: I'll use act_and_observe to click Submit and automatically get a screenshot once the screen settles.
{ToolUse("ipython", [], 'act_and_observe("left_click", coordinate=(760, 540))').to_output(tool_format)}
System: Screen changed (23.4% pixels differ)
Viewing image...

User: Open a terminal and run a command
Assistant: I'll open a terminal with a keyboard shortcut, then use act_and_observe for window_focus so the shell prompt has time to appear before I type.
{ToolUse("ipython", [], 'computer("key", text="ctrl+alt+t")').to_output(tool_format)}
System: Sent key sequence: ctrl+alt+t
{ToolUse("ipython", [], 'act_and_observe("window_focus", text="Terminal")').to_output(tool_format)}
System: Screen changed (18.7% pixels differ)
Viewing image...
{ToolUse("ipython", [], 'act_and_observe("type", text="ls -la" + chr(10))').to_output(tool_format)}
System: Screen changed (12.3% pixels differ)
Viewing image...

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

User: Open Firefox, go to https://x.com/compose/tweet, type "Hello from gptme!" and submit it — without filling up my context with screenshots
Assistant: I'll delegate this to computer_task() so all the intermediate screenshots stay in a subagent context rather than here.
{ToolUse("ipython", [], _COMPUTER_TASK_TWEET_EXAMPLE).to_output(tool_format)}
System: {{"status": "success", "result": "Tweet submitted successfully. Firefox opened, x.com/compose/tweet loaded, typed the message, clicked Tweet. Confirmed tweet posted.", "agent_id": "computer-task-a1b2c3d4"}}
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
        ToolFunction.from_callable(act_and_observe),
        ToolFunction.from_callable(computer_task),
        ToolFunction.from_callable(record_screen),
        ToolFunction.from_callable(start_recording),
    ],
    disabled_by_default=True,
)

__doc__ = tool.get_doc(__doc__)
