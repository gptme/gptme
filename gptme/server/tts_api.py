"""
TTS API endpoints for server-side text-to-speech via OpenRouter.

Provides a server-native TTS endpoint so the webui can speak assistant
messages without requiring a separate gptme-tts server. The endpoint
proxies to OpenRouter's ``/api/v1/audio/speech`` and returns WAV audio
that the browser can play natively.
"""

import logging
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
    """Synthesize speech from text via OpenRouter's speech API.

    Request body (JSON):
        - ``text`` (required): Text to speak.
        - ``model`` (optional): OpenRouter speech model ID.
          Default: ``x-ai/grok-voice-tts-1.0``.
        - ``voice`` (optional): Voice name for the model.
          Default: ``ara``.

    Returns:
        WAV audio binary with ``Content-Type: audio/wav``.

    Requires ``OPENROUTER_API_KEY`` to be configured (env or config file).
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
        return {
            "error": "OPENROUTER_API_KEY not configured. Set the environment variable or add it to config."
        }, 400

    model, error = _optional_string(data, "model", DEFAULT_MODEL)
    if error is not None:
        return error
    voice, error = _optional_string(data, "voice", DEFAULT_VOICE)
    if error is not None:
        return error

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
