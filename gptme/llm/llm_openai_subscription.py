"""OpenAI Subscription Provider.

Enables use of ChatGPT Plus/Pro subscriptions with gptme through the
ChatGPT backend API (not the Platform API). This uses OAuth authentication
against ChatGPT's web interface.

Based on research from opencode-openai-codex-auth plugin.

NOTICE: For personal development use with your own ChatGPT Plus/Pro subscription.
For production or multi-user applications, use the OpenAI Platform API.

Usage:
    1. Set OPENAI_SUBSCRIPTION_SESSION_TOKEN environment variable
       (Get from browser: chatgpt.com cookies -> "__Secure-next-auth.session-token")
    2. Use model like: openai-subscription/gpt-5.2

Endpoint: https://chatgpt.com/backend-api/codex/responses
"""

import json
import logging
import os
import time
from base64 import urlsafe_b64decode
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import requests

from ..message import Message

logger = logging.getLogger(__name__)

from typing import TypedDict

# ChatGPT backend API base URL
CHATGPT_BASE_URL = "https://chatgpt.com"
CODEX_ENDPOINT = f"{CHATGPT_BASE_URL}/backend-api/codex/responses"
SESSION_ENDPOINT = f"{CHATGPT_BASE_URL}/api/auth/session"


class _SubscriptionModelInfo(TypedDict):
    context: int
    reasoning_levels: list[str]


# Available models through subscription
# Format: model-name with optional reasoning level (none/low/medium/high/xhigh)
SUBSCRIPTION_MODELS: dict[str, _SubscriptionModelInfo] = {
    "gpt-5.2": {
        "context": 128_000,
        "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
    },
    "gpt-5.2-codex": {
        "context": 128_000,
        "reasoning_levels": ["low", "medium", "high", "xhigh"],
    },
    "gpt-5.1-codex-max": {
        "context": 128_000,
        "reasoning_levels": ["low", "medium", "high", "xhigh"],
    },
    "gpt-5.1-codex": {
        "context": 128_000,
        "reasoning_levels": ["low", "medium", "high"],
    },
    "gpt-5.1-codex-mini": {"context": 128_000, "reasoning_levels": ["medium", "high"]},
    "gpt-5.1": {
        "context": 128_000,
        "reasoning_levels": ["none", "low", "medium", "high"],
    },
}


@dataclass
class SubscriptionAuth:
    """Authentication state for OpenAI subscription."""

    access_token: str
    account_id: str
    expires_at: float | None = None


