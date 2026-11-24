from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from gptme.cli.util import main


def test_sound_list():
    result = CliRunner().invoke(main, ["sound", "list"])
    assert result.exit_code == 0
    assert "bell" in result.output.split()


def test_sound_play_unknown():
    result = CliRunner().invoke(main, ["sound", "play", "nonexistent"])
    assert result.exit_code == 2
    assert "unknown sound" in result.output


def test_sound_play_rejects_path_traversal(tmp_path):
    """sound play only accepts bundled sound names, never traversal or arbitrary paths (#5214)."""
    import os

    from gptme.util.sound import media_path

    evil_dir = tmp_path / "x"
    evil_dir.mkdir()
    evil_wav = evil_dir / "evil.wav"
    evil_wav.write_bytes((media_path / "bell.wav").read_bytes())

    rel_traversal = os.path.relpath(evil_wav.with_suffix(""), media_path)
    abs_target = str(evil_wav.with_suffix(""))

    with patch("gptme.cli.cmd_sound._play", return_value=True) as play_mock:
        for bad_name in (rel_traversal, abs_target, "../bell", "sub/bell", "bell\x00"):
            result = CliRunner().invoke(main, ["sound", "play", bad_name, "--no-bell"])
            assert result.exit_code == 2, (bad_name, result.output)
            assert "unknown sound" in result.output
            assert "available:" in result.output
            assert "bell" in result.output
        play_mock.assert_not_called()

        ok = CliRunner().invoke(main, ["sound", "play", "bell", "--no-bell"])
        assert ok.exit_code == 0, ok.output
        play_mock.assert_called_once()
        assert play_mock.call_args.args[0] == media_path / "bell.wav"


def test_sound_ding_plays_bell():
    with (
        patch("gptme.cli.cmd_sound._ring_terminal_bell") as ring,
        patch("gptme.cli.cmd_sound._play", return_value=True) as play,
    ):
        result = CliRunner().invoke(main, ["sound", "ding"])
    assert result.exit_code == 0
    ring.assert_called_once()
    assert play.call_args.args[0].name == "bell.wav"


def test_sound_ding_missing_bell_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("gptme.util.sound.media_path", tmp_path)
    result = CliRunner().invoke(main, ["sound", "ding"])
    assert result.exit_code == 1
    assert "Bell sound file not found" in result.output


def test_sound_ding_play_failure_exits():
    with (
        patch("gptme.cli.cmd_sound._ring_terminal_bell"),
        patch("gptme.cli.cmd_sound._play", return_value=False),
    ):
        result = CliRunner().invoke(main, ["sound", "ding"])
    assert result.exit_code == 1
    assert "No audio player available" in result.output


# Distribution payloads are checked by test_build_distribution_roundtrip,
# which builds and inspects real wheels rather than matching backend syntax.


def test_afplay_volume_args(tmp_path):
    from gptme.util import _sound_cmd

    wav = tmp_path / "x.wav"
    wav.touch()
    with (
        patch.object(
            _sound_cmd.shutil, "which", side_effect=lambda c: c == "afplay" or None
        ),
        patch.object(_sound_cmd.subprocess, "run") as run,
    ):
        run.return_value.returncode = 0
        assert _sound_cmd.play_with_system_command_blocking(wav, 0.7)
    assert run.call_args.args[0] == ["afplay", "-v", "0.7", str(wav)]


def test_play_fails_without_any_player(tmp_path):
    from gptme.cli.cmd_sound import _play

    with (
        patch(
            "gptme.util._sound_cmd.play_with_system_command_blocking",
            return_value=False,
        ),
        patch(
            "gptme.util._sound_sounddevice.is_sounddevice_available",
            return_value=False,
        ),
    ):
        assert not _play(tmp_path / "x.wav")


def test_afplay_full_volume_has_no_volume_arg(tmp_path):
    from gptme.util import _sound_cmd

    wav = tmp_path / "x.wav"
    with (
        patch.object(
            _sound_cmd.shutil, "which", side_effect=lambda c: c == "afplay" or None
        ),
        patch.object(_sound_cmd.subprocess, "run") as run,
    ):
        run.return_value.returncode = 0
        assert _sound_cmd.play_with_system_command_blocking(wav, 1.0)
    assert run.call_args.args[0] == ["afplay", str(wav)]


def test_falls_through_to_next_player_on_failure(tmp_path):
    from gptme.util import _sound_cmd

    wav = tmp_path / "x.wav"
    with (
        patch.object(_sound_cmd.shutil, "which", return_value="/usr/bin/x"),
        patch.object(_sound_cmd.subprocess, "run") as run,
    ):
        failed, ok = MagicMock(returncode=1, stderr=b""), MagicMock(returncode=0)
        run.side_effect = [failed, ok]
        assert _sound_cmd.play_with_system_command_blocking(wav, 1.0)
    assert [c.args[0][0] for c in run.call_args_list] == ["afplay", "paplay"]


def test_play_reports_sounddevice_failure(tmp_path):
    """A failing sounddevice fallback must not report success (#5152)."""
    from gptme.cli.cmd_sound import _play

    with (
        patch(
            "gptme.util._sound_cmd.play_with_system_command_blocking",
            return_value=False,
        ),
        patch(
            "gptme.util._sound_sounddevice.is_sounddevice_available",
            return_value=True,
        ),
        patch(
            "gptme.util._sound_sounddevice.load_wav_file",
            return_value=(44100, [0.0]),
        ),
        patch(
            "gptme.util._sound_sounddevice.play_with_sounddevice",
            side_effect=RuntimeError("PortAudio error"),
        ) as play,
    ):
        assert not _play(tmp_path / "x.wav", 0.5)
    play.assert_called_once_with([0.0], 44100, 0.5)


def test_play_reports_sounddevice_success(tmp_path):
    from gptme.cli.cmd_sound import _play

    with (
        patch(
            "gptme.util._sound_cmd.play_with_system_command_blocking",
            return_value=False,
        ),
        patch(
            "gptme.util._sound_sounddevice.is_sounddevice_available",
            return_value=True,
        ),
        patch(
            "gptme.util._sound_sounddevice.load_wav_file",
            return_value=(44100, [0.0]),
        ),
        patch("gptme.util._sound_sounddevice.play_with_sounddevice") as play,
    ):
        assert _play(tmp_path / "x.wav")
    play.assert_called_once()


def test_sounddevice_timeout_is_reported(monkeypatch):
    """A playback that stalls past the timeout raises instead of returning normally."""
    import pytest

    from gptme.util import _sound_sounddevice as sdmod

    sd = MagicMock()
    sd.get_stream.return_value.active = True  # never finishes
    monkeypatch.setattr(sdmod, "_load_audio_libs", lambda: {"sd": sd})
    monkeypatch.setattr(sdmod, "convert_audio_to_float32", lambda d: d)
    monkeypatch.setattr(sdmod, "get_output_device", lambda: (None, 44100))
    # Replace only the module's `time` binding: patching the global time module
    # would also feed this fake clock to logging (LogRecord calls time.time()).
    clock = iter([0.0, 31.0])
    fake_time = SimpleNamespace(time=lambda: next(clock), sleep=lambda s: None)
    monkeypatch.setattr(sdmod, "time", fake_time)

    with pytest.raises(TimeoutError):
        sdmod.play_with_sounddevice(1.0, 44100, 1.0)
    sd.stop.assert_called()
