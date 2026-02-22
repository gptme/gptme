"""OpenAI Subscription Provider.

Enables use of ChatGPT Plus/Pro subscriptions with gptme through the
ChatGPT backend API (not the Platform API). Uses OAuth 2.0 + PKCE for
authentication.

Uses the OpenAI **Responses API** format (not Chat Completions):
- ``input`` instead of ``messages`` for conversation items
- ``instructions`` for system-level guidance (separated from input)
- SSE events like ``response.output_text.delta`` and ``response.done``
See: https://platform.openai.com/docs/api-reference/responses

Based on opencode-openai-codex-auth plugin OAuth implementation.

NOTICE: For personal development use with your own ChatGPT Plus/Pro subscription.
For production or multi-user applications, use the OpenAI Platform API.

Usage:
    1. First run: `gptme auth openai-subscription` to authenticate
    2. Use model like: openai-subscription/gpt-5.2

Endpoint: https://chatgpt.com/backend-api/codex/responses

TODO: The regular OpenAI provider (llm_openai.py) could also be migrated to the
Responses API, which would allow unifying both providers. The Responses API is now
the recommended path forward for OpenAI and supports the same models. A unified
provider could share auth, request building, and response parsing, with the only
difference being the auth method (API key vs OAuth) and base URL.
"""

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import socket
import threading
import time
import webbrowser
from base64 import urlsafe_b64decode
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import requests

from ..message import Message
from ..tools.base import ToolSpec
from .utils import extract_tool_uses_from_assistant_message, parameters2dict

logger = logging.getLogger(__name__)

# OAuth Configuration (from opencode)
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CALLBACK_PORT = 1455
OAUTH_CALLBACK_PATH = "/auth/callback"
OAUTH_SCOPES = "openid profile email offline_access"

# ChatGPT backend API base URL
CHATGPT_BASE_URL = "https://chatgpt.com"
CODEX_ENDPOINT = f"{CHATGPT_BASE_URL}/backend-api/codex/responses"


