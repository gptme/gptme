"""gptme managed service provider.

Uses the gptme cloud service as an OpenAI-compatible LLM proxy/gateway.
Authenticates via RFC 8628 Device Flow tokens from ``gptme-auth login``,
or falls back to a ``GPTME_CLOUD_API_KEY`` environment variable.

Usage:
    1. Authenticate: ``gptme-auth login``
    2. List models:  ``gptme --list-models`` (shows ``gptme/anthropic/MODEL`` etc.)
    3. Use models:   ``gptme -m gptme/anthropic/claude-sonnet-4-6``

    Model IDs from the gptme cloud service include a provider prefix (e.g.
    ``anthropic/``, ``openai/``). The full model spec for gptme is therefore
    ``gptme/<provider>/<model>`` — e.g. ``gptme/anthropic/claude-sonnet-4-6``.

Token priority:
    1. Device Flow token from ``~/.config/gptme/auth/gptme-cloud-<hash>.json``
    2. ``GPTME_CLOUD_API_KEY`` environment variable
    3. Error with instructions

Base URL priority (chat completions):
    1. Token's ``base_url`` field (explicit, Supabase-aware)
    2. ``GPTME_CLOUD_BASE_URL`` environment variable
    3. Default: Supabase messages edge function

Models URL priority:
    1. Token's ``server_url`` field for custom-server tokens (no base_url)
    2. ``GPTME_CLOUD_MODELS_URL`` environment variable
    3. ``GPTME_CLOUD_BASE_URL`` for non-Supabase custom servers (env-var-only)
    4. Default: Supabase functions/v1 base

URL architecture:
    - LLM API calls go to Supabase edge functions, NOT fleet.gptme.ai.
    - fleet.gptme.ai only routes /api/v1/instances/ (traefik) and
      /api/v1/operator/ (fleet-operator). It has no /v1/chat/completions or
      /v1/models routes.
    - Chat completions: DEFAULT_BASE_URL (messages edge function)
    - Model listing:    DEFAULT_MODELS_BASE_URL (functions/v1 base)
    - Device auth:      DEFAULT_DEVICE_AUTH_URL (device-auth edge function)
"""

import hashlib
import json
import logging
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config

logger = logging.getLogger(__name__)