# Global auth state
_auth: SubscriptionAuth | None = None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (we trust ChatGPT's token)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")

    # Add padding if needed
    payload = parts[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding

    decoded = urlsafe_b64decode(payload)
    return json.loads(decoded)


def _extract_account_id(access_token: str) -> str:
    """Extract chatgpt-account-id from JWT claims."""
    payload = _decode_jwt_payload(access_token)

    # Account ID is in the custom claims
    # Look for https://api.openai.com/auth or similar claims
    for key, value in payload.items():
        if "openai" in key.lower() and isinstance(value, dict):
            if "organization_id" in value:
                return value["organization_id"]

    # Fallback: try 'sub' claim or organization patterns
    if "sub" in payload:
        return payload["sub"]

    raise ValueError("Could not extract account ID from JWT")


def _get_session_token() -> str:
    """Get session token from environment."""
    token = os.environ.get("OPENAI_SUBSCRIPTION_SESSION_TOKEN")
    if not token:
        raise ValueError(
            "OPENAI_SUBSCRIPTION_SESSION_TOKEN environment variable not set.\n"
            "To get your session token:\n"
            "1. Log into chatgpt.com in your browser\n"
            "2. Open DevTools (F12) -> Application -> Cookies\n"
            "3. Copy the value of '__Secure-next-auth.session-token'\n"
            "4. Set: export OPENAI_SUBSCRIPTION_SESSION_TOKEN='your-token'"
        )
    return token


def _refresh_auth() -> SubscriptionAuth:
    """Refresh authentication by getting access token from session."""
    global _auth

    session_token = _get_session_token()

    # Get session info which includes access token
    response = requests.get(
        SESSION_ENDPOINT,
        cookies={"__Secure-next-auth.session-token": session_token},
        headers={"User-Agent": "gptme/1.0"},
        timeout=30,
    )

    if response.status_code != 200:
        raise ValueError(
            f"Failed to get session: {response.status_code} - {response.text}"
        )

    session_data = response.json()
    access_token = session_data.get("accessToken")
    if not access_token:
        raise ValueError("No accessToken in session response. Token may have expired.")

    # Extract account ID from JWT
    account_id = _extract_account_id(access_token)

    # Calculate expiry (sessions typically last ~1 hour, but we refresh at 50 min)
    expires_at = time.time() + 50 * 60

    _auth = SubscriptionAuth(
        access_token=access_token,
        account_id=account_id,
        expires_at=expires_at,
    )

    logger.info("OpenAI subscription auth refreshed successfully")
    return _auth


def get_auth() -> SubscriptionAuth:
    """Get current auth, refreshing if needed."""
    global _auth

    if _auth is None or (_auth.expires_at and time.time() > _auth.expires_at):
        return _refresh_auth()

    return _auth


def _transform_to_codex_request(
    messages: list[dict[str, Any]],
    model: str,
    stream: bool = True,
    reasoning_level: str | None = None,
) -> dict[str, Any]:
    """Transform OpenAI-style request to Codex format."""
    # Parse model and reasoning level
    # Format: gpt-5.2 or gpt-5.2-codex
    # Reasoning level can be appended like: gpt-5.2:high
    base_model = model.split(":")[0] if ":" in model else model
    if ":" in model:
        reasoning_level = model.split(":")[1]

    # Default reasoning level
    if reasoning_level is None:
        model_info = SUBSCRIPTION_MODELS.get(base_model)
        if model_info:
            levels = model_info.get("reasoning_levels", ["medium"])
            reasoning_level = "medium" if "medium" in levels else levels[0]
        else:
            reasoning_level = "medium"

    return {
        "model": base_model,
        "messages": messages,
        "stream": stream,
        "store": False,  # REQUIRED for ChatGPT backend
        "reasoning": {
            "effort": reasoning_level,
        },
    }


def _parse_sse_response(line: str) -> dict[str, Any] | None:
    """Parse a single SSE line."""
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

    # Convert messages to API format
    api_messages = []
    for msg in messages:
        api_messages.append(
            {
                "role": msg.role,
                "content": msg.content,
            }
        )

    # Build request
    request_body = _transform_to_codex_request(
        messages=api_messages,
        model=model,
        stream=True,
    )

    # Add tools if provided (standard OpenAI format)
    if tools:
        request_body["tools"] = [
            t.to_param() if hasattr(t, "to_param") else t for t in tools
        ]

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

    # Process SSE stream
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue

        data = _parse_sse_response(line)
        if data is None:
            continue

        if data.get("done"):
            break

        # Extract content delta from response
        # Codex uses similar format to OpenAI chat completions
        choices = data.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield content


def chat(
    messages: list[Message],
    model: str,
    tools: list[Any] | None = None,
    **kwargs: Any,
) -> str:
    """Non-streaming completion from ChatGPT subscription API."""
    # Collect all streamed content
    content_parts = list(stream(messages, model, tools, **kwargs))
    return "".join(content_parts)


def init(config: Any) -> bool:
    """Initialize the OpenAI subscription provider."""
    try:
        # Verify we can authenticate
        get_auth()
        logger.info("OpenAI subscription provider initialized")
        return True
    except Exception as e:
        logger.warning(f"OpenAI subscription provider initialization failed: {e}")
        return False


def get_models() -> list[str]:
    """Return available models for subscription provider."""
    models = []
    for model, info in SUBSCRIPTION_MODELS.items():
        models.append(model)
        # Add variants with reasoning levels
        for level in info.get("reasoning_levels", []):
            if level != "none":
                models.append(f"{model}:{level}")
    return models
