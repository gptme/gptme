"""
Sounddevice-based audio playback using Python audio libraries.
"""

import logging
import os
import platform as platform_module
import time
from typing import Any

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


def is_sounddevice_available() -> bool:
    """Check if sounddevice audio playback is available."""
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
    # log.debug("Available audio devices:")
    # for i, dev in enumerate(devices):
    #     log.debug(
    #         f"  [{i}] {dev['name']} (in: {dev['max_input_channels']}, "
    #         f"out: {dev['max_output_channels']}, hostapi: {dev['hostapi']})"
    #     )

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


def resample_audio(data, orig_sr, target_sr):
    """Resample audio data to target sample rate."""
    if not has_audio_imports:
        raise RuntimeError("Audio libraries not available for resampling")

    if orig_sr == target_sr:
        return data

    duration = len(data) / orig_sr
    num_samples = int(duration * target_sr)
    return signal.resample(data, num_samples)


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


def play_with_sounddevice(data: Any, sample_rate: int, volume: float = 1.0):
    """Play audio data using sounddevice.

    Args:
        data: Audio data as numpy array
        sample_rate: Sample rate of the audio
        volume: Volume level (0.0 to 1.0)
    """
    if not has_audio_imports:
        raise RuntimeError("sounddevice not available")

    try:
        # Convert to float32 and apply volume
        data = convert_audio_to_float32(data)
        data = data * volume

        # Get output device with timeout
        try:
            output_device, device_sr = get_output_device()
            # Resample if needed
            if sample_rate != device_sr:
                data = resample_audio(data, sample_rate, device_sr)
                sample_rate = device_sr
        except RuntimeError as e:
            log.error(f"Device error: {e}")
            raise

        if output_device is not None:
            log.debug(f"Playing on device: {output_device}")
        else:
            log.debug("Playing on system default device")

        # Play with timeout protection
        sd.play(data, sample_rate, device=output_device)

        # Wait with timeout
        start_time = time.time()
        timeout = 30.0
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
        raise


def stop_sounddevice_audio():
    """Stop sounddevice audio playback."""
    if has_audio_imports:
        try:
            sd.stop()
        except Exception:
            pass


def load_wav_file(file_path):
    """Load a WAV file using scipy.

    Returns:
        tuple: (sample_rate, data) or None if loading failed
    """
    if not has_audio_imports:
        return None

    try:
        sample_rate, data = wavfile.read(file_path)
        return sample_rate, data
    except Exception as e:
        log.error(f"Failed to load WAV file {file_path}: {e}")
        return None
