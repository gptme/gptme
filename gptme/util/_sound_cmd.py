"""
Command-based audio playback using system audio players like paplay and ffplay.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)


class AudioPlayer(TypedDict):
    cmd: str
    args: list[str]


# Available system audio players (in order of preference)
AUDIO_PLAYERS: list[AudioPlayer] = [
    {"cmd": "paplay", "args": []},  # PulseAudio player
    {
        "cmd": "ffplay",
        "args": ["-nodisp", "-autoexit", "-loglevel", "quiet"],
    },  # FFmpeg player
]


def is_cmd_audio_available() -> bool:
    """Check if audio playback is available via system command-line tools."""
    for player in AUDIO_PLAYERS:
        if shutil.which(player["cmd"]):
            return True
    return False


def play_with_system_command_blocking(file_path: Path, volume: float = 1.0) -> bool:
    """Play audio file using system commands (blocking call for background thread).

    Args:
        file_path: Path to the audio file
        volume: Volume level (0.0 to 1.0)

    Returns:
        True if successfully played, False if all system players failed
    """
    for player in AUDIO_PLAYERS:
        if not shutil.which(player["cmd"]):
            continue

        try:
            cmd = [player["cmd"]] + player["args"]

            # Add volume control if supported
            if player["cmd"] == "paplay" and volume != 1.0:
                # paplay uses --volume (0-65536, where 65536 = 100%)
                vol_arg = str(int(volume * 65536))
                cmd.extend(["--volume", vol_arg])
            elif player["cmd"] == "ffplay" and volume != 1.0:
                # ffplay uses -volume (0-100)
                vol_arg = str(int(volume * 100))
                cmd.extend(["-volume", vol_arg])

            cmd.append(str(file_path))

            log.debug(f"Playing audio with {player['cmd']}: {' '.join(cmd)}")

            # Run the command with timeout (blocking in background thread)
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60,  # 60 second timeout, to account for long TTS outputs
                check=False,
            )

            if result.returncode == 0:
                log.debug(f"Successfully played audio with {player['cmd']}")
                return True
            else:
                log.debug(
                    f"{player['cmd']} failed with return code {result.returncode}"
                )
                if result.stderr:
                    stderr = result.stderr.decode().strip()
                    if stderr:
                        log.debug(f"{player['cmd']} stderr: {stderr}")

        except subprocess.TimeoutExpired:
            log.warning(f"Audio playback with {player['cmd']} timed out")
        except Exception as e:
            log.debug(f"Failed to play audio with {player['cmd']}: {e}")

    return False


def stop_system_audio():
    """Stop any running subprocess audio players."""
    try:
        subprocess.run(["pkill", "-f", "paplay"], capture_output=True, timeout=2)
        subprocess.run(["pkill", "-f", "ffplay"], capture_output=True, timeout=2)
    except Exception:
        pass
