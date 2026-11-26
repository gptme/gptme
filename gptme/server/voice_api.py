"""Realtime voice for gptme-server.

The server owns voice: when it is enabled (``gptme-server serve --voice`` or
``GPTME_SERVER_VOICE=1``), the server starts one `gptme-voice`_ sidecar per
realtime provider that has an API key (OpenAI Realtime via ``OPENAI_API_KEY``,
xAI Grok Realtime via ``XAI_API_KEY``). Each sidecar listens on loopback only
and requires a per-process secret, so other local processes cannot use it.

Clients never configure a voice URL. They ask the server what works::

    GET  /api/v2/voice                                  -> availability + providers
    POST /api/v2/conversations/<id>/voice/session       -> single-use ticket
    WS   /api/v2/conversations/<id>/voice?ticket=...    -> tunnelled to the sidecar

The ticket keeps the long-lived server token out of WebSocket URLs (browsers
cannot set an ``Authorization`` header on a WebSocket). The sidecar is told
which conversation the call belongs to, reads its context through the API and
appends transcript turns to it as they happen (see the ``/transcript`` route).

.. _gptme-voice: https://github.com/gptme/gptme-contrib/tree/master/packages/gptme-voice
"""

from __future__ import annotations

import atexit
import logging
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlencode

import flask
import requests

from ..config import get_config
from ..dirs import get_logs_dir
from .api_v2_common import _validate_conversation_id
from .auth import require_auth
from .openapi_docs import (
    CONVERSATION_ID_PARAM,
    ErrorResponse,
    VoiceSessionRequest,
    VoiceSessionResponse,
    VoiceStatusResponse,
    api_doc,
)
from .preview_proxy_api import _is_websocket_upgrade, _websocket_tunnel

logger = logging.getLogger(__name__)

voice_api = flask.Blueprint("voice_api", __name__)

# provider id -> (label, API key env var). Ids match gptme-voice's --provider.
VOICE_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("OpenAI Realtime", "OPENAI_API_KEY"),
    "grok": ("xAI Grok Realtime", "XAI_API_KEY"),
}

SECRET_HEADER = "X-Gptme-Voice-Secret"
TICKET_TTL_SECONDS = 60.0
_HEALTH_CACHE_SECONDS = 5.0


def _env(key: str) -> str | None:
    """Read a setting from the environment or the gptme config."""
    value = os.environ.get(key)
    if value:
        return value
    try:
        return get_config().get_env(key)
    except Exception:  # config unavailable in some test setups
        return None


def voice_enabled_from_env() -> bool:
    return (_env("GPTME_SERVER_VOICE") or "").lower() in ("1", "true", "yes")


def _resolve_sidecar_command() -> list[str] | None:
    """Command that starts gptme-voice-server, or None if it is not installed.

    ``GPTME_VOICE_SERVER_CMD`` overrides discovery, e.g. to run it from a
    gptme-contrib checkout: ``uv run --project .../gptme-voice gptme-voice-server``.
    """
    if cmd := _env("GPTME_VOICE_SERVER_CMD"):
        return shlex.split(cmd)
    if exe := shutil.which("gptme-voice-server"):
        return [exe]
    return None


def _workspace_agent_name(workspace: Path) -> str | None:
    """The ``[agent] name`` declared in the workspace's gptme.toml, if any."""
    from ..config import get_project_config  # fmt: skip

    try:
        config = get_project_config(workspace)
    except Exception:
        return None
    name = getattr(getattr(config, "agent", None), "name", None)
    return name.strip() if isinstance(name, str) and name.strip() else None


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class VoiceSidecar:
    provider: str
    port: int
    proc: subprocess.Popen
    _healthy_at: float = 0.0
    _checked_at: float = 0.0

    def running(self) -> bool:
        return self.proc.poll() is None

    def healthy(self) -> bool:
        if not self.running():
            return False
        now = time.monotonic()
        if now - self._checked_at < _HEALTH_CACHE_SECONDS:
            return self._healthy_at == self._checked_at
        self._checked_at = now
        try:
            ok = requests.get(f"http://127.0.0.1:{self.port}/", timeout=1).ok
        except requests.RequestException:
            ok = False
        if ok:
            self._healthy_at = now
        return ok


@dataclass
class _Ticket:
    conversation_id: str
    provider: str
    expires_at: float


