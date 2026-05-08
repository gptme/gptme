"""OpenRouter PKCE OAuth flow.

Implements the OpenRouter dynamic OAuth flow using PKCE so the user can
sign in via browser and receive a durable ``sk-or-...`` API key without
any client_id registration on our side.

Flow:
    1. Generate PKCE verifier + S256 challenge
    2. Open https://openrouter.ai/auth?... in browser
    3. Listen for the callback on http://127.0.0.1:3000/callback
    4. Exchange the returned code at https://openrouter.ai/api/v1/auth/keys
    5. Receive a durable ``sk-or-...`` key

Reference: https://openrouter.ai/docs/use-cases/oauth-pkce
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import socket
import threading
import time
import webbrowser
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

OPENROUTER_AUTH_URL = "https://openrouter.ai/auth"
OPENROUTER_TOKEN_URL = "https://openrouter.ai/api/v1/auth/keys"

# OpenRouter's documented callback port. Not configurable on the OpenRouter side.
OPENROUTER_CALLBACK_PORT = 3000
OPENROUTER_CALLBACK_PATH = "/callback"

DEFAULT_KEY_LABEL = "gptme"


class OAuthError(RuntimeError):
    """Raised when the OAuth flow fails."""


def generate_pkce() -> tuple[str, str]:
    """Generate a PKCE verifier and S256 code_challenge.

    Returns:
        (code_verifier, code_challenge) — both URL-safe base64 strings.
    """
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def is_port_available(port: int) -> bool:
    """Check whether ``port`` is free for binding on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the OpenRouter OAuth callback.

    State is shared via class variables — only one OAuth flow may run on
    OPENROUTER_CALLBACK_PORT at a time, which is enforced by the port check
    in :func:`authenticate`.
    """

    authorization_code: str | None = None
    error: str | None = None
    expected_state: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path != OPENROUTER_CALLBACK_PATH:
            self._respond_html(404, "Not found")
            return

        params = parse_qs(parsed.query)

        # CSRF: validate state parameter when one was issued
        if _CallbackHandler.expected_state is not None:
            received_state = params.get("state", [None])[0]
            if received_state != _CallbackHandler.expected_state:
                _CallbackHandler.error = (
                    "Invalid state parameter (possible CSRF attack)"
                )
                self._respond_html(400, "Security error: invalid state parameter")
                return

        if "code" in params:
            _CallbackHandler.authorization_code = params["code"][0]
            self._respond_html(
                200, "Authentication successful. You can close this window."
            )
        elif "error" in params:
            err_desc = params.get("error_description", params["error"])[0]
            _CallbackHandler.error = err_desc
            self._respond_html(400, f"Authentication failed: {err_desc}")
        else:
            self._respond_html(400, "No authorization code received")

    def _respond_html(self, status: int, message: str) -> None:
        html = (
            "<!DOCTYPE html><html><head>"
            '<meta charset="utf-8"><title>gptme — OpenRouter</title>'
            '</head><body style="font-family: system-ui; text-align: center; '
            'padding: 50px; color: #222; background: #fafafa;">'
            '<h1 style="font-size: 1.5em;">gptme</h1>'
            f'<p style="font-size: 1.2em;">{message}</p>'
            "<script>setTimeout(() => window.close(), 3000);</script>"
            "</body></html>"
        )
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


def authenticate(
    key_label: str = DEFAULT_KEY_LABEL,
    timeout: float = 300.0,
    open_browser: Callable[[str], bool] = webbrowser.open,
    callback_port: int = OPENROUTER_CALLBACK_PORT,
) -> str:
    """Run the OpenRouter PKCE OAuth flow and return a durable API key.

    Args:
        key_label: Label OpenRouter shows for the issued key in its dashboard.
        timeout: Seconds to wait for the callback before raising.
        open_browser: Hook for opening the auth URL (overridable for tests).
        callback_port: Localhost port for the redirect listener.

    Returns:
        The durable ``sk-or-...`` API key string.

    Raises:
        OAuthError: If the port is busy, the callback fails, or the token
            exchange does not return a usable key.
    """
    if not is_port_available(callback_port):
        raise OAuthError(
            f"Port {callback_port} is not available. OpenRouter requires "
            "this exact port for the OAuth callback. Close any process "
            "using it and try again."
        )

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    callback_url = f"http://127.0.0.1:{callback_port}{OPENROUTER_CALLBACK_PATH}"
    auth_params = {
        "callback_url": callback_url,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "key_label": key_label,
    }
    auth_url = f"{OPENROUTER_AUTH_URL}?{urlencode(auth_params)}"

    # Reset shared state for this flow
    _CallbackHandler.authorization_code = None
    _CallbackHandler.error = None
    _CallbackHandler.expected_state = state

    server = http.server.HTTPServer(("127.0.0.1", callback_port), _CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        print(f"Opening browser to {OPENROUTER_AUTH_URL} ...")
        try:
            open_browser(auth_url)
        except Exception as e:  # pragma: no cover — webbrowser is best-effort
            logger.warning(f"Could not auto-open browser: {e}")
        print(f"If the browser did not open, visit:\n  {auth_url}")
        print(f"Waiting for callback on {callback_url} ...")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if _CallbackHandler.authorization_code or _CallbackHandler.error:
                break
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()

    if _CallbackHandler.error:
        raise OAuthError(f"OAuth callback error: {_CallbackHandler.error}")
    if not _CallbackHandler.authorization_code:
        raise OAuthError(f"OAuth flow timed out after {timeout:.0f}s")

    code = _CallbackHandler.authorization_code
    return _exchange_code_for_key(code, code_verifier)


def _exchange_code_for_key(code: str, code_verifier: str) -> str:
    """Exchange a PKCE code for a durable OpenRouter API key.

    Returns the ``sk-or-...`` key string from the response.
    """
    response = requests.post(
        OPENROUTER_TOKEN_URL,
        json={
            "code": code,
            "code_verifier": code_verifier,
            "code_challenge_method": "S256",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise OAuthError(
            f"OpenRouter token exchange failed: HTTP {response.status_code} — {response.text}"
        )

    try:
        data = response.json()
    except ValueError as e:
        raise OAuthError(f"OpenRouter token response was not valid JSON: {e}") from e

    key = data.get("key")
    if not isinstance(key, str) or not key.startswith("sk-or-"):
        raise OAuthError(
            "OpenRouter token response did not include a usable 'key' field. "
            f"Response: {data!r}"
        )
    return key