def _get_token_storage_path() -> Path:
    """Get path to store OAuth tokens."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    token_dir = config_dir / "gptme" / "oauth"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / "openai_subscription.json"


@dataclass
class SubscriptionAuth:
    """Authentication state for OpenAI subscription."""

    access_token: str
    refresh_token: str | None
    account_id: str
    expires_at: float


# Global auth state
_auth: SubscriptionAuth | None = None


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge."""
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")

    payload = parts[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding

    decoded = urlsafe_b64decode(payload)
    return json.loads(decoded)


def _extract_account_id(access_token: str) -> str:
    """Extract chatgpt-account-id from JWT claims."""
    payload = _decode_jwt_payload(access_token)

    for key, value in payload.items():
        if "openai" in key.lower() and isinstance(value, dict):
            if "organization_id" in value:
                return value["organization_id"]

    if "sub" in payload:
        return payload["sub"]

    raise ValueError("Could not extract account ID from JWT")


def _get_token_expiry(access_token: str) -> float:
    """Extract expiry timestamp from JWT."""
    try:
        payload = _decode_jwt_payload(access_token)
        if "exp" in payload:
            return float(payload["exp"])
    except Exception as e:
        logger.warning(f"Failed to extract token expiry: {e}")
    return time.time() + 3600


def _save_tokens(auth: SubscriptionAuth) -> None:
    """Save tokens to disk."""
    token_path = _get_token_storage_path()
    data = {
        "access_token": auth.access_token,
        "refresh_token": auth.refresh_token,
        "account_id": auth.account_id,
        "expires_at": auth.expires_at,
    }
    token_path.write_text(json.dumps(data, indent=2))
    token_path.chmod(0o600)
    logger.debug(f"Saved tokens to {token_path}")


def _load_tokens() -> SubscriptionAuth | None:
    """Load tokens from disk."""
    token_path = _get_token_storage_path()
    if not token_path.exists():
        return None

    try:
        data = json.loads(token_path.read_text())
        return SubscriptionAuth(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            account_id=data["account_id"],
            expires_at=data["expires_at"],
        )
    except Exception as e:
        logger.warning(f"Failed to load tokens: {e}")
        return None


def _is_port_available(port: int) -> bool:
    """Check if a port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    # Thread-safe state using class variables (single OAuth flow at a time)
    # Protected by port check - only one flow can run on OAUTH_CALLBACK_PORT
    authorization_code: str | None = None
    error: str | None = None
    expected_state: str | None = None  # Set before starting server

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Validate state parameter to prevent CSRF attacks
        received_state = params.get("state", [None])[0]
        if received_state != _OAuthCallbackHandler.expected_state:
            _OAuthCallbackHandler.error = (
                "Invalid state parameter (possible CSRF attack)"
            )
            self._send_error_response("Security error: Invalid state parameter")
            return

        if "code" in params:
            _OAuthCallbackHandler.authorization_code = params["code"][0]
            self._send_success_response()
        elif "error" in params:
            err = params.get("error_description", params["error"])[0]
            _OAuthCallbackHandler.error = err
            self._send_error_response(err)
        else:
            self._send_error_response("No authorization code received")

    def _send_success_response(self) -> None:
        html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>gptme - Authentication Successful</title>
</head>
<body style="font-family: system-ui; text-align: center; padding: 50px; color: #222; background: #fafafa;">
<h1 style="font-size: 1.5em;">gptme</h1>
<p style="font-size: 1.2em;">Authentication successful. You can close this window and return to your terminal.</p>
<script>setTimeout(() => window.close(), 3000);</script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _send_error_response(self, error: str) -> None:
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>gptme - Authentication Failed</title>
</head>
<body style="font-family: system-ui; text-align: center; padding: 50px; color: #222; background: #fafafa;">
<h1 style="font-size: 1.5em;">gptme</h1>
<p style="font-size: 1.2em; color: #c00;">Authentication failed: {error}</p>
<p>Please close this window and try again.</p>
</body>
</html>"""
        self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


def oauth_authenticate() -> SubscriptionAuth:
    """Perform OAuth authentication flow.

    Opens browser for user to log in, handles callback, and exchanges
    authorization code for tokens.
    """
    if not _is_port_available(OAUTH_CALLBACK_PORT):
        raise ValueError(
            f"Port {OAUTH_CALLBACK_PORT} is not available. "
            "Please close any other gptme instances and try again."
        )

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)

    auth_params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": f"http://localhost:{OAUTH_CALLBACK_PORT}{OAUTH_CALLBACK_PATH}",
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
    }
    auth_url = f"{OAUTH_AUTH_URL}?{urlencode(auth_params)}"

    # Reset handler state (protected by port check - only one flow at a time)
    _OAuthCallbackHandler.authorization_code = None
    _OAuthCallbackHandler.error = None
    _OAuthCallbackHandler.expected_state = state  # For CSRF validation

    server = http.server.HTTPServer(
        ("127.0.0.1", OAUTH_CALLBACK_PORT),
        _OAuthCallbackHandler,
    )
    server.timeout = 120  # 2 minutes should be sufficient for browser auth

    print("\n🔐 Opening browser for OpenAI authentication...")
    print(f"   If browser doesn't open, visit:\n   {auth_url}")

    def open_browser() -> None:
        time.sleep(0.5)
        webbrowser.open(auth_url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"   Waiting for authentication callback on port {OAUTH_CALLBACK_PORT}...")
    try:
        while (
            _OAuthCallbackHandler.authorization_code is None
            and _OAuthCallbackHandler.error is None
        ):
            server.handle_request()
    finally:
        server.server_close()

    if _OAuthCallbackHandler.error:
        raise ValueError(f"OAuth error: {_OAuthCallbackHandler.error}")

    if not _OAuthCallbackHandler.authorization_code:
        raise ValueError("No authorization code received")

    print("   Exchanging authorization code for tokens...")
    token_response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": _OAuthCallbackHandler.authorization_code,
            "redirect_uri": f"http://localhost:{OAUTH_CALLBACK_PORT}{OAUTH_CALLBACK_PATH}",
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    if token_response.status_code != 200:
        raise ValueError(
            f"Token exchange failed: {token_response.status_code} - {token_response.text}"
        )

    tokens = token_response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        raise ValueError("No access token in response")

    account_id = _extract_account_id(access_token)
    expires_at = _get_token_expiry(access_token)

    auth = SubscriptionAuth(
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=account_id,
        expires_at=expires_at,
    )

    _save_tokens(auth)

    print("✅ Authentication successful!")
    return auth


