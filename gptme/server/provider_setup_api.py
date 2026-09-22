"""Provider setup API for gptme-server.

Exposes a background-OAuth flow for subscription providers (ChatGPT, Grok, OpenRouter)
so the webui can trigger and poll provider setup without a CLI prompt.

Endpoints:
  POST /api/v2/provider/setup          — start an OAuth flow; returns {setup_id}
  GET  /api/v2/provider/setup/<id>     — poll status: pending | connected | error
"""

import logging
import threading
import uuid
from typing import Literal

import flask
from flask import jsonify, request

from .auth import require_auth

logger = logging.getLogger(__name__)

provider_setup_api = flask.Blueprint("provider_setup_api", __name__)

# In-memory registry of active/completed setup flows.
# Keyed by setup_id (UUID string).
_setups: dict[
    str,
    dict[str, str | None],
] = {}
_setups_lock = threading.Lock()

ProviderName = Literal["openai-subscription", "grok-subscription", "openrouter-pkce"]

_VALID_PROVIDERS: set[str] = {
    "openai-subscription",
    "grok-subscription",
    "openrouter-pkce",
}


def _run_oauth(setup_id: str, provider: str) -> None:
    """Background thread: run blocking OAuth, update _setups on completion."""
    try:
        if provider == "openai-subscription":
            import gptme.llm.llm_openai_subscription as _openai_sub

            from ..llm.models import get_recommended_model

            _openai_sub.oauth_authenticate()
            model = (
                f"openai-subscription/{get_recommended_model('openai-subscription')}"
            )

        elif provider == "grok-subscription":
            import gptme.llm.llm_grok_subscription as _grok_sub

            from ..llm.models import get_recommended_model

            _grok_sub.oauth_authenticate()
            model = f"grok-subscription/{get_recommended_model('grok-subscription')}"

        elif provider == "openrouter-pkce":
            from ..config import set_config_value
            from ..llm.llm_openrouter_subscription import oauth_get_api_key
            from ..llm.models import get_recommended_model

            api_key = oauth_get_api_key()
            set_config_value("env.OPENROUTER_API_KEY", api_key, local=True)
            model = f"openrouter/{get_recommended_model('openrouter')}"

        else:
            raise ValueError(f"Unknown provider: {provider}")

        from ..config import set_config_value

        set_config_value("models.default", model)
        logger.info("Provider setup complete: %s → %s", provider, model)

        with _setups_lock:
            _setups[setup_id]["status"] = "connected"
            _setups[setup_id]["model"] = model

    except Exception as exc:
        logger.warning("Provider setup failed for %s: %s", provider, exc)
        with _setups_lock:
            _setups[setup_id]["status"] = "error"
            _setups[setup_id]["error"] = str(exc)


@provider_setup_api.route("/api/v2/provider/setup", methods=["POST"])
@require_auth
def start_provider_setup():
    """Start an OAuth flow for a subscription provider.

    Request body (JSON):
      provider: "openai-subscription" | "grok-subscription" | "openrouter-pkce"

    Response:
      {"setup_id": "<uuid>", "status": "pending"}
    """
    body = request.get_json(silent=True) or {}
    provider = body.get("provider", "")

    if provider not in _VALID_PROVIDERS:
        return (
            jsonify(
                {
                    "error": f"Unknown provider {provider!r}. "
                    f"Valid providers: {sorted(_VALID_PROVIDERS)}"
                }
            ),
            400,
        )

    # Cancel any previous pending setup for the same provider so the callback
    # port is not double-occupied if the user retries.
    with _setups_lock:
        for _sid, entry in list(_setups.items()):
            if entry["provider"] == provider and entry["status"] == "pending":
                entry["status"] = "cancelled"

    setup_id = str(uuid.uuid4())
    with _setups_lock:
        _setups[setup_id] = {
            "provider": provider,
            "status": "pending",
            "model": None,
            "error": None,
        }

    t = threading.Thread(
        target=_run_oauth,
        args=(setup_id, provider),
        daemon=True,
        name=f"oauth-{provider}-{setup_id[:8]}",
    )
    t.start()

    return jsonify({"setup_id": setup_id, "status": "pending"})


@provider_setup_api.route("/api/v2/provider/setup/<string:setup_id>", methods=["GET"])
@require_auth
def poll_provider_setup(setup_id: str):
    """Poll the status of an in-progress or completed provider setup.

    Response:
      {"setup_id": "...", "provider": "...", "status": "pending|connected|error|cancelled",
       "model": "<provider/model>" | null, "error": "<msg>" | null}
    """
    with _setups_lock:
        entry = _setups.get(setup_id)

    if entry is None:
        return jsonify({"error": f"Unknown setup_id: {setup_id}"}), 404

    return jsonify(
        {
            "setup_id": setup_id,
            "provider": entry["provider"],
            "status": entry["status"],
            "model": entry.get("model"),
            "error": entry.get("error"),
        }
    )