class GptmeAuthError(KeyError):
    """Raised when no valid gptme.ai credentials are found.

    Subclasses ``KeyError`` for backward compatibility with existing handlers.
    ``KeyError.__str__`` returns the *repr* of a single argument, which would
    print the multi-line hint as ``'...\\n  1. Run ...'``; render it verbatim.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if len(self.args) == 1 else super().__str__()


# All gptme cloud API traffic goes through Supabase edge functions.
# fleet.gptme.ai is for user instance routing only (/api/v1/instances/, /api/v1/operator/).
# auth.gptme.ai is the Supabase custom domain in front of the managed project
# (kpkxgnfpyntahyhckhgm.supabase.co). Clients talk to the domain we control so a
# later move to self-hosted Supabase is a DNS change, not a client release.
_SUPABASE_URL = "https://auth.gptme.ai"
_SUPABASE_FUNCTIONS_V1 = f"{_SUPABASE_URL}/functions/v1"

# Chat completions endpoint — the messages function ignores sub-path, so the
# OpenAI SDK's /chat/completions suffix lands here without issue.
DEFAULT_BASE_URL = f"{_SUPABASE_FUNCTIONS_V1}/messages"

# Base for model listing — OpenAI SDK appends /models → /functions/v1/models ✓
DEFAULT_MODELS_BASE_URL = _SUPABASE_FUNCTIONS_V1

# Service URL used for token storage keying (not for API calls).
DEFAULT_SERVICE_URL = _SUPABASE_URL

# Device auth edge function.
DEFAULT_DEVICE_AUTH_URL = f"{_SUPABASE_FUNCTIONS_V1}/device-auth"

# Token storage directory
_TOKEN_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gptme" / "auth"
)

# Raw Supabase project URL that auth.gptme.ai fronts. Still served by Supabase;
# tokens created before the custom domain are keyed by it and carry it as base_url.
_LEGACY_SUPABASE_URL = "https://kpkxgnfpyntahyhckhgm.supabase.co"
_SUPABASE_SERVICE_URLS = (DEFAULT_SERVICE_URL, _LEGACY_SUPABASE_URL)

# Legacy service URLs whose token files should be checked as a migration fallback
# when no token exists at the current DEFAULT_SERVICE_URL path.
_LEGACY_SERVICE_URLS = [
    _LEGACY_SUPABASE_URL,
    "https://fleet.gptme.ai",
]


def is_supabase_service_url(url: str) -> bool:
    """True if ``url`` is (or is under) the managed gptme.ai Supabase service.

    Matches both the custom domain and the raw project URL so tokens and env
    overrides that still use the old hostname keep the Supabase-specific paths.
    """
    stripped = url.rstrip("/")
    return any(
        stripped == u or stripped.startswith(u + "/") for u in _SUPABASE_SERVICE_URLS
    )


def _get_token_path(service_url: str | None = None) -> Path:
    """Get path to the Device Flow token file.

    Uses URL hash for multi-instance support.
    """
    url = service_url or DEFAULT_SERVICE_URL
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    return _TOKEN_DIR / f"gptme-cloud-{url_hash}.json"


def _load_token(service_url: str | None = None) -> dict | None:
    """Load Device Flow token from disk.

    Returns the token dict if valid, None otherwise.
    """
    token_path = _get_token_path(service_url)
    if not token_path.exists():
        # Migration: if using the default URL and no new token exists, check
        # legacy service URL paths so users authenticated before the Supabase
        # migration can still be found (their token has a server_url field
        # that get_base_url() uses to reconstruct the old-style URL).
        if service_url is None:
            for legacy_url in _LEGACY_SERVICE_URLS:
                legacy_path = _get_token_path(legacy_url)
                if legacy_path.exists():
                    token_path = legacy_path
                    break
            else:
                return None
        else:
            return None

    try:
        data = json.loads(token_path.read_bytes())
    except (ValueError, OSError) as e:
        logger.warning(f"Failed to read gptme token: {e}")
        return None

    # Must be a JSON object — a list, null, string, etc. is not a valid token
    if not isinstance(data, dict):
        logger.warning(
            "gptme token file is malformed (not a JSON object), treating as missing"
        )
        return None

    # Check expiration (with 60s buffer)
    # Treat missing/zero expires_at as expired to avoid bypassing expiration
    expires_at = data.get("expires_at", 0)
    if not isinstance(expires_at, (int, float)):
        logger.warning("gptme token has non-numeric expires_at, treating as expired")
        return None
    # auth_status formats this timestamp. Validate its representability here so
    # malformed numbers also leave API-key fallback usable (including huge ints).
    try:
        datetime.fromtimestamp(expires_at, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        logger.warning("gptme token has out-of-range expires_at, treating as expired")
        return None
    if not expires_at or time.time() > expires_at - 60:
        if expires_at:
            logger.warning(
                "gptme token expired, run `gptme-auth login` to re-authenticate"
            )
        return None

    # access_token must be a non-empty string to be usable
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        logger.warning(
            "gptme token has missing or non-string access_token, treating as missing"
        )
        return None

    return data


def get_api_key(config: Config) -> str:
    """Get the API key for the gptme provider.

    Priority:
    1. Device Flow token from disk
    2. ``GPTME_CLOUD_API_KEY`` environment variable
    3. Raises an error with instructions
    """
    # Try Device Flow token first
    token_data = _load_token()
    access_token = token_data.get("access_token") if token_data else None
    if isinstance(access_token, str) and access_token:
        return access_token

    # Fall back to API key env var
    api_key = config.get_env("GPTME_CLOUD_API_KEY")
    if api_key:
        return api_key

    raise GptmeAuthError(
        "gptme provider requires authentication. Either:\n"
        "  1. Run `gptme-auth login` to authenticate via Device Flow\n"
        "  2. Set the GPTME_CLOUD_API_KEY environment variable"
    )


def get_base_url(config: Config) -> str:
    """Get the base URL for gptme cloud chat completions.

    Checks (in order):
    1. Token file's ``base_url`` field (explicit, set by device_flow_authenticate)
    2. ``GPTME_CLOUD_BASE_URL`` environment variable
    3. Default: Supabase messages edge function

    The returned URL is used as the OpenAI SDK base_url. For chat completions,
    this points at the Supabase messages function. For model listing, use
    get_models_url() instead.
    """
    token_data = _load_token()
    if token_data:
        # New explicit base_url field (Supabase-aware, set by device_flow_authenticate)
        base_url_field = token_data.get("base_url")
        if isinstance(base_url_field, str) and base_url_field:
            return base_url_field.rstrip("/")
        # Legacy: token has server_url but no base_url — reconstruct old-style
        # (e.g. tokens saved before this fix, pointing at fleet.gptme.ai)
        server_url_field = token_data.get("server_url")
        if isinstance(server_url_field, str) and server_url_field:
            server_url = server_url_field.rstrip("/")
            if not server_url.endswith("/v1"):
                server_url += "/v1"
            return server_url

    env_url = config.get_env("GPTME_CLOUD_BASE_URL")
    if env_url:
        return env_url.rstrip("/")

    return DEFAULT_BASE_URL


def get_models_url(config: Config) -> str:
    """Get the base URL for gptme cloud model listing.

    The OpenAI SDK appends /models to the returned URL.
    Checks (in order):
    1. Token file's ``server_url`` field (custom-server token without base_url)
       → uses server_url + /v1 as the models base
    2. ``GPTME_CLOUD_MODELS_URL`` environment variable
    3. ``GPTME_CLOUD_BASE_URL`` env var when pointing at a non-Supabase server
       → env-var-only users with custom OpenAI-compatible APIs
    4. Default: Supabase functions/v1 base
    """
    token_data = _load_token()
    if token_data:
        # Custom-server token (server_url without explicit base_url)
        base_url_field = token_data.get("base_url")
        server_url_field = token_data.get("server_url")
        if (
            not (isinstance(base_url_field, str) and base_url_field)
            and isinstance(server_url_field, str)
            and server_url_field
        ):
            server_url = server_url_field.rstrip("/")
            if not server_url.endswith("/v1"):
                server_url += "/v1"
            return server_url
        # Default (Supabase) token with base_url — use the default models URL
        # which is the functions/v1 base the SDK needs for /models.

    env_url = config.get_env("GPTME_CLOUD_MODELS_URL")
    if env_url:
        return env_url.rstrip("/")

    # For env-var-only users with a custom server (GPTME_CLOUD_BASE_URL not pointing
    # at Supabase), use the same base URL for model listing — standard OpenAI-compatible
    # APIs serve both /chat/completions and /models from the same base.
    base_url = config.get_env("GPTME_CLOUD_BASE_URL")
    if base_url and not is_supabase_service_url(base_url):
        return base_url.rstrip("/")

    return DEFAULT_MODELS_BASE_URL


def device_flow_authenticate(
    server_url: str = DEFAULT_SERVICE_URL,
    device_auth_url: str = DEFAULT_DEVICE_AUTH_URL,
) -> dict:
    """Perform RFC 8628 Device Flow authentication.

    Args:
        server_url: Base URL of the gptme LLM service (without /v1 suffix).
            Used only for token storage keying, not for auth endpoints.
        device_auth_url: Base URL of the device-auth edge function.
            Defaults to the Supabase edge function that replaced the fleet-operator
            auth endpoints (gptme-cloud#287).

    Returns:
        Token data dict with access_token, expires_at, server_url.
    """
    import requests

    auth_base = device_auth_url.rstrip("/")
    service_base = server_url.rstrip("/").removesuffix("/v1")

    # Step 1: Request device authorization
    resp = requests.post(
        f"{auth_base}/authorize",
        json={"client_id": "gptme-cli"},
        timeout=30,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        verification_uri_complete = data.get("verification_uri_complete") or ""
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 900)
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Invalid device authorization response: {e}") from e

    # Step 2: Display code to user. Prefer the RFC 8628 complete URI when the
    # server sends it so the user does not retype the code (gptme.ai production
    # returns https://gptme.ai/device?code=XXXX-XXXX).
    visit_uri = verification_uri_complete.strip() or verification_uri
    print(f"\nVisit: {visit_uri}")
    print(f"Enter code: {user_code}")
    print("Waiting for you to authorize in the browser...\n")

    # Step 3: Poll for token
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)

        try:
            poll_resp = requests.post(
                f"{auth_base}/token",
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

            # Save token; keyed by service URL.
            # base_url is only set for the default Supabase service — custom servers
            # use the server_url+/v1 fallback in get_base_url() instead.
            result: dict = {
                "access_token": access_token,
                "expires_at": time.time() + token_data.get("expires_in", 86400),
                "server_url": service_base,
            }
            if is_supabase_service_url(service_base):
                result["base_url"] = f"{service_base}/functions/v1/messages"
            _save_token(result, service_base)
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

    raise RuntimeError(
        "Device flow timed out — code expired. "
        "Sign in or create an account at gptme.ai and retry, "
        "or set an API key (gptme-onboard / provider env vars)."
    )


def _save_token(token_data: dict, service_url: str | None = None) -> None:
    """Publish private JSON without truncating a previous usable login on failure.

    Refuse destination aliases rather than replacing only one hard-link name or
    following a symlink. This is not a concurrent-writer or power-loss guarantee.
    POSIX mode 0600 does not establish native Windows ACL protection.
    """
    content = json.dumps(token_data, indent=2)
    token_path = _get_token_path(service_url)
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = token_path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError("Token destination must be a regular, non-aliased file")

    # mkstemp opens exclusively with mode 0600, before any credential bytes are
    # written. A sibling keeps os.replace on the same filesystem.
    fd, name = tempfile.mkstemp(prefix=".gptme-token-", dir=token_path.parent)
    temporary = Path(name)
    try:
        try:
            stream = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            stream.write(content)
        os.replace(temporary, token_path)
    finally:
        temporary.unlink(missing_ok=True)
