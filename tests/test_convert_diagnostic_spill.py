"""Keep converter failure diagnostics recoverable before shortening them."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gptme.tools import convert


@pytest.mark.parametrize("tail", [False, True])
def test_large_diagnostic_is_saved_before_preview(tmp_path: Path, tail: bool) -> None:
    text = "BEGIN\n" + "x" * 2000 + "\nEND"
    manager = SimpleNamespace(logdir=tmp_path)
    with patch("gptme.logmanager.LogManager.get_current_log", return_value=manager):
        preview = convert._diagnostic_preview(text.encode(), "ffmpeg", 300, tail=tail)
    saved = list((tmp_path / "tool-outputs" / "convert").glob("*.txt"))
    assert len(saved) == 1
    assert text in saved[0].read_text()
    assert str(saved[0]) in preview
    assert (text[-300:] if tail else text[:300]) in preview
    assert "diagnostic characters omitted" in preview
    assert "full stderr saved to" in preview


def test_short_diagnostic_is_unchanged(tmp_path: Path) -> None:
    with patch("gptme.logmanager.LogManager.get_current_log") as current:
        assert (
            convert._diagnostic_preview(b"failed\xff", "ffmpeg", 300) == "failed\ufffd"
        )
    current.assert_not_called()
    assert not (tmp_path / "tool-outputs").exists()


@pytest.mark.parametrize("save_fails", [False, True])
def test_unavailable_storage_still_bounds_diagnostic(
    tmp_path: Path, save_fails: bool
) -> None:
    manager = SimpleNamespace(logdir=tmp_path) if save_fails else None
    with (
        patch("gptme.logmanager.LogManager.get_current_log", return_value=manager),
        patch(
            "gptme.util.output_storage.save_large_output", side_effect=OSError("full")
        ),
    ):
        preview = convert._diagnostic_preview(b"x" * 2000, "ffmpeg", 300)
    assert preview.startswith("x" * 300)
    assert "1700 diagnostic characters omitted" in preview
    assert "saved to" not in preview
    assert len(preview) < 400


@pytest.mark.skipif(sys.platform == "win32", reason="executable script fixture")
def test_video_failure_retains_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "video.mp4"
    src.write_bytes(b"fixture")
    diagnostic = b"BEGIN\n" + b"x" * 2000 + b"\nEND"
    fake_ffmpeg = tmp_path / "ffmpeg"
    fake_ffmpeg.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stderr.buffer.write({diagnostic!r})\nsys.exit(2)\n"
    )
    fake_ffmpeg.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    with (
        patch(
            "gptme.tools.convert.get_availability",
            return_value=SimpleNamespace(ffmpeg=True),
        ),
        patch(
            "gptme.logmanager.LogManager.get_current_log",
            return_value=SimpleNamespace(logdir=tmp_path / "conversation"),
        ),
    ):
        result = convert.VideoThumbnailConverter().convert(src, tmp_path / "frame.png")
    assert not result.success
    assert result.error is not None
    assert "full stderr saved to" in result.error
    saved = list((tmp_path / "conversation" / "tool-outputs" / "convert").glob("*.txt"))
    assert len(saved) == 1
    assert diagnostic.decode() in saved[0].read_text()


@pytest.mark.parametrize("diagnostic_size", [20, 2000])
@pytest.mark.parametrize("has_logdir", [False, True])
def test_libreoffice_failure_exposes_diagnostic_in_summary(
    tmp_path: Path, diagnostic_size: int, has_logdir: bool
) -> None:
    src = tmp_path / "document.doc"
    src.write_bytes(b"fixture")
    diagnostic = b"BEGIN\n" + b"x" * diagnostic_size + b"\nEND"
    logdir = tmp_path / "conversation"
    with (
        patch(
            "gptme.tools.convert.get_availability",
            return_value=SimpleNamespace(python_docx=False, libreoffice=True),
        ),
        patch(
            "gptme.tools.convert.subprocess.run",
            return_value=SimpleNamespace(returncode=2, stderr=diagnostic),
        ),
        patch(
            "gptme.logmanager.LogManager.get_current_log",
            return_value=SimpleNamespace(logdir=logdir) if has_logdir else None,
        ),
    ):
        result = convert.DocumentToTextConverter().convert(src, tmp_path / "out.txt")
    assert not result.success
    summary = result.summary()
    assert "libreoffice failed: " in summary
    assert diagnostic.decode()[:200] in summary
    assert "No document" not in summary
    saved = list((logdir / "tool-outputs" / "convert").glob("*.txt"))
    if diagnostic_size > 200:
        assert "diagnostic characters omitted" in summary
        if has_logdir:
            assert len(saved) == 1
            assert str(saved[0]) in summary
            assert diagnostic.decode() in saved[0].read_text()
        else:
            assert "saved to" not in summary
    else:
        assert "omitted" not in summary
    assert len(saved) == int(has_logdir and diagnostic_size > 200)


def test_exact_limit_does_not_spill() -> None:
    with patch("gptme.logmanager.LogManager.get_current_log") as current:
        assert convert._diagnostic_preview(b"x" * 300, "ffmpeg", 300) == "x" * 300
    current.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX private mode")
def test_diagnostics_are_private_and_conversation_scoped(tmp_path: Path) -> None:
    previews = []
    for name in ("first", "second"):
        root = tmp_path / name
        with patch(
            "gptme.logmanager.LogManager.get_current_log",
            return_value=SimpleNamespace(logdir=root),
        ):
            previews.append(convert._diagnostic_preview(b"x" * 2000, "ffmpeg", 300))
        saved = list((root / "tool-outputs" / "convert").glob("*.txt"))
        assert len(saved) == 1
        assert saved[0].stat().st_mode & 0o777 == 0o600
        assert saved[0].parent.stat().st_mode & 0o777 == 0o700
        assert str(saved[0]) in previews[-1]
    assert str(tmp_path / "second") not in previews[0]
    assert str(tmp_path / "first") not in previews[1]
