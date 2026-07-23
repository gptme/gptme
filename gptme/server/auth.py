"""
Authentication middleware for gptme-server.

Provides bearer token authentication for API access.
Authentication is only required when binding to network interfaces.

Supports three authentication methods (checked in order):
1. Authorization header (preferred for normal API calls)
2. HttpOnly cookie (preferred for SSE/EventSource connections)
3. Query parameter (deprecated, kept for backward compatibility)
"""

import logging
import os
import secrets
from functools import wraps

import flask
from flask import jsonify, request

logger = logging.getLogger(__name__)

# Token storage (in-memory, generated on startup)
_server_token: str | None = None

# Auth state (disabled for local-only binding)
_auth_enabled: bool = True

# Host-header validation state (DNS-rebinding hardening).
# Only enforced when auth is disabled for a loopback bind: a malicious page
# whose hostname re-resolves to 127.0.0.1 becomes same-origin with the local
# server and bypasses CORS entirely, gaining full unauthenticated API access
# (which includes shell execution via the agent). Validating the Host header
# blocks that vector without affecting legitimate localhost/127.0.0.1 clients.
_host_validation_enabled: bool = False
_allowed_hosts: set[str] = set()

# Hostnames always allowed for loopback binds (any port).
_DEFAULT_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Cookie configuration
AUTH_COOKIE_NAME = "gptme_auth"
AUTH_COOKIE_MAX_AGE = 86400  # 24 hours

# Blueprint for auth endpoints
auth_api = flask.Blueprint("auth_api", __name__)


def generate_token() -> str:
    """Generate a cryptographically secure random token.

    Returns:
        A URL-safe random string of 32+ characters.
    """
    return secrets.token_urlsafe(32)


def is_local_host(host: str) -> bool:
    """Check if host is a local-only address.

    Args:
        host: The host address to check.

    Returns:
        True if host is localhost or 127.0.0.1, False otherwise.
    """
    return host in ("127.0.0.1", "localhost", "::1")


def _extract_hostname(host_header: str) -> str:
    """Extract the hostname part (without port) from a Host header value.

    Handles IPv6 literals in brackets (``[::1]`` / ``[::1]:5700``) as well as
    plain hostnames and IPv4 addresses with an optional ``:port`` suffix. A
    trailing dot (the absolute-DNS root form, e.g. ``localhost.``) is stripped
    so that fully-qualified equivalents of an allowed host still match.

    Args:
        host_header: The raw Host header value (may include a port).

    Returns:
        The hostname portion, lowercased, with any port, IPv6 brackets, and a
        trailing root dot stripped.
    """
    value = host_header.strip()
    if value.startswith("["):
        # IPv6 literal: "[::1]" or "[::1]:5700"
        end = value.find("]")
        if end != -1:
            value = value[1:end]
        # else: malformed (no closing bracket) — fall through with raw value
    elif ":" in value:
        # Plain hostname or IPv4, optionally "host:port"
        value = value.rsplit(":", 1)[0]
    return _normalize_allowed_host(value)


def _normalize_allowed_host(name: str) -> str:
    """Normalize a hostname for allow-list comparison.

    Lowercases and strips a trailing absolute-DNS root dot so that both parsed
    Host headers and configured allow-list entries use the same canonical form
    (e.g. a configured ``gptme.local.`` matches an incoming ``Host: gptme.local``
    and vice versa).
    """
    return name.strip().removesuffix(".").lower()


