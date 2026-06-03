"""Tests for the browser thread module — threading, queue, retry, and error handling.

Tests cover:
- _is_connection_error: detection of browser connection failures
- Command dataclass: construction and fields
- BrowserThread: initialization, execute, stop, error handling
- Retry logic: connection error recovery with browser restart
- Timeout handling: execute deadline enforcement
- Thread lifecycle: start, ready event, cleanup
"""

import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("playwright")

from gptme.tools._browser_thread import (
    TIMEOUT,
    BrowserThread,
    Command,
    _connect_or_launch_browser,
    _is_connection_error,
)

# =============================================================================
# _is_connection_error tests
# =============================================================================


class TestIsConnectionError:
    """Test connection error detection."""

    @pytest.mark.parametrize(
        "msg",
        [
            "connection closed unexpectedly",
            "Browser has been closed",
            "Target closed while processing",
            "Connection terminated by remote",
            "pipe closed during operation",
            "WebSocket error: read ECONNRESET",
            "read ECONNRESET",
        ],
    )
    def test_detects_connection_errors(self, msg: str):
        assert _is_connection_error(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "CONNECTION CLOSED",  # case insensitive
            "BROWSER HAS BEEN CLOSED",
            "Pipe Closed",
        ],
    )
    def test_case_insensitive(self, msg: str):
        assert _is_connection_error(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "element not found",
            "timeout waiting for selector",
            "navigation failed",
            "click intercepted",
            "",
            "general error",
        ],
    )
    def test_rejects_non_connection_errors(self, msg: str):
        assert _is_connection_error(Exception(msg)) is False

    def test_handles_exception_subclasses(self):
        assert _is_connection_error(RuntimeError("connection closed")) is True
        assert _is_connection_error(OSError("pipe closed")) is True
        assert _is_connection_error(ValueError("connection closed")) is True


# =============================================================================
# Command dataclass tests
# =============================================================================


class TestCommand:
    """Test Command dataclass."""

    def test_construction(self):
        fn = lambda: None  # noqa: E731
        cmd = Command(func=fn, args=(1, 2), kwargs={"key": "val"})
        assert cmd.func is fn
        assert cmd.args == (1, 2)
        assert cmd.kwargs == {"key": "val"}

    def test_empty_args_kwargs(self):
        cmd = Command(func=print, args=(), kwargs={})
        assert cmd.args == ()
        assert cmd.kwargs == {}


# =============================================================================
# Browser launch tests
# =============================================================================


class TestConnectOrLaunchBrowser:
    def test_launches_bundled_chromium_by_default(self):
        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        result = _connect_or_launch_browser(mock_pw, None)

        assert result is mock_browser
        mock_pw.chromium.launch.assert_called_once_with()
        mock_pw.chromium.connect_over_cdp.assert_not_called()

    def test_connects_over_cdp_when_configured(self):
        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser

        result = _connect_or_launch_browser(mock_pw, "http://127.0.0.1:9222")

        assert result is mock_browser
        mock_pw.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9222"
        )
        mock_pw.chromium.launch.assert_not_called()


# =============================================================================
# BrowserThread tests (mocked playwright)
# =============================================================================


@pytest.fixture
def mock_playwright(monkeypatch):
    """Mock playwright to avoid needing a real browser."""
    # Remove real CDP URL from env so tests default to the launch path.
    # Tests that need CDP (e.g. test_connects_over_cdp_from_env) can
    # monkeypatch.setenv() to override.
    monkeypatch.delenv("GPTME_BROWSER_CDP_URL", raising=False)

    mock_pw_instance = MagicMock()
    mock_browser = MagicMock()
    mock_pw_instance.chromium.launch.return_value = mock_browser

    mock_pw_ctx = MagicMock()
    mock_pw_ctx.start.return_value = mock_pw_instance

    with (
        patch("gptme.tools._browser_thread.sync_playwright", return_value=mock_pw_ctx),
        patch("gptme.tools._browser_thread.get_config") as mock_config,
    ):
        # Make get_env read from os.environ with GPTME_ prefix (like the real impl)
        import os

        mock_config.return_value.get_env.side_effect = lambda key: os.environ.get(
            f"GPTME_{key}"
        )
        yield mock_pw_instance, mock_browser


