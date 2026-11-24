"""Play gptme's notification sounds from the command line.

Lets other harnesses reuse gptme's turn-complete ding. For example, a Claude
Code ``Stop`` hook can run ``gptme-util sound ding`` so a finished turn
sounds the same whether it came from gptme or Claude Code.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path

# Same default as gptme's own playback (gptme.util.sound.current_volume)
DEFAULT_VOLUME = 0.7

volume_option = click.option(
    "--volume",
    type=click.FloatRange(0.0, 1.0),
    default=DEFAULT_VOLUME,
    show_default=True,
    help="Playback volume.",
)


def _sound_names() -> list[str]:
    from ..util.sound import media_path

    return sorted(p.stem for p in media_path.glob("*.wav") if p.is_file())


def _ring_terminal_bell() -> None:
    """Ring the bell on the controlling terminal.

    Hooks run with stdout captured, so writing ``\\a`` to stdout would never
    reach the terminal; prefer ``/dev/tty`` and fall back to stderr.
    """
    try:
        with open("/dev/tty", "w") as tty:
            tty.write("\a")
            return
    except OSError:
        pass
    sys.stderr.write("\a")
    sys.stderr.flush()


def _play(path: Path, volume: float = DEFAULT_VOLUME) -> bool:
    """Play a sound file to completion (the process exits right after)."""
    from ..util._sound_cmd import play_with_system_command_blocking
    from ..util._sound_sounddevice import (
        is_sounddevice_available,
        load_wav_file,
        play_with_sounddevice,
    )

    if play_with_system_command_blocking(path, volume):
        return True
    if not is_sounddevice_available():
        return False
    # Play in this thread rather than through gptme's background playback
    # queue: the queue logs and swallows failures, which made a failed
    # sounddevice fallback exit 0 (#5152).
    wav = load_wav_file(path)
    if wav is None:
        return False
    sample_rate, data = wav
    try:
        play_with_sounddevice(data, sample_rate, volume)
    except Exception:
        return False  # play_with_sounddevice already logged the error
    return True


@click.group()
def sound() -> None:
    """Play gptme notification sounds (e.g. from other harnesses' hooks)."""


@sound.command("play")
@click.argument("name", default="bell")
@click.option("--bell/--no-bell", default=False, help="Also ring the terminal bell.")
@volume_option
def play(name: str, bell: bool, volume: float) -> None:
    """Play a bundled sound by NAME (default: bell). See `sound list`."""
    from ..util.sound import media_path

    available = _sound_names()
    if name not in available:
        listed = ", ".join(available) or "(none)"
        raise click.BadParameter(
            f"unknown sound {name!r}, available: {listed}",
            param_hint="NAME",
        )
    path = media_path / f"{name}.wav"
    if bell:
        _ring_terminal_bell()
    if not _play(path, volume):
        click.echo(
            "No audio player available or playback failed "
            "(tried afplay/paplay/ffplay, then sounddevice)",
            err=True,
        )
        sys.exit(1)


@sound.command("ding")
@volume_option
def ding(volume: float) -> None:
    """Ring the terminal bell and play the ding, like gptme does after a turn."""
    from ..util.sound import media_path

    path = media_path / "bell.wav"
    _ring_terminal_bell()
    if not path.exists():
        available = ", ".join(_sound_names()) or "(none)"
        click.echo(
            f"Bell sound file not found: {path}. Available: {available}",
            err=True,
        )
        sys.exit(1)
    if not _play(path, volume):
        click.echo(
            "No audio player available or playback failed "
            "(tried afplay/paplay/ffplay, then sounddevice)",
            err=True,
        )
        sys.exit(1)


@sound.command("list")
def list_sounds() -> None:
    """List bundled sounds."""
    for name in _sound_names():
        click.echo(name)
