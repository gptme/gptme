import platform
import subprocess
from pathlib import Path

from . import get_installed_programs

text = ""


def set_copytext(new_text: str):
    global text
    text = new_text


def paste() -> str | None:
    """
    Read text from clipboard.

    Returns:
        str | None: The clipboard text content, or None if clipboard is empty or error occurred
    """
    if platform.system() == "Linux":
        # check if xclip or wl-clipboard is installed
        installed = get_installed_programs(("xclip", "wl-paste"))
        if "wl-paste" in installed:
            try:
                result = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout
                return None
            except Exception:
                return None
        elif "xclip" in installed:
            try:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout
                return None
            except Exception:
                return None
        else:
            print("No clipboard utility found. Please install xclip or wl-clipboard.")
            return None
    elif platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception:
            return None

    return None


def is_image_path(content: str) -> bool:
    """
    Check if clipboard content is a valid path to an image file.

    Args:
        content: The clipboard content to check

    Returns:
        bool: True if content is a path to an existing image file
    """
    if not content:
        return False

    # Common image extensions
    image_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".ico",
    }

    try:
        # Clean up the path (remove quotes, whitespace)
        path_str = content.strip().strip('"').strip("'")
        path = Path(path_str)

        # Check if it's an existing file with an image extension
        if path.exists() and path.is_file() and path.suffix.lower() in image_extensions:
            return True

    except Exception:
        pass

    return False


def copy() -> bool:
    """return True if successful"""

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
