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

# Recording state
_recording = False
_audio_data: list[Any] = []
_sample_rate = 16000  # Whisper expects 16kHz

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


def _record_audio_callback(indata, frames, time_info, status):
    """Callback for sounddevice recording."""
    global _audio_data
    if status:
        log.warning(f"Recording status: {status}")
    if _recording:
        _audio_data.append(indata.copy())


def start_recording() -> bool:
    """Start recording audio from microphone.

    Returns:
        True if recording started successfully, False otherwise.
    """
    global _recording, _audio_data

    if not _is_stt_available():
        console.print("[red]STT not available: sounddevice not installed[/red]")
        return False

    if _recording:
        console.print("[yellow]Already recording[/yellow]")
        return False

    import sounddevice as sd

    _audio_data = []
    _recording = True

    try:
        # Start recording stream
        sd.InputStream(
            samplerate=_sample_rate,
            channels=1,
            dtype="float32",
            callback=_record_audio_callback,
        ).start()
        console.print("[green]🎤 Recording... Press Enter to stop[/green]")
        return True
    except Exception as e:
        log.error(f"Failed to start recording: {e}")
        _recording = False
        console.print(f"[red]Failed to start recording: {e}[/red]")
        return False


def stop_recording() -> bytes | None:
    """Stop recording and return audio data as WAV bytes.

    Returns:
        WAV audio data as bytes, or None if no audio recorded.
    """
    global _recording, _audio_data

    if not _recording:
        return None

    _recording = False

    if not _audio_data:
        console.print("[yellow]No audio recorded[/yellow]")
        return None

    try:
        import numpy as np
        import sounddevice as sd
        from scipy.io import wavfile

        # Stop all streams
        sd.stop()

        # Combine audio chunks
        audio = np.concatenate(_audio_data)

        # Convert to 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)

        # Save to WAV bytes
        buffer = io.BytesIO()
        wavfile.write(buffer, _sample_rate, audio_int16)
        buffer.seek(0)

        duration = len(audio) / _sample_rate
        console.print(f"[green]✓ Recorded {duration:.1f}s of audio[/green]")

        return buffer.read()
    except Exception as e:
        log.error(f"Failed to process recording: {e}")
        console.print(f"[red]Failed to process recording: {e}[/red]")
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

    This is a blocking function that records until the user presses Enter.

    Args:
        language: Optional language hint for transcription.

    Returns:
        Transcribed text, or None if recording/transcription failed.
    """
    if not _is_stt_available():
        console.print("[red]STT not available: sounddevice not installed[/red]")
        console.print("Install with: pip install sounddevice scipy numpy")
        return None

    import sounddevice as sd

    console.print("[cyan]🎤 Press Enter to start recording...[/cyan]")
    input()

    # Start recording in a separate stream
    audio_chunks: list[Any] = []

    def callback(indata, frames, time_info, status):
        if status:
            log.warning(f"Recording status: {status}")
        audio_chunks.append(indata.copy())

    try:
        import numpy as np
        from scipy.io import wavfile

        with sd.InputStream(
            samplerate=_sample_rate,
            channels=1,
            dtype="float32",
            callback=callback,
        ):
            console.print("[green]🎤 Recording... Press Enter to stop[/green]")
            input()

        if not audio_chunks:
            console.print("[yellow]No audio recorded[/yellow]")
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

        # Transcribe
        return transcribe_audio(audio_data, language)

    except Exception as e:
        log.error(f"Recording failed: {e}")
        console.print(f"[red]Recording failed: {e}[/red]")
        return None


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
