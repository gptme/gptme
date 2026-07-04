"""Tests for screen-recording helpers: start_recording / record_screen (#216).

These tests are intentionally offline — they mock ffmpeg so no X11 display or
real encoder is needed.  A subprocess.Popen mock returns a process that stays
alive until stop() is called, exactly like a real ffmpeg invocation.
"""

from __future__ import annotations

import tempfile
import threading
import time
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alive_proc():
    """Return a plain MagicMock that acts like a long-running Popen process.

    Deliberately NOT spec'd against subprocess.Popen — when Popen itself is
    patched via mock.patch, passing spec=subprocess.Popen would resolve to the
    mock class object (not the real class), causing InvalidSpecError.
    """
    proc = mock.MagicMock()
    proc.returncode = None  # set the attribute so it's accessible
    _alive = [True]

    def _poll():
        return None if _alive[0] else 0

    def _terminate():
        _alive[0] = False
        proc.returncode = 0

    proc.poll.side_effect = _poll
    proc.terminate.side_effect = _terminate
    proc.wait.return_value = 0
    return proc


# ---------------------------------------------------------------------------
# ScreenRecording
# ---------------------------------------------------------------------------


class TestScreenRecording:
    def test_stop_returns_output_path(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        out = tmp_path / "test.mp4"
        proc = _make_alive_proc()
        rec = ScreenRecording(proc, out)
        result = rec.stop()
        assert result == out
        proc.terminate.assert_called_once()

    def test_stop_is_idempotent(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        proc = _make_alive_proc()
        rec = ScreenRecording(proc, tmp_path / "x.mp4")
        rec.stop()
        rec.stop()  # should not raise or call terminate a second time
        assert proc.terminate.call_count == 1

    def test_stop_is_thread_safe(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        proc = _make_alive_proc()

        def _slow_terminate():
            time.sleep(0.05)
            proc.returncode = 0

        proc.terminate.side_effect = _slow_terminate
        rec = ScreenRecording(proc, tmp_path / "thread-safe.mp4")

        start = threading.Barrier(3)
        done = threading.Barrier(3)

        def _call_stop():
            start.wait()
            rec.stop()
            done.wait()

        t1 = threading.Thread(target=_call_stop)
        t2 = threading.Thread(target=_call_stop)
        t1.start()
        t2.start()
        start.wait()
        done.wait()
        t1.join()
        t2.join()

        assert proc.terminate.call_count == 1

    def test_stop_raises_when_ffmpeg_exited_early(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        proc = mock.MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        stderr = tempfile.TemporaryFile()
        stderr.write(b"Cannot open display :99\n")
        stderr.flush()
        rec = ScreenRecording(proc, tmp_path / "failed.mp4", stderr)

        with pytest.raises(RuntimeError, match="Cannot open display"):
            rec.stop()
        proc.terminate.assert_not_called()

    def test_stop_repeats_cached_early_exit_error(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        proc = mock.MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        stderr = tempfile.TemporaryFile()
        stderr.write(b"Cannot open display :99\n")
        stderr.flush()
        rec = ScreenRecording(proc, tmp_path / "failed-twice.mp4", stderr)

        with pytest.raises(RuntimeError, match="Cannot open display"):
            rec.stop()
        with pytest.raises(RuntimeError, match="Cannot open display"):
            rec.stop()

    def test_context_manager_calls_stop(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        proc = _make_alive_proc()
        out = tmp_path / "ctx.mp4"
        with ScreenRecording(proc, out) as rec:
            assert rec.output_path == out
        proc.terminate.assert_called_once()

    def test_output_path_attribute(self, tmp_path):
        from gptme.tools.computer import ScreenRecording

        out = tmp_path / "attr.mp4"
        rec = ScreenRecording(_make_alive_proc(), out)
        assert rec.output_path == out


# ---------------------------------------------------------------------------
# start_recording — mocked ffmpeg
# ---------------------------------------------------------------------------


class TestStartRecording:
    @pytest.fixture(autouse=True)
    def _patch_which(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            yield

    @pytest.fixture(autouse=True)
    def _patch_resolution(self):
        with mock.patch(
            "gptme.tools.computer._get_display_resolution", return_value=(1024, 768)
        ):
            yield

    def test_returns_screen_recording_handle(self, tmp_path):
        from gptme.tools.computer import ScreenRecording, start_recording

        proc = _make_alive_proc()
        out = tmp_path / "rec.mp4"
        with (
            mock.patch("subprocess.Popen", return_value=proc),
            mock.patch("gptme.tools.computer._sleep"),
        ):
            rec = start_recording(output=out)
        assert isinstance(rec, ScreenRecording)
        assert rec.output_path == out

    def test_default_output_in_tmpdir(self, tmp_path):
        from gptme.tools.computer import start_recording

        proc = _make_alive_proc()
        with (
            mock.patch("subprocess.Popen", return_value=proc),
            mock.patch("gptme.tools.computer._sleep"),
        ):
            rec = start_recording()
        assert rec.output_path.suffix == ".mp4"
        rec.stop()

    def test_ffmpeg_not_found_raises(self, tmp_path):
        from gptme.tools.computer import start_recording

        with (
            mock.patch("shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="ffmpeg not found"),
        ):
            start_recording(output=tmp_path / "x.mp4")

    def test_ffmpeg_exits_immediately_raises(self, tmp_path):
        from gptme.tools.computer import start_recording

        proc = mock.MagicMock()
        proc.poll.return_value = 1  # non-None = already exited
        proc.returncode = 1
        with (
            mock.patch("subprocess.Popen", return_value=proc),
            mock.patch("gptme.tools.computer._sleep"),
            pytest.raises(RuntimeError, match="ffmpeg exited immediately"),
        ):
            start_recording(output=tmp_path / "fail.mp4")

    def test_ffmpeg_immediate_exit_includes_stderr(self, tmp_path):
        from gptme.tools.computer import start_recording

        proc = mock.MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1

        def _fake_popen(cmd, **kwargs):
            kwargs["stderr"].write(b"No such display\n")
            kwargs["stderr"].flush()
            return proc

        with (
            mock.patch("subprocess.Popen", side_effect=_fake_popen),
            mock.patch("gptme.tools.computer._sleep"),
            pytest.raises(RuntimeError, match="No such display"),
        ):
            start_recording(output=tmp_path / "fail.mp4")

    def test_startup_interrupt_terminates_proc(self, tmp_path):
        """If _sleep(0.3) is interrupted (e.g. KeyboardInterrupt), proc must be cleaned up."""
        from gptme.tools.computer import start_recording

        proc = _make_alive_proc()

        with (
            mock.patch("subprocess.Popen", return_value=proc),
            mock.patch(
                "gptme.tools.computer._sleep",
                side_effect=KeyboardInterrupt,
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            start_recording(output=tmp_path / "int.mp4")

        proc.terminate.assert_called_once()

    def test_fps_forwarded_in_cmd(self, tmp_path):
        from gptme.tools.computer import start_recording

        captured_cmd: list[list[str]] = []

        def _fake_popen(cmd, **kwargs):
            captured_cmd.append(cmd)
            return _make_alive_proc()

        with (
            mock.patch("subprocess.Popen", side_effect=_fake_popen),
            mock.patch("gptme.tools.computer._sleep"),
        ):
            rec = start_recording(output=tmp_path / "fps.mp4", fps=5)
        rec.stop()
        cmd = captured_cmd[0]
        assert "5" in cmd  # fps value appears in the ffmpeg command

    def test_display_used_on_linux(self, tmp_path):
        import platform

        from gptme.tools.computer import start_recording

        captured_cmd: list[list[str]] = []

        def _fake_popen(cmd, **kwargs):
            captured_cmd.append(cmd)
            return _make_alive_proc()

        with (
            mock.patch("subprocess.Popen", side_effect=_fake_popen),
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch.object(platform, "system", return_value="Linux"),
            mock.patch("gptme.tools.computer.IS_MACOS", False),
        ):
            rec = start_recording(output=tmp_path / "disp.mp4", display=":99")
        rec.stop()
        cmd = captured_cmd[0]
        assert ":99" in cmd


# ---------------------------------------------------------------------------
# record_screen — synchronous wrapper
# ---------------------------------------------------------------------------


class TestRecordScreen:
    @pytest.fixture(autouse=True)
    def _patch_which(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            yield

    @pytest.fixture(autouse=True)
    def _patch_resolution(self):
        with mock.patch(
            "gptme.tools.computer._get_display_resolution", return_value=(1024, 768)
        ):
            yield

    def test_returns_path_to_file(self, tmp_path):
        from gptme.tools.computer import record_screen

        proc = _make_alive_proc()
        out = tmp_path / "sync.mp4"
        with (
            mock.patch("subprocess.Popen", return_value=proc),
            mock.patch("gptme.tools.computer._sleep"),
        ):
            result = record_screen(output=out, duration=1.0)
        assert result == out

    def test_sleeps_for_duration(self, tmp_path):
        from gptme.tools.computer import record_screen

        sleep_calls: list[float] = []

        with (
            mock.patch("subprocess.Popen", return_value=_make_alive_proc()),
            mock.patch(
                "gptme.tools.computer._sleep",
                side_effect=lambda d: sleep_calls.append(d),
            ),
        ):
            record_screen(output=tmp_path / "dur.mp4", duration=7.5)

        # _sleep is called once during Popen startup check (0.3s) and once for duration
        duration_calls = [d for d in sleep_calls if d == 7.5]
        assert duration_calls, f"Expected a 7.5s sleep; got {sleep_calls}"

    def test_stops_recording_when_sleep_raises(self, tmp_path):
        from gptme.tools.computer import record_screen

        proc = _make_alive_proc()

        def _sleep_or_interrupt(delay):
            if delay == 7.5:
                raise KeyboardInterrupt

        with (
            mock.patch("subprocess.Popen", return_value=proc),
            mock.patch("gptme.tools.computer._sleep", side_effect=_sleep_or_interrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            record_screen(output=tmp_path / "interrupted.mp4", duration=7.5)

        proc.terminate.assert_called_once()

    def test_ffmpeg_not_found_raises(self, tmp_path):
        from gptme.tools.computer import record_screen

        with (
            mock.patch("shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="ffmpeg not found"),
        ):
            record_screen(output=tmp_path / "x.mp4")


# ---------------------------------------------------------------------------
# _ffmpeg_record_cmd — unit tests for command construction
# ---------------------------------------------------------------------------


class TestFfmpegRecordCmd:
    def test_linux_uses_x11grab(self, tmp_path):
        from gptme.tools.computer import _ffmpeg_record_cmd

        with mock.patch("gptme.tools.computer.IS_MACOS", False):
            cmd = _ffmpeg_record_cmd(
                tmp_path / "out.mp4",
                fps=10,
                duration=None,
                display=":1",
                width=1024,
                height=768,
            )
        assert "x11grab" in cmd
        assert "avfoundation" not in cmd

    def test_duration_added_when_given(self, tmp_path):
        from gptme.tools.computer import _ffmpeg_record_cmd

        cmd = _ffmpeg_record_cmd(
            tmp_path / "out.mp4",
            fps=10,
            duration=30.0,
            display=":1",
            width=1024,
            height=768,
        )
        assert "-t" in cmd
        idx = cmd.index("-t")
        assert cmd[idx + 1] == "30.0"

    def test_no_duration_flag_when_none(self, tmp_path):
        from gptme.tools.computer import _ffmpeg_record_cmd

        cmd = _ffmpeg_record_cmd(
            tmp_path / "out.mp4",
            fps=10,
            duration=None,
            display=":1",
            width=1024,
            height=768,
        )
        assert "-t" not in cmd

    def test_output_path_is_last_arg(self, tmp_path):
        from gptme.tools.computer import _ffmpeg_record_cmd

        out = tmp_path / "last.mp4"
        cmd = _ffmpeg_record_cmd(
            out, fps=10, duration=5.0, display=":1", width=1024, height=768
        )
        assert cmd[-1] == str(out)

    def test_faststart_flag_included(self, tmp_path):
        from gptme.tools.computer import _ffmpeg_record_cmd

        cmd = _ffmpeg_record_cmd(
            tmp_path / "out.mp4",
            fps=10,
            duration=None,
            display=":1",
            width=1024,
            height=768,
        )
        assert "+faststart" in " ".join(cmd)

    def test_macos_uses_avfoundation(self, tmp_path):
        import platform

        from gptme.tools.computer import _ffmpeg_record_cmd

        with (
            mock.patch.object(platform, "system", return_value="Darwin"),
            mock.patch("gptme.tools.computer.IS_MACOS", True),
        ):
            cmd = _ffmpeg_record_cmd(
                tmp_path / "out.mp4",
                fps=10,
                duration=None,
                display=":1",
                width=1024,
                height=768,
            )
        assert "avfoundation" in cmd
        assert "x11grab" not in cmd


# ---------------------------------------------------------------------------
# CLI — gptme-util computer record
# ---------------------------------------------------------------------------


class TestRecordCLI:
    """Tests for `gptme-util computer record` command."""

    def test_help_text_present(self):
        from click.testing import CliRunner

        from gptme.cli.cmd_computer import record_cmd

        runner = CliRunner()
        result = runner.invoke(record_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--duration" in result.output
        assert "--fps" in result.output

    def test_ffmpeg_not_found_exits_1(self, monkeypatch):
        from click.testing import CliRunner

        from gptme.cli.cmd_computer import record_cmd

        monkeypatch.setattr("shutil.which", lambda _: None)
        runner = CliRunner()
        result = runner.invoke(record_cmd, ["--duration", "1"])
        assert result.exit_code == 1
        assert "ffmpeg not found" in result.output

    def test_success_prints_path(self, tmp_path, monkeypatch):
        from pathlib import Path as _Path

        from click.testing import CliRunner

        from gptme.cli.cmd_computer import record_cmd

        out = tmp_path / "out.mp4"
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

        def fake_record_screen(**kwargs):
            return _Path(kwargs.get("output") or str(out))

        monkeypatch.setattr("gptme.tools.computer.record_screen", fake_record_screen)

        runner = CliRunner()
        result = runner.invoke(record_cmd, [str(out), "--duration", "2"])
        assert result.exit_code == 0
        assert str(out) in result.output

    def test_invalid_duration_exits_1(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from gptme.cli.cmd_computer import record_cmd

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
        runner = CliRunner()
        result = runner.invoke(record_cmd, ["--duration", "-5"])
        assert result.exit_code == 1
        assert "duration" in result.output.lower()

    def test_invalid_fps_exits_1(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from gptme.cli.cmd_computer import record_cmd

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
        runner = CliRunner()
        result = runner.invoke(record_cmd, ["--fps", "0", "--duration", "1"])
        assert result.exit_code == 1
        assert "fps" in result.output.lower()
