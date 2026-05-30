"""Tests for artifact descriptor emission in the computer tool (#830 Phase 2)."""

from pathlib import Path

import pytest

pytest.importorskip(
    "PIL", reason="PIL not installed, install with 'pip install pillow'"
)

from gptme.tools.computer import _make_screenshot_msg  # fmt: skip


def _write_png(path: Path) -> None:
    """Write a minimal 1x1 white PNG for testing."""
    from PIL import Image

    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    img.save(path, "PNG")


class TestMakeScreenshotMsg:
    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "ghost.png"
        assert not path.exists()
        result = _make_screenshot_msg(path)
        assert result is None

    def test_returns_message_for_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "shot.png"
        _write_png(path)
        result = _make_screenshot_msg(path)
        assert result is not None

    def test_message_has_artifact_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "shot.png"
        _write_png(path)
        result = _make_screenshot_msg(path)
        assert result is not None
        assert result.metadata is not None
        assert "artifacts" in result.metadata
        artifacts = result.metadata["artifacts"]
        assert len(artifacts) == 1

    def test_artifact_descriptor_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "shot.png"
        _write_png(path)
        result = _make_screenshot_msg(path, tool="computer")
        assert result is not None
        descriptor = result.metadata["artifacts"][0]  # type: ignore
        assert descriptor["source_type"] == "attachment"
        assert descriptor["path"] == str(path)
        assert descriptor["kind"] == "image"
        assert descriptor["mime_type"] == "image/png"
        assert descriptor["tool"] == "computer"

    def test_custom_tool_name(self, tmp_path: Path) -> None:
        path = tmp_path / "shot.png"
        _write_png(path)
        result = _make_screenshot_msg(path, tool="myrobot")
        assert result is not None
        descriptor = result.metadata["artifacts"][0]  # type: ignore
        assert descriptor["tool"] == "myrobot"

    def test_message_files_still_present(self, tmp_path: Path) -> None:
        """Artifact metadata should not displace the files attachment."""
        path = tmp_path / "shot.png"
        _write_png(path)
        result = _make_screenshot_msg(path)
        assert result is not None
        assert result.files, "message must still carry files for vision"
