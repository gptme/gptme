import logging
import platform
import subprocess
import tempfile
from pathlib import Path

from . import get_installed_programs

logger = logging.getLogger(__name__)

text = ""


def set_copytext(new_text: str):
    global text
    text = new_text


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
        if "xclip" in installed:
            output = subprocess.run(
                ["xclip", "-selection", "clipboard"], check=False, input=text, text=True
            )
            if output.returncode != 0:
                print("xclip failed to copy to clipboard.")
                return False
            return True
        print("No clipboard utility found. Please install xclip or wl-clipboard.")
        return False
    if platform.system() == "Darwin":
        output = subprocess.run(["pbcopy"], check=False, input=text, text=True)
        if output.returncode != 0:
            print("pbcopy failed to copy to clipboard.")
            return False
        return True

    return False


# Module-level variable for configurable attachments directory
_attachments_dir: Path | None = None


def set_attachments_dir(path: Path) -> None:
    """Set the directory where pasted images are saved."""
    global _attachments_dir
    _attachments_dir = path


def paste_image() -> Path | None:
    """Get image from clipboard and save to a file.

    Returns path to saved image file, or None if no image in clipboard.
    """
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return None

    try:
        img = ImageGrab.grabclipboard()
        if img is None:
            return None

        # Handle file paths (Windows behavior: grabclipboard returns list of paths)
        if isinstance(img, list):
            for item in img:
                path = Path(item)
                if path.exists() and path.suffix.lower() in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif",
                    ".bmp",
                    ".webp",
                ):
                    return path
            return None

        if isinstance(img, Image.Image):
            save_dir = _attachments_dir or Path(tempfile.gettempdir())
            save_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime

            filename = f"paste_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            save_path = save_dir / filename
            img.save(str(save_path), "PNG")
            return save_path

        return None
    except Exception:
        logger.debug("Failed to get image from clipboard", exc_info=True)
        return None


def paste_text() -> str | None:
    """Get text from clipboard.

    Returns text content, or None if failed.
    """
    try:
        if platform.system() == "Linux":
            installed = get_installed_programs(("xclip", "wl-paste"))
            if "wl-paste" in installed:
                result = subprocess.run(
                    ["wl-paste"], capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    return result.stdout
            elif "xclip" in installed:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                return result.stdout
    except Exception:
        pass
    return None
