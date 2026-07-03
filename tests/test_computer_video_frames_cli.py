"""Tests for gptme-util computer video-frames (cmd_computer.py).

Unit-tests the video-frames CLI command without requiring ffmpeg or a real
video file.  All subprocess calls and filesystem checks are monkey-patched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_computer import video_frames_cmd


def _make_fake_ffmpeg(out_dir: Path, n_frames: int = 3):
    """Return a fake ffmpeg run() that creates N PNG files in the output dir."""

    def fake_run(cmd, **kwargs):
        # Determine output dir from the pattern argument (last arg)
        pattern = cmd[-1]  # e.g. /tmp/gptme-video-frames-xxx/frame_%04d.png
        out = Path(pattern).parent
        for i in range(1, n_frames + 1):
            (out / f"frame_{i:04d}.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
            )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return fake_run


class TestVideoFramesCmd:
    """Tests for `gptme-util computer video-frames`."""

    def test_help_text(self):
        runner = CliRunner()
        result = runner.invoke(video_frames_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--fps" in result.output
        assert "--limit" in result.output
        assert "--output-dir" in result.output
        assert "--json" in result.output

    def test_basic_extraction_prints_frame_paths(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "frames"

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=_make_fake_ffmpeg(out_dir, n_frames=3)),
        ):
            result = runner.invoke(
                video_frames_cmd,
                ["--output-dir", str(out_dir), str(video)],
            )

        assert result.exit_code == 0, result.output
        lines = [ln for ln in result.output.splitlines() if ln]
        assert len(lines) == 3
        assert all("frame_" in ln for ln in lines)

    def test_json_output_structure(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "frames"

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=_make_fake_ffmpeg(out_dir, n_frames=2)),
        ):
            result = runner.invoke(
                video_frames_cmd,
                ["--output-dir", str(out_dir), "--json", str(video)],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["count"] == 2
        assert data["fps"] == 1.0
        assert len(data["frames"]) == 2

    def test_fps_passed_to_ffmpeg(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "frames"
        captured_cmd: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            out = Path(cmd[-1]).parent
            (out / "frame_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(
                video_frames_cmd,
                ["--output-dir", str(out_dir), "--fps", "0.5", str(video)],
            )

        assert result.exit_code == 0, result.output
        assert any("fps=0.5" in arg for arg in captured_cmd[0])

    def test_limit_passed_to_ffmpeg(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "frames"
        captured_cmd: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            out = Path(cmd[-1]).parent
            (out / "frame_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(
                video_frames_cmd,
                ["--output-dir", str(out_dir), "--limit", "7", str(video)],
            )

        assert result.exit_code == 0, result.output
        assert "-frames:v" in captured_cmd[0]
        assert "7" in captured_cmd[0]

    def test_missing_ffmpeg_exits_with_error(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")

        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(video_frames_cmd, [str(video)])

        assert result.exit_code != 0
        assert "ffmpeg" in result.output
        assert "apt install" in result.output

    def test_missing_input_file_exits_with_error(self, tmp_path):
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = runner.invoke(
                video_frames_cmd, [str(tmp_path / "nonexistent.mp4")]
            )

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_ffmpeg_failure_exits_with_error(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")

        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, b"", b"Invalid data found")

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(video_frames_cmd, [str(video)])

        assert result.exit_code != 0
        assert "ffmpeg failed" in result.output

    def test_ffmpeg_timeout_exits_with_error(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 120)

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(video_frames_cmd, [str(video)])

        assert result.exit_code != 0
        assert "timed out" in result.output

    def test_no_frames_extracted_exits_with_error(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "frames"

        def fake_run(cmd, **kwargs):
            # ffmpeg "succeeds" but produces no PNG files
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(
                video_frames_cmd, ["--output-dir", str(out_dir), str(video)]
            )

        assert result.exit_code != 0
        assert "no frames" in result.output

    def test_invalid_fps_exits_with_error(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")

        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = runner.invoke(video_frames_cmd, ["--fps", "-1", str(video)])

        assert result.exit_code != 0
        assert "fps" in result.output.lower()

    def test_invalid_limit_exits_with_error(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")

        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = runner.invoke(video_frames_cmd, ["--limit", "0", str(video)])

        assert result.exit_code != 0
        assert "limit" in result.output.lower()

    def test_output_dir_created_if_missing(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "nested" / "frames"

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=_make_fake_ffmpeg(out_dir, n_frames=1)),
        ):
            result = runner.invoke(
                video_frames_cmd, ["--output-dir", str(out_dir), str(video)]
            )

        assert result.exit_code == 0, result.output
        assert out_dir.exists()

    def test_default_tempdir_used_when_no_output_dir(self, tmp_path):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")

        created_dirs: list[Path] = []

        def fake_mkdtemp(prefix=""):
            d = tmp_path / "tmp-frames"
            d.mkdir()
            created_dirs.append(d)
            return str(d)

        def fake_run(cmd, **kwargs):
            out = Path(cmd[-1]).parent
            (out / "frame_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.run", side_effect=fake_run),
            patch("tempfile.mkdtemp", side_effect=fake_mkdtemp),
        ):
            result = runner.invoke(video_frames_cmd, [str(video)])

        assert result.exit_code == 0, result.output
        assert len(created_dirs) == 1

    @pytest.mark.parametrize("n_frames", [1, 5, 10])
    def test_json_count_matches_extracted_frames(self, tmp_path, n_frames):
        video = tmp_path / "rec.mp4"
        video.write_bytes(b"fake-mp4")
        out_dir = tmp_path / "frames"

        runner = CliRunner()
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "subprocess.run",
                side_effect=_make_fake_ffmpeg(out_dir, n_frames=n_frames),
            ),
        ):
            result = runner.invoke(
                video_frames_cmd,
                ["--output-dir", str(out_dir), "--json", str(video)],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["count"] == n_frames
