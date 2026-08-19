"""Tests for ActivityWatch-driven tool selection (--tool-select-by-app)."""

import json
import urllib.error
from unittest.mock import patch

import pytest

from gptme.config import Config, ProjectConfig, UserConfig
from gptme.tools import _allowlist_from_activitywatch, _tool_select_by_app_rules
from gptme.tools._activitywatch import (
    get_current_app,
    match_app_rules,
    resolve_allowlist_for_current_app,
)

BUCKETS = {
    "aw-watcher-afk_host": {"type": "afkstatus"},
    "aw-watcher-window_host": {"type": "currentwindow"},
}


def _fake_aw(app: str | None, *, buckets=None, events=None):
    """Return a _get_json stub mimicking the ActivityWatch REST API."""
    resolved_buckets = BUCKETS if buckets is None else buckets
    if events is None:
        events = [] if app is None else [{"data": {"app": app, "title": "some title"}}]

    def _get_json(url: str, timeout: float):
        if url.endswith("/api/0/buckets/"):
            return resolved_buckets
        if "/events" in url:
            assert "aw-watcher-window_host" in url
            return events
        raise AssertionError(f"unexpected url: {url}")

    return _get_json


@pytest.mark.parametrize(
    ("app", "expected"),
    [
        ("firefox", ["browser", "read"]),
        ("Firefox Developer Edition", ["browser", "read"]),
        ("Code", ["shell", "patch"]),
        ("Alacritty", None),
    ],
)
def test_match_app_rules_is_case_insensitive_glob(app, expected):
    rules = {"*firefox*": ["browser", "read"], "code": ["shell", "patch"]}
    assert match_app_rules(app, rules) == expected


def test_match_app_rules_first_match_wins():
    rules = {"*fire*": ["browser"], "*firefox*": ["read"]}
    assert match_app_rules("firefox", rules) == ["browser"]


def test_get_current_app_reads_window_bucket():
    with patch("gptme.tools._activitywatch._get_json", _fake_aw("firefox")):
        assert get_current_app(server="http://aw.test") == "firefox"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"buckets": {"aw-watcher-afk_host": {"type": "afkstatus"}}},  # no window bucket
        {"events": []},  # bucket exists but has no events
        {"events": [{"data": {}}]},  # event without an app field
        {"events": [{"data": {"app": ""}}]},  # empty app name
        {"buckets": []},  # malformed buckets payload
    ],
)
def test_get_current_app_degrades_to_none(kwargs):
    with patch("gptme.tools._activitywatch._get_json", _fake_aw("firefox", **kwargs)):
        assert get_current_app(server="http://aw.test") is None


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("connection refused"),
        OSError("timed out"),
        json.JSONDecodeError("bad", "", 0),
    ],
)
def test_get_current_app_offline_server_degrades_to_none(exc):
    """An offline/unreachable ActivityWatch must never raise into the caller."""
    with patch("gptme.tools._activitywatch._get_json", side_effect=exc):
        assert get_current_app(server="http://aw.test") is None


def test_resolve_allowlist_for_current_app():
    rules = {"*firefox*": ["browser", "read"]}
    with patch("gptme.tools._activitywatch._get_json", _fake_aw("firefox")):
        assert resolve_allowlist_for_current_app(rules) == ["browser", "read"]


def test_resolve_allowlist_without_rules_skips_the_query():
    """No configured rules means no reason to hit the server at all."""
    with patch("gptme.tools._activitywatch._get_json", side_effect=AssertionError):
        assert resolve_allowlist_for_current_app({}) is None


def test_resolve_allowlist_unmatched_app_falls_back():
    with patch("gptme.tools._activitywatch._get_json", _fake_aw("Alacritty")):
        assert resolve_allowlist_for_current_app({"*firefox*": ["browser"]}) is None


def _config(user_rules=None, project_rules=None) -> Config:
    user = UserConfig()
    user.settings.tool_select_by_app = user_rules or {}
    project = None
    if project_rules is not None:
        project = ProjectConfig()
        project.settings.tool_select_by_app = project_rules
    return Config(user=user, project=project)


