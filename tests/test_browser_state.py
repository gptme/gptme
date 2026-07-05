"""Tests for browser session state management — save_browser_state / load_browser_state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_storage_state_override():
    """Reset the in-session storage state override before and after each test.

    Prevents one test's state from leaking into the next — especially important
    when testing set_storage_state_override() / get_context_options() interactions.
    """
    from gptme.tools._browser_thread import set_storage_state_override

    set_storage_state_override(None)
    yield
    set_storage_state_override(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state_file(tmp_path: Path) -> Path:
    """Write a minimal valid Playwright storage-state JSON to tmp_path."""
    state: dict = {"cookies": [], "origins": []}
    p = tmp_path / "session.json"
    p.write_text(json.dumps(state))
    return p


# ---------------------------------------------------------------------------
# Tests for load_browser_state (in-session state restoration)
# ---------------------------------------------------------------------------


def test_load_browser_state_sets_override(tmp_path, monkeypatch):
    """load_browser_state() sets the in-session storage-state override."""
    import gptme.tools._browser_thread as _bt
    from gptme.tools._browser_playwright import _do_load_browser_state

    state_file = _make_state_file(tmp_path)

    # Track close_current_page calls
    closed: list[bool] = []
    monkeypatch.setattr(
        "gptme.tools._browser_playwright._close_current_page",
        lambda: closed.append(True),
    )

    result = _do_load_browser_state(None, str(state_file))  # type: ignore[arg-type]

    assert _bt._override_storage_state == state_file
    assert "open_page" in result  # message tells user what to do next
    assert closed == [True]  # current page was closed


def test_load_browser_state_missing_file_raises(tmp_path, monkeypatch):
    """load_browser_state() raises FileNotFoundError for a non-existent path."""
    from gptme.tools._browser_playwright import _do_load_browser_state

    monkeypatch.setattr(
        "gptme.tools._browser_playwright._close_current_page", lambda: None
    )

    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(FileNotFoundError) as exc_info:
        _do_load_browser_state(None, str(missing))  # type: ignore[arg-type]
    assert "does-not-exist.json" in str(exc_info.value)
    assert "save_browser_state" in str(exc_info.value)


def test_load_browser_state_expands_tilde(tmp_path, monkeypatch):
    """load_browser_state() expands ~ in the path."""
    import gptme.tools._browser_thread as _bt
    from gptme.tools._browser_playwright import _do_load_browser_state

    _make_state_file(tmp_path)  # creates tmp_path/session.json

    # expanduser() reads $HOME, not Path.home() — patch the env var
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "gptme.tools._browser_playwright._close_current_page", lambda: None
    )

    _do_load_browser_state(None, "~/session.json")  # type: ignore[arg-type]

    assert _bt._override_storage_state == tmp_path / "session.json"


def test_get_context_options_uses_override(tmp_path):
    """get_context_options() picks up the in-session override over the env var."""
    from gptme.tools._browser_thread import (
        get_context_options,
        set_storage_state_override,
    )

    state_file = _make_state_file(tmp_path)
    set_storage_state_override(state_file)

    opts = get_context_options()
    assert opts.get("storage_state") == str(state_file)


def test_get_context_options_override_beats_env_var(tmp_path, monkeypatch):
    """In-session override takes priority over the GPTME_BROWSER_STORAGE_STATE env var."""
    from gptme.tools._browser_thread import (
        get_context_options,
        set_storage_state_override,
    )

    override_file = tmp_path / "override.json"
    override_file.write_text(json.dumps({"cookies": [], "origins": []}))

    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps({"cookies": [], "origins": []}))

    # Set the env var to point at env_file
    monkeypatch.setenv("GPTME_BROWSER_STORAGE_STATE", str(env_file))

    # Set the override to point at override_file
    set_storage_state_override(override_file)

    try:
        opts = get_context_options()
        # Override wins
        assert opts.get("storage_state") == str(override_file)
    finally:
        set_storage_state_override(None)


def test_get_context_options_no_override_uses_env_var(tmp_path, monkeypatch):
    """Without an override, get_context_options() falls back to the env var."""
    from gptme.tools._browser_thread import (
        get_context_options,
        set_storage_state_override,
    )

    set_storage_state_override(None)

    state_file = _make_state_file(tmp_path)
    monkeypatch.setenv("GPTME_BROWSER_STORAGE_STATE", str(state_file))

    opts = get_context_options()
    assert opts.get("storage_state") == str(state_file)


def test_load_browser_state_closes_current_page(tmp_path, monkeypatch):
    """load_browser_state() always closes the current page before setting the override."""
    from gptme.tools._browser_playwright import _do_load_browser_state

    state_file = _make_state_file(tmp_path)

    close_calls: list[bool] = []
    monkeypatch.setattr(
        "gptme.tools._browser_playwright._close_current_page",
        lambda: close_calls.append(True),
    )

    _do_load_browser_state(None, str(state_file))  # type: ignore[arg-type]

    assert close_calls == [True], "expected exactly one close_current_page() call"


def test_load_browser_state_registered_in_tool_spec():
    """load_browser_state is registered as a ToolFunction in the browser ToolSpec."""
    from gptme.tools.browser import tool

    fn_names = [f.name for f in tool.functions or []]
    assert "load_browser_state" in fn_names, (
        f"load_browser_state not found in browser tool functions; found: {fn_names}"
    )


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    """save path roundtrips through load: saved path == loaded override."""
    import gptme.tools._browser_thread as _bt
    from gptme.tools._browser_playwright import _do_load_browser_state

    state_file = _make_state_file(tmp_path)

    monkeypatch.setattr(
        "gptme.tools._browser_playwright._close_current_page", lambda: None
    )

    _do_load_browser_state(None, str(state_file))  # type: ignore[arg-type]

    # The override should point at the same file we started with
    assert _bt._override_storage_state is not None
    assert _bt._override_storage_state.resolve() == state_file.resolve()
