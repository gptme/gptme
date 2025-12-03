"""
Speech-to-text (STT) tool for transcribing audio input.

Uses OpenAI Whisper API for transcription by default.

.. rubric:: Usage

.. code-block:: bash

    # Set OpenAI API key
    export OPENAI_API_KEY=your-api-key

    # Enable STT in gptme
    # In interactive mode, use /voice command

.. rubric:: Environment Variables

- ``OPENAI_API_KEY``: Required for Whisper API transcription.
- ``GPTME_STT_MODEL``: Whisper model to use (default: whisper-1).
- ``GPTME_STT_LANGUAGE``: Language hint for transcription (default: auto-detect).
"""

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ..util import console
from .base import ToolSpec

# Setup logging
log = logging.getLogger(__name__)

# Sample rate for recording (Whisper expects 16kHz)
_sample_rate = 16000

# Check for required imports
has_stt_imports = False
try:
    import numpy as np  # noqa: F401
    import sounddevice as sd  # noqa: F401

    has_stt_imports = True
except (ImportError, OSError):
    has_stt_imports = False


def _is_stt_available() -> bool:
    """Check if STT is available (has sounddevice for recording)."""
    return has_stt_imports


def _get_openai_client():
    """Get OpenAI client for Whisper API."""
    try:
        from openai import OpenAI

        return OpenAI()
    except ImportError:
        log.error("OpenAI package not installed. Install with: pip install openai")
        return None
    except Exception as e:
        # OpenAI client raises if API key is missing
        log.debug(f"Failed to create OpenAI client: {e}")
        return None


def transcribe_audio(audio_data: bytes, language: str | None = None) -> str | None:
    """Transcribe audio data using OpenAI Whisper API.

    Args:
        audio_data: WAV audio data as bytes.
        language: Optional language hint for transcription.

    Returns:
        Transcribed text, or None if transcription failed.
    """
    client = _get_openai_client()
    if not client:
        return None

    model = os.environ.get("GPTME_STT_MODEL", "whisper-1")
    language = language or os.environ.get("GPTME_STT_LANGUAGE")

    try:
        # Create a temporary file for the audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = Path(tmp.name)

        try:
            # Transcribe using Whisper API
            with open(tmp_path, "rb") as audio_file:
                kwargs: dict[str, Any] = {"model": model, "file": audio_file}
                if language:
                    kwargs["language"] = language

                transcription = client.audio.transcriptions.create(**kwargs)

            return transcription.text
        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)

    except Exception as e:
        log.error(f"Transcription failed: {e}")
        console.print(f"[red]Transcription failed: {e}[/red]")
        return None


def record_and_transcribe(language: str | None = None) -> str | None:
    """Record audio and transcribe it.

    Records in a background thread until user presses Enter or Ctrl+C.

    Args:
        language: Optional language hint for transcription.

    Returns:
        Transcribed text, or None if recording/transcription failed.
    """
    if not _is_stt_available():
        console.print("[red]STT not available: sounddevice not installed[/red]")
        console.print("Install with: pip install sounddevice scipy numpy")
        return None

    import threading

    import numpy as np
    import sounddevice as sd
    from scipy.io import wavfile

    # Find and display input device info
    try:
        devices = sd.query_devices()
        default_input = sd.default.device[0]  # Index 0 is input, 1 is output

        if default_input is None or default_input < 0:
            console.print("[red]No default input device found[/red]")
            console.print("Available devices:")
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    console.print(f"  [{i}] {d['name']} (inputs: {d['max_input_channels']})")
            return None

        input_device = devices[default_input]
        device_name = input_device["name"]
        console.print(f"[cyan]🎤 Using input device: {device_name}[/cyan]")
        console.print("[cyan]Press Enter to start recording...[/cyan]")
    except Exception as e:
        console.print(f"[red]Failed to query audio devices: {e}[/red]")
        return None

    input()

    # Recording state (shared with background thread)
    audio_chunks: list[Any] = []
    stop_event = threading.Event()
    recording_error: Exception | None = None
    callback_count = [0]  # Use list to allow mutation in nested function
    status_warnings: list[str] = []

    def recording_thread_fn():
        """Background thread that records audio until stop_event is set."""
        nonlocal recording_error
        try:
            def callback(indata, frames, time_info, status):
                callback_count[0] += 1
                if status:
                    status_warnings.append(str(status))
                    log.warning(f"Recording status: {status}")
                if not stop_event.is_set():
                    audio_chunks.append(indata.copy())

            with sd.InputStream(
                samplerate=_sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
                device=default_input,  # Explicit device selection
            ):
                # Keep stream open until stop_event is set
                while not stop_event.is_set():
                    stop_event.wait(0.1)
        except Exception as e:
            recording_error = e
            log.error(f"Recording thread error: {e}")

    # Start recording in background thread (daemon=True allows clean exit)
    recording_thread = threading.Thread(target=recording_thread_fn, daemon=True)
    recording_thread.start()

    console.print("[green]🎤 Recording... Press Enter to stop[/green]")

    # Main thread waits for Enter or Ctrl+C
    cancelled = False
    try:
        input()  # Blocking wait for Enter key
    except KeyboardInterrupt:
        cancelled = True
        console.print("\n[yellow]Recording cancelled[/yellow]")

    # Signal recording thread to stop and wait for it
    stop_event.set()
    recording_thread.join(timeout=2.0)

    if cancelled:
        return None

    if recording_error:
        console.print(f"[red]Recording failed: {recording_error}[/red]")
        return None

    # Show diagnostic info
    if callback_count[0] == 0:
        console.print("[red]Recording callback was never called![/red]")
        console.print(f"[red]Device '{device_name}' may not be working properly.[/red]")
        console.print("Try selecting a different input device or check system audio settings.")
        return None

    if status_warnings:
        console.print(f"[yellow]Audio warnings: {', '.join(set(status_warnings))}[/yellow]")

    log.debug(f"Recording complete: {callback_count[0]} callbacks, {len(audio_chunks)} chunks")

    if not audio_chunks:
        console.print(f"[yellow]No audio recorded (callback called {callback_count[0]} times)[/yellow]")
        console.print("The input device may have issues or audio level is too low.")
        return None

    # Process audio
    audio = np.concatenate(audio_chunks)
    audio_int16 = (audio * 32767).astype(np.int16)

    # Save to WAV bytes
    buffer = io.BytesIO()
    wavfile.write(buffer, _sample_rate, audio_int16)
    buffer.seek(0)
    audio_data = buffer.read()

    duration = len(audio) / _sample_rate
    console.print(f"[cyan]✓ Recorded {duration:.1f}s, transcribing...[/cyan]")

    return transcribe_audio(audio_data, language)


# Tool specification
tool = ToolSpec(
    name="stt",
    desc="Speech-to-text transcription using OpenAI Whisper",
    instructions="Use the /voice command in interactive mode to record and transcribe speech.",
    available=_is_stt_available(),
    block_types=[],  # No code blocks, uses command interface
    functions=[record_and_transcribe, transcribe_audio],
)
__doc__ = tool.get_doc(__doc__)
