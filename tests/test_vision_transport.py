"""Regression coverage for view_image attachments reaching provider image payloads."""

import base64
import io
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from PIL import Image

from gptme.llm.llm_anthropic import _process_file as anthropic_process_file
from gptme.llm.llm_openai import _process_file as openai_process_file
from gptme.llm.models import ModelMeta
from gptme.message import Message
from gptme.tools import vision

Provider = Literal["anthropic", "openai"]


@pytest.fixture(autouse=True)
def isolated_temp_files(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Do not leave generated images or touch another test's cleanup registry."""
    monkeypatch.setattr(vision, "_temp_files", [])
    yield
    vision._cleanup_temp_files()


@pytest.fixture(params=["anthropic", "openai"])
def provider(request: pytest.FixtureRequest) -> Provider:
    return cast(Provider, request.param)


def _payload(message: Message, provider: Provider) -> tuple[str, bytes]:
    """Use the real serializer, including its extension filtering and base64 encoding."""
    if provider == "anthropic":
        result = anthropic_process_file(message.to_dict())
        images = [part for part in result["content"] if part["type"] == "image"]
        assert len(images) == 1, result
        source = images[0]["source"]
        assert source["type"] == "base64"
        return source["media_type"], base64.b64decode(source["data"], validate=True)
    result = openai_process_file(
        message.to_dict(),
        ModelMeta(
            provider="openai", model="vision-test", context=8192, supports_vision=True
        ),
    )
    images = [part for part in result["content"] if part["type"] == "image_url"]
    assert len(images) == 1, result
    header, data = images[0]["image_url"]["url"].split(",", 1)
    assert header.startswith("data:") and header.endswith(";base64")
    return header.removeprefix("data:").removesuffix(";base64"), base64.b64decode(
        data, validate=True
    )


def _pattern() -> Image.Image:
    image = Image.new("RGB", (3, 2))
    image.putdata(
        [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
        ]
    )
    return image


@pytest.mark.parametrize(
    ("image_format", "orientation"), [("BMP", None), ("TIFF", None), ("TIFF", 6)]
)
def test_small_unsupported_image_transport(
    tmp_path: Path, provider: Provider, image_format: str, orientation: int | None
) -> None:
    path = tmp_path / f"small.{image_format.lower()}"
    expected = _pattern()
    if orientation is None:
        expected.save(path, image_format)
    else:
        expected.save(path, image_format, tiffinfo={274: orientation})
        expected = expected.transpose(Image.Transpose.ROTATE_270)
    original = path.read_bytes()
    assert len(original) < 1024 * 1024

    message = vision.view_image(path)
    media_type, data = _payload(message, provider)
    assert media_type == "image/png"
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == expected.size
        assert decoded.convert("RGB").tobytes() == expected.tobytes()
        assert decoded.getexif().get(274, 1) == 1
    assert len(message.files) == 1
    assert message.files[0] != path
    assert isinstance(message.files[0], Path)
    assert message.files[0].suffix == ".png"
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode", ["L", "RGBA"])
def test_tiff_normalized_modes(tmp_path: Path, provider: Provider, mode: str) -> None:
    path = tmp_path / "mode.tiff"
    expected = _pattern().convert(mode)
    if mode == "RGBA":
        expected.putpixel((0, 0), (255, 0, 0, 100))
    expected.save(path)
    original = path.read_bytes()
    media_type, data = _payload(vision.view_image(path), provider)
    assert media_type == "image/png"
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.mode == ("RGBA" if mode == "RGBA" else "RGB")
        assert decoded.size == expected.size
        assert decoded.tobytes() == expected.convert(decoded.mode).tobytes()
    assert path.read_bytes() == original


def test_normalized_png_too_large_falls_back_to_jpeg(
    tmp_path: Path, provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "compact.tiff"
    rng = random.Random(4626)
    image = Image.frombytes("P", (800, 800), rng.randbytes(800 * 800))
    image.putpalette(rng.randbytes(768))
    image.save(path, "TIFF", compression="tiff_lzw")
    original = path.read_bytes()
    assert len(original) < 1024 * 1024
    png = io.BytesIO()
    image.convert("RGB").save(png, "PNG")
    assert len(png.getvalue()) > 1024 * 1024

    created: list[Path] = []
    named_temp_file = vision.tempfile.NamedTemporaryFile

    def track_temp_file(*args: Any, **kwargs: Any) -> Any:
        result = named_temp_file(*args, **kwargs)
        created.append(Path(result.name))
        return result

    monkeypatch.setattr(vision.tempfile, "NamedTemporaryFile", track_temp_file)
    try:
        message = vision.view_image(path)
        media_type, data = _payload(message, provider)
        assert media_type == "image/jpeg"
        assert len(data) < 1024 * 1024
        with Image.open(io.BytesIO(data)) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.size == image.size
            decoded.load()
        discarded = [output for output in created if output.suffix == ".png"]
        assert len(discarded) == 1
        assert not discarded[0].exists()
        assert discarded[0] not in vision._temp_files
        assert message.files[0] in vision._temp_files
        assert path.read_bytes() == original
    finally:
        for output in created:
            output.unlink(missing_ok=True)


@pytest.mark.parametrize(("image_format", "suffix"), [("BMP", ".png"), ("PNG", ".jpg")])
def test_mismatched_suffix_normalizes(
    tmp_path: Path, provider: Provider, image_format: str, suffix: str
) -> None:
    path = tmp_path / f"renamed{suffix}"
    expected = _pattern()
    expected.save(path, image_format)
    original = path.read_bytes()
    message = vision.view_image(path)
    media_type, data = _payload(message, provider)
    assert media_type == "image/png"
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == expected.size
        assert decoded.convert("RGB").tobytes() == expected.tobytes()
    assert message.files != [path]
    assert path.read_bytes() == original


def test_supported_png_passthrough(tmp_path: Path, provider: Provider) -> None:
    path = tmp_path / "supported.png"
    expected = _pattern().convert("RGBA")
    expected.putpixel((0, 0), (255, 0, 0, 100))
    expected.save(path)
    original = path.read_bytes()

    message = vision.view_image(path)
    media_type, data = _payload(message, provider)
    assert media_type == "image/png"
    assert data == original
    assert message.files == [path.absolute()]
    assert not vision._temp_files
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == expected.size
        assert decoded.tobytes() == expected.tobytes()
    assert path.read_bytes() == original


def test_large_bmp_compression_transport(tmp_path: Path, provider: Provider) -> None:
    path = tmp_path / "large.bmp"
    expected = Image.new("RGB", (800, 800), (64, 128, 192))
    expected.save(path)
    original = path.read_bytes()
    assert len(original) > 1024 * 1024

    message = vision.view_image(path)
    media_type, data = _payload(message, provider)
    assert media_type == "image/jpeg"
    assert len(data) < 1024 * 1024
    assert message.files != [path]
    assert "compressed" in message.content.lower()
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == expected.size
        pixel = decoded.getpixel((400, 400))
        assert isinstance(pixel, tuple)
        assert all(abs(a - b) <= 2 for a, b in zip(pixel, (64, 128, 192)))
    assert path.read_bytes() == original


@pytest.mark.parametrize("suffix", [".bmp", ".tiff", ".png", ".unknown"])
def test_invalid_image_fails_visibly(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"corrupt{suffix}"
    path.write_bytes(b"not an image")
    try:
        message = vision.view_image(path)
    except (OSError, ValueError):
        return  # Raising a decode error is an acceptable visible failure.
    assert not message.files
    assert "viewing" not in message.content.lower()
    assert any(
        word in message.content.lower()
        for word in ("error", "invalid", "unsupported", "failed")
    )


def test_normalized_image_registered_for_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "small.bmp"
    _pattern().save(path)
    original = path.read_bytes()
    message = vision.view_image(path)
    assert len(message.files) == 1
    normalized = message.files[0]
    assert isinstance(normalized, Path)
    assert normalized != path
    assert normalized.suffix == ".png"
    assert normalized.exists()
    assert normalized in vision._temp_files
    vision._cleanup_temp_files()
    assert not normalized.exists()
    assert path.read_bytes() == original


def test_normalization_encoder_error_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "small.bmp"
    _pattern().save(path)
    original = path.read_bytes()
    created: list[Path] = []
    named_temp_file = vision.tempfile.NamedTemporaryFile

    def track_temp_file(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["suffix"] == ".png"
        result = named_temp_file(*args, **kwargs)
        created.append(Path(result.name))
        return result

    def fail_save(self: Image.Image, *args: Any, **kwargs: Any) -> None:
        # Simulate an encoder that fails after writing a partial output.
        assert created and not vision._temp_files
        created[-1].write_bytes(b"partial PNG")
        raise OSError("PNG encoder failed")

    monkeypatch.setattr(vision.tempfile, "NamedTemporaryFile", track_temp_file)
    monkeypatch.setattr(Image.Image, "save", fail_save)
    try:
        with pytest.raises(OSError, match="PNG encoder failed"):
            vision.view_image(path)
        assert len(created) == 1
        assert not created[0].exists()
        assert not vision._temp_files
        assert path.read_bytes() == original
    finally:
        for output in created:
            output.unlink(missing_ok=True)
