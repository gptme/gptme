"""Grok Subscription Provider.

Enables use of SuperGrok/SuperGrok-Heavy subscriptions with gptme through
xAI's OpenAI-compatible API, using OAuth tokens from the grok CLI.

The grok CLI (https://grok.com) stores OAuth tokens at ~/.grok/auth.json.
This provider reads those tokens and uses them to authenticate with
xAI's API (api.x.ai), which accepts the same JWT access tokens.

Prerequisite: Install and authenticate the grok CLI first:
    1. Install: download from https://grok.com/download or ``pip install grok-cli``
    2. Login: ``grok login``
    3. Use:   ``gptme --model grok-subscription/grok-4.5``

Or authenticate directly via gptme (opens grok.com in browser):
    ``gptme auth grok-subscription``

NOTICE: For personal development use with your own SuperGrok subscription.
For production or multi-user applications, use the xAI Platform API (``xai``
provider) with an API key from console.x.ai.

Endpoint: https://api.x.ai/v1 (OpenAI-compatible Chat Completions)
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# xAI OAuth configuration (same client ID as grok CLI)
OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OAUTH_ISSUER = "https://auth.x.ai"
OAUTH_TOKEN_URL = f"{OAUTH_ISSUER}/oauth2/token"
OAUTH_AUTH_URL = f"{OAUTH_ISSUER}/oauth2/authorize"
OAUTH_CALLBACK_PORT = (
    1456  # local port for OAuth callback (1455 is openai-subscription's)
)
OAUTH_SCOPES = "openid profile email offline_access grok-cli:access api:access"

# xAI API base URL (OpenAI-compatible)
XAI_BASE_URL = "https://api.x.ai/v1"

# grok CLI auth storage key format: "{issuer}::{client_id}"
GROK_AUTH_KEY = f"{OAUTH_ISSUER}::{OAUTH_CLIENT_ID}"


def _get_grok_cli_auth_path() -> Path:
    """Get path to grok CLI auth file."""
    return Path.home() / ".grok" / "auth.json"


def _get_token_storage_path() -> Path:
    """Get path to store gptme-managed grok subscription tokens."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    token_dir = config_dir / "gptme" / "oauth"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / "grok_subscription.json"


@dataclass
class SubscriptionAuth:
    """Authentication state for grok subscription."""

    access_token: str
    refresh_token: str | None
    expires_at: float


# Global auth state (in-memory cache)
_auth: SubscriptionAuth | None = None


def _parse_expires_at(expires_at_str: str) -> float:
    """Parse ISO 8601 expiry string to Unix timestamp."""
    try:
        import re
        from datetime import datetime

        # Python's fromisoformat supports up to 6 decimal places (microseconds).
        # Grok CLI stores nanoseconds (9 digits) — truncate the excess.
        normalized = re.sub(r"(\.\d{6})\d+", r"\1", expires_at_str)
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return 0.0  # treat unparseable expiry as already-expired to force refresh


def _load_grok_cli_tokens() -> SubscriptionAuth | None:
    """Load tokens from grok CLI auth file (~/.grok/auth.json).

    The grok CLI stores tokens as a dict keyed by "{issuer}::{client_id}".
    Each entry has: key (access token), refresh_token, expires_at (ISO 8601).

    Falls back to any auth.x.ai key if our exact client ID is not found.
    """
    grok_path = _get_grok_cli_auth_path()
    if not grok_path.exists():
        return None

    try:
        data = json.loads(grok_path.read_text())

        # First try to find the entry for our exact OAuth client ID.
        entry = data.get(GROK_AUTH_KEY)

        # If not found, fall back to any auth.x.ai key
        if entry is None:
            for key, value in data.items():
                if "auth.x.ai" in key:
                    entry = value
                    break

        if entry is None:
            logger.warning(
                "Expected grok auth key %r not found; "
                "run 'grok login' or 'gptme auth grok-subscription' to authenticate",
                GROK_AUTH_KEY,
            )
            return None

        access_token = entry.get("key")
        if not access_token:
            return None

        return SubscriptionAuth(
            access_token=access_token,
            refresh_token=entry.get("refresh_token"),
            expires_at=_parse_expires_at(entry.get("expires_at", "")),
        )
    except Exception as e:
        logger.debug(f"Failed to load grok CLI tokens: {e}")
        return None


def _load_stored_tokens() -> SubscriptionAuth | None:
    """Load tokens stored by gptme (from a previous ``gptme auth grok-subscription``)."""
    token_path = _get_token_storage_path()
    if not token_path.exists():
        return None

    try:
        data = json.loads(token_path.read_text())
        return SubscriptionAuth(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=float(data["expires_at"]),
        )
    except Exception as e:
        logger.debug(f"Failed to load stored grok tokens: {e}")
        return None


def _save_tokens(auth: SubscriptionAuth) -> None:
    """Save tokens to gptme's token storage."""
    token_path = _get_token_storage_path()
    data = {
        "access_token": auth.access_token,
        "refresh_token": auth.refresh_token,
        "expires_at": auth.expires_at,
    }
    token_path.write_text(json.dumps(data, indent=2))
    token_path.chmod(0o600)
    logger.debug(f"Saved grok subscription tokens to {token_path}")


