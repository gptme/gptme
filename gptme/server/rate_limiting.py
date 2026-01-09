"""
Rate limiting for gptme server.

Provides protection against abuse through configurable rate limits on API endpoints.
Rate limits are enforced per-IP address by default.

Configuration via environment variables:
- GPTME_RATE_LIMIT_ENABLED: Enable rate limiting (default: true)
- GPTME_RATE_LIMIT_DEFAULT: Default rate limit (default: "200 per minute")
- GPTME_RATE_LIMIT_STORAGE: Storage backend (default: "memory://")

Usage:
    from .rate_limiting import limiter

    @api.route("/endpoint")
    @limiter.limit("30 per minute")
    def endpoint():
        ...
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Rate limiting configuration defaults
DEFAULT_RATE_LIMIT = "200 per minute"
GENERATE_RATE_LIMIT = "30 per minute"
AUTH_RATE_LIMIT = "10 per minute"

# Track whether rate limiting is available
_flask_limiter_available = False
_limiter_instance: Any = None


def _is_rate_limit_enabled() -> bool:
    """Check if rate limiting should be enabled."""
    return os.environ.get("GPTME_RATE_LIMIT_ENABLED", "true").lower() not in (
        "0",
        "false",
        "no",
    )


def _get_rate_limit(env_var: str, default: str) -> str:
    """Get rate limit from environment variable or use default."""
    return os.environ.get(env_var, default)


# Try to import and create limiter at module level
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    _flask_limiter_available = True

    # Create limiter instance (will be initialized with app later)
    _limiter_instance = Limiter(
        key_func=get_remote_address,
        # Default limits applied to all routes
        default_limits=[_get_rate_limit("GPTME_RATE_LIMIT_DEFAULT", DEFAULT_RATE_LIMIT)],
        # Include rate limit headers in responses
        headers_enabled=True,
        # Storage URI (can be overridden in init_rate_limiting)
        storage_uri=os.environ.get("GPTME_RATE_LIMIT_STORAGE", "memory://"),
    )
except ImportError:
    _flask_limiter_available = False
    _limiter_instance = None


class _NoOpLimiter:
    """No-op limiter that does nothing when rate limiting is disabled."""

    def limit(self, *args, **kwargs):
        """Return a no-op decorator."""

        def decorator(f):
            return f

        return decorator

    def exempt(self, f):
        """Return the function unchanged."""
        return f

    def init_app(self, app):
        """No-op initialization."""
        pass


# Export the limiter (either real or no-op)
if _flask_limiter_available:
    limiter = _limiter_instance
else:
    limiter = _NoOpLimiter()


def init_rate_limiting(app) -> None:
    """Initialize rate limiting for the Flask app.

    Args:
        app: Flask application instance

    This must be called in create_app() to bind the limiter to the Flask app.
    If flask-limiter is not installed or rate limiting is disabled,
    this function does nothing.
    """
    global limiter

    if not _is_rate_limit_enabled():
        logger.info("Rate limiting disabled via GPTME_RATE_LIMIT_ENABLED")
        limiter = _NoOpLimiter()
        return

    if not _flask_limiter_available:
        logger.warning(
            "flask-limiter not installed. Rate limiting disabled. "
            "Install with: pip install gptme[server]"
        )
        limiter = _NoOpLimiter()
        return

    # Initialize the limiter with the Flask app
    _limiter_instance.init_app(app)

    # Set up breach logging
    @_limiter_instance.request_filter
    def _log_ratelimit():
        """Log rate limit breaches."""
        return False  # Don't exempt any requests

    storage_uri = os.environ.get("GPTME_RATE_LIMIT_STORAGE", "memory://")
    default_limit = _get_rate_limit("GPTME_RATE_LIMIT_DEFAULT", DEFAULT_RATE_LIMIT)
    logger.info(
        f"Rate limiting enabled: default={default_limit}, storage={storage_uri}"
    )


# Convenience functions for common rate limits
def get_generate_limit() -> str:
    """Get the rate limit for LLM generation endpoints."""
    return _get_rate_limit("GPTME_RATE_LIMIT_GENERATE", GENERATE_RATE_LIMIT)


def get_auth_limit() -> str:
    """Get the rate limit for authentication endpoints."""
    return _get_rate_limit("GPTME_RATE_LIMIT_AUTH", AUTH_RATE_LIMIT)
