"""gptme.ai managed service provider.

Uses the gptme.ai service as an OpenAI-compatible LLM proxy/gateway.
Authenticates via RFC 8628 Device Flow tokens from ``gptme-auth gptme-ai``,
or falls back to a ``GPTME_AI_API_KEY`` environment variable.

Usage:
    1. Authenticate: ``gptme-auth gptme-ai``
    2. Use models:   ``gptme -m gptme-ai/claude-sonnet-4-6``

The token file is stored at ``~/.config/gptme/auth/gptme-ai.json`` with format::

    {"access_token": "...", "expires_at": 1234567890.0, "server_url": "..."}
"""

import json
import logging
import os
import time
from pathlib import Path

from ..config import Config

logger = logging.getLogger(__name__)

# Default base URL for the gptme.ai API proxy
DEFAULT_BASE_URL = "https://api.gptme.ai/v1"


def _get_token_path() -> Path:
    """Get path to the Device Flow token file."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_dir / "gptme" / "auth" / "gptme-ai.json"


def _load_token() -> dict | None:
    """Load Device Flow token from disk.

    Returns the token dict if valid, None otherwise.
    """
    token_path = _get_token_path()
    if not token_path.exists():
        return None

    try:
        data = json.loads(token_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read gptme-ai token: {e}")
        return None

    # Check expiration (with 60s buffer)
    expires_at = data.get("expires_at", 0)
    if expires_at and time.time() > expires_at - 60:
        logger.warning(
            "gptme-ai token expired, run `gptme-auth gptme-ai` to re-authenticate"
        )
        return None

    return data


def get_api_key(config: Config) -> str:
    """Get the API key for gptme-ai.

    Priority:
    1. Device Flow token from ``~/.config/gptme/auth/gptme-ai.json``
    2. ``GPTME_AI_API_KEY`` environment variable
    3. Raises an error with instructions
    """
    # Try Device Flow token first
    token_data = _load_token()
    if token_data and token_data.get("access_token"):
        return token_data["access_token"]

    # Fall back to API key env var
    api_key = config.get_env("GPTME_AI_API_KEY")
    if api_key:
        return api_key

    raise KeyError(
        "gptme-ai requires authentication. Either:\n"
        "  1. Run `gptme-auth gptme-ai` to authenticate via Device Flow\n"
        "  2. Set the GPTME_AI_API_KEY environment variable"
    )


def get_base_url(config: Config) -> str:
    """Get the base URL for the gptme-ai API.

    Checks (in order):
    1. Token file's ``server_url`` field
    2. ``GPTME_AI_BASE_URL`` environment variable
    3. Default: https://api.gptme.ai/v1
    """
    # Check token file for server URL
    token_data = _load_token()
    if token_data and token_data.get("server_url"):
        server_url = token_data["server_url"].rstrip("/")
        if not server_url.endswith("/v1"):
            server_url += "/v1"
        return server_url

    # Check env var
    env_url = config.get_env("GPTME_AI_BASE_URL")
    if env_url:
        return env_url.rstrip("/")

    return DEFAULT_BASE_URL


def device_flow_authenticate(server_url: str = DEFAULT_BASE_URL) -> dict:
    """Perform RFC 8628 Device Flow authentication.

    Args:
        server_url: Base URL of the gptme.ai service (without /v1 suffix).

    Returns:
        Token data dict with access_token, expires_at, server_url.
    """
    import requests

    # Strip /v1 suffix for auth endpoints
    auth_base = server_url.rstrip("/")
    auth_base = auth_base.removesuffix("/v1")

    # Step 1: Request device authorization
    resp = requests.post(
        f"{auth_base}/api/v1/auth/device/authorize",
        json={"client_id": "gptme-cli"},
        timeout=30,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 900)
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Invalid device authorization response: {e}") from e

    # Step 2: Display code to user
    print(f"\nVisit: {verification_uri}")
    print(f"Enter code: {user_code}\n")

    # Step 3: Poll for token
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)

        try:
            poll_resp = requests.post(
                f"{auth_base}/api/v1/auth/device/token",
                json={
                    "device_code": device_code,
                    "client_id": "gptme-cli",
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=30,
            )
        except requests.RequestException:
            continue

        if poll_resp.status_code == 200:
            try:
                token_data = poll_resp.json()
                access_token = token_data["access_token"]
            except (json.JSONDecodeError, KeyError) as e:
                raise RuntimeError(f"Invalid token response: {e}") from e

            # Save token
            result = {
                "access_token": access_token,
                "expires_at": time.time() + token_data.get("expires_in", 86400),
                "server_url": auth_base,
            }
            _save_token(result)
            return result

        if poll_resp.status_code == 428:
            # authorization_pending — keep polling
            continue

        # Other errors
        try:
            error = poll_resp.json().get("error", "unknown")
        except (json.JSONDecodeError, KeyError):
            error = f"HTTP {poll_resp.status_code}"

        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = min(interval + 5, 30)
            continue
        raise RuntimeError(f"Device flow failed: {error}")

    raise RuntimeError("Device flow timed out — code expired")


def _save_token(token_data: dict) -> None:
    """Save token data to disk."""
    token_path = _get_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token_data, indent=2))
    # Restrict permissions (owner read/write only)
    token_path.chmod(0o600)
