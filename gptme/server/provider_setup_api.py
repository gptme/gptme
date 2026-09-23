"""Provider setup API for gptme-server.

Exposes a background-OAuth flow for subscription providers (ChatGPT, Grok, OpenRouter)
so the webui can trigger and poll provider setup without a CLI prompt.

Endpoints:
  POST /api/v2/provider/setup          — start an OAuth flow; returns {setup_id}
  GET  /api/v2/provider/setup/<id>     — poll status: pending | connected | error
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Literal

import flask
from flask import jsonify, request

from .auth import require_auth
from .openapi_docs import (
    ErrorResponse,
    ProviderSetupStartRequest,
    ProviderSetupStartResponse,
    ProviderSetupStatusResponse,
    api_doc,
)

logger = logging.getLogger(__name__)

provider_setup_api = flask.Blueprint("provider_setup_api", __name__)

# Real Flask app reference, captured once when the blueprint is registered.
# Background threads cannot use flask.current_app (a proxy that requires a request
# context), so _apply_runtime_model receives this reference instead.
_flask_app: flask.Flask | None = None


@provider_setup_api.record_once
def _capture_app(state: flask.blueprints.BlueprintSetupState) -> None:
    global _flask_app
    _flask_app = state.app


# In-memory registry of setup flows. Keyed by setup_id (UUID string).
# Completed/failed/cancelled entries expire; pending ones are kept until they
# finish so a poll can still observe the terminal state.
_setups: dict[str, dict[str, Any]] = {}
_cancels: dict[str, threading.Event] = {}
_setups_lock = threading.Lock()

_SETUP_TTL_S = 600
_MAX_SETUPS = 32

ProviderName = Literal["openai-subscription", "grok-subscription", "openrouter-pkce"]

_VALID_PROVIDERS: set[str] = {
    "openai-subscription",
    "grok-subscription",
    "openrouter-pkce",
}


def _prune_setups_locked() -> None:
    """Drop expired non-pending records and cap registry size. Caller holds lock."""
    now = time.monotonic()
    stale = [
        sid
        for sid, entry in _setups.items()
        if entry.get("status") != "pending"
        and now - float(entry.get("created_at") or 0) > _SETUP_TTL_S
    ]
    for sid in stale:
        _setups.pop(sid, None)
        _cancels.pop(sid, None)
    if len(_setups) <= _MAX_SETUPS:
        return
    overflow = sorted(
        (
            (float(entry.get("created_at") or 0), sid)
            for sid, entry in _setups.items()
            if entry.get("status") != "pending"
        )
    )
    to_drop = len(_setups) - _MAX_SETUPS
    for _, sid in overflow[:to_drop]:
        _setups.pop(sid, None)
        _cancels.pop(sid, None)


def _still_pending(setup_id: str) -> bool:
    with _setups_lock:
        entry = _setups.get(setup_id)
        return entry is not None and entry.get("status") == "pending"


def _apply_runtime_model(app: flask.Flask, model: str) -> None:
    """Persist models.default and apply it to the running server when possible."""
    from .api_v2 import _persist_default_model

    with app.app_context():
        restart_required = _persist_default_model(model)
    if restart_required:
        logger.warning(
            "Persisted default model %s but could not apply it in-process; restart required",
            model,
        )


def _store_oauth_url(setup_id: str, url: str) -> None:
    """Store the OAuth URL in the setup entry so poll can expose it."""
    with _setups_lock:
        entry = _setups.get(setup_id)
        if entry is not None and entry.get("status") == "pending":
            entry["oauth_url"] = url


def _run_oauth(
    setup_id: str,
    provider: str,
    app: flask.Flask,
    cancel_event: threading.Event,
) -> None:
    """Background thread: run blocking OAuth, update _setups on completion."""

    def _on_url(url: str) -> None:
        _store_oauth_url(setup_id, url)

    try:
        if provider == "openai-subscription":
            import gptme.llm.llm_openai_subscription as _openai_sub

            from ..llm.models import get_recommended_model

            _openai_sub.oauth_authenticate(
                cancel_event=cancel_event, on_url_ready=_on_url
            )
            model = (
                f"openai-subscription/{get_recommended_model('openai-subscription')}"
            )

        elif provider == "grok-subscription":
            import gptme.llm.llm_grok_subscription as _grok_sub

            from ..llm.models import get_recommended_model

            _grok_sub.oauth_authenticate(
                cancel_event=cancel_event, on_url_ready=_on_url
            )
            model = f"grok-subscription/{get_recommended_model('grok-subscription')}"

        elif provider == "openrouter-pkce":
            from ..config import set_config_value
            from ..llm.llm_openrouter_subscription import oauth_get_api_key
            from ..llm.models import get_recommended_model

            api_key = oauth_get_api_key(cancel_event=cancel_event, on_url_ready=_on_url)
            if not _still_pending(setup_id):
                logger.info("Ignoring OpenRouter key for cancelled setup %s", setup_id)
                return
            set_config_value(
                "env.OPENROUTER_API_KEY", api_key, reload=False, local=True
            )
            os.environ["OPENROUTER_API_KEY"] = api_key
            model = f"openrouter/{get_recommended_model('openrouter')}"

        else:
            raise ValueError(f"Unknown provider: {provider}")

        if not _still_pending(setup_id):
            logger.info("Ignoring completed OAuth for cancelled setup %s", setup_id)
            return

        _apply_runtime_model(app, model)
        logger.info("Provider setup complete: %s → %s", provider, model)

        with _setups_lock:
            entry = _setups.get(setup_id)
            if entry is None or entry.get("status") != "pending":
                return
            entry["status"] = "connected"
            entry["model"] = model

    except Exception as exc:
        logger.warning("Provider setup failed for %s: %s", provider, exc)
        with _setups_lock:
            entry = _setups.get(setup_id)
            if entry is None or entry.get("status") != "pending":
                return
            entry["status"] = "error"
            entry["error"] = str(exc)


@provider_setup_api.route("/api/v2/provider/setup", methods=["POST"])
@require_auth
@api_doc(
    summary="Start subscription provider OAuth setup",
    description=(
        "Start a background OAuth/PKCE flow for a subscription provider "
        "(openai-subscription, grok-subscription, or openrouter-pkce). "
        "Opens the system browser on the server host and waits for the local "
        "OAuth callback. Returns a setup_id that the client polls via "
        "GET /api/v2/provider/setup/{setup_id}. Starting a new flow for the "
        "same provider cancels any pending one so the callback port can be reused."
    ),
    request_body=ProviderSetupStartRequest,
    responses={200: ProviderSetupStartResponse, 400: ErrorResponse},
    tags=["user"],
)
def start_provider_setup():
    """Start an OAuth flow for a subscription provider.

    Request body (JSON):
      provider: "openai-subscription" | "grok-subscription" | "openrouter-pkce"

    Response:
      {"setup_id": "<uuid>", "status": "pending"}
    """
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
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

    cancel_event = threading.Event()
    setup_id = str(uuid.uuid4())
    assert _flask_app is not None, "Blueprint not registered with a Flask app"
    app = _flask_app

    # Cancel any previous pending setup for the same provider so the callback
    # port is not double-occupied if the user retries.
    with _setups_lock:
        _prune_setups_locked()
        for sid, entry in list(_setups.items()):
            if entry.get("provider") == provider and entry.get("status") == "pending":
                entry["status"] = "cancelled"
                event = _cancels.get(sid)
                if event is not None:
                    event.set()
        _setups[setup_id] = {
            "provider": provider,
            "status": "pending",
            "model": None,
            "error": None,
            "oauth_url": None,
            "created_at": time.monotonic(),
        }
        _cancels[setup_id] = cancel_event

    t = threading.Thread(
        target=_run_oauth,
        args=(setup_id, provider, app, cancel_event),
        daemon=True,
        name=f"oauth-{provider}-{setup_id[:8]}",
    )
    t.start()

    return jsonify({"setup_id": setup_id, "status": "pending"})


@provider_setup_api.route("/api/v2/provider/setup/<string:setup_id>", methods=["GET"])
@require_auth
@api_doc(
    summary="Poll subscription provider OAuth setup",
    description=(
        "Return the current status of a provider setup started via "
        "POST /api/v2/provider/setup. Poll until status is connected, error, "
        "or cancelled. Unknown or expired setup IDs return 404."
    ),
    responses={200: ProviderSetupStatusResponse, 404: ErrorResponse},
    tags=["user"],
)
def poll_provider_setup(setup_id: str):
    """Poll the status of an in-progress or completed provider setup.

    Response:
      {"setup_id": "...", "provider": "...", "status": "pending|connected|error|cancelled",
       "model": "<provider/model>" | null, "error": "<msg>" | null}
    """
    with _setups_lock:
        _prune_setups_locked()
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
            "oauth_url": entry.get("oauth_url"),
        }
    )
