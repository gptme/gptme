"""
Tools for viewing images, giving the assistant vision.

Requires a model which supports vision, such as GPT-4o, Anthropic, and Llama 3.2.
"""

import tempfile
from pathlib import Path

from PIL import Image

from ..message import Message
from .base import ToolSpec


def view_image(image_path: Path | str) -> Message:
    """View an image. Large images (>1MB) will be automatically scaled down."""
    if isinstance(image_path, str):
        image_path = Path(image_path)

    if not image_path.exists():
        return Message("system", f"Image not found at `{image_path}`")

    file_size = image_path.stat().st_size
    MAX_SIZE = 1024 * 1024  # 1MB in bytes

    with Image.open(image_path) as img:
        dimensions = img.size
        msg_parts = [
            f"Image size: {dimensions[0]}x{dimensions[1]}, {file_size/1024:.1f}KB"
        ]

        if file_size <= MAX_SIZE:
            msg_parts.append("No scaling required (under 1MB)")
            return Message(
                "system",
                f"Viewing image at `{image_path}`\n" + "\n".join(msg_parts),
                files=[image_path.absolute()],
            )

        # Convert RGBA to RGB if needed
        if img.mode == "RGBA":
            img = img.convert("RGB")

        # First try just compressing as JPG without scaling
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img.save(tmp.name, "JPEG", quality=85)
            compressed_size = Path(tmp.name).stat().st_size
            msg_parts.append(f"Compressed to: {compressed_size/1024:.1f}KB")

            # If compression alone wasn't enough, scale down and compress
            if compressed_size > MAX_SIZE:
                # Calculate scaling factor to get file size roughly under 1MB
                scale_factor = (MAX_SIZE / compressed_size) ** 0.5
                new_size = tuple(int(dim * scale_factor) for dim in img.size)
                msg_parts.append(f"Scaling from {dimensions} to {new_size}")

                # Create a scaled version
                scaled_img = img.resize(new_size, Image.Resampling.LANCZOS)
                scaled_img.save(tmp.name, "JPEG", quality=85)
                final_size = Path(tmp.name).stat().st_size
                msg_parts.append(f"Final size after scaling: {final_size/1024:.1f}KB")
                dimensions = new_size

            scaled_path = Path(tmp.name)

        action = "scaled and compressed" if compressed_size > MAX_SIZE else "compressed"
        return Message(
            "system",
            f"Viewing {action} image ({dimensions[0]}x{dimensions[1]}) from {image_path}\n"
            + "\n".join(msg_parts),
            files=[scaled_path.absolute()],
        )


instructions = """
Use the `view_image` Python function with `ipython` tool to view an image file.

You can also use Ctrl+V to paste an image from the clipboard. If an image is detected,
it will be automatically saved and `view_image()` code will be inserted into the prompt.
""".strip()

tool = ToolSpec(
    name="vision",
    desc="Viewing images",
    functions=[view_image],
)
