"""Tests for the URL safety host allowlist feature."""

import pytest

from gptme.tools._url_safety import (
    _host_matches,
    _validate_url_scheme,
    parse_allow_hosts,
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


def test_case_insensitive_hostname():
    assert _host_matches("GitHub.com", "github.com") is True


def test_case_insensitive_pattern():
    assert _host_matches("github.com", "GitHub.com") is True


def test_case_insensitive_wildcard():
    assert _host_matches("API.GitHub.com", "*.github.com") is True


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


def test_empty_allowlist_blocks_all_hosts():
    # An explicit empty list is "block everything", not "unrestricted".
    set_session_allow_hosts([])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://github.com/")


def test_url_with_uppercase_host_matches_lowercase_allowlist():
    set_session_allow_hosts(["github.com"])
    _validate_url_scheme("https://GitHub.com/user/repo")  # no raise


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


# ── parse_allow_hosts (the actual CLI/env-var parsing path) ────────────────


def test_parse_allow_hosts_comma_separated():
    assert parse_allow_hosts("github.com, api.github.com") == [
        "github.com",
        "api.github.com",
    ]


def test_parse_allow_hosts_single():
    assert parse_allow_hosts("github.com") == ["github.com"]


def test_parse_allow_hosts_none_is_unrestricted():
    assert parse_allow_hosts(None) is None


def test_parse_allow_hosts_empty_string_is_empty_list():
    # Must be [] (block all), not None (unrestricted): the CLI guard used to
    # coerce "" to None, silently disabling the restriction.
    assert parse_allow_hosts("") == []


def test_parse_allow_hosts_whitespace_only_is_empty_list():
    assert parse_allow_hosts("   ") == []


def test_parse_allow_hosts_lowercases():
    assert parse_allow_hosts("GitHub.com, API.OpenAI.com") == [
        "github.com",
        "api.openai.com",
    ]
