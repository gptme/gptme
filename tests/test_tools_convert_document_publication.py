"""Real-filesystem publication checks for successful LibreOffice output."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.tools.convert import DocumentToTextConverter, ToolAvailability

PAYLOAD = "Converted café 東京\r\nsecond line\n".encode()


@pytest.mark.parametrize("suffix", [".doc", ".odt", ".docx"])
@pytest.mark.parametrize(
    "cross_device", [False, True], ids=["same-device", "cross-device"]
)
@pytest.mark.parametrize("existing", [False, True], ids=["new", "overwrite"])
def test_libreoffice_publishes_exact_bytes(
    tmp_path: Path, suffix: str, cross_device: bool, existing: bool
) -> None:
    """Substitute only external extraction; exercise real publication and cleanup."""
    source = tmp_path / f"source{suffix}"
    source.write_bytes(b"unchanged source document")
    output_dirs: list[Path] = []

    def successful_extract(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess:
        output_dir = Path(command[command.index("--outdir") + 1])
        output_dirs.append(output_dir)
        (output_dir / "source.txt").write_bytes(PAYLOAD)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    if cross_device:
        shared_memory = Path("/dev/shm")
        if not shared_memory.is_dir():
            pytest.skip("no writable cross-device fixture directory")
        try:
            directory = tempfile.TemporaryDirectory(dir=shared_memory)
        except OSError:
            pytest.skip("no writable cross-device fixture directory")
    else:
        directory = tempfile.TemporaryDirectory(dir=tmp_path)
    with directory as destination_dir:
        dest = Path(destination_dir) / "output.md"
        if existing:
            dest.write_bytes(b"old output")
        # Keep converter availability isolated, including DOCX's fallback branch.
        with (
            patch(
                "gptme.tools.convert.get_availability",
                return_value=ToolAvailability(libreoffice=True),
            ),
            patch("gptme.tools.convert.subprocess.run", side_effect=successful_extract),
        ):
            result = DocumentToTextConverter().convert(source, dest)
        if (
            cross_device
            and output_dirs[0].parent.stat().st_dev == dest.parent.stat().st_dev
        ):
            pytest.skip("temporary extraction and destination share a device")
        assert result.success, result.error
        assert result.output_path == dest
        assert result.converter_used == "libreoffice"
        assert dest.read_bytes() == PAYLOAD
        assert source.read_bytes() == b"unchanged source document"
        assert output_dirs and all(not path.exists() for path in output_dirs)
        assert list(dest.parent.iterdir()) == [dest]


def test_libreoffice_publication_failure_is_not_success(tmp_path: Path) -> None:
    """Reject a missing destination parent before launching extraction."""
    source = tmp_path / "source.odt"
    source.write_bytes(b"unchanged source document")
    dest = tmp_path / "missing" / "output.txt"

    with (
        patch(
            "gptme.tools.convert.get_availability",
            return_value=ToolAvailability(libreoffice=True),
        ),
        patch("gptme.tools.convert.subprocess.run") as run,
    ):
        result = DocumentToTextConverter().convert(source, dest)
    assert not result.success
    assert result.output_path is None
    assert result.converter_used == "document-to-text"
    assert result.error and f"Could not write {dest}" in result.error
    run.assert_not_called()
    assert not dest.exists()
    assert source.read_bytes() == b"unchanged source document"
    assert set(tmp_path.iterdir()) == {source}


def test_libreoffice_partial_copy_preserves_existing_output(tmp_path: Path) -> None:
    """A failed copy must not truncate an existing destination or leave staging dirt."""
    source = tmp_path / "source.odt"
    source.write_bytes(b"unchanged source document")
    dest = tmp_path / "output.txt"
    dest.write_bytes(b"old output")
    output_dirs: list[Path] = []

    def successful_extract(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess:
        output_dir = Path(command[command.index("--outdir") + 1])
        output_dirs.append(output_dir)
        (output_dir / "source.txt").write_bytes(PAYLOAD)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def failed_copy(src: Path, target: Path) -> None:
        target.write_bytes(b"partial")
        raise OSError("copy interrupted")

    with (
        patch(
            "gptme.tools.convert.get_availability",
            return_value=ToolAvailability(libreoffice=True),
        ),
        patch("gptme.tools.convert.subprocess.run", side_effect=successful_extract),
        patch("gptme.tools.convert.shutil.copyfile", side_effect=failed_copy),
    ):
        result = DocumentToTextConverter().convert(source, dest)
    assert not result.success
    assert result.output_path is None
    assert result.error and "copy interrupted" in result.error
    assert dest.read_bytes() == b"old output"
    assert source.read_bytes() == b"unchanged source document"
    assert set(tmp_path.iterdir()) == {source, dest}
    assert output_dirs and all(not path.exists() for path in output_dirs)
