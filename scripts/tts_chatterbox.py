#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "gradio-client",
#     "click",
# ]
# ///
"""
Enhanced Chatterbox TTS implementation.

Can be used as both a standalone CLI tool and as a backend module.
"""

import io
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import click
import numpy as np
import scipy.io.wavfile as wavfile
from gradio_client import Client, handle_file

log = logging.getLogger(__name__)

# Supported audio file extensions for voice samples
audio_extensions = {".wav"}  # , ".mp3", ".flac", ".ogg", ".m4a"}


def _list_voices(voice_sample_dir: str | Path) -> list[str]:
    """List available voice samples in the specified directory."""
    voice_sample_dir = Path(voice_sample_dir)
    if not voice_sample_dir.exists():
        return []

    voices = []
    for file_path in voice_sample_dir.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in audio_extensions:
            voices.append(file_path.name)

    return sorted(voices)


class ChatterboxTTSBackend:
    """Chatterbox TTS backend implementation."""

    def __init__(
        self,
        hf_token: str | None = None,
        voice_sample_dir: str | None = None,
    ):
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        if not self.hf_token:
            raise ValueError(
                "HF_TOKEN environment variable is required for Chatterbox TTS"
            )

        self.client = None

        self.voice_sample_dir = (
            Path(voice_sample_dir) if voice_sample_dir else Path(__file__).parent
        )

        # first voice sample found in the directory
        voices = _list_voices(self.voice_sample_dir)
        self.default_voice: str | None = voices[0] if voices else None

    def initialize(self, voice: str | None = None) -> None:
        """Initialize the Chatterbox TTS client."""
        try:
            src = os.environ.get("GRADIO_SRC", "ResembleAI/Chatterbox")
            self.client = Client(src, hf_token=self.hf_token)
            # self.client = Client("http://127.0.0.1:7860")
            log.info("Chatterbox TTS client initialized")

            if voice:
                self.default_voice = voice
                # Verify voice sample exists
                voice_path = self.voice_sample_dir / voice
                if not voice_path.exists():
                    available = self.list_voices()
                    raise ValueError(
                        f"Voice sample '{voice}' not found. Available: {available}"
                    )

        except Exception as e:
            log.error(f"Failed to initialize Chatterbox client: {e}")
            raise

    def list_voices(self) -> list[str]:
        """List available voice samples in the voice sample directory."""
        return _list_voices(self.voice_sample_dir)

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        exaggeration: float = 0.5,
        temperature: float = 0.8,
        seed: int = 0,
        cfgw: float = 0.5,
    ) -> io.BytesIO:
        """Convert text to speech using Chatterbox TTS."""
        if not self.client:
            raise RuntimeError("Client not initialized. Call initialize() first.")

        voice_file = voice or self.default_voice
        if not voice_file:
            raise ValueError("Voice parameter is required for Chatterbox TTS")

        voice_path = self.voice_sample_dir / voice_file
        if not voice_path.exists():
            available = self.list_voices()
            raise ValueError(
                f"Voice sample '{voice_file}' not found. Available: {available}"
            )

        try:
            log.info(f"Generating audio with Chatterbox (voice: {voice_file})")

            # Call the Chatterbox API
            result = self.client.predict(
                text_input=text,
                audio_prompt_path_input=handle_file(str(voice_path)),
                exaggeration_input=exaggeration,
                temperature_input=temperature,
                seed_num_input=seed,
                cfgw_input=cfgw,
                api_name="/generate_tts_audio",
            )

            # The result should be a path to the generated audio file
            if not result or not isinstance(result, str) or not result.endswith(".wav"):
                raise ValueError(f"Unexpected result from Chatterbox API: {result}")

            # Read the generated audio file
            sample_rate, audio_data = wavfile.read(result)

            # Normalize to [-1, 1] range if needed
            if np.max(np.abs(audio_data)) > 1.0:
                audio_data = audio_data / np.max(np.abs(audio_data))

            # Convert to 16-bit integer format (standard for WAV files)
            if audio_data.dtype != np.int16:
                if audio_data.dtype.kind == "f":  # floating point
                    audio_int16 = (audio_data * 32767).astype(np.int16)
                else:  # integer
                    audio_int16 = audio_data.astype(np.int16)
            else:
                audio_int16 = audio_data

            # Write to buffer in correct format
            buffer = io.BytesIO()
            wavfile.write(buffer, sample_rate, audio_int16)
            buffer.seek(0)

            return buffer

        except Exception as e:
            log.error(f"Failed to generate speech with Chatterbox: {e}")
            raise

    def get_info(self) -> dict:
        """Get backend information."""
        return {
            "name": "chatterbox",
            "version": "gradio-client",
            "voice": {
                "default": self.default_voice,
                "available": self.list_voices(),
                "sample_dir": str(self.voice_sample_dir),
            },
            "requires_auth": True,
        }