class TestBrowserThreadInit:
    """Test BrowserThread initialization."""

    def test_starts_and_becomes_ready(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        try:
            assert bt.ready.is_set()
            assert bt.thread.is_alive()
            assert bt._init_error is None
        finally:
            bt.stop()

    def test_init_error_missing_executable(self, mock_playwright):
        mock_pw, _ = mock_playwright
        mock_pw.chromium.launch.side_effect = RuntimeError(
            "Executable doesn't exist at /path/to/chromium"
        )
        with pytest.raises(RuntimeError, match="Browser executable not found"):
            BrowserThread()

    def test_init_error_generic(self, mock_playwright):
        mock_pw, _ = mock_playwright
        mock_pw.chromium.launch.side_effect = OSError("cannot allocate memory")
        with pytest.raises(OSError, match="cannot allocate memory"):
            BrowserThread()

    def test_connects_over_cdp_when_configured(self, mock_playwright):
        mock_pw, _ = mock_playwright
        mock_cdp_browser = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_cdp_browser

        bt = BrowserThread(cdp_url="http://127.0.0.1:9222")
        try:
            mock_pw.chromium.connect_over_cdp.assert_called_once_with(
                "http://127.0.0.1:9222"
            )
            mock_pw.chromium.launch.assert_not_called()
        finally:
            bt.stop()

    def test_connects_over_cdp_from_env(self, mock_playwright, monkeypatch):
        mock_pw, _ = mock_playwright
        monkeypatch.setenv("GPTME_BROWSER_CDP_URL", "http://127.0.0.1:9223")

        bt = BrowserThread()
        try:
            mock_pw.chromium.connect_over_cdp.assert_called_once_with(
                "http://127.0.0.1:9223"
            )
            mock_pw.chromium.launch.assert_not_called()
        finally:
            bt.stop()

    def test_cdp_connection_error_propagates(self, mock_playwright):
        mock_pw, _ = mock_playwright
        mock_pw.chromium.connect_over_cdp.side_effect = OSError("cdp unavailable")

        with pytest.raises(OSError, match="cdp unavailable"):
            BrowserThread(cdp_url="http://127.0.0.1:9222")


class TestBrowserThreadExecute:
    """Test BrowserThread.execute method."""

    def test_execute_simple_function(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        try:
            result = bt.execute(lambda browser: "hello")
            assert result == "hello"
        finally:
            bt.stop()

    def test_execute_passes_browser_and_args(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        try:
            received = {}

            def capture(browser, x, y, key=None):
                received["browser"] = browser
                received["x"] = x
                received["y"] = y
                received["key"] = key
                return "done"

            result = bt.execute(capture, 1, 2, key="val")
            assert result == "done"
            assert received["browser"] is mock_browser
            assert received["x"] == 1
            assert received["y"] == 2
            assert received["key"] == "val"
        finally:
            bt.stop()

    def test_execute_propagates_non_connection_error(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        try:
            with pytest.raises(ValueError, match="bad input"):
                bt.execute(
                    lambda browser: (_ for _ in ()).throw(ValueError("bad input"))
                )
        finally:
            bt.stop()

    def test_execute_raises_if_thread_dead(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        bt.stop()
        bt.thread.join(timeout=5)
        with pytest.raises(RuntimeError, match="Browser thread died"):
            bt.execute(lambda browser: None)

    def test_execute_multiple_commands_sequentially(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        try:
            results = [bt.execute(lambda browser, n=i: n * 2) for i in range(5)]
            assert results == [0, 2, 4, 6, 8]
        finally:
            bt.stop()


class TestBrowserThreadRetry:
    """Test connection error retry logic."""

    def test_retries_on_connection_error(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        call_count = 0

        def flaky_func(browser):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("connection closed")
            return "recovered"

        bt = BrowserThread()
        try:
            result = bt.execute(flaky_func)
            assert result == "recovered"
            assert call_count == 2
            # Browser should have been relaunched
            assert mock_pw.chromium.launch.call_count >= 2
        finally:
            bt.stop()

    def test_gives_up_after_max_retries(self, mock_playwright):
        """When command keeps failing with connection errors after successful restarts,
        the last error is propagated (not a restart error)."""
        mock_pw, mock_browser = mock_playwright

        def always_fail(browser):
            raise RuntimeError("connection closed permanently")

        bt = BrowserThread()
        try:
            with pytest.raises(RuntimeError, match="connection closed permanently"):
                bt.execute(always_fail)
        finally:
            bt.stop()

    def test_restart_failure_reports_error(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright

        def fail_func(browser):
            raise RuntimeError("connection closed")

        # First launch succeeds, restart fails
        launch_count = 0

        def launch_side_effect():
            nonlocal launch_count
            launch_count += 1
            if launch_count > 1:
                raise RuntimeError("cannot restart")
            return mock_browser

        mock_pw.chromium.launch.side_effect = launch_side_effect

        bt = BrowserThread()
        try:
            assert bt._init_error is None
            with pytest.raises(RuntimeError, match="Browser restart failed"):
                bt.execute(fail_func)
            assert bt._init_error is None
        finally:
            bt.stop()

    def test_non_connection_error_not_retried(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        call_count = 0

        def fail_once(browser):
            nonlocal call_count
            call_count += 1
            raise TypeError("not a connection error")

        bt = BrowserThread()
        try:
            with pytest.raises(TypeError, match="not a connection error"):
                bt.execute(fail_once)
            # Should NOT retry for non-connection errors
            assert call_count == 1
        finally:
            bt.stop()


class TestBrowserThreadStop:
    """Test BrowserThread.stop method."""

    def test_stop_cleans_up(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        bt.stop()
        bt.thread.join(timeout=5)
        assert not bt.thread.is_alive()
        mock_browser.close.assert_called_once()
        mock_pw.stop.assert_called_once()

    def test_stop_idempotent(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        bt.stop()
        bt.thread.join(timeout=5)
        # Second stop should not raise
        bt.stop()

    def test_cleanup_handles_browser_close_error(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        mock_browser.close.side_effect = RuntimeError("connection closed")
        bt = BrowserThread()
        # Should not raise despite browser close error
        bt.stop()
        bt.thread.join(timeout=5)
        assert not bt.thread.is_alive()


class TestBrowserThreadTimeout:
    """Test timeout handling in execute."""

    def test_execute_timeout(self, mock_playwright):
        mock_pw, mock_browser = mock_playwright
        bt = BrowserThread()
        try:
            # Patch TIMEOUT to a short value for testing; slow_func sleeps just
            # long enough to exceed it but not so long that cleanup blocks (5 s
            # previously).
            with patch("gptme.tools._browser_thread.TIMEOUT", 0.1):

                def slow_func(browser):
                    time.sleep(0.2)
                    return "too late"

                with pytest.raises(TimeoutError, match="timed out"):
                    bt.execute(slow_func)
        finally:
            bt.stop()


class TestBrowserThreadConstants:
    """Test module-level constants."""

    def test_timeout_is_positive(self):
        assert TIMEOUT > 0

    def test_timeout_is_reasonable(self):
        # Should be between 5 and 120 seconds
        assert 5 <= TIMEOUT <= 120