@dataclass
class VoiceManager:
    """Starts and tracks the gptme-voice sidecars for this server."""

    server_url: str
    server_token: str | None
    workspace: Path
    # Default model for lookups the voice model hands to gptme.
    model: str | None = None
    command: list[str] | None = None
    sidecars: dict[str, VoiceSidecar] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    reason: str | None = None
    secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    _tickets: dict[str, _Ticket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        if self.command is None:
            self.command = _resolve_sidecar_command()
        if self.command is None:
            self.reason = (
                "gptme-voice is not installed "
                "(install it, or set GPTME_VOICE_SERVER_CMD)"
            )
            logger.warning("Voice disabled: %s", self.reason)
            return

        for provider, (_label, key_env) in VOICE_PROVIDERS.items():
            if not _env(key_env):
                self.unavailable[provider] = f"{key_env} not set"
                continue
            self._spawn(provider)

        if not self.sidecars:
            self.reason = "no realtime provider API key configured"
            logger.warning("Voice disabled: %s", self.reason)
        atexit.register(self.stop)

    def _spawn(self, provider: str) -> None:
        assert self.command is not None
        port = _free_loopback_port()
        env = dict(os.environ)
        # Forward provider keys from gptme config, so keys that live only in
        # config.local.toml reach the sidecar.
        for _label, key_env in VOICE_PROVIDERS.values():
            if (value := _env(key_env)) and key_env not in env:
                env[key_env] = value
        # Who the voice model says it is: the workspace's agent (e.g. an agent
        # running its own server) or else gptme. Without this, gptme-voice falls
        # back to a generic "Agent".
        # Lookups the voice model hands off run this server's gptme with its
        # default model, not whatever gptme is first on PATH with gptme-voice's
        # hardcoded fallback model (which the provider key may not allow).
        if not env.get("GPTME_VOICE_SUBAGENT_PATH"):
            server_gptme = Path(sys.executable).parent / "gptme"
            if server_gptme.exists():
                env["GPTME_VOICE_SUBAGENT_PATH"] = str(server_gptme)
        if self.model and not (
            env.get("GPTME_VOICE_SUBAGENT_MODEL")
            or env.get("GPTME_VOICE_SUBAGENT_MODEL_FAST")
            or env.get("GPTME_VOICE_SUBAGENT_MODEL_SMART")
        ):
            env["GPTME_VOICE_SUBAGENT_MODEL"] = self.model
        if not env.get("GPTME_VOICE_AGENT_NAME"):
            env["GPTME_VOICE_AGENT_NAME"] = (
                _workspace_agent_name(self.workspace) or "gptme"
            )
        env.update(
            {
                "GPTME_VOICE_BROWSER_SECRET": self.secret,
                "GPTME_VOICE_GPTME_SERVER_URL": self.server_url,
                # gptme-voice skips server calls when the key is empty; with
                # auth disabled any non-empty value is accepted.
                "GPTME_VOICE_GPTME_SERVER_KEY": self.server_token or "auth-disabled",
            }
        )
        args = [
            *self.command,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--provider",
            provider,
            "--workspace",
            str(self.workspace),
            "--enable-browser-transport",
        ]
        try:
            proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL)
        except OSError as exc:
            self.unavailable[provider] = f"failed to start gptme-voice: {exc}"
            logger.warning("Voice sidecar for %s failed to start: %s", provider, exc)
            return
        self.sidecars[provider] = VoiceSidecar(provider=provider, port=port, proc=proc)
        logger.info("Started gptme-voice (%s) on 127.0.0.1:%d", provider, port)

    def stop(self) -> None:
        for sidecar in self.sidecars.values():
            if sidecar.running():
                sidecar.proc.terminate()
        for sidecar in self.sidecars.values():
            try:
                sidecar.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sidecar.proc.kill()

    def status(self) -> dict:
        providers = []
        for provider, (label, _key_env) in VOICE_PROVIDERS.items():
            sidecar = self.sidecars.get(provider)
            if sidecar is None:
                providers.append(
                    {
                        "id": provider,
                        "label": label,
                        "available": False,
                        "reason": self.unavailable.get(provider, self.reason),
                    }
                )
                continue
            ok = sidecar.healthy()
            providers.append(
                {
                    "id": provider,
                    "label": label,
                    "available": ok,
                    "reason": None if ok else "voice service not responding",
                }
            )
        available = [p["id"] for p in providers if p["available"]]
        return {
            "available": bool(available),
            "reason": None if available else (self.reason or "voice not ready"),
            "providers": providers,
            "default_provider": available[0] if available else None,
        }

    def issue_ticket(self, conversation_id: str, provider: str) -> str:
        ticket = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._tickets = {
                k: t for k, t in self._tickets.items() if t.expires_at > now
            }
            self._tickets[ticket] = _Ticket(
                conversation_id, provider, now + TICKET_TTL_SECONDS
            )
        return ticket

    def redeem_ticket(self, ticket: str, conversation_id: str) -> _Ticket | None:
        with self._lock:
            entry = self._tickets.pop(ticket, None)
        if entry is None or entry.expires_at < time.monotonic():
            return None
        # Conversation ids are not secret (and may be non-ASCII, which
        # compare_digest rejects); the ticket itself was matched exactly.
        if entry.conversation_id != conversation_id:
            return None
        return entry


_manager: VoiceManager | None = None


def init_voice(
    *,
    host: str,
    port: int,
    token: str | None,
    workspace: Path,
    model: str | None = None,
) -> None:
    """Start the voice sidecars. Called by ``gptme-server serve`` when enabled."""
    global _manager
    connect_host = host
    if host in ("0.0.0.0", "", "localhost"):
        connect_host = "127.0.0.1"
    elif host == "::":
        connect_host = "::1"
    if ":" in connect_host:
        connect_host = f"[{connect_host}]"
    _manager = VoiceManager(
        server_url=f"http://{connect_host}:{port}",
        server_token=token,
        workspace=workspace,
        model=model,
    )
    _manager.start()