def test_rules_merge_project_over_user():
    config = _config(
        user_rules={"*firefox*": ["browser"], "code": ["shell"]},
        project_rules={"code": ["shell", "patch"]},
    )
    assert _tool_select_by_app_rules(config) == {
        "*firefox*": ["browser"],
        "code": ["shell", "patch"],
    }


def test_allowlist_from_activitywatch_requires_opt_in(monkeypatch):
    monkeypatch.delenv("GPTME_TOOL_SELECT_BY_APP", raising=False)
    config = _config(user_rules={"*firefox*": ["browser"]})
    with patch("gptme.tools._activitywatch._get_json", side_effect=AssertionError):
        assert _allowlist_from_activitywatch(config) is None


def test_allowlist_from_activitywatch_opted_in(monkeypatch):
    monkeypatch.setenv("GPTME_TOOL_SELECT_BY_APP", "1")
    config = _config(user_rules={"*firefox*": ["browser", "read"]})
    with patch("gptme.tools._activitywatch._get_json", _fake_aw("firefox")):
        assert _allowlist_from_activitywatch(config) == ["browser", "read"]


def test_allowlist_from_activitywatch_opted_in_without_rules(monkeypatch):
    monkeypatch.setenv("GPTME_TOOL_SELECT_BY_APP", "1")
    with patch("gptme.tools._activitywatch._get_json", side_effect=AssertionError):
        assert _allowlist_from_activitywatch(_config()) is None


def test_rules_project_glob_beats_user_catchall():
    """Project-specific globs should be evaluated before a broad user catch-all."""
    config = _config(
        user_rules={"*": ["shell"]},
        project_rules={"*firefox*": ["browser"]},
    )
    rules = _tool_select_by_app_rules(config)
    # Project rule must come first in iteration so it wins for "firefox"
    result = match_app_rules("firefox", rules)
    assert result == ["browser"], (
        "Project glob '*firefox*' should win over user catch-all '*'"
    )


def test_allowlist_from_activitywatch_uses_config_env_server_url(monkeypatch):
    """AW_SERVER_URL set in [env] config layer must reach the AW client."""
    monkeypatch.setenv("GPTME_TOOL_SELECT_BY_APP", "1")
    # Ensure process env does NOT have the override so only config-layer wins
    monkeypatch.delenv("AW_SERVER_URL", raising=False)
    monkeypatch.delenv("GPTME_AW_SERVER_URL", raising=False)

    captured_urls: list[str] = []

    def _spy_get_json(url: str, timeout: float):
        captured_urls.append(url)
        if url.endswith("/api/0/buckets/"):
            return BUCKETS
        if "/events" in url:
            return [{"data": {"app": "firefox", "title": "t"}}]
        raise AssertionError(f"unexpected url: {url}")

    config = _config(user_rules={"*firefox*": ["browser"]})
    # Set custom server URL via the config user-env layer (simulates [env] in gptme.toml)
    config.user.env["AW_SERVER_URL"] = "http://aw-custom:5600"

    with patch("gptme.tools._activitywatch._get_json", _spy_get_json):
        result = _allowlist_from_activitywatch(config)

    assert result == ["browser"]
    assert all(u.startswith("http://aw-custom:5600") for u in captured_urls), (
        f"Expected custom AW server URL, got: {captured_urls}"
    )


def test_allowlist_from_activitywatch_rejects_non_string_server_url(monkeypatch):
    """A non-string AW_SERVER_URL (e.g. integer from TOML) must warn and return None."""
    monkeypatch.setenv("GPTME_TOOL_SELECT_BY_APP", "1")
    monkeypatch.delenv("AW_SERVER_URL", raising=False)

    config = _config(user_rules={"*": ["shell"]})
    # Simulate a misconfigured integer port number instead of a URL string
    config.user.env["AW_SERVER_URL"] = 5600  # type: ignore[assignment]

    with patch("gptme.tools._activitywatch._get_json") as mock_get_json:
        result = _allowlist_from_activitywatch(config)

    # Must gracefully return None instead of crashing on .rstrip()
    assert result is None, "Non-string AW_SERVER_URL should fall back to None"
    mock_get_json.assert_not_called()
