"""
Sound utility for playing audio files and system sounds.

Extracts core audio playback functionality for reuse across TTS and UI sounds.
"""

import logging
import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..config import get_config
from ._sound_cmd import (
    is_cmd_audio_available,
    play_with_system_command_blocking,
    stop_system_audio,
)
from ._sound_sounddevice import (
    convert_audio_to_float32,
    get_output_device,
    is_sounddevice_available,
    load_wav_file,
    play_with_sounddevice,
    resample_audio,
    stop_sounddevice_audio,
)

log = logging.getLogger(__name__)

# Audio playback state
audio_queue: queue.Queue[tuple[Any, int] | None] = queue.Queue()
playback_thread: threading.Thread | None = None
current_volume = 0.7

media_path = Path(__file__).parent.parent.parent / "media"


def is_audio_available() -> bool:
    """Check if audio playback is available via system tools or sounddevice."""
    return is_cmd_audio_available() or is_sounddevice_available()


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
            if is_cmd_audio_available():
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=".wav", delete=False
                    ) as tmp_file:
                        tmp_path = Path(tmp_file.name)

                        # Convert and save to temp WAV file
                        if is_sounddevice_available():
                            data_converted = convert_audio_to_float32(data)
                            data_int16 = (data_converted * 32767).astype("int16")
                        else:
                            # Fallback conversion without numpy
                            data_int16 = data

                        # Save WAV file (requires scipy)
                        if is_sounddevice_available():
                            import scipy.io.wavfile as wavfile

                            wavfile.write(tmp_path, sample_rate, data_int16)

                            # Try to play with system commands
                            if play_with_system_command_blocking(
                                tmp_path, current_volume
                            ):
                                success = True

                        # Clean up temp file
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass

                except Exception as e:
                    log.debug(f"System command playback failed: {e}")

            # Fall back to sounddevice if system commands failed
            if not success and is_sounddevice_available():
                log.debug("System audio players failed, falling back to sounddevice")
                try:
                    play_with_sounddevice(data, sample_rate, current_volume)
                except Exception as e:
                    log.error(f"sounddevice playback error: {e}")

            audio_queue.task_done()
        except Exception as e:
            log.error(f"Error in audio playback thread: {e}")
            if not audio_queue.empty():
                audio_queue.task_done()


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
    stop_system_audio()

    # Stop sounddevice if available
    stop_sounddevice_audio()

    # Clear queue
    while not audio_queue.empty():
        try:
            audio_queue.get_nowait()
            audio_queue.task_done()
        except queue.Empty:
            break


def play_audio_data(data: Any, sample_rate: int, block: bool = False):
    """Play audio data directly (uses sounddevice as fallback).

    Args:
        data: Audio data as numpy array
        sample_rate: Sample rate of the audio
        block: If True, wait for audio to finish playing
    """
    if not is_audio_available():
        log.debug("Audio not available, skipping playback")
        return

    try:
        # Convert to float32 for consistent processing
        if is_sounddevice_available():
            data = convert_audio_to_float32(data)

            # Get output device for sample rate
            try:
                _, device_sr = get_output_device()
                # Resample if needed
                if sample_rate != device_sr:
                    data = resample_audio(data, sample_rate, device_sr)
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
    if not is_audio_available():
        log.warning("No audio playback methods available")
        return

    try:
        # Load the audio file and queue it for background playback
        if file_path.suffix.lower() == ".wav":
            if is_sounddevice_available():
                wav_data = load_wav_file(file_path)
                if wav_data:
                    sample_rate, data = wav_data
                    play_audio_data(data, sample_rate, block)
                else:
                    log.error(f"Failed to load WAV file: {file_path}")
            else:
                log.warning("WAV file playback requires sounddevice libraries")
        elif file_path.suffix.lower() == ".mp3":
            # For MP3 files, we'd need additional libraries like pydub
            log.warning("MP3 files not directly supported, need to convert to WAV")
        else:
            log.warning(f"Unsupported audio format: {file_path.suffix}")
    except Exception as e:
        log.error(f"Failed to play sound file {file_path}: {e}")


def play_ding():
    """Play the UI ding sound."""
    log.debug("Playing ding sound")
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
    if is_sounddevice_available() and playback_thread and playback_thread.is_alive():
        try:
            audio_queue.join()
        except Exception as e:
            log.debug(f"Error waiting for audio: {e}")


def print_bell():
    """Ring the terminal bell or play ding sound if available."""
    import sys

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


# Audio recording support
def is_recording_available() -> bool:
    """Check if audio recording is available via sounddevice."""
    return is_sounddevice_available()


def get_input_devices() -> list[dict[str, Any]]:
    """Get list of available input devices.

    Returns:
        List of device info dicts with 'index', 'name', 'channels' keys.
    """
    if not is_sounddevice_available():
        return []

    import sounddevice as sd

    devices = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devices.append({
                    "index": i,
                    "name": d["name"],
                    "channels": d["max_input_channels"],
                    "sample_rate": int(d["default_samplerate"]),
                })
    except Exception as e:
        log.error(f"Failed to query input devices: {e}")

    return devices


