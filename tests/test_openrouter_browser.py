"""Browser selection for the OpenRouter API-key OAuth flow."""

import socket
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from gptme.llm import llm_openrouter_subscription as oauth


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_oauth_browser_selection(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """macOS uses Launch Services, not webbrowser's terminal-browser fallback."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(oauth, "OAUTH_CALLBACK_PORT", port)

    def open_url(url: str) -> bool:
        params = parse_qs(urlparse(url).query)
        response = requests.get(
            params["callback_url"][0], params={"code": "test-code"}, timeout=2
        )
        response.raise_for_status()
        return True

    native_open = Mock(side_effect=lambda url: 0 if open_url(url) else 1)
    browser_open = Mock(side_effect=open_url)
    monkeypatch.setattr("click.launch", native_open)
    monkeypatch.setattr("webbrowser.open", browser_open)
    monkeypatch.setattr("sys.platform", platform)
    # A terminal-browser preference must not hijack macOS OAuth.
    monkeypatch.setenv("BROWSER", "lynx")
    monkeypatch.delenv("DISPLAY", raising=False)
    response = Mock()
    response.json.return_value = {"key": "sk-or-test-browser"}
    post = Mock(return_value=response)
    monkeypatch.setattr(oauth.requests, "post", post)
    urls: list[str] = []

    assert (
        oauth.oauth_get_api_key(timeout=2, on_url_ready=urls.append)
        == "sk-or-test-browser"
    )
    chosen, unused = (
        (native_open, browser_open)
        if platform == "darwin"
        else (browser_open, native_open)
    )
    chosen.assert_called_once_with(urls[0])
    unused.assert_not_called()
    assert post.call_args.kwargs["json"]["code"] == "test-code"


def test_oauth_can_skip_macos_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server/CLI manual-URL callback must still suppress browser launch."""
    monkeypatch.setattr(oauth, "OAUTH_CALLBACK_PORT", 0)
    native_open = Mock()
    browser_open = Mock()
    monkeypatch.setattr("click.launch", native_open)
    monkeypatch.setattr("webbrowser.open", browser_open)
    monkeypatch.setattr("sys.platform", "darwin")
    with pytest.raises(RuntimeError, match="timed out"):
        oauth.oauth_get_api_key(timeout=0, on_url_ready=lambda _url: False)
    native_open.assert_not_called()
    browser_open.assert_not_called()


@pytest.mark.parametrize("failure", [OSError("open unavailable"), 1])
def test_macos_launch_failure_keeps_manual_callback_alive(
    monkeypatch: pytest.MonkeyPatch, failure: OSError | int
) -> None:
    monkeypatch.setattr(oauth, "OAUTH_CALLBACK_PORT", 0)
    # Use the port actually bound by HTTPServer, since port 0 picks a free one.
    server_type = oauth.http.server.HTTPServer
    servers = []

    def make_server(*args, **kwargs):
        server = server_type(*args, **kwargs)
        servers.append(server)
        return server

    monkeypatch.setattr(oauth.http.server, "HTTPServer", make_server)
    monkeypatch.setattr("sys.platform", "darwin")
    native_open = Mock(
        side_effect=failure if isinstance(failure, OSError) else None,
        return_value=failure,
    )
    monkeypatch.setattr("click.launch", native_open)
    response = Mock()
    response.json.return_value = {"key": "sk-or-manual-callback"}
    monkeypatch.setattr(oauth.requests, "post", Mock(return_value=response))

    def on_url_ready(_url: str) -> None:
        # A callback is available even if the automatic launch subsequently fails.
        requests.get(
            f"http://127.0.0.1:{servers[0].server_port}{oauth.OAUTH_CALLBACK_PATH}",
            params={"code": "manual-code"},
            timeout=2,
        ).raise_for_status()

    assert (
        oauth.oauth_get_api_key(timeout=2, on_url_ready=on_url_ready)
        == "sk-or-manual-callback"
    )
    native_open.assert_called_once()