def init_host_validation(
    host: str = "127.0.0.1", allowed_hosts: list[str] | None = None
) -> None:
    """Configure Host-header validation for DNS-rebinding hardening.

    Must be called after :func:`init_auth` (it reads ``_auth_enabled``).

    Validation is only enabled when auth is disabled for a loopback bind — the
    one case where a DNS-rebinding page can reach the API without credentials.
    It is skipped when:

    - Auth is enabled (network bind): the bearer token already gates access.
    - ``GPTME_DISABLE_AUTH`` is explicitly set and no ``--allowed-hosts`` was
      given: the operator owns security here (e.g. gptme-cloud pods bind
      ``0.0.0.0`` behind an authenticated ingress). We must not break those
      setups. If the operator *does* pass ``--allowed-hosts``, we honor it and
      enforce the check against the given hosts.

    Args:
        host: The host the server is binding to. A concrete (non-wildcard)
            bind host is added to the allow-list so proxied setups work.
        allowed_hosts: Extra hostnames to allow (from ``--allowed-hosts``), for
            users who proxy their local server behind a hostname.
    """
    global _host_validation_enabled, _allowed_hosts

    extra = {_normalize_allowed_host(h) for h in (allowed_hosts or []) if h.strip()}

    # Auth enabled → bearer token gates access, no Host check needed.
    if _auth_enabled:
        _host_validation_enabled = False
        _allowed_hosts = set()
        return

    # Auth explicitly disabled via env (operator owns security). Skip the check
    # unless they opted into an explicit allow-list.
    disabled_via_env = os.environ.get("GPTME_DISABLE_AUTH", "").lower() in (
        "true",
        "1",
        "yes",
    )
    if disabled_via_env and not extra:
        _host_validation_enabled = False
        _allowed_hosts = set()
        return

    # Auth disabled for a loopback bind (the DNS-rebinding vector), or
    # GPTME_DISABLE_AUTH with an explicit allow-list. Enforce Host validation.
    allowed = set(_DEFAULT_ALLOWED_HOSTS)
    if host and host not in ("0.0.0.0", "::"):
        allowed.add(_normalize_allowed_host(host))
    allowed |= extra
    _allowed_hosts = allowed
    _host_validation_enabled = True


def validate_host():
    """Flask ``before_request`` hook enforcing Host-header validation.

    Returns a 403 JSON response for a disallowed Host, or ``None`` to let the
    request proceed. A no-op when validation is disabled (see
    :func:`init_host_validation`).
    """
    if not _host_validation_enabled:
        return None

    hostname = _extract_hostname(request.host or "")
    if hostname in _allowed_hosts:
        return None

    logger.warning("Rejected request with disallowed Host header: %r", request.host)
    return (
        jsonify(
            {
                "error": (
                    f"Host '{request.host}' is not allowed. The local gptme-server "
                    "validates the Host header to block DNS-rebinding attacks. "
                    "If this host is legitimate (e.g. a reverse proxy), allow it "
                    f"with: gptme-server serve --allowed-hosts {hostname}"
                )
            }
        ),
        403,
    )


def get_server_token() -> str | None:
    """Get the server authentication token from environment.

    If GPTME_SERVER_TOKEN is not set, auto-generates a secure token
    for the server session with a warning.

    Returns:
        The current server token from GPTME_SERVER_TOKEN env var,
        or an auto-generated token if not configured.
    """
    global _server_token
    if _server_token is None:
        # Check environment variable
        env_token = os.environ.get("GPTME_SERVER_TOKEN")
        if env_token:
            _server_token = env_token
            logger.info("Using token from GPTME_SERVER_TOKEN environment variable")
        else:
            # Auto-generate secure token if not configured
            _server_token = generate_token()
            logger.warning("=" * 60)
            logger.warning("⚠️  AUTO-GENERATED TOKEN (Security Notice)")
            logger.warning("=" * 60)
            logger.warning(f"Token: {_server_token}")
            logger.warning("")
            logger.warning(
                "GPTME_SERVER_TOKEN was not set, so a random token was generated."
            )
            logger.warning("This token is only valid for this server session.")
            logger.warning("")
            logger.warning("For persistent authentication, set GPTME_SERVER_TOKEN:")
            logger.warning("  export GPTME_SERVER_TOKEN=your-secret-token")
            logger.warning("  gptme-server serve")
            logger.warning("=" * 60)
    return _server_token


def set_server_token(token: str) -> None:
    """Set the server authentication token.

    Args:
        token: The token to set.
    """
    global _server_token
    _server_token = token
    logger.info("Server token updated")


