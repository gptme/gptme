"""
Sound utility for playing audio files and system sounds.

Extracts core audio playback functionality for reuse across TTS and UI sounds.
"""

import logging
import os
import platform as platform_module
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, TypedDict

from ..config import get_config


class AudioPlayer(TypedDict):
    cmd: str
    args: list[str]


log = logging.getLogger(__name__)

# Check for audio dependencies
has_audio_imports = False
try:
    import numpy as np  # fmt: skip
    import scipy.io.wavfile as wavfile  # fmt: skip
    import scipy.signal as signal  # fmt: skip
    import sounddevice as sd  # fmt: skip

    has_audio_imports = True
except (ImportError, OSError):
    # sounddevice may throw OSError("PortAudio library not found")
    has_audio_imports = False

# Audio playback state
audio_queue: queue.Queue[tuple[Any, int] | None] = queue.Queue()
playback_thread: threading.Thread | None = None
current_volume = 0.7

media_path = Path(__file__).parent.parent.parent / "media"

# Available system audio players (in order of preference)
AUDIO_PLAYERS: list[AudioPlayer] = [
    {"cmd": "paplay", "args": []},  # PulseAudio player
    {
        "cmd": "ffplay",
        "args": ["-nodisp", "-autoexit", "-loglevel", "quiet"],
    },  # FFmpeg player
]


def is_audio_available() -> bool:
    """Check if audio playback is available via system tools or sounddevice."""
    # Check if any system audio player is available
    for player in AUDIO_PLAYERS:
        if shutil.which(player["cmd"]):
            return True

    # Fall back to checking sounddevice
    return has_audio_imports


def _check_device_override(devices) -> tuple[int, int] | None:
    """Check for environment variable device override."""
    if device_override := os.getenv("GPTME_AUDIO_DEVICE"):
        try:
            device_index = int(device_override)
            if 0 <= device_index < len(devices):
                device_info = sd.query_devices(device_index)
                if device_info["max_output_channels"] > 0:
                    log.debug(f"Using device override: {device_info['name']}")
                    return device_index, int(device_info["default_samplerate"])
                else:
                    log.warning(
                        f"Override device {device_index} has no output channels"
                    )
            else:
                log.warning(f"Override device index {device_index} out of range")
        except ValueError:
            log.warning(f"Invalid device override value: {device_override}")
    return None


def _get_output_device_macos(devices) -> tuple[int, int]:
    """Get the best output device for macOS."""
    # Try system default first
    try:
        default_output = sd.default.device[1]
        if default_output is not None:
            device_info = sd.query_devices(default_output)
            if device_info["max_output_channels"] > 0:
                log.debug(f"Using system default output device: {device_info['name']}")
                return default_output, int(device_info["default_samplerate"])
    except Exception as e:
        log.debug(f"Could not use default device: {e}")

    # Prefer CoreAudio devices
    coreaudio_device = next(
        (
            i
            for i, d in enumerate(devices)
            if d["max_output_channels"] > 0 and d["hostapi"] == 2
        ),
        None,
    )
    if coreaudio_device is not None:
        device_info = sd.query_devices(coreaudio_device)
        log.debug(f"Using CoreAudio device: {device_info['name']}")
        return coreaudio_device, int(device_info["default_samplerate"])

    # Fallback to any output device
    output_device = next(
        (i for i, d in enumerate(devices) if d["max_output_channels"] > 0),
        None,
    )
    if output_device is None:
        raise RuntimeError("No suitable audio output device found on macOS")

    device_info = sd.query_devices(output_device)
    log.debug(f"Fallback to device: {device_info['name']}")
    return output_device, int(device_info["default_samplerate"])


