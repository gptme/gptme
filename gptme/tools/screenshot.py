"""
A simple screenshot tool, using `screencapture` on macOS and `scrot` or `gnome-screenshot` on Linux.
"""

import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .base import ToolSpec, ToolUse

OUTPUT_DIR = Path("/tmp/outputs")
IS_MACOS = platform.system() == "Darwin"
IS_WAYLAND = os.environ.get("XDG_SESSION_TYPE") == "wayland"


# TODO: check for this instead of prompting the llm
INSTRUCTIONS = (
    "If all you see is a wallpaper, the user may have to allow screen capture in `System Preferences -> Security & Privacy -> Screen Recording`."
    if IS_MACOS
    else ""
)


def _validate_screenshot_path(path: Path) -> Path:
    """Validate that screenshot path is within allowed directory.

    Security: Prevents arbitrary file writes via path traversal.
    See: https://github.com/gptme/gptme/issues/1021

    Args:
        path: User-provided path for screenshot

    Returns:
        Resolved path within OUTPUT_DIR

    Raises:
        ValueError: If path would escape OUTPUT_DIR
    """
    # Ensure OUTPUT_DIR exists for path resolution
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve both paths to handle .. and symlinks
    resolved_output_dir = OUTPUT_DIR.resolve()
    resolved_path = path.resolve()

    # Check if resolved path is within OUTPUT_DIR
    try:
        resolved_path.relative_to(resolved_output_dir)
    except ValueError:
        raise ValueError(
            f"Screenshot path must be within {OUTPUT_DIR}. "
            f"Got: {path} (resolves to {resolved_path})"
        ) from None

    return resolved_path


def screenshot(path: Path | None = None) -> Path:
    """
    Take a screenshot and save it to a file.
    """

    if path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / f"screenshot_{timestamp}.png"
        # Ensure OUTPUT_DIR exists
        path.parent.mkdir(parents=True, exist_ok=True)
        path = path.resolve()
    else:
        # Validate user-provided path stays within OUTPUT_DIR
        path = _validate_screenshot_path(path)
        # Ensure parent directory exists within OUTPUT_DIR
        path.parent.mkdir(parents=True, exist_ok=True)

    if IS_MACOS:
        subprocess.run(["screencapture", str(path)], check=True)
        return path
    elif os.name == "posix":
        # TODO: add support for specifying window/fullscreen?
        if shutil.which("gnome-screenshot"):
            subprocess.run(["gnome-screenshot", "-f", str(path)], check=True)
            return path
        elif not IS_WAYLAND and shutil.which("scrot"):
            subprocess.run(["scrot", "--overwrite", str(path)], check=True)
            return path
        else:
            raise NotImplementedError("No supported screenshot method available")
    else:
        raise NotImplementedError(
            "Screenshot functionality is only available on macOS and Linux."
        )


tool = ToolSpec(
    name="screenshot",
    desc="Take a screenshot",
    instructions=INSTRUCTIONS,
    functions=[screenshot],
    examples=f"""
To take a screenshot and view it immediately:

{ToolUse("ipython", [], "view_image(screenshot())")}

This will take a screenshot, save it to a file, and include the image in the chat.
    """,
)