def _update_grok_cli_tokens(auth: SubscriptionAuth) -> None:
    """Write refreshed tokens back to grok CLI auth file to keep them in sync.

    Uses a separate .lock file as the flock target so the lock inode stays
    constant across os.replace() calls (locking auth.json directly is unsafe
    because os.replace() swaps the inode, releasing concurrent flocks).
    """
    import fcntl

    grok_path = _get_grok_cli_auth_path()
    if not grok_path.exists():
        return

    lock_path = grok_path.parent / (grok_path.name + ".lock")
    tmp_path = grok_path.parent / f"{grok_path.name}.tmp.{os.getpid()}"
    try:
        lock_fd = open(lock_path, "a")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if not grok_path.exists():
                return
            data = json.loads(grok_path.read_text())
            if GROK_AUTH_KEY not in data:
                return

            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(auth.expires_at, tz=timezone.utc)
            entry = data[GROK_AUTH_KEY]
            entry["key"] = auth.access_token
            entry["expires_at"] = dt.strftime("%Y-%m-%dT%H:%M:%S.%f000Z")
            if auth.refresh_token:
                entry["refresh_token"] = auth.refresh_token

            tmp_path.write_text(json.dumps(data, indent=2))
            os.replace(tmp_path, grok_path)
            logger.debug("Updated grok CLI auth file with refreshed tokens")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    except Exception as e:
        logger.debug(f"Failed to update grok CLI auth file: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _refresh_access_token(
    refresh_token: str, timeout: float | tuple[float, float] = 30
) -> SubscriptionAuth:
    """Refresh access token using OAuth2 refresh_token grant."""
    response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )

    if response.status_code != 200:
        raise ValueError(
            f"Token refresh failed: {response.status_code} {response.text[:300]}"
        )

    tokens = response.json()
    access_token = tokens.get("access_token")
    new_refresh_token = tokens.get("refresh_token", refresh_token)
    expires_in = tokens.get("expires_in", 21600)  # default 6h

    if not access_token:
        raise ValueError("No access token in refresh response")

    auth = SubscriptionAuth(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_at=time.time() + expires_in,
    )
    _save_tokens(auth)
    logger.info("Grok subscription access token refreshed")
    return auth


def get_auth(timeout: float | tuple[float, float] = 30) -> SubscriptionAuth:
    """Get a valid access token, loading or refreshing as needed.

    Priority order:
    1. In-memory cache (if not expired)
    2. grok CLI tokens (~/.grok/auth.json) — if valid or refreshable
    3. gptme-stored tokens (~/.config/gptme/oauth/grok_subscription.json)
    """
    global _auth

    # In-memory cache still valid
    if _auth is not None and time.time() < _auth.expires_at - 300:
        return _auth

    # Gather candidates: grok CLI tokens take priority (managed by the CLI)
    sources = []
    cli_auth = _load_grok_cli_tokens()
    if cli_auth is not None:
        sources.append(("grok CLI", cli_auth, True))

    stored_auth = _load_stored_tokens()
    if stored_auth is not None:
        sources.append(("gptme storage", stored_auth, False))

    last_error: Exception | None = None

    for source_name, source_auth, is_cli in sources:
        # Still valid?
        if time.time() < source_auth.expires_at - 300:
            _auth = source_auth
            return _auth

        # Try refresh
        if source_auth.refresh_token:
            try:
                new_auth = _refresh_access_token(source_auth.refresh_token, timeout)
                if is_cli:
                    _update_grok_cli_tokens(new_auth)
                _auth = new_auth
                # Re-initialize the cached OpenAI client so it uses the new token
                try:
                    from .llm_openai import _init_openai_client

                    _init_openai_client(
                        "grok-subscription",
                        api_key=_auth.access_token,
                        base_url=XAI_BASE_URL,
                    )
                except Exception:
                    pass  # client may not be set up yet; init() will handle it
                return _auth
            except Exception as e:
                logger.warning(f"Token refresh failed ({source_name}): {e}")
                last_error = e

    if last_error is not None:
        raise ValueError(
            f"Grok subscription token refresh failed: {last_error}\n"
            "This may be a temporary issue. If persistent, re-authenticate:\n"
            "  grok login\n"
            "  or: gptme auth grok-subscription"
        ) from last_error

    raise ValueError(
        "Grok subscription not authenticated.\n"
        "Install and authenticate the grok CLI:\n"
        "  grok login\n"
        "Or authenticate directly:\n"
        "  gptme auth grok-subscription"
    )


def init(config: Any) -> bool:
    """Initialize the grok subscription provider.

    Loads stored tokens and registers an OpenAI-compatible client pointed at
    xAI's API using the subscription access token as the bearer credential.
    Returns True whether or not tokens are available (provider is always usable).
    """
    global _auth

    # Collect all token sources
    cli_auth = _load_grok_cli_tokens()
    stored_auth = _load_stored_tokens()

    initial_auth: SubscriptionAuth | None = None
    for source_auth in [cli_auth, stored_auth]:
        if source_auth is None:
            continue
        if time.time() < source_auth.expires_at - 300:
            initial_auth = source_auth
            break

    if initial_auth is None:
        # Try to refresh from any available refresh token
        for source_name, source_auth, is_cli in [
            ("grok CLI", cli_auth, True),
            ("gptme storage", stored_auth, False),
        ]:
            if source_auth is not None and source_auth.refresh_token:
                try:
                    initial_auth = _refresh_access_token(source_auth.refresh_token)
                    if is_cli:
                        _update_grok_cli_tokens(initial_auth)
                    break
                except Exception as e:
                    logger.debug(
                        f"Token refresh during init failed ({source_name}): {e}"
                    )

    if initial_auth is not None:
        _auth = initial_auth
        from .llm_openai import _init_openai_client

        _init_openai_client(
            "grok-subscription",
            api_key=_auth.access_token,
            base_url=XAI_BASE_URL,
        )
        logger.info("Grok subscription provider initialized with stored tokens")
    else:
        logger.info(
            "Grok subscription provider available "
            "(run 'grok login' or 'gptme auth grok-subscription' to authenticate)"
        )

    return True