def _get_output_device_linux(devices) -> tuple[int, int]:
    """Get the best output device for Linux."""
    # Try system default first
    try:
        default_output = sd.default.device[1]
        if default_output is not None:
            device_info = sd.query_devices(default_output)
            if device_info["max_output_channels"] > 0:
                log.debug(f"Using system default output device: {device_info['name']}")
                return default_output, int(device_info["default_samplerate"])
    except Exception as e:
        log.debug(f"Could not use default device: {e}")

    # Try ALSA devices (more reliable than PulseAudio for sounddevice)
    alsa_device = next(
        (
            i
            for i, d in enumerate(devices)
            if ("alsa" in d["name"].lower() or "hw:" in d["name"].lower())
            and d["max_output_channels"] > 0
        ),
        None,
    )
    if alsa_device is not None:
        device_info = sd.query_devices(alsa_device)
        log.debug(f"Using ALSA device: {device_info['name']}")
        return alsa_device, int(device_info["default_samplerate"])

    # Fallback to PulseAudio if ALSA not found
    pulse_device = next(
        (
            i
            for i, d in enumerate(devices)
            if "pulse" in d["name"].lower() and d["max_output_channels"] > 0
        ),
        None,
    )
    if pulse_device is not None:
        device_info = sd.query_devices(pulse_device)
        log.debug(f"Using PulseAudio device: {device_info['name']}")
        return pulse_device, int(device_info["default_samplerate"])

    # Last resort: any working output device
    output_device = next(
        (i for i, d in enumerate(devices) if d["max_output_channels"] > 0),
        None,
    )
    if output_device is None:
        raise RuntimeError("No suitable audio output device found on Linux")

    device_info = sd.query_devices(output_device)
    log.debug(f"Fallback to device: {device_info['name']}")
    return output_device, int(device_info["default_samplerate"])


def get_output_device() -> tuple[int, int]:
    """Get the best available output device and its sample rate."""
    if not has_audio_imports:
        raise RuntimeError("Audio imports not available")

    devices = sd.query_devices()
    log.debug("Available audio devices:")
    for i, dev in enumerate(devices):
        log.debug(
            f"  [{i}] {dev['name']} (in: {dev['max_input_channels']}, "
            f"out: {dev['max_output_channels']}, hostapi: {dev['hostapi']})"
        )

    # Check for environment variable override first
    if override_result := _check_device_override(devices):
        return override_result

    # Use platform-specific logic
    system = platform_module.system()

    if system == "Darwin":  # macOS
        return _get_output_device_macos(devices)
    elif system == "Linux":
        return _get_output_device_linux(devices)
    else:
        # Windows or other platforms - use simple default logic
        try:
            default_output = sd.default.device[1]
            if default_output is not None:
                device_info = sd.query_devices(default_output)
                if device_info["max_output_channels"] > 0:
                    log.debug(f"Using system default: {device_info['name']}")
                    return default_output, int(device_info["default_samplerate"])
        except Exception:
            pass

        # Fallback for other platforms
        output_device = next(
            (i for i, d in enumerate(devices) if d["max_output_channels"] > 0),
            None,
        )
        if output_device is None:
            raise RuntimeError(f"No suitable audio output device found on {system}")

        device_info = sd.query_devices(output_device)
        return output_device, int(device_info["default_samplerate"])


def _resample_audio(data, orig_sr, target_sr):
    """Resample audio data to target sample rate."""
    if orig_sr == target_sr:
        return data

    duration = len(data) / orig_sr
    num_samples = int(duration * target_sr)
    return signal.resample(data, num_samples)


