"""Tests for API quota error detection and skip behavior."""

from conftest import _QUOTA_ERROR_PATTERNS


def test_quota_patterns_detect_anthropic_usage_limits():
    """Test that common API quota error messages are detected."""
    error_messages = [
        "You have reached your specified API usage limits. You will regain access on 2026-04-01",
        "Error: rate limit exceeded, please retry after 60 seconds",
        "You exceeded your current quota, please check your plan",
        "insufficient_quota: Your account has insufficient funds",
        "Error: billing hard limit reached",
        "Your spending limit has been reached",
    ]
    for msg in error_messages:
        assert any(p in msg.lower() for p in _QUOTA_ERROR_PATTERNS), (
            f"Pattern not detected for: {msg}"
        )


def test_quota_patterns_no_false_positives():
    """Test that normal errors are not misidentified as quota errors."""
    normal_errors = [
        "Invalid API key provided",
        "Model not found: claude-nonexistent",
        "Connection timeout after 30 seconds",
        "Internal server error",
        "Invalid request: messages must be non-empty",
    ]
    for msg in normal_errors:
        assert not any(p in msg.lower() for p in _QUOTA_ERROR_PATTERNS), (
            f"False positive for: {msg}"
        )
