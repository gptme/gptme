import logging
import platform
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from ..constants import IMAGE_EXTENSIONS
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


def paste_image(attachments_dir: Path | None = None) -> Path | str | None:
    """
    Get image from clipboard and save to a file, or return path/URL.

    Args:
        attachments_dir: Directory to save clipboard images. If None, uses tempfile.

    Returns:
        - Path to the saved image file if image data was in clipboard
        - String path if a local image file path was in clipboard
        - String URL if an image URL was in clipboard
        - None if no image-related content in clipboard
    """
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        # Silently fail if Pillow not installed
        return None

    try:
        # Get image from clipboard
        img = ImageGrab.grabclipboard()

        if img is None:
            # No image in clipboard - could be text (URL or path)
            return None

        # Handle file paths (Windows behavior)
        if isinstance(img, list):
            # On Windows, grabclipboard can return a list of file paths
            for item in img:
                path = Path(item)
                if path.exists() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    return str(path)
            return None

        # If we have an actual image object, save it to file
        if isinstance(img, Image.Image):
            if attachments_dir:
                # Save to attachments directory with timestamp-based filename
                attachments_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                image_path = attachments_dir / f"pasted_{timestamp}.png"
                img.save(image_path, "PNG")
                return image_path
            else:
                # Fall back to temporary file
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    img.save(tmp.name, "PNG")
                    return Path(tmp.name)

        return None

    except Exception as e:
        # Log error instead of silently failing
        logger.warning(f"Failed to paste image from clipboard: {e}")
        return None


def paste_text() -> str | None:
    """
    Get text from clipboard.

    Returns:
        Text content from clipboard, or None if failed
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
        elif platform.system() == "Windows":
            # Use PowerShell Get-Clipboard command for Windows
            result = subprocess.run(
                ["powershell", "-command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout
        # No clipboard tool available or subprocess failed
        return None
    except Exception as e:
        logger.warning(f"Failed to paste text from clipboard: {e}")
        return None