def _audio_player_thread_fn() -> None:
    """Background thread for playing audio using system commands (preferred) or sounddevice (fallback)."""
    log.debug("Audio player thread started")
    while True:
        try:
            # Get audio data from queue
            log.debug("Waiting for audio data...")
            item = audio_queue.get()
            if item is None:  # Sentinel value to stop thread
                log.debug("Received stop signal")
                break

            data, sample_rate = item

            # Apply volume
            data = data * current_volume
            log.debug(
                f"Playing audio: shape={data.shape}, sr={sample_rate}, vol={current_volume}"
            )

            # First try to save as temp file and play with system commands
            success = False
            try:
                import tempfile

                with tempfile.NamedTemporaryFile(
                    suffix=".wav", delete=False
                ) as tmp_file:
                    tmp_path = Path(tmp_file.name)

                    # Convert and save to temp WAV file
                    data_int16 = (data * 32767).astype("int16")
                    wavfile.write(tmp_path, sample_rate, data_int16)

                    # Try to play with system commands
                    if _play_with_system_command_blocking(tmp_path, current_volume):
                        success = True

                    # Clean up temp file
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

            except Exception as e:
                log.debug(f"System command playback failed: {e}")

            # Fall back to sounddevice if system commands failed
            if not success:
                log.debug("System audio players failed, falling back to sounddevice")
                try:
                    # Get output device with timeout
                    try:
                        output_device, _ = get_output_device()
                        if output_device is not None:
                            log.debug(f"Playing on device: {output_device}")
                        else:
                            log.debug("Playing on system default device")
                    except RuntimeError as e:
                        log.error(f"Device error: {e}")
                        audio_queue.task_done()
                        continue

                    # Play with timeout protection
                    sd.play(data, sample_rate, device=output_device)

                    # Wait with timeout
                    start_time = time.time()
                    timeout = 10.0  # 10 second timeout
                    while sd.get_stream().active:
                        if time.time() - start_time > timeout:
                            log.warning("Audio playback timed out, stopping")
                            sd.stop()
                            break
                        time.sleep(0.1)

                    log.debug("Finished playing audio chunk")
                except Exception as e:
                    log.error(f"sounddevice playback error: {e}")
                    try:
                        sd.stop()
                    except Exception:
                        pass

            audio_queue.task_done()
        except Exception as e:
            log.error(f"Error in audio playback thread: {e}")
            if not audio_queue.empty():
                audio_queue.task_done()


def _play_with_system_command_blocking(file_path: Path, volume: float = 1.0) -> bool:
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
                timeout=10,  # 10 second timeout
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


def ensure_playback_thread():
    """Ensure the audio playback thread is running (only for sounddevice fallback)."""
    global playback_thread

    if playback_thread is None or not playback_thread.is_alive():
        playback_thread = threading.Thread(target=_audio_player_thread_fn, daemon=True)
        playback_thread.start()


def set_volume(volume: float):
    """Set the volume for audio playback (0.0 to 1.0)."""
    global current_volume
    current_volume = max(0.0, min(1.0, volume))
    log.debug(f"Audio volume set to {current_volume:.2f}")


def stop_audio():
    """Stop audio playback and clear queue."""
    # Stop any running subprocess audio players
    try:
        subprocess.run(["pkill", "-f", "paplay"], capture_output=True, timeout=2)
        subprocess.run(["pkill", "-f", "ffplay"], capture_output=True, timeout=2)
    except Exception:
        pass

    # Stop sounddevice if available
    if has_audio_imports:
        try:
            sd.stop()
        except Exception:
            pass

    # Clear queue
    while not audio_queue.empty():
        try:
            audio_queue.get_nowait()
            audio_queue.task_done()
        except queue.Empty:
            break


def convert_audio_to_float32(data: Any) -> Any:
    """Convert audio data to float32 format for consistent processing.

    Args:
        data: Audio data as numpy array

    Returns:
        Audio data converted to float32 format
    """
    if not has_audio_imports:
        return data

    # Convert to float32 for consistent processing
    if data.dtype != np.float32:
        if data.dtype.kind == "i":  # integer
            data = data.astype(np.float32) / np.iinfo(data.dtype).max
        elif data.dtype.kind == "f":  # floating point
            # Normalize to [-1, 1] if needed
            if np.max(np.abs(data)) > 1.0:
                data = data / np.max(np.abs(data))
            data = data.astype(np.float32)

    return data


def play_audio_data(data: Any, sample_rate: int, block: bool = False):
    """Play audio data directly (uses sounddevice as fallback).

    Args:
        data: Audio data as numpy array
        sample_rate: Sample rate of the audio
        block: If True, wait for audio to finish playing
    """
    if not has_audio_imports:
        log.debug("Audio not available, skipping playback")
        return

    try:
        # Convert to float32 for consistent processing
        data = convert_audio_to_float32(data)

        # Get output device for sample rate
        try:
            _, device_sr = get_output_device()
            # Resample if needed
            if sample_rate != device_sr:
                data = _resample_audio(data, sample_rate, device_sr)
                sample_rate = device_sr
        except RuntimeError as e:
            log.error(f"Device error: {e}")
            return

        # Ensure playback thread is running
        ensure_playback_thread()

        # Queue for playback
        audio_queue.put((data, sample_rate))

        if block:
            audio_queue.join()

    except Exception as e:
        log.error(f"Failed to play audio: {e}")