def get_default_input_device() -> dict[str, Any] | None:
    """Get the default input device info.

    Returns:
        Device info dict or None if no input device available.
    """
    if not is_sounddevice_available():
        return None

    import sounddevice as sd

    try:
        default_input = sd.default.device[0]
        if default_input is None or default_input < 0:
            return None

        devices = sd.query_devices()
        device = devices[default_input]
        return {
            "index": default_input,
            "name": device["name"],
            "channels": device["max_input_channels"],
            "sample_rate": int(device["default_samplerate"]),
        }
    except Exception as e:
        log.error(f"Failed to get default input device: {e}")
        return None


def record_audio(
    sample_rate: int = 16000,
    channels: int = 1,
    device: int | None = None,
    max_duration: float | None = None,
) -> bytes | None:
    """Record audio from the microphone until interrupted.

    Records audio in a blocking manner. The recording can be stopped by:
    - KeyboardInterrupt (Ctrl+C)
    - Reaching max_duration if specified

    Args:
        sample_rate: Sample rate for recording (default 16000 for speech).
        channels: Number of channels (default 1 for mono).
        device: Optional device index. Uses default if None.
        max_duration: Optional maximum recording duration in seconds.

    Returns:
        WAV file bytes, or None if recording failed/cancelled.
    """
    if not is_sounddevice_available():
        log.error("Recording not available: sounddevice not installed")
        return None

    import io

    import numpy as np
    import sounddevice as sd
    from scipy.io import wavfile

    # Use default device if not specified
    if device is None:
        default_device = get_default_input_device()
        if default_device is None:
            log.error("No default input device found")
            return None
        device = default_device["index"]

    # Collect audio data
    audio_chunks: list[Any] = []
    chunk_count = 0

    def callback(indata, frames, time_info, status):
        nonlocal chunk_count
        if status:
            log.warning(f"Recording status: {status}")
        audio_chunks.append(indata.copy())
        chunk_count += 1

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            callback=callback,
            device=device,
        ):
            # Record until interrupted or max_duration reached
            if max_duration:
                time.sleep(max_duration)
            else:
                # Block until KeyboardInterrupt
                while True:
                    time.sleep(0.1)

    except KeyboardInterrupt:
        log.debug("Recording interrupted by user")
    except Exception as e:
        log.error(f"Recording failed: {e}")
        return None

    if not audio_chunks:
        log.warning(f"No audio recorded (callback count: {chunk_count})")
        return None

    log.debug(f"Recording complete: {chunk_count} callbacks, {len(audio_chunks)} chunks")

    # Concatenate and convert to WAV bytes
    try:
        audio = np.concatenate(audio_chunks)
        audio_int16 = (audio * 32767).astype(np.int16)

        buffer = io.BytesIO()
        wavfile.write(buffer, sample_rate, audio_int16)
        buffer.seek(0)
        return buffer.read()

    except Exception as e:
        log.error(f"Failed to process audio: {e}")
        return None


def record_audio_interactive(
    sample_rate: int = 16000,
    channels: int = 1,
    device: int | None = None,
) -> bytes | None:
    """Record audio interactively with Enter to start/stop.

    This function provides an interactive recording experience:
    1. Displays the input device being used
    2. Waits for Enter to start recording
    3. Records until Enter is pressed again or Ctrl+C

    Args:
        sample_rate: Sample rate for recording (default 16000 for speech).
        channels: Number of channels (default 1 for mono).
        device: Optional device index. Uses default if None.

    Returns:
        WAV file bytes, or None if recording failed/cancelled.
    """
    if not is_sounddevice_available():
        log.error("Recording not available: sounddevice not installed")
        return None

    import io
    import threading

    import numpy as np
    import sounddevice as sd
    from scipy.io import wavfile

    # Get device info
    if device is None:
        device_info = get_default_input_device()
        if device_info is None:
            log.error("No default input device found")
            return None
        device = device_info["index"]
        device_name = device_info["name"]
    else:
        try:
            devices = sd.query_devices()
            device_name = devices[device]["name"]
        except Exception:
            device_name = f"device {device}"

    log.info(f"Using input device: {device_name}")

    # Collect audio data
    audio_chunks: list[Any] = []
    stop_flag = threading.Event()
    recording_started = threading.Event()

    def callback(indata, frames, time_info, status):
        if status:
            log.warning(f"Recording status: {status}")
        if recording_started.is_set() and not stop_flag.is_set():
            audio_chunks.append(indata.copy())

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            callback=callback,
            device=device,
        ):
            # Signal that we're ready
            recording_started.set()
            log.info("Recording started - press Enter to stop")

            # Wait for Enter or interrupt
            try:
                input()
            except EOFError:
                pass

            stop_flag.set()

    except KeyboardInterrupt:
        log.debug("Recording cancelled by user")
        return None
    except Exception as e:
        log.error(f"Recording failed: {e}")
        return None

    if not audio_chunks:
        log.warning("No audio recorded")
        return None

    duration = len(audio_chunks) * 1024 / sample_rate  # Approximate
    log.debug(f"Recorded approximately {duration:.1f}s of audio")

    # Concatenate and convert to WAV bytes
    try:
        audio = np.concatenate(audio_chunks)
        audio_int16 = (audio * 32767).astype(np.int16)

        buffer = io.BytesIO()
        wavfile.write(buffer, sample_rate, audio_int16)
        buffer.seek(0)
        return buffer.read()

    except Exception as e:
        log.error(f"Failed to process audio: {e}")
        return None
