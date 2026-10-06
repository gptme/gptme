from unittest.mock import patch

from click.testing import CliRunner

from gptme.cli.util import main


def test_sound_list():
    result = CliRunner().invoke(main, ["sound", "list"])
    assert result.exit_code == 0
    assert "bell" in result.output.split()


def test_sound_play_unknown():
    result = CliRunner().invoke(main, ["sound", "play", "nonexistent"])
    assert result.exit_code != 0
    assert "unknown sound" in result.output


def test_sound_ding_plays_bell():
    with (
        patch("gptme.cli.cmd_sound._ring_terminal_bell") as ring,
        patch("gptme.cli.cmd_sound._play", return_value=True) as play,
    ):
        result = CliRunner().invoke(main, ["sound", "ding"])
    assert result.exit_code == 0
    ring.assert_called_once()
    assert play.call_args.args[0].name == "bell.wav"


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
