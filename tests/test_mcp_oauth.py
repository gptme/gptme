"""Tests for the MCP OAuth helpers (token storage, loopback redirect, opt-out)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from pydantic import AnyUrl

if TYPE_CHECKING:
    from pathlib import Path

from gptme.mcp.oauth import (
    FileTokenStorage,
    LoopbackReceiver,
    OAuthLoginUnavailable,
    build_oauth_provider,
    close_oauth_provider,
    has_authorization_header,
    oauth_enabled,
    token_storage_path,
)

URL = "https://mcp.example.com/mcp"


def _token(access: str = "access-1"):
    from mcp.shared.auth import OAuthToken

    return OAuthToken(
        access_token=access, token_type="Bearer", refresh_token="refresh-1"
    )


def _client_info():
    from mcp.shared.auth import OAuthClientInformationFull

    return OAuthClientInformationFull(
        client_id="client-1",
        redirect_uris=[AnyUrl("http://127.0.0.1:1234/callback")],
    )


def test_has_authorization_header_is_case_insensitive():
    assert has_authorization_header({"Authorization": "Bearer x"})
    assert has_authorization_header({"authorization": "Bearer x"})
    assert not has_authorization_header({"X-Api-Key": "x"})
    assert not has_authorization_header(None)
    assert not has_authorization_header({})


def test_oauth_enabled_defaults_on_and_honours_opt_out():
    assert oauth_enabled({})
    assert not oauth_enabled({"GPTME_MCP_OAUTH": "0"})
    assert not oauth_enabled({"GPTME_MCP_OAUTH": "off"})
    assert oauth_enabled({"GPTME_MCP_OAUTH": "1"})


def test_token_storage_path_separates_servers_and_urls(tmp_path: Path):
    same_url = token_storage_path("alpha", URL, config_dir=tmp_path)
    other_server = token_storage_path("beta", URL, config_dir=tmp_path)
    other_url = token_storage_path("alpha", URL + "/v2", config_dir=tmp_path)
    assert same_url != other_server
    # A server moved to another resource must not reuse the old token.
    assert same_url != other_url
    assert same_url.parent == tmp_path / "mcp-oauth"


def test_file_storage_roundtrips_tokens_and_client_info(tmp_path: Path):
    storage = FileTokenStorage(tmp_path / "server.json")

    assert asyncio.run(storage.get_tokens()) is None
    assert asyncio.run(storage.get_client_info()) is None

    asyncio.run(storage.set_tokens(_token()))
    asyncio.run(storage.set_client_info(_client_info()))

    tokens = asyncio.run(storage.get_tokens())
    info = asyncio.run(storage.get_client_info())
    assert tokens is not None and tokens.access_token == "access-1"
    assert tokens.refresh_token == "refresh-1"
    assert info is not None and info.client_id == "client-1"

    # Writing one side must not drop the other.
    asyncio.run(storage.set_tokens(_token("access-2")))
    assert asyncio.run(storage.get_client_info()) is not None
    on_disk = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
    assert set(on_disk) == {"tokens", "client_info"}


def test_file_storage_treats_corrupt_file_as_no_credentials(tmp_path: Path):
    path = tmp_path / "server.json"
    path.write_text("{not json", encoding="utf-8")
    storage = FileTokenStorage(path)

    assert asyncio.run(storage.get_tokens()) is None

    # A broken file must not stop a fresh authorization from being stored.
    asyncio.run(storage.set_tokens(_token("new")))
    tokens = asyncio.run(storage.get_tokens())
    assert tokens is not None and tokens.access_token == "new"


def test_file_storage_treats_undecodable_file_as_no_credentials(tmp_path: Path):
    path = tmp_path / "server.json"
    # A partially overwritten file is not valid UTF-8; that must not raise.
    path.write_bytes(b'\xff\xfe{"tokens"')
    storage = FileTokenStorage(path)

    assert asyncio.run(storage.get_tokens()) is None
    asyncio.run(storage.set_tokens(_token("fresh")))
    tokens = asyncio.run(storage.get_tokens())
    assert tokens is not None and tokens.access_token == "fresh"


def test_oauth_disabled_returns_no_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GPTME_MCP_OAUTH", "0")
    assert build_oauth_provider("demo", URL) is None


def test_provider_redirects_to_a_loopback_listener(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GPTME_MCP_OAUTH", raising=False)
    handler = build_oauth_provider("demo", URL, can_prompt=False)
    assert handler is not None
    try:
        redirect_uris = handler.provider.context.client_metadata.redirect_uris or []
        assert len(redirect_uris) == 1
        redirect = str(redirect_uris[0])
        assert redirect.startswith("http://127.0.0.1:")
        assert redirect.endswith("/callback")
        assert (
            handler.provider.context.client_metadata.token_endpoint_auth_method
            == "none"
        )
    finally:
        close_oauth_provider(handler)


def test_redirect_handler_refuses_when_it_cannot_open_a_browser(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("GPTME_MCP_OAUTH", raising=False)
    oauth = build_oauth_provider("demo", URL, can_prompt=False)
    assert oauth is not None
    redirect = oauth.provider.context.redirect_handler
    assert redirect is not None

    async def authorize() -> None:
        await redirect("https://auth.example.com/authorize?x=1")

    with pytest.raises(OAuthLoginUnavailable) as excinfo:
        asyncio.run(authorize())
    message = str(excinfo.value)
    assert "cannot open a browser" in message
    # The message has to say what to do instead of just failing.
    assert "[mcp.servers.headers]" in message
    # The transport reports this as a cancellation, so the handler has to keep
    # the reason for the caller to report it instead of a generic 401.
    assert isinstance(oauth.last_error, OAuthLoginUnavailable)
    close_oauth_provider(oauth)


def _get(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def test_loopback_receiver_returns_code_and_state():
    receiver = LoopbackReceiver()
    try:
        target = f"{receiver.redirect_uri}?code=abc123&state=nonce"
        thread = threading.Thread(target=_get, args=(target,), daemon=True)
        thread.start()
        code, state = asyncio.run(receiver.wait(5))
        thread.join(timeout=5)
        assert (code, state) == ("abc123", "nonce")
    finally:
        receiver.close()


def test_loopback_receiver_serves_a_second_flow_on_the_same_port():
    """A refresh failure restarts the flow; the registered port must still work."""
    receiver = LoopbackReceiver()
    try:
        for code in ("first-code", "second-code"):
            receiver.reset()
            target = f"{receiver.redirect_uri}?code={code}&state=s"
            thread = threading.Thread(target=_get, args=(target,), daemon=True)
            thread.start()
            assert asyncio.run(receiver.wait(5)) == (code, "s")
            thread.join(timeout=5)
    finally:
        receiver.close()


def test_loopback_receiver_surfaces_a_denied_authorization():
    receiver = LoopbackReceiver()
    try:
        target = f"{receiver.redirect_uri}?error=access_denied&error_description=nope"
        thread = threading.Thread(target=_get, args=(target,), daemon=True)
        thread.start()
        with pytest.raises(OAuthLoginUnavailable) as excinfo:
            asyncio.run(receiver.wait(5))
        thread.join(timeout=5)
        assert "nope" in str(excinfo.value)
    finally:
        receiver.close()


def test_loopback_receiver_times_out_instead_of_hanging():
    receiver = LoopbackReceiver()
    try:
        with pytest.raises(OAuthLoginUnavailable) as excinfo:
            asyncio.run(receiver.wait(0.1))
        assert "timed out" in str(excinfo.value)
    finally:
        receiver.close()


def test_closing_the_receiver_releases_a_pending_wait():
    receiver = LoopbackReceiver()

    async def scenario() -> str:
        task = asyncio.create_task(receiver.wait(30))
        await asyncio.sleep(0.2)  # let the worker thread start blocking
        receiver.close()
        with pytest.raises(OAuthLoginUnavailable) as excinfo:
            # If close() fails to wake the waiter this trips the guard instead
            # of hanging the test for the full OAuth timeout.
            await asyncio.wait_for(task, timeout=5)
        return str(excinfo.value)

    try:
        message = asyncio.run(scenario())
    finally:
        receiver.close()
    assert "closed" in message


def test_idle_connection_does_not_block_the_callback():
    """A browser may open the socket before sending the request."""
    receiver = LoopbackReceiver()
    idle = socket.create_connection(
        (urlsplit(receiver.redirect_uri).hostname, urlsplit(receiver.redirect_uri).port)
    )
    try:
        thread = threading.Thread(
            target=_get,
            args=(f"{receiver.redirect_uri}?code=ok&state=s",),
            daemon=True,
        )
        thread.start()
        assert asyncio.run(receiver.wait(10)) == ("ok", "s")
        thread.join(timeout=5)
    finally:
        idle.close()
        receiver.close()


def _client_with_server(server, monkeypatch: pytest.MonkeyPatch):
    import gptme.mcp.client as client_module
    from gptme.config import Config
    from gptme.config.models import MCPConfig, UserConfig

    monkeypatch.delenv("GPTME_MCP_OAUTH", raising=False)
    config = Config(user=UserConfig(mcp=MCPConfig(enabled=True, servers=[server])))
    return client_module.MCPClient(config)


def _capture_auth(client, monkeypatch: pytest.MonkeyPatch) -> dict:
    import mcp.types as types

    captured: dict = {}

    async def fake_setup(url, headers, use_sse=False, auth=None):
        captured["auth"] = auth
        return types.ListToolsResult(tools=[]), None

    monkeypatch.setattr(client, "_setup_http_connection", fake_setup)
    return captured


def test_connect_skips_oauth_when_a_static_authorization_header_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    from gptme.config.models import MCPServerConfig

    server = MCPServerConfig(
        name="with-header", url=URL, headers={"Authorization": "Bearer static"}
    )
    client = _client_with_server(server, monkeypatch)
    captured = _capture_auth(client, monkeypatch)
    try:
        client.connect("with-header")
        # Static headers win: no browser round-trip may start on a 401.
        assert captured["auth"] is None
        assert client._oauth_provider is None
    finally:
        client.close()


def test_connect_enables_oauth_when_no_authorization_header_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    from gptme.config.models import MCPServerConfig

    server = MCPServerConfig(name="no-header", url=URL)
    client = _client_with_server(server, monkeypatch)
    captured = _capture_auth(client, monkeypatch)
    try:
        client.connect("no-header")
        assert captured["auth"] is not None
        assert client._oauth_provider is not None
        assert client._oauth_provider.provider is captured["auth"]
    finally:
        client.close()
    # close() releases the loopback listener.
    assert client._oauth_provider is None


def test_oauth_flow_runs_discovery_and_reports_headless_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Drive a real 401 through the provider: discovery, registration, no hang."""
    import http.server

    import gptme.mcp.oauth as oauth_module
    from gptme.config.models import MCPServerConfig

    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "GPTME_MCP_OAUTH",
    ):
        monkeypatch.delenv(key, raising=False)
    # Deterministic headless: no tty and no browser to open.
    monkeypatch.setattr(oauth_module, "_can_prompt_for_browser", lambda: False)
    # Keep the registered client info out of the developer's real config dir.
    monkeypatch.setattr(oauth_module, "get_config_dir", lambda: tmp_path)

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        issuer = ""

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/.well-known/oauth-protected-resource":
                self._json(
                    200,
                    {
                        "resource": self.issuer + "/mcp",
                        "authorization_servers": [self.issuer],
                    },
                )
            elif self.path == "/.well-known/oauth-authorization-server":
                self._json(
                    200,
                    {
                        "issuer": self.issuer,
                        "authorization_endpoint": self.issuer + "/authorize",
                        "token_endpoint": self.issuer + "/token",
                        "registration_endpoint": self.issuer + "/register",
                        "response_types_supported": ["code"],
                        "grant_types_supported": [
                            "authorization_code",
                            "refresh_token",
                        ],
                        "code_challenge_methods_supported": ["S256"],
                    },
                )
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.path == "/register":
                payload = json.loads(body or b"{}")
                self._json(
                    201,
                    {
                        **payload,
                        "client_id": "test-client",
                        "client_id_issued_at": 1700000000,
                    },
                )
            else:
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate",
                    'Bearer realm="mcp", resource_metadata='
                    f'"{self.issuer}/.well-known/oauth-protected-resource"',
                )
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    issuer = f"http://127.0.0.1:{httpd.server_address[1]}"
    _Handler.issuer = issuer
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    server = MCPServerConfig(name="oauth-demo", url=f"{issuer}/mcp")
    client = _client_with_server(server, monkeypatch)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            client.connect("oauth-demo")
        message = str(excinfo.value)
        # The whole point: the 401 turns into an actionable sentence, not a
        # cancellation or a bare status code.
        assert "cannot open a browser" in message
        assert "cancelled while connecting" not in message
        # The listener is bound to a port; a failed handshake must release it
        # instead of holding it for the lifetime of the process.
        assert client._oauth_provider is None
    finally:
        client.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_initialize_budget_allows_a_browser_round_trip():
    import httpx

    from gptme.mcp.client import MCP_INITIALIZE_TIMEOUT_S, _initialize_timeout

    assert _initialize_timeout(None) == MCP_INITIALIZE_TIMEOUT_S
    # Browser consent happens inside initialize(), so the handshake cannot use
    # the plain 5 s budget once OAuth is in play.
    assert _initialize_timeout(httpx.Auth()) > 60


def test_auth_failure_says_the_oauth_flow_ran(monkeypatch: pytest.MonkeyPatch):
    import http.server

    from gptme.mcp.client import _diagnose_http_auth_failure

    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)

    class _Handler(http.server.BaseHTTPRequestHandler):
        # Drain the probe body and answer keep-alive style: leaving it unread
        # stalls the next probe on the same connection.
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                'Bearer realm="mcp", resource_metadata='
                '"http://127.0.0.1/.well-known/oauth-protected-resource"',
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
        plain = _diagnose_http_auth_failure(url, {})
        oauth = _diagnose_http_auth_failure(url, {}, True)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert plain is not None and "did not run the MCP OAuth" in plain
    assert oauth is not None and "ran the MCP OAuth authorization-code flow" in oauth
    assert "OAuth protected-resource metadata" in oauth