def _refresh_access_token(refresh_token: str) -> SubscriptionAuth:
    """Refresh access token using refresh token."""
    response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    if response.status_code != 200:
        raise ValueError(f"Token refresh failed: {response.status_code}")

    tokens = response.json()
    access_token = tokens.get("access_token")
    new_refresh_token = tokens.get("refresh_token", refresh_token)

    if not access_token:
        raise ValueError("No access token in refresh response")

    account_id = _extract_account_id(access_token)
    expires_at = _get_token_expiry(access_token)

    auth = SubscriptionAuth(
        access_token=access_token,
        refresh_token=new_refresh_token,
        account_id=account_id,
        expires_at=expires_at,
    )

    _save_tokens(auth)
    logger.info("Access token refreshed successfully")
    return auth


def get_auth() -> SubscriptionAuth:
    """Get current auth, refreshing or prompting login if needed."""
    global _auth

    if _auth is not None:
        if time.time() < _auth.expires_at - 300:
            return _auth

        if _auth.refresh_token:
            try:
                _auth = _refresh_access_token(_auth.refresh_token)
                return _auth
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")

    stored_auth = _load_tokens()
    if stored_auth is not None:
        if time.time() < stored_auth.expires_at - 300:
            _auth = stored_auth
            return _auth

        if stored_auth.refresh_token:
            try:
                _auth = _refresh_access_token(stored_auth.refresh_token)
                return _auth
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")

    raise ValueError(
        "OpenAI subscription not authenticated.\n"
        "Please run: gptme auth openai-subscription"
    )


def _messages_to_responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert gptme Messages to Responses API input items.

    Handles three cases:
    - System messages with call_id → ``function_call_output`` items
    - Assistant messages containing tool calls → ``function_call`` items + text message
    - Regular messages → ``message`` items with role/content
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        # Tool result: system message with call_id
        if msg.role == "system" and msg.call_id:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.call_id,
                    "output": msg.content,
                }
            )
            continue

        # Assistant message: may contain tool calls (e.g. @ipython(call_123): {...})
        if msg.role == "assistant":
            content_parts, tool_uses = extract_tool_uses_from_assistant_message(
                msg.content, tool_format_override="tool"
            )
            # Emit function_call items for each tool use found
            items.extend(
                {
                    "type": "function_call",
                    "name": tooluse.tool,
                    "call_id": tooluse.call_id or "",
                    "arguments": json.dumps(tooluse.kwargs or {}),
                }
                for tooluse in tool_uses
            )
            # Emit remaining text content as a message (if any)
            text = "".join(
                p["text"] if isinstance(p, dict) else str(p) for p in content_parts
            ).strip()
            if text:
                items.append({"role": "assistant", "content": text})
            elif not tool_uses:
                # No tool uses and no text — still include the message
                items.append({"role": "assistant", "content": msg.content})
            continue

        # Regular message (user, system without call_id)
        items.append({"role": msg.role, "content": msg.content})

    return items


def _spec2tool(spec: ToolSpec) -> dict[str, Any]:
    """Convert a ToolSpec to Responses API function tool format.

    The Responses API uses a flat structure (name/description/parameters at top level),
    unlike Chat Completions which nests them under a ``function`` key.
    """
    name = spec.block_types[0] if spec.block_types else spec.name
    description = spec.get_instructions("tool")
    if len(description) > 1024:
        description = description[:1024]
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters2dict(spec.parameters),
    }


