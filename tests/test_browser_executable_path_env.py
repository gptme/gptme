"""Tests for independent GPTME_BROWSER_EXECUTABLE_PATH environment setting.

Regression tests for gptme/gptme#4167: independent executable path setting
with correct precedence (explicit constructor > env var > legacy engine path).
"""

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("playwright")

from gptme.tools._browser_thread import BrowserThread


@pytest.fixture
def mock_playwright(monkeypatch):
    """Mock Playwright with clean environment."""
    # Isolate from developer environment. get_env() reads both the prefixed
    # (GPTME_) and unprefixed forms, so clear both for each variable.
    monkeypatch.delenv("GPTME_BROWSER_ENGINE", raising=False)
    monkeypatch.delenv("BROWSER_ENGINE", raising=False)
    monkeypatch.delenv("GPTME_BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.delenv("BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.delenv("GPTME_BROWSER_CDP_URL", raising=False)
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    with patch("gptme.tools._browser_thread.sync_playwright") as mock_sync_pw:
        mock_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw
        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_pw.firefox.launch.return_value = mock_browser
        yield mock_pw, mock_browser


class TestBrowserExecutablePathPrecedence:
    """Test GPTME_BROWSER_EXECUTABLE_PATH precedence and behavior."""

    @pytest.mark.parametrize(
        ("engine_env", "exec_env", "explicit_exec", "expected_engine", "expected_exec"),
        [
            # (GPTME_BROWSER_ENGINE, GPTME_BROWSER_EXECUTABLE_PATH, constructor arg, expected engine, expected executable)
            # Default: no env vars
            (None, None, None, "chromium", None),
            # Independent env only
            (None, "/custom/chromium", None, "chromium", "/custom/chromium"),
            # Named engine + independent env
            ("firefox", "/custom/firefox", None, "firefox", "/custom/firefox"),
            # Legacy path (Firefox default)
            ("/legacy/firefox", None, None, "firefox", "/legacy/firefox"),
            # Independent env wins over legacy path
            ("/legacy/firefox", "/new/firefox", None, "firefox", "/new/firefox"),
            # Explicit constructor wins over env
            (
                None,
                "/env/chromium",
                "/explicit/chromium",
                "chromium",
                "/explicit/chromium",
            ),
            # Explicit constructor wins over both
            (
                "/legacy/firefox",
                "/env/firefox",
                "/explicit/firefox",
                "firefox",
                "/explicit/firefox",
            ),
            # Empty independent env behaves as absent
            (None, "", None, "chromium", None),
            # Whitespace independent env behaves as absent
            (None, "   ", None, "chromium", None),
        ],
    )
    def test_executable_path_precedence(
        self,
        mock_playwright,
        monkeypatch,
        engine_env,
        exec_env,
        explicit_exec,
        expected_engine,
        expected_exec,
    ):
        """Test precedence: explicit constructor > independent env > legacy engine path."""
        mock_pw, mock_browser = mock_playwright

        # Set environment
        if engine_env is not None:
            monkeypatch.setenv("GPTME_BROWSER_ENGINE", engine_env)
        if exec_env is not None:
            monkeypatch.setenv("GPTME_BROWSER_EXECUTABLE_PATH", exec_env)

        # Create browser thread
        if explicit_exec is not None:
            bt = BrowserThread(executable_path=explicit_exec)
        else:
            bt = BrowserThread()

        try:
            # Verify engine and executable_path
            assert bt.engine == expected_engine
            assert bt.executable_path == expected_exec

            # Verify launcher was called with correct kwargs
            launcher = (
                mock_pw.chromium.launch
                if expected_engine == "chromium"
                else mock_pw.firefox.launch
            )
            launcher.assert_called_once()
            call_kwargs = launcher.call_args[1]

            if expected_exec:
                assert call_kwargs.get("executable_path") == expected_exec
            else:
                assert "executable_path" not in call_kwargs
        finally:
            bt.stop()

    @pytest.mark.parametrize(
        "message",
        [
            "Executable doesn't exist at /missing/chromium",
            "Failed to launch chromium because executable doesn't exist at /missing/chromium",
        ],
    )
    def test_missing_custom_executable_fails_closed(
        self, mock_playwright, monkeypatch, message
    ):
        """Missing custom executable produces actionable error, no implicit download."""
        mock_pw, _ = mock_playwright
        mock_pw.chromium.launch.side_effect = RuntimeError(message)

        monkeypatch.setenv("GPTME_BROWSER_EXECUTABLE_PATH", "/missing/chromium")

        with pytest.raises(RuntimeError, match="Custom browser executable not found"):
            BrowserThread()

    def test_explicit_engine_ignores_legacy_engine_path(
        self, mock_playwright, monkeypatch
    ):
        """Explicit engine must not read legacy GPTME_BROWSER_ENGINE path."""
        mock_pw, mock_browser = mock_playwright

        # Legacy path that would normally parse as firefox
        monkeypatch.setenv("GPTME_BROWSER_ENGINE", "/path/to/custom/firefox")

        # Explicit engine should ignore the legacy path
        bt = BrowserThread(engine="chromium")
        try:
            assert bt.engine == "chromium"
            assert bt.executable_path is None
            mock_pw.chromium.launch.assert_called_once()
            call_kwargs = mock_pw.chromium.launch.call_args[1]
            assert "executable_path" not in call_kwargs
        finally:
            bt.stop()

    def test_explicit_engine_with_env_executable(self, mock_playwright, monkeypatch):
        """Explicit engine can use environment executable."""
        mock_pw, mock_browser = mock_playwright

        monkeypatch.setenv("GPTME_BROWSER_EXECUTABLE_PATH", "/custom/chromium")

        bt = BrowserThread(engine="chromium")
        try:
            assert bt.engine == "chromium"
            assert bt.executable_path == "/custom/chromium"
            call_kwargs = mock_pw.chromium.launch.call_args[1]
            assert call_kwargs.get("executable_path") == "/custom/chromium"
        finally:
            bt.stop()

    def test_independent_env_wins_over_legacy_engine_path(
        self, mock_playwright, monkeypatch
    ):
        """GPTME_BROWSER_EXECUTABLE_PATH wins over legacy GPTME_BROWSER_ENGINE path."""
        mock_pw, mock_browser = mock_playwright

        # Legacy path that would parse as firefox with /legacy/firefox
        monkeypatch.setenv("GPTME_BROWSER_ENGINE", "/legacy/firefox")
        # Independent setting should win
        monkeypatch.setenv("GPTME_BROWSER_EXECUTABLE_PATH", "/new/firefox")

        bt = BrowserThread()
        try:
            # Should use firefox engine (from legacy) but /new/firefox executable (from independent)
            assert bt.engine == "firefox"
            assert bt.executable_path == "/new/firefox"
            call_kwargs = mock_pw.firefox.launch.call_args[1]
            assert call_kwargs.get("executable_path") == "/new/firefox"
        finally:
            bt.stop()
