"""Grok Subscription Provider.

Enables use of SuperGrok subscriptions with gptme by reusing the credentials
stored by the grok CLI (~/.grok/auth.json). No separate auth flow needed if
the grok CLI is already authenticated.

Uses the standard OpenAI Chat Completions API format served by xAI's CLI proxy:
- Endpoint: https://cli-chat-proxy.grok.com/v1/chat/completions
- Auth: Bearer token from grok CLI credential store
- Token refresh via OIDC at https://auth.x.ai/oauth2/token

NOTICE: For personal development use with your own SuperGrok subscription.
For production or multi-user applications, use the xAI Platform API with an
API key instead.

Usage:
    # Authenticate once via the grok CLI:
    grok auth login
    # Then use any grok-4.x model:
    gptme --model grok-subscription/grok-4.5 "hello"
"""

import json
import logging
import os
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from ..message import Message, msgs2dicts

logger = logging.getLogger(__name__)

# xAI CLI proxy — OpenAI-compatible chat completions endpoint
GROK_SUBSCRIPTION_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
GROK_SUBSCRIPTION_ENDPOINT = f"{GROK_SUBSCRIPTION_BASE_URL}/chat/completions"

# OIDC config (from grok CLI binary / auth.json)
GROK_OIDC_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
GROK_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"

# Required version header — the proxy enforces a minimum client version
GROK_CLIENT_VERSION = "0.2.93"


def _get_grok_auth_path() -> Path:
    """Return path to the grok CLI auth store."""
    return Path.home() / ".grok" / "auth.json"


def _get_grok_version_path() -> Path:
    """Return path to the grok CLI version file."""
    return Path.home() / ".grok" / "version.json"


def _get_client_version() -> str:
    """Read the installed grok CLI version (falls back to GROK_CLIENT_VERSION)."""
    try:
        data = json.loads(_get_grok_version_path().read_text())
        return str(data.get("version", GROK_CLIENT_VERSION))
    except Exception:
        return GROK_CLIENT_VERSION


@dataclass
class GrokAuth:
    """Authentication state for grok subscription."""

    access_token: str
    refresh_token: str | None
    expires_at: float  # Unix timestamp


_auth: GrokAuth | None = None
# Track which key was loaded from the credential store so we write back to the
# same entry (not just whichever happens to be first in a multi-entry store).
_credential_key: str | None = None