def _transform_to_codex_request(
    input_items: list[dict[str, Any]],
    model: str,
    stream: bool = True,
    reasoning_level: str | None = None,
) -> dict[str, Any]:
    """Build a Responses API request body from pre-converted input items.

    Extracts system messages as ``instructions``, passes everything else
    (messages, function_call, function_call_output) as ``input``.
    """
    base_model = model.split(":")[0] if ":" in model else model
    if ":" in model:
        reasoning_level = model.split(":")[1]

    if reasoning_level is None:
        reasoning_level = "medium"

    # System messages (role-based, no "type") become instructions
    system_parts = []
    api_input = []
    for item in input_items:
        if item.get("role") == "system":
            system_parts.append(item["content"])
        else:
            api_input.append(item)

    instructions = (
        "\n\n".join(system_parts) if system_parts else "You are a helpful assistant."
    )

    return {
        "model": base_model,
        "instructions": instructions,
        "input": api_input,
        "stream": stream,
        "store": False,
        "reasoning": {
            "effort": reasoning_level,
        },
    }


def _parse_sse_response(line: bytes | str) -> dict[str, Any] | None:
    """Parse a single SSE line (bytes or str)."""
    if isinstance(line, bytes):
        line = line.decode("utf-8")

    if not line.startswith("data:"):
        return None

    data = line[5:].strip()
    if data == "[DONE]":
        return {"done": True}

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def stream(
    messages: list[Message],
    model: str,
    tools: list[Any] | None = None,
    **kwargs: Any,
) -> Generator[str, None, None]:
    """Stream completion from ChatGPT subscription API."""
    auth = get_auth()

    api_messages = _messages_to_responses_input(messages)

    request_body = _transform_to_codex_request(
        input_items=api_messages,
        model=model,
        stream=True,
    )

    if tools:
        request_body["tools"] = [_spec2tool(t) for t in tools]

    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "chatgpt-account-id": auth.account_id,
        "originator": "gptme",
        "session_id": str(uuid4()),
    }

    response = requests.post(
        CODEX_ENDPOINT,
        json=request_body,
        headers=headers,
        stream=True,
        timeout=120,
    )

    if response.status_code != 200:
        error_text = response.text[:500]
        raise ValueError(f"Codex API error {response.status_code}: {error_text}")

    for line in response.iter_lines():
        if not line:
            continue

        data = _parse_sse_response(line)
        if data is None:
            continue

        if data.get("done"):
            break

        # Handle Responses API SSE events
        event_type = data.get("type", "")

        if event_type == "response.output_text.delta":
            delta_text = data.get("delta", "")
            if delta_text:
                yield delta_text

        elif event_type == "response.output_item.added":
            # Function call start: emit @name(call_id): prefix
            item = data.get("item", {})
            if item.get("type") == "function_call":
                name = item.get("name", "")
                call_id = item.get("call_id", "")
                yield f"\n@{name}({call_id}): "

        elif event_type == "response.function_call_arguments.delta":
            # Function call argument chunks
            delta_text = data.get("delta", "")
            if delta_text:
                yield delta_text

        elif event_type == "response.done":
            break


def chat(
    messages: list[Message],
    model: str,
    tools: list[Any] | None = None,
    **kwargs: Any,
) -> str:
    """Non-streaming completion from ChatGPT subscription API."""
    content_parts = list(stream(messages, model, tools, **kwargs))
    return "".join(content_parts)


def init(config: Any) -> bool:
    """Initialize the OpenAI subscription provider."""
    global _auth

    stored_auth = _load_tokens()
    if stored_auth is not None:
        if time.time() < stored_auth.expires_at - 300:
            _auth = stored_auth
            logger.info("OpenAI subscription provider initialized with stored tokens")
            return True

        if stored_auth.refresh_token:
            try:
                _auth = _refresh_access_token(stored_auth.refresh_token)
                logger.info(
                    "OpenAI subscription provider initialized with refreshed token"
                )
                return True
            except Exception as e:
                logger.debug(f"Token refresh failed during init: {e}")

    logger.info(
        "OpenAI subscription provider available "
        "(run 'gptme auth openai-subscription' to authenticate)"
    )
    return True
