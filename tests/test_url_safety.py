"""Tests for the URL safety host allowlist feature."""

import pytest

from gptme.tools._url_safety import (
    _host_matches,
    _validate_url_scheme,
    set_session_allow_hosts,
)


def teardown_function():
    """Reset allowlist after each test."""
    set_session_allow_hosts(None)


# ── _host_matches ──────────────────────────────────────────────────────────


def test_exact_match():
    assert _host_matches("github.com", "github.com") is True


def test_exact_mismatch():
    assert _host_matches("notgithub.com", "github.com") is False


def test_wildcard_subdomain_matches():
    assert _host_matches("api.github.com", "*.github.com") is True


def test_wildcard_deep_subdomain_matches():
    assert _host_matches("raw.githubusercontent.com", "*.githubusercontent.com") is True


def test_wildcard_does_not_match_base():
    # *.github.com should NOT match github.com itself
    assert _host_matches("github.com", "*.github.com") is False


def test_wildcard_does_not_match_sibling():
    assert _host_matches("notgithub.com", "*.github.com") is False


def test_wildcard_does_not_partial_match():
    assert _host_matches("evildotgithub.com", "*.github.com") is False


# ── _validate_url_host via _validate_url_scheme ────────────────────────────


def test_none_allowlist_unrestricted():
    set_session_allow_hosts(None)
    _validate_url_scheme("https://urlquery.net/")  # no raise


def test_host_not_in_allowlist_raises():
    set_session_allow_hosts(["github.com"])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://urlquery.net/fetch?url=https://github.com")


def test_allowed_host_passes():
    set_session_allow_hosts(["github.com"])
    _validate_url_scheme("https://github.com/user/repo")  # no raise


def test_wildcard_subdomain_allowed():
    set_session_allow_hosts(["*.github.com"])
    _validate_url_scheme("https://api.github.com/v3/repos")  # no raise


def test_wildcard_does_not_cover_base_domain():
    set_session_allow_hosts(["*.github.com"])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://github.com/")


def test_exact_domain_no_suffix_bleed():
    set_session_allow_hosts(["github.com"])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://notgithub.com/")


def test_multiple_allowed_hosts():
    set_session_allow_hosts(["github.com", "api.openai.com"])
    _validate_url_scheme("https://github.com/")  # no raise
    _validate_url_scheme("https://api.openai.com/")  # no raise
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://evil.com/")


def test_error_message_names_host_and_list():
    set_session_allow_hosts(["github.com"])
    with pytest.raises(
        ValueError, match="not in the session's allowed-hosts"
    ) as exc_info:
        _validate_url_scheme("https://urlquery.net/")
    msg = str(exc_info.value)
    assert "urlquery.net" in msg
    assert "github.com" in msg
    assert "--allow-hosts" in msg


# ── env-var parsing contract (unit only — no actual env reading) ──────────


def test_env_var_comma_parsing():
    raw = "github.com, api.github.com"
    parsed = [h.strip() for h in raw.split(",") if h.strip()]
    assert parsed == ["github.com", "api.github.com"]


def test_env_var_single():
    raw = "github.com"
    parsed = [h.strip() for h in raw.split(",") if h.strip()]
    assert parsed == ["github.com"]


def test_env_var_empty_string_gives_empty_list():
    raw = ""
    parsed = [h.strip() for h in raw.split(",") if h.strip()]
    assert parsed == []
