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
