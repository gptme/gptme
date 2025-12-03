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

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..commands import CommandContext

from ..message import Message
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

    Uses the sound module for recording. Press Enter to stop recording.

    Args:
        language: Optional language hint for transcription.

    Returns:
        Transcribed text, or None if recording/transcription failed.
    """
    from ..util.sound import (
        get_default_input_device,
        get_input_devices,
        is_recording_available,
        record_audio_interactive,
    )

    if not is_recording_available():
        console.print("[red]STT not available: sounddevice not installed[/red]")
        console.print("Install with: pip install sounddevice scipy numpy")
        return None

    # Show device info
    device = get_default_input_device()
    if device is None:
        console.print("[red]No default input device found[/red]")
        console.print("Available input devices:")
        for d in get_input_devices():
            console.print(f"  [{d['index']}] {d['name']} (inputs: {d['channels']})")
        return None

    console.print(f"[cyan]🎤 Using input device: {device['name']}[/cyan]")
    console.print("[cyan]Press Enter to start recording...[/cyan]")

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        console.print("[yellow]Recording cancelled[/yellow]")
        return None

    console.print("[green]🎤 Recording... Press Enter to stop[/green]")

    # Record audio using sound module
    audio_data = record_audio_interactive(
        sample_rate=_sample_rate,
        channels=1,
        device=device["index"],
    )

    if audio_data is None:
        console.print("[yellow]No audio recorded or recording cancelled[/yellow]")
        return None

    # Estimate duration from WAV data (header is 44 bytes, 16-bit mono)
    audio_bytes = len(audio_data) - 44
    duration = audio_bytes / (2 * _sample_rate)
    console.print(f"[cyan]✓ Recorded {duration:.1f}s, transcribing...[/cyan]")

    return transcribe_audio(audio_data, language)


def _cmd_voice(ctx: CommandContext) -> Generator[Message, None, None]:
    """Record and transcribe speech input using STT."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()

    # Get optional language from args
    language = ctx.args[0] if ctx.args else None

    # Record and transcribe
    text = record_and_transcribe(language=language)

    if text:
        # Return the transcribed text as a user message
        yield Message("user", text)


# Tool specification
tool = ToolSpec(
    name="stt",
    desc="Speech-to-text transcription using OpenAI Whisper",
    instructions="Use the /voice command in interactive mode to record and transcribe speech.",
    available=_is_stt_available(),
    block_types=[],  # No code blocks, uses command interface
    functions=[record_and_transcribe, transcribe_audio],
    commands={"voice": _cmd_voice},
)
__doc__ = tool.get_doc(__doc__)
