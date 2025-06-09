#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi>=0.109.0",
#   "uvicorn>=0.27.0",
#   "click",
#   "gradio-client",
#   "kokoro>=0.9.0",
#   "scipy",
# ]
# ///
"""
Multi-backend TTS server supporting Kokoro and Chatterbox TTS.

Usage:
    ./tts_server.py --backend kokoro
    ./tts_server.py --backend chatterbox

API Endpoints:
    GET /tts?text=Hello&voice=af_heart&speed=1.0 - Convert text to speech
    GET /health - Check server health and backend info
    GET /voices - List available voices
    GET /backends - List available backends
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from importlib.util import find_spec
from pathlib import Path
from textwrap import shorten
from typing import Literal

import click
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize backend on startup."""
    global current_backend

    try:
        if backend_name == "kokoro":
            current_backend = TTSBackendLoader.load_kokoro_backend(
                lang_code=os.getenv("TTS_LANG", "a"),
                voice=os.getenv("TTS_VOICE", "af_heart"),
            )
        elif backend_name == "chatterbox":
            current_backend = TTSBackendLoader.load_chatterbox_backend(
                voice_sample_dir=os.getenv("TTS_VOICE_DIR"),
                voice=os.getenv("TTS_VOICE"),
            )
        else:
            raise ValueError(f"Unknown backend: {backend_name}")

        log.info(f"Successfully initialized {backend_name} backend")

    except Exception as e:
        log.error(f"Failed to initialize backend: {e}")
        raise

    yield

    # TODO: cleanup


# Initialize FastAPI app
app = FastAPI(title="Multi-Backend TTS Server", lifespan=lifespan)

# Global variables
current_backend = None
backend_name = "kokoro"  # Default backend


