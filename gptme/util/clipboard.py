"""
Clipboard utilities for copy/paste functionality.
"""

import logging
import platform
import subprocess
import tempfile
from pathlib import Path

from . import get_installed_programs

logger = logging.getLogger(__name__)

# Global variable for copy functionality
text = ""


def set_copytext(new_text: str):
    """Set text to be copied to clipboard."""
    global text
    text = new_text


def copy() -> bool:
    """Copy text to clipboard. Returns True if successful."""
    global text
    if platform.system() == "Linux":
        # check if xclip or wl-clipboard is installed
        installed = get_installed_programs(("xclip", "wl-copy"))
        if "wl-copy" in installed:
            output = subprocess.run(["wl-copy"], input=text, text=True, check=True)
            if output.returncode != 0:
                print("wl-copy failed to copy to clipboard.")
                return False
            return True
        elif "xclip" in installed:
            output = subprocess.run(
                ["xclip", "-selection", "clipboard"], input=text, text=True
            )
            if output.returncode != 0:
                print("xclip failed to copy to clipboard.")
                return False
            return True
        else:
            print("No clipboard utility found. Please install xclip or wl-clipboard.")
            return False
    elif platform.system() == "Darwin":
        output = subprocess.run(["pbcopy"], input=text, text=True)
        if output.returncode != 0:
            print("pbcopy failed to copy to clipboard.")
            return False
        return True

    return False


def get_clipboard_image() -> Path | None:
    """
    Check clipboard for image data and save it to a temporary file if found.

    Returns:
        Path to the saved image file if an image is in the clipboard, None otherwise.
    """
    try:
        import pyclip

        # Try to get image from clipboard
        image_data = pyclip.paste_image()

        if image_data is None:
            return None

        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            # pyclip returns PIL Image object
            if isinstance(image_data, bytes):
                # Handle raw bytes
                tmp.write(image_data)
                tmp_path = Path(tmp.name)
            else:
                # Handle PIL Image object
                image_data.save(tmp.name, "PNG")
                tmp_path = Path(tmp.name)

        logger.info(f"Saved clipboard image to {tmp_path}")
        return tmp_path

    except ImportError:
        logger.warning("pyclip not available, clipboard image paste disabled")
        return None
    except Exception as e:
        logger.debug(f"Failed to get clipboard image: {e}")
        return None


def get_clipboard_text() -> str | None:
    """
    Get text from clipboard.

    Returns:
        Clipboard text content if available, None otherwise.
    """
    try:
        import pyclip

        text = pyclip.paste()
        if text and isinstance(text, str | bytes):
            return text.decode("utf-8") if isinstance(text, bytes) else text
        return None

    except ImportError:
        logger.warning("pyclip not available, clipboard text paste disabled")
        return None
    except Exception as e:
        logger.debug(f"Failed to get clipboard text: {e}")
        return None
