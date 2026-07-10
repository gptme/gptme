"""
TTS API endpoints for server-side text-to-speech.

Provides a server-native TTS endpoint so the webui can speak assistant
messages without requiring a separate gptme-tts server. The endpoint
first tries OpenRouter's ``/api/v1/audio/speech`` when
``OPENROUTER_API_KEY`` is configured; when the key is absent it falls
back to a local `kokoro-onnx <https://github.com/thewh1teagle/kokoro-onnx>`_
model (if installed and model files are present under ``~/.cache/kokoro/``).
"""

import io
import logging
import os
from typing import Any, TypedDict

import flask
import requests

from .auth import require_auth

logger = logging.getLogger(__name__)

tts_api = flask.Blueprint("tts_api", __name__)

OPENROUTER_SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
DEFAULT_MODEL = "x-ai/grok-voice-tts-1.0"
DEFAULT_VOICE = "ara"
REQUEST_TIMEOUT = 30

KOKORO_MODEL_PATH = os.path.expanduser("~/.cache/kokoro/kokoro-v1.0.int8.onnx")
KOKORO_VOICES_PATH = os.path.expanduser("~/.cache/kokoro/voices-v1.0.bin")
KOKORO_DEFAULT_VOICE = "af_heart"

# Optional local TTS via kokoro-onnx (lazy-loaded on first use).
try:
    from kokoro_onnx import Kokoro as _KokoroClass
except ImportError:
    _KokoroClass = None

_kokoro_instance: Any = None


def _get_kokoro() -> Any:
    """Return a cached Kokoro instance, or None if unavailable."""
    global _kokoro_instance
    if _KokoroClass is None:
        return None
    if _kokoro_instance is None:
        if os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH):
            try:
                _kokoro_instance = _KokoroClass(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
                logger.info("Loaded local Kokoro TTS model from %s", KOKORO_MODEL_PATH)
            except Exception as e:
                logger.warning("Failed to load Kokoro TTS model: %s", e)
    return _kokoro_instance


def _synthesize_kokoro(text: str) -> flask.Response | tuple[dict[str, str], int]:
    """Synthesize speech with the local kokoro-onnx model."""
    kokoro = _get_kokoro()
    if kokoro is None:
        if _KokoroClass is None:
            return (
                {
                    "error": (
                        "No TTS backend available: OPENROUTER_API_KEY not set and"
                        " kokoro-onnx is not installed."
                        " Install it with: pip install kokoro-onnx soundfile"
                    )
                },
                503,
            )
        return (
            {
                "error": (
                    "No TTS backend available: OPENROUTER_API_KEY not set and"
                    " kokoro-onnx model files not found under ~/.cache/kokoro/."
                    " Download kokoro-v1.0.int8.onnx and voices-v1.0.bin from"
                    " https://github.com/thewh1teagle/kokoro-onnx/releases"
                )
            },
            503,
        )

    try:
        import soundfile as sf

        samples, sample_rate = kokoro.create(
            text, voice=KOKORO_DEFAULT_VOICE, speed=1.0, lang="en-us"
        )
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV")
        buf.seek(0)
        return flask.Response(
            buf.read(),
            content_type="audio/wav",
            headers={"X-TTS-Backend": "kokoro-onnx"},
        )
    except ImportError:
        return (
            {
                "error": (
                    "soundfile is required for kokoro-onnx TTS."
                    " Install it with: pip install soundfile"
                )
            },
            503,
        )
    except Exception as e:
        logger.error("Kokoro TTS synthesis failed: %s", e)
        return {"error": "Local TTS synthesis failed"}, 500


class TTSRequest(TypedDict, total=False):
    text: str
    model: str
    voice: str


def _optional_string(
    data: dict[str, Any], field: str, default: str
) -> tuple[str | None, tuple[dict[str, str], int] | None]:
    value = data.get(field)
    if value is None or value == "":
        return default, None
    if not isinstance(value, str):
        return None, ({"error": f"{field} must be a string"}, 400)
    return value, None


@tts_api.route("/api/v2/audio/speech", methods=["POST"])
@require_auth
def synthesize_speech():
    """Synthesize speech from text.

    Tries OpenRouter first (when ``OPENROUTER_API_KEY`` is configured), then
    falls back to local kokoro-onnx TTS if the key is absent.

    Request body (JSON):
        - ``text`` (required): Text to speak.
        - ``model`` (optional): OpenRouter speech model ID (ignored for kokoro).
          Default: ``x-ai/grok-voice-tts-1.0``.
        - ``voice`` (optional): Voice name for the OpenRouter model (ignored for
          kokoro, which always uses ``af_heart``). Default: ``ara``.

    Returns:
        WAV audio binary with ``Content-Type: audio/wav``.

    When ``OPENROUTER_API_KEY`` is absent, requires ``kokoro-onnx`` and
    ``soundfile`` to be installed and model files in ``~/.cache/kokoro/``.
    """
    raw_data = flask.request.get_json(silent=True)
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    raw_text = data.get("text")
    if raw_text is not None and not isinstance(raw_text, str):
        return {"error": "text must be a string"}, 400

    text = (raw_text or "").strip()
    if not text:
        return {"error": "text is required"}, 400
    if len(text) > 1000:
        return {"error": "text too long (max 1000 characters)"}, 400

    from ..config import get_config

    config = get_config()
    api_key = config.get_env("OPENROUTER_API_KEY")

    if not api_key:
        return _synthesize_kokoro(text)

    model, error = _optional_string(data, "model", DEFAULT_MODEL)
    if error is not None:
        return error
    assert model is not None
    voice, error = _optional_string(data, "voice", DEFAULT_VOICE)
    if error is not None:
        return error
    assert voice is not None

    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "wav",
        "speed": 1.0,
    }

    try:
        resp = requests.post(
            OPENROUTER_SPEECH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        logger.error("OpenRouter TTS request timed out after %ss", REQUEST_TIMEOUT)
        return {"error": "TTS request timed out"}, 504
    except requests.exceptions.ConnectionError as e:
        logger.error("OpenRouter TTS connection error: %s", e)
        return {"error": "TTS service unavailable"}, 502

    if not resp.ok:
        logger.error(
            "OpenRouter TTS returned %s: %s",
            resp.status_code,
            resp.text[:500],
        )
        return {"error": f"TTS provider error: {resp.status_code}"}, 502

    return flask.Response(
        resp.content,
        content_type="audio/wav",
        headers={"X-OpenRouter-Model": model},
    )
