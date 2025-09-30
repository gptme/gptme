"""Utilities for image handling and detection."""

import re
from pathlib import Path

from ..constants import IMAGE_EXTENSIONS


def is_image_url(url: str) -> bool:
    """
    Check if a URL is likely an image URL.

    Args:
        url: The URL to check

    Returns:
        True if the URL is likely an image URL
    """
    if not re.match(r"https?://", url):
        return False

    url_lower = url.lower()

    # Check if URL ends with image extension
    if any(url_lower.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True

    # Check if URL contains image indicators
    image_indicators = ["image", "img", "photo", "picture"]
    if any(indicator in url_lower for indicator in image_indicators):
        return True

    return False


def is_image_path(path: str | Path) -> bool:
    """
    Check if a path points to an image file.

    Args:
        path: The path to check

    Returns:
        True if the path exists and is an image file
    """
    path_obj = Path(path)
    return path_obj.exists() and path_obj.suffix.lower() in IMAGE_EXTENSIONS


def is_image_content(text: str) -> tuple[bool, str | None]:
    """
    Detect if text contains an image URL or path.

    Args:
        text: The text to check

    Returns:
        A tuple of (is_image, image_path_or_url):
        - is_image: True if the text is an image URL or path
        - image_path_or_url: The image URL or path if detected, None otherwise
    """
    text = text.strip()

    # Remove quotes if present (drag-and-drop often adds quotes)
    if text.startswith(("'", '"')) and text.endswith(text[0]):
        text_unquoted = text[1:-1]
    else:
        text_unquoted = text

    # Check if it's a local image file path
    if is_image_path(text_unquoted):
        return True, text_unquoted

    # Check if it's an image URL
    if is_image_url(text_unquoted):
        return True, text_unquoted

    return False, None