def get_voice_manager() -> VoiceManager | None:
    return _manager


@voice_api.route("/api/v2/voice", methods=["GET"])
@require_auth
@api_doc(
    summary="Realtime voice status",
    description="Whether realtime voice works on this server, and with which providers. "
    "Voice is configured on the server (gptme-server serve --voice); clients show "
    "voice only when this reports an available provider.",
    responses={200: VoiceStatusResponse},
    tags=["voice"],
)
def api_voice_status():
    """Whether realtime voice works on this server, and with which providers."""
    if _manager is None:
        return flask.jsonify(
            {
                "available": False,
                "reason": "voice is not enabled on this server",
                "providers": [],
                "default_provider": None,
            }
        )
    return flask.jsonify(_manager.status())


@voice_api.route(
    "/api/v2/conversations/<string:conversation_id>/voice/session",
    methods=["POST"],
)
@require_auth
@api_doc(
    summary="Start a voice call (ticket)",
    description="Issue a single-use, short-lived ticket for opening the voice "
    "WebSocket of a conversation. Browsers cannot set an Authorization header on a "
    "WebSocket, so the ticket keeps the server token out of the URL.",
    request_body=VoiceSessionRequest,
    responses={
        200: VoiceSessionResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        503: ErrorResponse,
    },
    parameters=[CONVERSATION_ID_PARAM],
    tags=["voice"],
)
def api_voice_session(conversation_id: str):
    """Issue a single-use ticket for opening the voice WebSocket."""
    if error := _validate_conversation_id(conversation_id):
        return error
    if _manager is None:
        return flask.jsonify({"error": "voice is not enabled on this server"}), 404
    if not (get_logs_dir() / conversation_id).is_dir():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    body = flask.request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400
    requested = body.get("provider")
    if requested is not None and not isinstance(requested, str):
        return flask.jsonify({"error": "provider must be a string"}), 400
    status = _manager.status()
    provider = requested or status["default_provider"]
    if provider not in VOICE_PROVIDERS:
        return flask.jsonify({"error": f"unknown voice provider: {provider}"}), 400
    entry = next(p for p in status["providers"] if p["id"] == provider)
    if not entry["available"]:
        return (
            flask.jsonify(
                {"error": f"{entry['label']} unavailable: {entry['reason']}"}
            ),
            503,
        )

    ticket = _manager.issue_ticket(conversation_id, provider)
    return flask.jsonify(
        {
            "provider": provider,
            "ticket": ticket,
            # One encoded path component: ids may contain '?' or '#'.
            "path": f"/api/v2/conversations/{quote(conversation_id, safe='')}/voice",
            "expires_in": TICKET_TTL_SECONDS,
        }
    )


# websocket=True: Werkzeug routes an Upgrade request as ws://, and a plain
# HTTP rule would reject it with WebsocketMismatch (400) before the view runs.
@voice_api.route(
    "/api/v2/conversations/<string:conversation_id>/voice",
    methods=["GET"],
    websocket=True,
)
@api_doc(
    summary="Voice call WebSocket",
    description="WebSocket upgrade (authenticated by ?ticket= from .../voice/session), "
    "tunnelled to the voice sidecar: binary PCM16 audio frames in both directions, "
    "JSON control frames ('ready', 'audio_end', 'clear', 'sound_cue'; client sends "
    "'commit' and 'text').",
    responses={101: None, 400: ErrorResponse, 401: ErrorResponse, 503: ErrorResponse},
    parameters=[
        CONVERSATION_ID_PARAM,
        {
            "name": "ticket",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
            "description": "Single-use ticket from .../voice/session",
        },
    ],
    tags=["voice"],
)
def api_voice_websocket(conversation_id: str):
    """Tunnel the browser's voice WebSocket to the provider's sidecar.

    Authenticated by the single-use ticket from ``.../voice/session``.
    """
    if error := _validate_conversation_id(conversation_id):
        return error
    if not _is_websocket_upgrade(flask.request):
        return flask.jsonify({"error": "WebSocket upgrade required"}), 400
    if _manager is None:
        return flask.jsonify({"error": "voice is not enabled on this server"}), 404

    ticket = flask.request.args.get("ticket", "")
    entry = _manager.redeem_ticket(ticket, conversation_id) if ticket else None
    if entry is None:
        return flask.jsonify({"error": "invalid or expired voice ticket"}), 401

    sidecar = _manager.sidecars.get(entry.provider)
    if sidecar is None or not sidecar.running():
        return flask.jsonify({"error": "voice service not running"}), 503

    query = urlencode({"conversation_id": conversation_id})
    return _websocket_tunnel(
        sidecar.port,
        "voice",
        flask.request.environ,
        query=query,
        extra_headers={SECRET_HEADER: _manager.secret},
    )
