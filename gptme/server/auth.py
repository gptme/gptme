"""
Authentication middleware for gptme-server.

Provides bearer token authentication for API access.
Authentication is only required when binding to network interfaces.
"""

import logging
import os
import secrets
from functools import wraps

from flask import jsonify, request

logger = logging.getLogger(__name__)

# Token storage (in-memory, generated on startup)
_server_token: str | None = None

# Auth state (disabled for local-only binding)
_auth_enabled: bool = True


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

    Security Warning:
        Query parameter authentication (via ?token=xxx) is supported but
        LESS SECURE than Authorization headers because tokens appear in:
        - Server access logs
        - Browser history
        - Referrer headers
        - Proxy server logs

        Only use query parameters for SSE connections where custom headers
        aren't supported. For all other requests, use Authorization headers.

        Future: Implement cookie-based authentication for SSE to eliminate this risk.

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

        # Check Authorization header first (preferred method)
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
        else:
            # ⚠️  SECURITY WARNING: Query parameter fallback for SSE/EventSource
            # This is LESS SECURE as tokens appear in URLs and logs
            # Only use for SSE connections where Authorization headers aren't supported
            # TODO: Replace with cookie-based authentication for SSE
            token = request.args.get("token")
            if token:
                logger.debug("Using query parameter authentication (SSE fallback)")

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
