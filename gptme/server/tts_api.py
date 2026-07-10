"""
TTS API endpoints for server-side text-to-speech.

Provides a server-native TTS endpoint so the webui can speak assistant
messages without requiring a separate gptme-tts server. The endpoint
first tries OpenRouter's ``/api/v1/audio/speech`` when
``OPENROUTER_API_KEY`` is configured; when the key is absent it falls
back to a local `kokoro-onnx <https://github.com/thewh1teagle/kokoro-onnx>`_
model (if installed and model files are present under ``~/.cache/kokoro/``).
"""

import glob
import io
import logging
import os
import threading
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

KOKORO_CACHE_DIR = os.path.expanduser("~/.cache/kokoro")
KOKORO_MODEL_PATH = os.path.join(KOKORO_CACHE_DIR, "kokoro-v1.0.int8.onnx")
KOKORO_VOICES_PATH = os.path.join(KOKORO_CACHE_DIR, "voices-v1.0.bin")
KOKORO_DEFAULT_VOICE = "af_heart"

# Known kokoro-onnx release filenames, in preference order, checked before
# falling back to a glob match (so unknown/future release names still work).
_KOKORO_MODEL_CANDIDATES = (
    "kokoro-v1.0.int8.onnx",
    "kokoro-v1.0.fp16.onnx",
    "kokoro-v1.0.onnx",
)
_KOKORO_VOICES_CANDIDATES = ("voices-v1.0.bin",)


def _find_kokoro_files() -> tuple[str, str] | None:
    """Locate the Kokoro model and voices files.

    Checks the ``KOKORO_MODEL_PATH``/``KOKORO_VOICES_PATH`` module constants
    first (kept test-patchable and for backwards compatibility); if both
    exist, they're used as-is. Otherwise searches ``KOKORO_CACHE_DIR`` for any
    known kokoro-onnx release filename, falling back to a sorted glob match
    so newer/alternate model releases work without code changes.

    Returns None if no matching model+voices pair is found.
    """
    if os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH):
        return KOKORO_MODEL_PATH, KOKORO_VOICES_PATH

    model_path = None
    for name in _KOKORO_MODEL_CANDIDATES:
        candidate = os.path.join(KOKORO_CACHE_DIR, name)
        if os.path.exists(candidate):
            model_path = candidate
            break
    if model_path is None:
        matches = sorted(glob.glob(os.path.join(KOKORO_CACHE_DIR, "kokoro*.onnx")))
        if matches:
            model_path = matches[0]

    voices_path = None
    for name in _KOKORO_VOICES_CANDIDATES:
        candidate = os.path.join(KOKORO_CACHE_DIR, name)
        if os.path.exists(candidate):
            voices_path = candidate
            break
    if voices_path is None:
        matches = sorted(glob.glob(os.path.join(KOKORO_CACHE_DIR, "voices*.bin")))
        if matches:
            voices_path = matches[0]

    if model_path is None or voices_path is None:
        return None
    return model_path, voices_path


# Optional local TTS via kokoro-onnx (lazy-loaded on first use).
try:
    from kokoro_onnx import Kokoro as _KokoroClass
except ImportError:
    _KokoroClass = None

_kokoro_instance: Any = None
_kokoro_lock = threading.Lock()


def _get_kokoro() -> Any:
    """Return a cached Kokoro instance, or None if unavailable.

    Uses double-checked locking so concurrent first requests don't race to
    load the (large) ONNX model twice.
    """
    global _kokoro_instance
    if _KokoroClass is None:
        return None
    if _kokoro_instance is not None:
        return _kokoro_instance
    with _kokoro_lock:
        if _kokoro_instance is None:
            found = _find_kokoro_files()
            if found is not None:
                model_path, voices_path = found
                try:
                    _kokoro_instance = _KokoroClass(model_path, voices_path)
                    logger.info("Loaded local Kokoro TTS model from %s", model_path)
                except Exception as e:
                    logger.warning("Failed to load Kokoro TTS model: %s", e)
    return _kokoro_instance


def _resolve_kokoro_voice(kokoro: Any, requested_voice: str | None) -> str | None:
    """Pick a voice to pass to kokoro, or None if no voices are available.

    Prefers ``requested_voice`` if it exists in ``kokoro.get_voices()``, else
    falls back to ``KOKORO_DEFAULT_VOICE``, else the first available voice.
    If the available voices can't be determined, trusts the caller and uses
    ``requested_voice`` or ``KOKORO_DEFAULT_VOICE`` as-is (today's behavior).
    """
    try:
        available = list(kokoro.get_voices())
    except Exception as e:
        logger.warning("Could not determine available Kokoro voices: %s", e)
        return requested_voice or KOKORO_DEFAULT_VOICE

    if not available:
        return None

    if requested_voice and requested_voice in available:
        return requested_voice
    if KOKORO_DEFAULT_VOICE in available:
        if requested_voice:
            logger.warning(
                "Requested Kokoro voice %r not available, falling back to %r",
                requested_voice,
                KOKORO_DEFAULT_VOICE,
            )
        return KOKORO_DEFAULT_VOICE

    fallback = available[0]
    logger.warning(
        "Requested Kokoro voice %r not available and default %r missing,"
        " falling back to %r",
        requested_voice,
        KOKORO_DEFAULT_VOICE,
        fallback,
    )
    return fallback


def _synthesize_kokoro(
    text: str, requested_voice: str | None = None
) -> flask.Response | tuple[dict[str, str], int]:
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
                    " Download a model (e.g. kokoro-v1.0.int8.onnx) and"
                    " voices-v1.0.bin from"
                    " https://github.com/thewh1teagle/kokoro-onnx/releases"
                    " into ~/.cache/kokoro/."
                )
            },
            503,
        )

    voice = _resolve_kokoro_voice(kokoro, requested_voice)
    if voice is None:
        return {"error": "Kokoro voices file contains no voices"}, 503

    try:
        import soundfile as sf

        samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
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
        - ``voice`` (optional): Voice name. For OpenRouter, defaults to
          ``ara``. For kokoro, honored when it names one of the model's
          available voices, falling back to ``af_heart`` otherwise.

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

    model, error = _optional_string(data, "model", DEFAULT_MODEL)
    if error is not None:
        return error
    assert model is not None
    voice, error = _optional_string(data, "voice", DEFAULT_VOICE)
    if error is not None:
        return error
    assert voice is not None

    if not api_key:
        raw_voice = data.get("voice")
        requested_voice = (
            raw_voice if isinstance(raw_voice, str) and raw_voice else None
        )
        return _synthesize_kokoro(text, requested_voice)

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