def play_sound_file(file_path: Path, block: bool = False):
    """Play a sound file using the background audio system.

    Args:
        file_path: Path to the sound file
        block: If True, wait for audio to finish playing
    """
    if not file_path.exists():
        log.warning(f"Sound file not found: {file_path}")
        return

    # Always use the background thread system for consistent non-blocking behavior
    if not has_audio_imports:
        log.warning("No audio playback methods available")
        return

    try:
        # Load the audio file and queue it for background playback
        if file_path.suffix.lower() == ".wav":
            sample_rate, data = wavfile.read(file_path)
            play_audio_data(data, sample_rate, block)
        elif file_path.suffix.lower() == ".mp3":
            # For MP3 files, we'd need additional libraries like pydub
            log.warning("MP3 files not directly supported, need to convert to WAV")
        else:
            log.warning(f"Unsupported audio format: {file_path.suffix}")
    except Exception as e:
        log.error(f"Failed to play sound file {file_path}: {e}")


def play_ding():
    """Play the UI ding sound."""
    log.info("Playing ding sound")
    # Get the bell sound file from the package
    bell_path = media_path / "bell.wav"

    if bell_path.exists():
        play_sound_file(bell_path, block=False)
    else:
        log.warning(f"Bell sound file not found: {bell_path}")


def play_tool_sound(sound_type: str):
    """Play a tool sound.

    Args:
        sound_type: Type of sound to play. One of:
            - "sawing": General tool use (sawing sound)
            - "drilling": General tool use (drilling sound)
            - "page_turn": Read operations
            - "seashell_click": Shell commands
            - "camera_shutter": Screenshot operations
    """
    if not is_audio_available():
        log.debug("Audio not available, skipping tool sound playback")
        return

    if not get_config().get_env_bool("GPTME_TOOL_SOUNDS"):
        log.debug("GPTME_TOOL_SOUNDS not enabled, skipping tool sound playback")
        return

    # Get the sound file from the package
    sound_path = media_path / f"{sound_type}.wav"

    if sound_path.exists():
        log.debug(f"Playing tool sound: {sound_type}")
        play_sound_file(sound_path, block=False)
    else:
        log.warning(f"Tool sound file not found: {sound_path}")


def get_tool_sound_for_tool(tool_name: str) -> str | None:
    """Get the appropriate sound type for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Sound type to play, or None if no specific sound is configured
    """
    # Map tools to their sounds
    tool_sound_map = {
        # Read operations - page turn sound
        "read": "page_turn",
        # Shell commands - seashell click sound
        "shell": "seashell_click",
        # Screenshot - camera shutter sound
        "screenshot": "camera_shutter",
        # File write operations - file write sound
        "save": "file_write",
        "append": "file_write",
        "patch": "file_write",
        "morph": "file_write",
        # General tool use - sawing sound by default
        # We can add more specific mappings here
        "python": "sawing",
        "ipython": "sawing",
        "browser": "sawing",
        "gh": "sawing",
        "tmux": "sawing",
        "computer": "sawing",
        "chats": "sawing",
        "rag": "sawing",
        "subagent": "sawing",
    }

    return tool_sound_map.get(tool_name)


def wait_for_audio():
    """Wait for all audio playback to finish."""
    # For system commands, we can't easily wait, so just give a short delay
    time.sleep(0.1)

    # For sounddevice fallback
    if has_audio_imports and playback_thread and playback_thread.is_alive():
        try:
            audio_queue.join()
        except Exception as e:
            log.debug(f"Error waiting for audio: {e}")


def print_bell():
    """Ring the terminal bell or play ding sound if available."""
    # Terminal bell
    sys.stdout.write("\a")
    sys.stdout.flush()

    # If audio is available and GPTME_DING is enabled, play the ding sound
    if is_audio_available() and get_config().get_env_bool("GPTME_DING"):
        play_ding()
    else:
        if not is_audio_available():
            log.info("Audio not available, skipping ding sound playback")
        else:
            log.debug("GPTME_DING not set, skipping ding sound playback")
