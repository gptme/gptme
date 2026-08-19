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