def _load_grok_tokens() -> GrokAuth | None:
    """Load the auth token stored by the grok CLI."""
    global _credential_key
    path = _get_grok_auth_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        if not raw:
            return None
        # auth.json maps {issuer::client_id: {...token data...}}
        # Prefer the entry whose key contains our known client ID; fall back to first.
        preferred_key = next(
            (k for k in raw if GROK_OIDC_CLIENT_ID in k),
            next(iter(raw)),
        )
        entry = raw[preferred_key]
        if not entry or "key" not in entry:
            return None
        _credential_key = preferred_key
        access_token = entry["key"]
        refresh_token = entry.get("refresh_token")
        expires_at_str = entry.get("expires_at", "")
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            expires_at = dt.timestamp()
        except Exception:
            expires_at = time.time() + 3600
        return GrokAuth(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
    except Exception as e:
        logger.warning("Failed to load grok auth tokens: %s", e)
        return None


def _refresh_access_token(refresh_token: str) -> GrokAuth:
    """Refresh the access token using the stored refresh token."""
    resp = requests.post(
        GROK_TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "client_id": GROK_OIDC_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ValueError(
            f"Grok token refresh failed: {resp.status_code} — {resp.text[:200]}"
        )
    tokens = resp.json()
    access_token = tokens.get("access_token")
    if not access_token:
        raise ValueError("No access_token in grok token refresh response")
    new_refresh = tokens.get("refresh_token", refresh_token)
    # Derive expiry from JWT exp claim if possible
    try:
        import base64

        payload = access_token.split(".")[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        expires_at = float(exp) if exp else time.time() + 3600
    except Exception:
        expires_at = time.time() + 3600
    auth = GrokAuth(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_at=expires_at,
    )
    # Update the grok auth store so the refreshed token persists
    _write_grok_tokens(auth)
    logger.info("Grok subscription token refreshed successfully")
    return auth


def _write_grok_tokens(auth: GrokAuth) -> None:
    """Persist refreshed tokens back to ~/.grok/auth.json.

    Uses the same key that was identified during the most recent _load_grok_tokens
    call so that multi-entry stores are updated correctly.

    Uses an exclusive flock + per-pid temp file + os.replace() so concurrent
    gptme processes and the grok CLI don't interleave their writes.
    """
    import fcntl

    global _credential_key
    path = _get_grok_auth_path()
    tmp = path.parent / f"{path.name}.tmp.{os.getpid()}"
    try:
        # Hold an exclusive lock for the full read-modify-write cycle.
        # Open with 'a+' so we create the file if absent without truncating.
        lock_fd = open(path, "a+")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            lock_fd.seek(0)
            raw_text = lock_fd.read()
            raw: dict = json.loads(raw_text) if raw_text.strip() else {}
            # Resolve the target key: prefer the one we previously loaded from,
            # then the entry containing our client ID, then the first entry, then
            # the canonical default.
            default_key = f"{GROK_TOKEN_ENDPOINT}::{GROK_OIDC_CLIENT_ID}"
            if _credential_key and _credential_key in raw:
                key = _credential_key
            elif raw:
                key = next(
                    (k for k in raw if GROK_OIDC_CLIENT_ID in k),
                    next(iter(raw)),
                )
            else:
                key = default_key
            entry = raw.get(key, {})
            entry["key"] = auth.access_token
            if auth.refresh_token:
                entry["refresh_token"] = auth.refresh_token
            from datetime import datetime, timezone

            entry["expires_at"] = datetime.fromtimestamp(
                auth.expires_at, tz=timezone.utc
            ).isoformat()
            raw[key] = entry
            # Atomic publish: write to per-pid temp then rename under the lock
            tmp.write_text(json.dumps(raw, indent=2))
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    except Exception as e:
        logger.warning("Failed to persist refreshed grok tokens: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def get_auth() -> GrokAuth:
    """Return a valid GrokAuth, refreshing if needed.

    Raises ValueError if no credentials are available.
    """
    global _auth

    # Return in-memory auth if still valid (5-min buffer)
    if _auth is not None and time.time() < _auth.expires_at - 300:
        return _auth

    # Try refreshing in-memory auth first
    if _auth is not None and _auth.refresh_token:
        try:
            _auth = _refresh_access_token(_auth.refresh_token)
            return _auth
        except Exception as e:
            logger.warning("In-memory token refresh failed: %s", e)

    # Load from grok CLI auth store
    stored = _load_grok_tokens()
    if stored is not None:
        if time.time() < stored.expires_at - 300:
            _auth = stored
            return _auth
        if stored.refresh_token:
            try:
                _auth = _refresh_access_token(stored.refresh_token)
                return _auth
            except Exception as e:
                logger.warning("Stored token refresh failed: %s", e)

    raise ValueError(
        "Grok subscription not authenticated.\n"
        "Please run: grok auth login\n"
        "(Install the grok CLI from https://x.ai/grok)"
    )


def _make_headers() -> dict[str, str]:
    """Build request headers for the grok subscription API."""
    auth = get_auth()
    return {
        "Authorization": f"Bearer {auth.access_token}",
        "Content-Type": "application/json",
        "x-grok-client-version": _get_client_version(),
    }


def _messages_to_openai(
    messages: list[Message], tools: list[Any] | None = None
) -> list[dict[str, Any]]:
    """Convert gptme messages to OpenAI chat completions format.

    Handles tool calls (assistant → tool_calls) and tool results (system+call_id
    → role:tool) using the same helpers as the main OpenAI provider.
    Also expands image file attachments into base64 image_url content parts.
    """
    from .llm_openai import (
        _handle_tools,
        _merge_tool_results_with_same_call_id,
    )

    raw_dicts = msgs2dicts(messages)
    if tools:
        raw_dicts = list(
            _merge_tool_results_with_same_call_id(_handle_tools(raw_dicts))
        )
    else:
        raw_dicts = list(_handle_tools(raw_dicts))

    result = []
    for msg in raw_dicts:
        files = msg.pop("files", None) or []
        if files:
            parts: list[dict[str, Any]] = []
            if msg.get("content"):
                parts.append({"type": "text", "text": msg["content"]})
            for f in files:
                try:
                    import base64
                    import mimetypes

                    fpath = Path(f) if isinstance(f, str) else Path(str(f))
                    mime = mimetypes.guess_type(str(fpath))[0] or "image/png"
                    if mime.startswith("image/"):
                        data = base64.b64encode(fpath.read_bytes()).decode()
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{data}"},
                            }
                        )
                except Exception as exc:
                    logger.warning("Could not attach file %s: %s", f, exc)
            if parts:
                msg = dict(msg)
                msg["content"] = parts
        result.append(msg)
    return result


def _tools_to_openai(tools: list[Any], model: str) -> list[dict[str, Any]]:
    """Convert ToolSpec list to OpenAI-compatible tool schema dicts."""
    from .llm_openai import _spec2tool
    from .models import get_model

    model_meta = get_model(f"grok-subscription/{model}")
    return [dict(_spec2tool(t, model_meta)) for t in tools]


def stream(
    messages: list[Message],
    model: str,
    tools: list[Any] | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> Generator[str, None, None]:
    """Stream completion from grok subscription API."""
    api_messages = _messages_to_openai(messages, tools)
    body: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "stream": True,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = _tools_to_openai(tools, model)
        body["tool_choice"] = "auto"

    resp = requests.post(
        GROK_SUBSCRIPTION_ENDPOINT,
        json=body,
        headers=_make_headers(),
        stream=True,
        timeout=(30, 300),
    )
    if resp.status_code != 200:
        raise ValueError(
            f"Grok subscription API error {resp.status_code}: {resp.text[:400]}"
        )

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = (
            raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        )
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if content:
            yield content


def chat(
    messages: list[Message],
    model: str,
    tools: list[Any] | None = None,
    **kwargs: Any,
) -> str:
    """Non-streaming completion from grok subscription API."""
    return "".join(stream(messages, model, tools, **kwargs))


def init(config: Any) -> bool:
    """Initialize the grok subscription provider."""
    global _auth
    stored = _load_grok_tokens()
    if stored is None:
        logger.info(
            "Grok subscription provider available "
            "(run 'grok auth login' to authenticate)"
        )
        return True

    if time.time() < stored.expires_at - 300:
        _auth = stored
        logger.info("Grok subscription provider initialized with stored token")
        return True

    if stored.refresh_token:
        try:
            _auth = _refresh_access_token(stored.refresh_token)
            logger.info("Grok subscription provider initialized with refreshed token")
            return True
        except Exception as e:
            logger.debug("Token refresh during init failed: %s", e)

    logger.info(
        "Grok subscription provider available but token expired "
        "(run 'grok auth login' to re-authenticate)"
    )
    return True