def require_auth(f):
    """Decorator to require bearer token authentication.

    Authentication is only required when binding to network interfaces.
    When binding to localhost (127.0.0.1), authentication is disabled
    for seamless local development.

    Checks authentication in order of preference:
    1. Authorization header (most secure, use for normal API calls)
    2. HttpOnly cookie (secure, use for SSE/EventSource connections)
    3. Query parameter (deprecated, kept for backward compatibility)

    Returns:
        Decorated function that validates bearer token before execution.

    Raises:
        401 Unauthorized: Missing or invalid authentication credentials
            (only when auth is enabled for network binding).
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip authentication for local-only binding
        if not _auth_enabled:
            return f(*args, **kwargs)

        # Authentication is required for network binding
        server_token = get_server_token()
        if not server_token:
            logger.error("Server token not available but auth is enabled")
            return jsonify({"error": "Authentication system error"}), 500

        # 1. Check Authorization header (preferred method)
        auth_header = request.headers.get("Authorization")
        token = None

        if auth_header:
            try:
                scheme, token = auth_header.split(" ", 1)
                if scheme.lower() != "bearer":
                    logger.warning(f"Invalid authentication scheme: {scheme}")
                    return jsonify({"error": "Invalid authentication scheme"}), 401
            except ValueError:
                logger.warning("Invalid Authorization header format")
                return jsonify({"error": "Invalid authorization header format"}), 401

        # 2. Check HttpOnly cookie (for SSE/EventSource connections)
        if not token:
            token = request.cookies.get(AUTH_COOKIE_NAME)
            if token:
                logger.debug("Using cookie authentication")

        # 3. Query parameter fallback (deprecated, kept for backward compat)
        if not token:
            token = request.args.get("token")
            if token:
                logger.debug(
                    "Using query parameter authentication (deprecated, use cookie)"
                )

        if not token:
            logger.warning("Missing authentication credentials")
            return jsonify({"error": "Missing authentication credentials"}), 401

        if not secrets.compare_digest(token, server_token):
            logger.warning("Invalid or expired token")
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)

    return decorated_function


def init_auth(host: str = "127.0.0.1", display: bool = True) -> str | None:
    """Initialize authentication system.

    Args:
        host: The host address the server is binding to.
        display: Whether to display the token in logs (default: True).

    Returns:
        The server token (only generated when binding to network,
        None for local-only binding).
    """
    global _auth_enabled

    # Check if auth is explicitly disabled via environment variable
    if os.environ.get("GPTME_DISABLE_AUTH", "").lower() in ("true", "1", "yes"):
        _auth_enabled = False
        if display:
            logger.info("=" * 60)
            logger.info("gptme-server (Auth Disabled)")
            logger.info("=" * 60)
            logger.info(f"Binding to: {host}")
            logger.info("Authentication: DISABLED (via GPTME_DISABLE_AUTH)")
            logger.info("")
            logger.info("⚠️  WARNING: Server is accessible without authentication!")
            logger.info(
                "Only use this in environments with external auth (e.g., k8s ingress)"
            )
            logger.info("=" * 60)
        return None

    # Disable auth for local-only binding
    if is_local_host(host):
        _auth_enabled = False
        if display:
            logger.info("=" * 60)
            logger.info("gptme-server (Local Mode)")
            logger.info("=" * 60)
            logger.info(f"Binding to: {host} (local-only)")
            logger.info("Authentication: DISABLED")
            logger.info("")
            logger.info("This is safe for local development.")
            logger.info("For network access, use --host 0.0.0.0 (enables auth)")
            logger.info("=" * 60)
        return None

    # Enable auth for network binding
    _auth_enabled = True
    token = get_server_token()

    if display and token:
        # Check if token is from environment or auto-generated
        env_token = os.environ.get("GPTME_SERVER_TOKEN")
        logger.info("=" * 60)
        logger.info("gptme-server Authentication")
        logger.info("=" * 60)
        if env_token:
            logger.info(f"Token: {token}")
            logger.info("")
            logger.info("Authentication is ENABLED (token from environment)")
            logger.info("Change token with: GPTME_SERVER_TOKEN=xxx gptme-server serve")
        else:
            logger.info("Authentication is ENABLED (auto-generated token)")
            logger.info("See warning above for the generated token.")
        logger.info("")
        logger.info("Retrieve current token: gptme-server token")
        logger.info("=" * 60)

    return token


@auth_api.route("/api/v2/auth/cookie", methods=["POST"])
@require_auth
def set_auth_cookie():
    """Set an HttpOnly authentication cookie.

    Requires a valid Bearer token in the Authorization header.
    Sets an HttpOnly cookie that will be sent automatically with
    subsequent requests, including SSE/EventSource connections.

    This eliminates the need for query parameter authentication,
    which exposes tokens in URLs and logs.

    Returns:
        200 with success message and Set-Cookie header.
    """
    server_token = get_server_token()
    if not server_token:
        return jsonify({"error": "Authentication system error"}), 500

    response = jsonify({"ok": True, "message": "Auth cookie set"})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        server_token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        path="/api/",
    )
    logger.info("Auth cookie set for client")
    return response


@auth_api.route("/api/v2/auth/cookie", methods=["DELETE"])
def clear_auth_cookie():
    """Clear the authentication cookie (logout).

    Returns:
        200 with success message and expired Set-Cookie header.
    """
    response = jsonify({"ok": True, "message": "Auth cookie cleared"})
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path="/api/",
        secure=request.is_secure,
        samesite="Lax",
    )
    logger.info("Auth cookie cleared for client")
    return response
