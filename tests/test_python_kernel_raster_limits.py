"""Bounded kernel raster decoding, without kernel startup or inference."""

import base64
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from gptme.tools import python_kernel as kernel


def raster(fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(buffer, format=fmt)
    return buffer.getvalue()


def collect(
    data: dict[str, Any],
    images: list[kernel.KernelRaster] | None = None,
    output_type: str = "display_data",
) -> tuple[list[kernel.KernelRaster], str | None]:
    if images is None:
        images = []
    notice = kernel._collect_raster(data, output_type, images)
    return images, notice


@pytest.mark.parametrize(
    ("mime", "fmt"),
    [
        ("image/png", "PNG"),
        ("image/jpeg", "JPEG"),
        ("image/webp", "WEBP"),
        ("image/gif", "GIF"),
    ],
)
def test_collect_supported_raster(mime: str, fmt: str) -> None:
    payload = raster(fmt)
    images, notice = collect(
        {mime: base64.b64encode(payload).decode(), "text/plain": "fallback"}
    )
    assert notice is None
    assert len(images) == 1
    assert images[0].data == payload
    assert images[0].mime_type == mime
    assert images[0].output_type == "display_data"


def test_collect_only_one_representation_per_output() -> None:
    encoded = base64.b64encode(raster()).decode()
    images, notice = collect(
        {"image/png": encoded, "image/jpeg": encoded}, output_type="execute_result"
    )
    assert notice is None
    assert len(images) == 1
    assert images[0].output_type == "execute_result"


@pytest.mark.parametrize(
    "data",
    [
        {"image/png": "%%%"},
        {"image/png": "é"},
        {"image/png": ["not", "a", "string"]},
        {"image/png": base64.b64encode(b"not an image").decode()},
        {"image/png": base64.b64encode(raster("JPEG")).decode()},
        {"image/svg+xml": "<svg onload='alert(1)'/>"},
        {"text/html": "<script>alert(1)</script>"},
    ],
)
def test_collect_invalid_or_unsupported_output(data: dict[str, Any]) -> None:
    images, notice = collect(data)
    assert not images
    if "text/html" not in data:
        assert notice


def test_image_size_checked_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kernel, "_MAX_RASTER_BYTES", 4)

    def unexpected_decode(*args: Any, **kwargs: Any) -> bytes:
        pytest.fail("oversize base64 must not be decoded")

    monkeypatch.setattr(kernel.base64, "b64decode", unexpected_decode)
    images, notice = collect({"image/png": "A" * 16})
    assert not images
    assert notice


def test_decoded_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = raster()
    monkeypatch.setattr(kernel, "_MAX_RASTER_BYTES", len(payload) - 1)
    images, notice = collect({"image/png": base64.b64encode(payload).decode()})
    assert not images
    assert notice


def test_aggregate_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = raster()
    monkeypatch.setattr(kernel, "_MAX_RASTER_TOTAL_BYTES", len(payload))
    images, notice = collect({"image/png": base64.b64encode(payload).decode()})
    assert notice is None
    _, notice = collect({"image/png": base64.b64encode(payload).decode()}, images)
    assert len(images) == 1
    assert notice


def test_image_count_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kernel, "_MAX_RASTER_IMAGES", 1)
    encoded = base64.b64encode(raster()).decode()
    images, notice = collect({"image/png": encoded})
    assert notice is None
    _, notice = collect({"image/png": encoded}, images)
    assert len(images) == 1
    assert notice


def test_pixel_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kernel, "_MAX_RASTER_PIXELS", 5)
    images, notice = collect({"image/png": base64.b64encode(raster()).decode()})
    assert not images
    assert notice


def test_text_only_output_does_not_invent_image_warning() -> None:
    images, notice = collect({"text/plain": "42"})
    assert not images
    assert notice is None


def test_image_attachment_private_retained_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "Path"
) -> None:
    import stat

    from gptme.logmanager import LogManager
    from gptme.tools import python as py

    monkeypatch.setattr(LogManager, "get_current_log", lambda: None)
    monkeypatch.setattr(py.tempfile, "tempdir", str(tmp_path))
    payload = raster()
    result = kernel.KernelExecResult(
        images=[kernel.KernelRaster(payload, "image/png", "display_data")]
    )
    files, artifacts, notices = py._kernel_image_attachments(result)
    assert not notices
    assert len(files) == 1
    assert files[0].parent == tmp_path
    assert files[0].read_bytes() == payload
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    # No conversation-relative descriptor exists in standalone use. Message.files
    # still delivers bytes; don't mislabel an absolute temp path as an attachment.
    assert not artifacts


