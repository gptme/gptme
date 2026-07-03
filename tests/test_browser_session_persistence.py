"""Tests for browser session persistence via GPTME_BROWSER_STORAGE_STATE (#216).

Validates that:
- get_context_options() includes storage_state when the env var points to an
  existing file, and omits it when the path doesn't exist.
- save_browser_state() raises a clear error when no page is open.
- The public browser.save_browser_state() wrapper delegates correctly.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Unit tests for get_context_options()
# ---------------------------------------------------------------------------


def test_get_context_options_no_env_var():
    """Without GPTME_BROWSER_STORAGE_STATE, storage_state key is absent."""
    from gptme.tools._browser_thread import get_context_options

    with patch.dict(os.environ, {}, clear=False):
        # Remove the var if it happens to be set in the test environment
        os.environ.pop("GPTME_BROWSER_STORAGE_STATE", None)
        opts = get_context_options()
    assert "storage_state" not in opts
    # Base options must still be present
    assert "locale" in opts


def test_get_context_options_with_existing_file(tmp_path):
    """When env var points to an existing file, storage_state is included."""
    from gptme.tools._browser_thread import get_context_options

    state_file = tmp_path / "session.json"
    state_file.write_text('{"cookies": [], "origins": []}')

    with patch.dict(os.environ, {"GPTME_BROWSER_STORAGE_STATE": str(state_file)}):
        opts = get_context_options()

    assert "storage_state" in opts
    assert opts["storage_state"] == str(state_file)


def test_get_context_options_with_missing_file(tmp_path):
    """When env var points to a missing file, storage_state is NOT included (warn, don't crash)."""
    from gptme.tools._browser_thread import get_context_options

    missing = str(tmp_path / "nonexistent.json")

    with patch.dict(os.environ, {"GPTME_BROWSER_STORAGE_STATE": missing}):
        opts = get_context_options()

    assert "storage_state" not in opts
    # Base options still present
    assert "locale" in opts


def test_get_context_options_with_tilde_path(tmp_path, monkeypatch):
    """~ in the path is expanded before existence check."""
    from gptme.tools._browser_thread import get_context_options

    # Point ~ at tmp_path so ~ expansion is testable without touching real home dir
    monkeypatch.setenv("HOME", str(tmp_path))
    state_file = tmp_path / "session.json"
    state_file.write_text('{"cookies": [], "origins": []}')

    with patch.dict(os.environ, {"GPTME_BROWSER_STORAGE_STATE": "~/session.json"}):
        opts = get_context_options()

    assert "storage_state" in opts
    assert Path(opts["storage_state"]) == state_file


def test_get_context_options_preserves_base_options(tmp_path):
    """Loading storage_state must not clobber locale/geolocation/permissions."""
    from gptme.tools._browser_thread import DEFAULT_CONTEXT_OPTIONS, get_context_options

    state_file = tmp_path / "session.json"
    state_file.write_text('{"cookies": [], "origins": []}')

    with patch.dict(os.environ, {"GPTME_BROWSER_STORAGE_STATE": str(state_file)}):
        opts = get_context_options()

    for key in DEFAULT_CONTEXT_OPTIONS:
        assert key in opts, f"Base option {key!r} was dropped from context options"


# ---------------------------------------------------------------------------
# Unit tests for save_browser_state() (no real browser needed)
# ---------------------------------------------------------------------------


def test_save_browser_state_no_open_page_raises():
    """save_browser_state() raises RuntimeError when no page/context is open."""
    import gptme.tools._browser_playwright as _pw

    # Ensure no page is open
    _pw._current_page = None
    _pw._current_context = None
    _pw._browser = None

    from gptme.tools._browser_playwright import _do_save_browser_state

    with (
        patch("gptme.tools._browser_playwright._current_context", None),
        patch("gptme.tools._browser_playwright._browser", None),
    ):
        try:
            _do_save_browser_state(MagicMock(), "/tmp/state.json")
            raise AssertionError("Should have raised RuntimeError")
        except RuntimeError as e:
            assert "open_page" in str(e).lower() or "No browser context" in str(e)


def test_save_browser_state_calls_storage_state(tmp_path):
    """save_browser_state() calls context.storage_state(path=...) with the right path."""
    import gptme.tools._browser_playwright as _pw

    mock_context = MagicMock()
    # Simulate a valid session JSON being written
    state_path = tmp_path / "session.json"

    def fake_storage_state(path=None):
        Path(path).write_text('{"cookies":[],"origins":[]}')

    mock_context.storage_state.side_effect = fake_storage_state

    from gptme.tools._browser_playwright import _do_save_browser_state

    with patch.object(_pw, "_current_context", mock_context):
        result = _do_save_browser_state(MagicMock(), str(state_path))

    mock_context.storage_state.assert_called_once_with(path=str(state_path))
    assert str(state_path) in result


def test_save_browser_state_creates_parent_dirs(tmp_path):
    """save_browser_state() creates missing parent directories."""
    import gptme.tools._browser_playwright as _pw

    mock_context = MagicMock()
    nested_path = tmp_path / "a" / "b" / "c" / "session.json"

    def fake_storage_state(path=None):
        # The parent must already exist by the time this is called
        Path(path).write_text('{"cookies":[],"origins":[]}')

    mock_context.storage_state.side_effect = fake_storage_state

    from gptme.tools._browser_playwright import _do_save_browser_state

    with patch.object(_pw, "_current_context", mock_context):
        _do_save_browser_state(MagicMock(), str(nested_path))

    assert nested_path.parent.exists()


# ---------------------------------------------------------------------------
# Integration: browser.save_browser_state() delegates to playwright backend
# ---------------------------------------------------------------------------


def test_browser_save_browser_state_delegates_to_playwright():
    """browser.save_browser_state() delegates to the playwright implementation."""
    from unittest.mock import patch as _patch

    with (
        _patch("gptme.tools.browser.browser", "playwright"),
        _patch(
            "gptme.tools.browser.save_browser_state_pw", return_value="Saved."
        ) as mock_pw,
    ):
        from gptme.tools.browser import save_browser_state

        result = save_browser_state("~/state.json")

    mock_pw.assert_called_once_with("~/state.json")
    assert result == "Saved."


def test_browser_save_browser_state_raises_for_lynx():
    """save_browser_state() raises ValueError for the lynx backend."""
    with patch("gptme.tools.browser.browser", "lynx"):
        from gptme.tools.browser import save_browser_state

        try:
            save_browser_state("/tmp/state.json")
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "playwright" in str(e).lower()
