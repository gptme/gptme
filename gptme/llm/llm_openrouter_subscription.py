"""OpenRouter OAuth helper.

Authenticates via OpenRouter's PKCE OAuth flow and returns a persistent API key
that is stored in the gptme config.  Unlike the ChatGPT / Grok subscription
providers, OpenRouter OAuth produces a **permanent API key** (not an expiring
access token), so no refresh machinery is needed.

Flow
----
1. Generate a PKCE code-verifier/challenge pair.
2. Open ``https://openrouter.ai/auth?callback_url=…&code_challenge=…`` in the
   browser.
3. Start a local HTTP server on ``OAUTH_CALLBACK_PORT`` to receive the redirect.
4. Exchange the returned ``code`` for the API key via a POST to the OpenRouter
   key-exchange endpoint.
5. Return the ``sk-or-v1-…`` key string; callers persist it to the config.

Usage
-----
From setup.py ``_setup_openrouter_oauth()`` or directly::

    from gptme.llm.llm_openrouter_subscription import oauth_get_api_key
    api_key = oauth_get_api_key()

Or via the auth CLI::

    gptme auth openrouter
"""

import base64
import hashlib
import http.server
import logging
import secrets
import socket
import threading
from urllib.parse import parse_qs, urlencode, urlparse

import requests

logger = logging.getLogger(__name__)

# OpenRouter OAuth endpoints
OAUTH_AUTH_URL = "https://openrouter.ai/auth"
OAUTH_KEY_EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"
OAUTH_CALLBACK_PORT = 1458  # 1455=openai-sub, 1456=grok-sub, 1457=reserved
OAUTH_CALLBACK_PATH = "/auth/callback"


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE-S256."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def oauth_get_api_key() -> str:
    """Run the OpenRouter PKCE OAuth flow and return the API key.

    Opens the user's browser, waits for the local OAuth callback, then
    exchanges the authorization code for a permanent ``sk-or-v1-…`` key.

    Raises
    ------
    RuntimeError
        If the browser callback does not arrive within the timeout, the code
        exchange fails, or the server cannot bind the callback port.
    """
    import webbrowser

    code_verifier, code_challenge = _generate_pkce()
    callback_url = f"http://localhost:{OAUTH_CALLBACK_PORT}{OAUTH_CALLBACK_PATH}"

    params = {
        "callback_url": callback_url,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{OAUTH_AUTH_URL}?{urlencode(params)}"

    # Shared state between the HTTP handler and this function
    _result: dict[str, object] = {}
    _done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # silence access log

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != OAUTH_CALLBACK_PATH:
                self._respond(404, "Not found")
                return

            qs = parse_qs(parsed.query)
            code_list = qs.get("code", [])
            if not code_list:
                _result["error"] = Exception("No 'code' in OAuth callback URL")
            else:
                _result["code"] = code_list[0]

            self._respond(
                200,
                "<html><body><h2>Authentication complete — you can close this tab."
                "</h2></body></html>",
                content_type="text/html",
            )
            _done.set()

        def _respond(self, status: int, body: str, content_type: str = "text/plain"):
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    try:
        server = http.server.HTTPServer(("127.0.0.1", OAUTH_CALLBACK_PORT), _Handler)
    except OSError as exc:
        raise RuntimeError(
            f"Could not start OAuth callback server on port {OAUTH_CALLBACK_PORT}: {exc}"
        ) from exc

    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    webbrowser.open(auth_url)

    # Wait up to 5 minutes for the user to complete the browser flow
    if not _done.wait(timeout=300):
        server.server_close()
        raise RuntimeError(
            "OpenRouter OAuth timed out (no browser callback after 5 minutes)."
        )
    server.server_close()

    if "error" in _result:
        raise RuntimeError(f"OpenRouter OAuth error: {_result['error']}")

    auth_code = _result.get("code")
    if not isinstance(auth_code, str):
        raise RuntimeError("OpenRouter OAuth callback did not return a code.")

    # Exchange the authorization code for a permanent API key
    try:
        resp = requests.post(
            OAUTH_KEY_EXCHANGE_URL,
            json={"code": auth_code, "code_verifier": code_verifier},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"OpenRouter key exchange failed: {exc}") from exc

    api_key: str = data.get("key", "")
    if not api_key:
        raise RuntimeError(f"OpenRouter key exchange returned no 'key' field: {data!r}")

    return api_key


def _check_port_available(port: int) -> bool:
    """Return True if the given TCP port is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0