class TTSBackendLoader:
    """Dynamic backend loader for TTS backends."""

    @staticmethod
    def load_kokoro_backend(lang_code: str = "a", voice: str = "af_heart"):
        """Load and initialize Kokoro TTS backend."""
        try:
            # Import the module
            sys.path.insert(0, str(Path(__file__).parent))
            from tts_kokoro import KokoroTTSBackend  # fmt: skip

            backend = KokoroTTSBackend(lang_code=lang_code, voice=voice)
            backend.initialize(voice)
            return backend

        except ImportError as e:
            raise ImportError(
                f"Failed to import Kokoro backend: {e}. Install dependencies with: pip install kokoro scipy soundfile numpy"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Kokoro backend: {e}") from e

    @staticmethod
    def load_chatterbox_backend(
        voice_sample_dir: str | None = None, voice: str | None = None
    ):
        """Load and initialize Chatterbox TTS backend."""
        try:
            # Import the module
            sys.path.insert(0, str(Path(__file__).parent))
            from tts_chatterbox import ChatterboxTTSBackend  # fmt: skip

            backend = ChatterboxTTSBackend(voice_sample_dir=voice_sample_dir)
            backend.initialize(voice)
            return backend

        except ImportError as e:
            raise ImportError(
                f"Failed to import Chatterbox backend: {e}. Install dependencies with: pip install gradio-client"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Chatterbox backend: {e}") from e

    @staticmethod
    def get_available_backends() -> list[str]:
        """Get list of available backends."""
        backends = []

        # Check if Kokoro is available
        if find_spec("kokoro"):
            backends.append("kokoro")

        # Check if Chatterbox is available
        if os.getenv("HF_TOKEN"):
            if find_spec("gradio_client"):
                backends.append("chatterbox")

        return backends


def get_backend():
    """Get the current backend instance."""
    global current_backend
    if current_backend is None:
        raise HTTPException(status_code=500, detail="Backend not initialized")
    return current_backend


@app.get("/")
async def root():
    """Root endpoint with server information."""
    return {
        "message": "Multi-Backend TTS Server",
        "backend": backend_name,
        "endpoints": {
            "tts": "/tts?text=Hello&voice=af_heart&speed=1.0",
            "health": "/health",
            "voices": "/voices",
            "backends": "/backends",
        },
    }


@app.get("/health")
async def health():
    """Health check endpoint with backend information."""
    backend = get_backend()

    try:
        backend_info = backend.get_info()
        return {"status": "healthy", "backend": backend_name, **backend_info}
    except Exception as e:
        log.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Backend health check failed: {e}"
        ) from e


@app.get("/voices")
async def list_voices():
    """List available voices for the current backend."""
    backend = get_backend()

    try:
        if hasattr(backend, "list_voices"):
            voices = backend.list_voices()
        else:
            voices = []

        return {"backend": backend_name, "voices": voices, "count": len(voices)}
    except Exception as e:
        log.error(f"Failed to list voices: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to list voices: {e}"
        ) from e


@app.get("/backends")
async def list_backends():
    """List available TTS backends."""
    try:
        available = TTSBackendLoader.get_available_backends()
        return {
            "current": backend_name,
            "available": available,
            "descriptions": {
                "kokoro": "Local neural TTS with multiple languages and voices",
                "chatterbox": "Cloud-based TTS with voice cloning capabilities",
            },
        }
    except Exception as e:
        log.error(f"Failed to list backends: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to list backends: {e}"
        ) from e


@app.get("/tts")
async def text_to_speech(
    text: str,
    voice: str | None = None,
    speed: float = 1.0,
    # Chatterbox-specific parameters
    exaggeration: float = 0.5,
    temperature: float = 0.8,
    seed: int = 0,
    cfgw: float = 0.5,
) -> StreamingResponse:
    """Convert text to speech and return audio stream."""
    backend = get_backend()

    if not text.strip():
        raise HTTPException(status_code=400, detail="Text parameter cannot be empty")

    if speed <= 0 or speed > 3.0:
        raise HTTPException(status_code=400, detail="Speed must be between 0 and 3.0")

    try:
        log.info(
            f"Generating audio: {shorten(text, 50, placeholder='...')} (backend: {backend_name})"
        )

        # Prepare synthesis parameters based on backend
        if backend_name == "kokoro":
            audio_buffer = await asyncio.to_thread(
                backend.synthesize, text=text, voice=voice, speed=speed
            )
        elif backend_name == "chatterbox":
            audio_buffer = await asyncio.to_thread(
                backend.synthesize,
                text=text,
                voice=voice,
                speed=speed,
                exaggeration=exaggeration,
                temperature=temperature,
                seed=seed,
                cfgw=cfgw,
            )
        else:
            raise HTTPException(
                status_code=500, detail=f"Unknown backend: {backend_name}"
            )

        return StreamingResponse(
            audio_buffer,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="speech.wav"'},
        )

    except ValueError as e:
        log.error(f"Invalid input: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error(f"Failed to generate speech: {e}")
        raise HTTPException(
            status_code=500, detail=f"Speech generation failed: {e}"
        ) from e


@click.command()
@click.option("--port", default=8000, help="Port to run the server on")
@click.option("--host", default="127.0.0.1", help="Host to run the server on")
@click.option(
    "--backend",
    type=click.Choice(["kokoro", "chatterbox"]),
    default="kokoro",
    help="TTS backend to use",
)
@click.option("--voice", help="Default voice to use")
@click.option(
    "--lang",
    default="a",
    help="Language code for Kokoro (a=American English, b=British English, etc)",
)
@click.option("--voice-dir", help="Directory containing voice samples for Chatterbox")
@click.option("--list-voices", is_flag=True, help="List available voices and exit")
@click.option("--list-backends", is_flag=True, help="List available backends and exit")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(
    port: int,
    host: str,
    backend: Literal["kokoro", "chatterbox"],
    voice: str | None,
    lang: str,
    voice_dir: str | None,
    list_voices: bool,
    list_backends: bool,
    verbose: bool,
):
    """Run the multi-backend TTS server."""
    global backend_name, current_backend

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    backend_name = backend

    # Set environment variables for backend configuration
    if voice:
        os.environ["TTS_VOICE"] = voice
    if lang:
        os.environ["TTS_LANG"] = lang
    if voice_dir:
        os.environ["TTS_VOICE_DIR"] = voice_dir

    if list_backends:
        available = TTSBackendLoader.get_available_backends()
        click.echo("Available TTS backends:")
        for b in available:
            click.echo(f"  - {b}")
        if not available:
            click.echo(
                "  No backends available. Install dependencies for kokoro or chatterbox."
            )
        return

    if list_voices:
        try:
            # Initialize the specified backend temporarily
            if backend == "kokoro":
                temp_backend = TTSBackendLoader.load_kokoro_backend(
                    lang, voice or "af_heart"
                )
            elif backend == "chatterbox":
                temp_backend = TTSBackendLoader.load_chatterbox_backend(
                    voice_dir, voice
                )
            else:
                click.echo(f"Unknown backend: {backend}", err=True)
                return

            voices = temp_backend.list_voices()
            click.echo(f"Available voices for {backend}:")
            for v in voices:
                click.echo(f"  - {v}")

        except Exception as e:
            click.echo(f"Error listing voices: {e}", err=True)
            return

    # Check if the selected backend is available
    available_backends = TTSBackendLoader.get_available_backends()
    if backend not in available_backends:
        click.echo(f"Backend '{backend}' is not available.", err=True)
        click.echo(f"Available backends: {available_backends}", err=True)
        if backend == "kokoro":
            click.echo(
                "Install Kokoro dependencies: pip install kokoro scipy soundfile numpy",
                err=True,
            )
        elif backend == "chatterbox":
            click.echo(
                "Install Chatterbox dependencies: pip install gradio-client", err=True
            )
            click.echo("Set HF_TOKEN environment variable", err=True)
        sys.exit(1)

    log.info(f"Starting TTS server on {host}:{port}")
    log.info(f"Using backend: {backend}")
    if voice:
        log.info(f"Default voice: {voice}")
    if backend == "kokoro":
        log.info(f"Language: {lang}")
    if voice_dir:
        log.info(f"Voice directory: {voice_dir}")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
