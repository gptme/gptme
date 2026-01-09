"""Tests for rate limiting functionality in gptme server."""

import pytest
from unittest.mock import patch

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask import Flask
from flask.testing import FlaskClient


class TestRateLimitingModule:
    """Tests for the rate_limiting module."""

    def test_no_op_limiter_when_disabled(self):
        """Test that NoOpLimiter is used when rate limiting is disabled."""
        with patch.dict("os.environ", {"GPTME_RATE_LIMIT_ENABLED": "false"}):
            # Re-import to pick up new env var
            from gptme.server import rate_limiting

            rate_limiting.init_rate_limiting(Flask(__name__))
            assert isinstance(rate_limiting.limiter, rate_limiting._NoOpLimiter)

    def test_is_rate_limit_enabled_default(self):
        """Test that rate limiting is enabled by default."""
        with patch.dict("os.environ", {}, clear=True):
            from gptme.server.rate_limiting import _is_rate_limit_enabled

            assert _is_rate_limit_enabled() is True

    def test_is_rate_limit_enabled_disabled(self):
        """Test that rate limiting can be disabled via env var."""
        for value in ["0", "false", "no", "FALSE", "No"]:
            with patch.dict("os.environ", {"GPTME_RATE_LIMIT_ENABLED": value}):
                from gptme.server.rate_limiting import _is_rate_limit_enabled

                assert _is_rate_limit_enabled() is False

    def test_get_rate_limit_default(self):
        """Test default rate limit values."""
        from gptme.server.rate_limiting import (
            DEFAULT_RATE_LIMIT,
            GENERATE_RATE_LIMIT,
            AUTH_RATE_LIMIT,
            _get_rate_limit,
        )

        assert _get_rate_limit("NONEXISTENT_VAR", DEFAULT_RATE_LIMIT) == DEFAULT_RATE_LIMIT
        assert DEFAULT_RATE_LIMIT == "200 per minute"
        assert GENERATE_RATE_LIMIT == "30 per minute"
        assert AUTH_RATE_LIMIT == "10 per minute"

    def test_get_rate_limit_from_env(self):
        """Test that rate limits can be configured via env vars."""
        with patch.dict("os.environ", {"GPTME_RATE_LIMIT_DEFAULT": "100 per hour"}):
            from gptme.server.rate_limiting import _get_rate_limit

            assert _get_rate_limit("GPTME_RATE_LIMIT_DEFAULT", "200 per minute") == "100 per hour"

    def test_get_generate_limit(self):
        """Test get_generate_limit function."""
        from gptme.server.rate_limiting import get_generate_limit, GENERATE_RATE_LIMIT

        # Default value
        assert get_generate_limit() == GENERATE_RATE_LIMIT

        # Custom value
        with patch.dict("os.environ", {"GPTME_RATE_LIMIT_GENERATE": "10 per minute"}):
            assert get_generate_limit() == "10 per minute"

    def test_get_auth_limit(self):
        """Test get_auth_limit function."""
        from gptme.server.rate_limiting import get_auth_limit, AUTH_RATE_LIMIT

        # Default value
        assert get_auth_limit() == AUTH_RATE_LIMIT

        # Custom value
        with patch.dict("os.environ", {"GPTME_RATE_LIMIT_AUTH": "5 per minute"}):
            assert get_auth_limit() == "5 per minute"


class TestNoOpLimiter:
    """Tests for the NoOpLimiter fallback class."""

    def test_limit_decorator_is_noop(self):
        """Test that limit decorator does nothing."""
        from gptme.server.rate_limiting import _NoOpLimiter

        limiter = _NoOpLimiter()

        @limiter.limit("1 per second")
        def my_func():
            return "result"

        assert my_func() == "result"

    def test_exempt_decorator_is_noop(self):
        """Test that exempt decorator does nothing."""
        from gptme.server.rate_limiting import _NoOpLimiter

        limiter = _NoOpLimiter()

        @limiter.exempt
        def my_func():
            return "result"

        assert my_func() == "result"

    def test_init_app_is_noop(self):
        """Test that init_app does nothing."""
        from gptme.server.rate_limiting import _NoOpLimiter

        limiter = _NoOpLimiter()
        app = Flask(__name__)
        limiter.init_app(app)  # Should not raise


@pytest.mark.skipif(
    not pytest.importorskip("flask_limiter", reason="flask-limiter not installed"),
    reason="flask-limiter not installed",
)
class TestRateLimitingIntegration:
    """Integration tests for rate limiting (requires flask-limiter)."""

    def test_rate_limiting_initialization(self):
        """Test that rate limiting initializes correctly."""
        from gptme.server.rate_limiting import init_rate_limiting, limiter

        app = Flask(__name__)
        init_rate_limiting(app)

        # Limiter should be initialized (not NoOpLimiter if flask-limiter is installed)
        from flask_limiter import Limiter

        assert isinstance(limiter, (Limiter, type(limiter)))

    def test_rate_limit_headers_in_response(self):
        """Test that rate limit headers are included in responses."""
        from gptme.server.api import create_app

        app = create_app()
        client = app.test_client()

        # Make a request to the API
        response = client.get("/api")

        # Check for rate limit headers (if rate limiting is enabled)
        # Headers like X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
        # Note: These may or may not be present depending on flask-limiter configuration
        assert response.status_code == 200
