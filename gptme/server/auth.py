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


def get_server_token() -> str:
    """Get or generate the server authentication token from environment.

    Returns:
        The current server token from GPTME_SERVER_TOKEN env var,
        or generates one if not set.
    """
    global _server_token
    if _server_token is None:
        # Check environment variable first
        env_token = os.environ.get("GPTME_SERVER_TOKEN")
        if env_token:
            _server_token = env_token
            logger.info("Using token from GPTME_SERVER_TOKEN environment variable")
        else:
            _server_token = generate_token()
            # Store in environment for retrieval
            os.environ["GPTME_SERVER_TOKEN"] = _server_token
            logger.info(f"Generated new server token: {_server_token}")
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
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            logger.warning("Missing Authorization header")
            return jsonify({"error": "Missing authentication credentials"}), 401

        try:
            scheme, token = auth_header.split(" ", 1)
        except ValueError:
            logger.warning("Invalid Authorization header format")
            return jsonify({"error": "Invalid authorization header format"}), 401

        if scheme.lower() != "bearer":
            logger.warning(f"Invalid authentication scheme: {scheme}")
            return jsonify({"error": "Invalid authentication scheme"}), 401

        if not secrets.compare_digest(token, get_server_token()):
            logger.warning("Invalid or expired token")
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)

    return decorated_function


def init_auth(display: bool = True) -> str:
    """Initialize authentication system.

    Args:
        display: Whether to display the token in logs (default: True).

    Returns:
        The generated server token.
    """
    token = get_server_token()

    if display:
        logger.info("=" * 60)
        logger.info("gptme-server Authentication Token")
        logger.info("=" * 60)
        logger.info(f"Token: {token}")
        logger.info("")
        logger.info("Set custom token with: GPTME_SERVER_TOKEN=xxx gptme server serve")
        logger.info("Or retrieve current token: gptme server token")
        logger.info("=" * 60)

    return token