def test_failed_image_write_keeps_text_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from gptme.tools import python as py

    def fail_write(*args: Any, **kwargs: Any) -> None:
        raise OSError("storage unavailable")

    monkeypatch.setattr(py.tempfile, "NamedTemporaryFile", fail_write)
    result = kernel.KernelExecResult(
        stdout="kept stdout",
        stderr="kept stderr",
        error={"ename": "ValueError", "evalue": "kept error"},
        images=[kernel.KernelRaster(raster(), "image/png", "display_data")],
    )
    backend = MagicMock()
    backend._bridge.messages = []
    backend.execute.return_value = result
    monkeypatch.setattr(kernel, "get_backend", lambda: backend)
    messages = list(py._execute_python_kernel("display(image)"))
    assert len(messages) == 1
    assert not messages[0].files
    assert messages[0].metadata is None
    assert all(
        text in messages[0].content
        for text in (
            "kept stdout",
            "kept stderr",
            "kept error",
            "Could not retain kernel image",
        )
    )


def test_conversation_raster_descriptor_uses_attachment_relative_path(
    tmp_path: Path,
) -> None:
    from gptme.logmanager import LogManager
    from gptme.tools import python as py

    with (
        LogManager.preserve_current(),
        LogManager(logdir=tmp_path / "conversation", lock=False) as manager,
    ):
        result = kernel.KernelExecResult(
            images=[kernel.KernelRaster(raster(), "image/png", "execute_result")]
        )
        files, artifacts, notices = py._kernel_image_attachments(result)
        assert not notices
        assert len(files) == 1
        assert artifacts[0]["source_type"] == "attachment"
        assert artifacts[0]["path"] == files[0].relative_to(manager.logdir).as_posix()
        assert artifacts[0]["title"] == "Kernel execute_result image"


@pytest.mark.parametrize("corruption", ["truncated", "crc"])
def test_corrupt_png_not_forwarded(corruption: str) -> None:
    payload = raster()
    if corruption == "truncated":
        payload = payload[:-12]  # Missing IEND: load() alone accepts it.
    else:
        damaged = bytearray(payload)
        position = damaged.index(b"IDAT")
        damaged[position + 4] ^= 1
        payload = bytes(damaged)
    images, notice = collect({"image/png": base64.b64encode(payload).decode()})
    assert not images
    assert notice


def test_raster_survives_log_snapshot_and_has_one_artifact(tmp_path: Path) -> None:
    from gptme.logmanager import LogManager
    from gptme.message import Message
    from gptme.server.artifacts_api import derive_artifacts
    from gptme.tools import python as py
    from gptme.util.file_storage import get_stored_path

    with (
        LogManager.preserve_current(),
        LogManager(logdir=tmp_path / "conversation", lock=False) as manager,
    ):
        payload = raster()
        files, descriptors, _ = py._kernel_image_attachments(
            kernel.KernelExecResult(
                images=[kernel.KernelRaster(payload, "image/png", "display_data")]
            )
        )
        manager.append(
            Message(
                "system",
                "kernel raster",
                files=list(files),
                metadata={"artifacts": descriptors},
            )
        )
        manager.write()
        artifacts = derive_artifacts(manager)
        assert len(artifacts) == 1
        assert artifacts[0].provenance.tool == "python"
        assert artifacts[0].source.path == descriptors[0]["path"]
        saved = manager.log.messages[-1]
        stored = get_stored_path(
            manager.logdir, saved.file_hashes[str(files[0])], ".png"
        )
        assert stored is not None and stored.read_bytes() == payload
        files[0].unlink()  # The log's content-addressed snapshot is independent.
        assert stored.read_bytes() == payload


def test_failed_cleanup_does_not_hide_cell_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gptme.tools import python as py

    class BrokenFile:
        name = str(tmp_path / "partial.png")

        def __enter__(self) -> "BrokenFile":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def write(self, data: bytes) -> None:
            raise OSError("disk full")

    def fail_unlink(*args: Any, **kwargs: Any) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(
        py.tempfile, "NamedTemporaryFile", lambda **kwargs: BrokenFile()
    )
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    files, descriptors, notices = py._kernel_image_attachments(
        kernel.KernelExecResult(
            images=[kernel.KernelRaster(raster(), "image/png", "display_data")]
        )
    )
    assert not files and not descriptors
    assert "Could not retain" in notices[-1]
