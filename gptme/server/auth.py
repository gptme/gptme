"""
Authentication middleware for gptme-server.

Provides bearer token authentication for API access.
"""

import logging
import os
import secrets
from functools import wraps

from flask import jsonify, request

logger = logging.getLogger(__name__)

# Token storage (in-memory, generated on startup)
_server_token: str | None = None


def generate_token() -> str:
    """Generate a cryptographically secure random token.

    Returns:
        A URL-safe random string of 32+ characters.
    """
    return secrets.token_urlsafe(32)


def get_server_token() -> str | None:
    """Get the server authentication token from environment.

    Returns:
        The current server token from GPTME_SERVER_TOKEN env var,
        or None if not configured.
    """
    global _server_token
    if _server_token is None:
        # Check environment variable
        env_token = os.environ.get("GPTME_SERVER_TOKEN")
        if env_token:
            _server_token = env_token
            logger.info("Using token from GPTME_SERVER_TOKEN environment variable")
        # No auto-generation - return None if not configured
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

    Usage:
        @api.route("/api/protected")
        @require_auth
        def protected_endpoint():
            return {"data": "protected"}

    Returns:
        Decorated function that validates bearer token before execution.

    Raises:
        401 Unauthorized: Missing or invalid authentication credentials.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip authentication if no token is configured
        server_token = get_server_token()
        if server_token is None:
            return f(*args, **kwargs)

        # Token is configured, require authentication
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
            # Fallback to query parameter for SSE/EventSource compatibility
            # (browsers' EventSource API doesn't support custom headers)
            token = request.args.get("token")

        if not token:
            logger.warning("Missing authentication credentials")
            return jsonify({"error": "Missing authentication credentials"}), 401

        if not secrets.compare_digest(token, server_token):
            logger.warning("Invalid or expired token")
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)

    return decorated_function


def init_auth(display: bool = True) -> str | None:
    """Initialize authentication system.

    Args:
        display: Whether to display the token in logs (default: True).

    Returns:
        The server token if configured, None otherwise.
    """
    token = get_server_token()

    if display:
        if token:
            logger.info("=" * 60)
            logger.info("gptme-server Authentication")
            logger.info("=" * 60)
            logger.info(f"Token: {token}")
            logger.info("")
            logger.info("Authentication is ENABLED")
            logger.info("Change token with: GPTME_SERVER_TOKEN=xxx gptme-server serve")
            logger.info("Or retrieve current token: gptme-server token")
            logger.info("=" * 60)
        else:
            logger.info("=" * 60)
            logger.info("gptme-server Authentication")
            logger.info("=" * 60)
            logger.info("Authentication is DISABLED (no token configured)")
            logger.info("")
            logger.info("To enable authentication for local network exposure:")
            logger.info("  GPTME_SERVER_TOKEN=your-secret-token gptme-server serve")
            logger.info("=" * 60)

    return token