def generate_audio_cli(
    text: str,
    voice_sample_path: str,
    output_path: str | None = None,
) -> str:
    """CLI function to generate audio and return the output path."""
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        raise ValueError("Please set the HF_TOKEN environment variable.")

    script_dir = Path(__file__).parent
    voice_path = script_dir / voice_sample_path

    if not voice_path.exists():
        raise FileNotFoundError(f"Voice sample not found: {voice_path}")

    try:
        client = Client("ResembleAI/Chatterbox", hf_token=hf_token)
        result = client.predict(
            text_input=text,
            audio_prompt_path_input=handle_file(str(voice_path)),
            exaggeration_input=0.5,
            temperature_input=0.8,
            seed_num_input=0,
            cfgw_input=0.5,
            api_name="/generate_tts_audio",
        )

        if output_path:
            # Copy result to specified output path

            shutil.copy2(result, output_path)
            return output_path
        else:
            return result

    except Exception as e:
        log.error(f"Error generating audio: {e}")
        raise


@click.command()
@click.argument("text")
@click.argument("voice_sample_path")
@click.option("--output", "-o", help="Output file path")
@click.option("--voice-dir", help="Directory containing voice samples", default=None)
@click.option("--exaggeration", default=0.5, help="Exaggeration level (0.0-1.0)")
@click.option("--temperature", default=0.5, help="Temperature for generation")
@click.option("--seed", default=0, help="Random seed")
@click.option("--cfgw", default=0.5, help="CFG weight")
@click.option("--list-voices", is_flag=True, help="List available voice samples")
def main(
    text: str,
    voice_sample_path: str,
    output: str | None,
    voice_dir: str | None,
    exaggeration: float,
    temperature: float,
    seed: int,
    cfgw: float,
    list_voices: bool,
):
    """Generate speech using Chatterbox TTS.

    TEXT: The text to convert to speech
    VOICE_SAMPLE_PATH: Path to the voice sample file (relative to voice directory)
    """
    if list_voices:
        backend = ChatterboxTTSBackend(voice_sample_dir=voice_dir)
        voices = backend.list_voices()
        if voices:
            click.echo("Available voice samples:")
            for voice in voices:
                click.echo(f"  - {voice}")
        else:
            click.echo("No voice samples found.")
        return

    try:
        if voice_dir:
            # Use the backend class for more control
            backend = ChatterboxTTSBackend(voice_sample_dir=voice_dir)
            backend.initialize(voice_sample_path)

            audio_buffer = backend.synthesize(
                text,
                voice_sample_path,
                exaggeration=exaggeration,
                temperature=temperature,
                seed=seed,
                cfgw=cfgw,
            )

            if output:
                with open(output, "wb") as f:
                    f.write(audio_buffer.read())
                result_path = output
            else:
                # Save to temporary file
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio_buffer.read())
                    result_path = f.name
        else:
            # Use the simpler CLI function
            result_path = generate_audio_cli(text, voice_sample_path, output)

        print(result_path)

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    # Support the old command line interface for backward compatibility
    if len(sys.argv) >= 3 and not any(arg.startswith("-") for arg in sys.argv[1:]):
        # Old style: ./chatterbox.py TEXT VOICE_SAMPLE_PATH
        try:
            result_path = generate_audio_cli(sys.argv[1], sys.argv[2])
            print(result_path)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # New click-based CLI
        main()
