"""
Tools for viewing images, giving the assistant vision.

Requires a model which supports vision, such as GPT-4o, Anthropic, and Llama 3.2.
"""

import atexit
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from ..message import Message
from .base import ToolFunction, ToolSpec

# Track temp files for cleanup on exit
_temp_files: list[Path] = []


def _cleanup_temp_files() -> None:
    for f in _temp_files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def _vision_allowed_roots() -> list[Path]:
    """Directories from which view_image may attach a file to the model.

    Auto-approval of the vision tool only holds if this boundary does. Workspace
    images, screenshot output, and the process temp dir (pytest + scaled copies)
    are in-bounds; home-directory and system paths are not.
    """
    from .screenshot import OUTPUT_DIR

    return [
        Path.cwd().resolve(),
        OUTPUT_DIR.resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]


def _vision_path_denied(path: Path) -> str | None:
    """Return an error message if *path* is outside the vision path boundary."""
    try:
        resolved = path.expanduser().resolve()
    except OSError as exc:
        return f"Image path could not be resolved: `{path}` ({exc})"
    for root in _vision_allowed_roots():
        try:
            resolved.relative_to(root)
            return None
        except ValueError:
            continue
    return (
        f"Image path is outside allowed directories: `{path}` "
        f"(resolves to `{resolved}`). Allowed: workspace, screenshot output "
        f"({_vision_allowed_roots()[1]}), and temp."
    )


def view_image(image_path: "Path | str | Image.Image") -> Message:
    """View an image. Large images (>1MB) will be automatically scaled down."""
    # Handle PIL Image objects
    if isinstance(image_path, Image.Image):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image_path.save(tmp.name)
            image_path = Path(tmp.name)
            _temp_files.append(image_path)

    if isinstance(image_path, str):
        image_path = Path(image_path)
    if isinstance(image_path, Path):
        image_path = image_path.expanduser()

    # Confine before exists() so out-of-bound paths do not leak file presence.
    if denied := _vision_path_denied(image_path):
        return Message("system", denied)

    if not image_path.exists():
        return Message("system", f"Image not found at `{image_path}`")

    file_size = image_path.stat().st_size
    MAX_SIZE = 1024 * 1024  # 1MB in bytes

    with Image.open(image_path) as img:
        dimensions: tuple[int, int] = (img.size[0], img.size[1])
        msg_parts = [
            f"Image size: {dimensions[0]}x{dimensions[1]}, {file_size / 1024:.1f}KB"
        ]

        normalized: Image.Image | None = None
        if file_size <= MAX_SIZE:
            # Provider serializers infer MIME from the suffix and only accept
            # these containers. Pillow can also read BMP/TIFF and renamed files.
            supported_suffixes = {
                "JPEG": {".jpg", ".jpeg"},
                "PNG": {".png"},
                "GIF": {".gif"},
                "WEBP": {".webp"},
            }
            if image_path.suffix.lower() in supported_suffixes.get(
                img.format or "", set()
            ):
                msg_parts.append("No scaling required (under 1MB)")
                return Message(
                    "system",
                    f"Viewing image at `{image_path}`\n" + "\n".join(msg_parts),
                    files=[image_path.absolute()],
                )

            # Normalize displayed pixels losslessly for small readable containers.
            # Close the handle before encoding (also works on Windows).
            oriented = ImageOps.exif_transpose(img)
            mode = (
                "RGBA"
                if "A" in oriented.getbands() or "transparency" in oriented.info
                else "RGB"
            )
            normalized = oriented.convert(mode)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                normalized_path = Path(tmp.name)
            try:
                normalized.save(normalized_path, "PNG")
                normalized_size = normalized_path.stat().st_size
            except BaseException:
                normalized_path.unlink(missing_ok=True)
                raise
            if normalized_size <= MAX_SIZE:
                _temp_files.append(normalized_path)
                msg_parts.append(f"Normalized to PNG: {normalized_size / 1024:.1f}KB")
                return Message(
                    "system",
                    f"Viewing normalized image ({normalized.width}x{normalized.height}) from `{image_path}`\n"
                    + "\n".join(msg_parts),
                    files=[normalized_path.absolute()],
                )
            # A small compressed source may expand as PNG; keep the existing
            # JPEG compression/scaling path rather than bypassing its size gate.
            normalized_path.unlink(missing_ok=True)

        # JPEG output drops orientation metadata, so apply it to pixels first.
        oriented = (
            normalized if normalized is not None else ImageOps.exif_transpose(img)
        )
        dimensions = oriented.size

        # Convert RGBA to RGB if needed (convert both branches to ensure Image type)
        out: Image.Image = (
            oriented.convert("RGB")
            if oriented.mode == "RGBA"
            else oriented.convert(oriented.mode)
        )

        # First try just compressing as JPG without scaling
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            out.save(tmp.name, "JPEG", quality=85)
            compressed_size = Path(tmp.name).stat().st_size
            msg_parts.append(f"Compressed to: {compressed_size / 1024:.1f}KB")

            # If compression alone wasn't enough, scale down and compress
            if compressed_size > MAX_SIZE:
                # Calculate scaling factor to get file size roughly under 1MB
                scale_factor = (MAX_SIZE / compressed_size) ** 0.5
                new_size: tuple[int, int] = (
                    int(out.size[0] * scale_factor),
                    int(out.size[1] * scale_factor),
                )
                msg_parts.append(f"Scaling from {dimensions} to {new_size}")

                # Create a scaled version
                scaled_img = out.resize(new_size, Image.Resampling.LANCZOS)
                scaled_img.save(tmp.name, "JPEG", quality=85)
                final_size = Path(tmp.name).stat().st_size
                msg_parts.append(f"Final size after scaling: {final_size / 1024:.1f}KB")
                dimensions = new_size

            scaled_path = Path(tmp.name)
            _temp_files.append(scaled_path)

        action = "scaled and compressed" if compressed_size > MAX_SIZE else "compressed"
        return Message(
            "system",
            f"Viewing {action} image ({dimensions[0]}x{dimensions[1]}) from {image_path}\n"
            + "\n".join(msg_parts),
            files=[scaled_path.absolute()],
        )


instructions = """
### When to use vision

Use vision when you have a local image file (screenshot, photo, diagram, chart,
or plot) that needs visual inspection or analysis. Prefer vision over guessing
from filenames — pass the actual pixels to the model.

Do **not** use vision for:
{% if tools: read, browser %}
- Images at a URL — fetch with `read` or visit with `browser` instead
{% elif tools: read %}
- Images at a URL — fetch with `read` first, then pass the local path
{% elif tools: browser %}
- Images at a URL — visit with `browser` instead
{% endif %}
{% if tools: screenshot %}
- Taking a new screenshot — use the `screenshot` tool, then pass the path to vision
{% endif %}

Use the `view_image` Python function with `ipython` tool to view an image file.
""".strip()

tool = ToolSpec(
    name="vision",
    desc="Viewing images",
    instructions=instructions,
    functions=[ToolFunction.from_callable(view_image)],
    read_only=True,
)
