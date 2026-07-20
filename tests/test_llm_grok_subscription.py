"""Tests for the grok-subscription provider."""

import json
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from gptme.llm import llm_grok_subscription
from gptme.llm.llm_grok_subscription import (
    GrokAuth,
    _load_grok_tokens,
    _write_grok_tokens,
)
from gptme.message import Message


def _make_auth() -> GrokAuth:
    return GrokAuth(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_at=9_999_999_999.0,
    )


def _make_auth_json(
    key: str = "https://auth.x.ai/oauth2/token::b1a00492-073a-47ea-816f-4c329264a828",
) -> dict:
    return {
        key: {
            "key": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
    }


class _FakeSSEStreamResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.status_code = 200
        self.text = ""
        self._events = events

    def iter_lines(self) -> Iterator[bytes]:
        for event in self._events:
            yield f"data: {json.dumps(event)}".encode()
        yield b"data: [DONE]"


def _run_stream(events: list[dict[str, Any]]) -> str:
    auth = _make_auth()
    response = _FakeSSEStreamResponse(events)
    with (
        patch("gptme.llm.llm_grok_subscription.get_auth", return_value=auth),
        patch("gptme.llm.llm_grok_subscription.requests.post", return_value=response),
    ):
        return "".join(
            llm_grok_subscription.stream(
                [Message(role="user", content="hello")], "grok-4.5"
            )
        )


# ── stream tests ──────────────────────────────────────────────────────────────


def test_stream_basic_text():
    output = _run_stream(
        [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": ", world!"}}]},
        ]
    )
    assert output == "Hello, world!"


def test_stream_empty_delta_ignored():
    output = _run_stream(
        [
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {}}]},
            {"choices": [{"delta": {"content": "!"}}]},
        ]
    )
    assert output == "Hi!"


def test_stream_no_choices_ignored():
    output = _run_stream(
        [
            {"choices": [{"delta": {"content": "A"}}]},
            {"not_choices": True},
            {"choices": [{"delta": {"content": "B"}}]},
        ]
    )
    assert output == "AB"


def test_stream_builds_correct_request():
    auth = _make_auth()
    response = _FakeSSEStreamResponse([{"choices": [{"delta": {"content": "ok"}}]}])
    with (
        patch("gptme.llm.llm_grok_subscription.get_auth", return_value=auth),
        patch(
            "gptme.llm.llm_grok_subscription.requests.post", return_value=response
        ) as mock_post,
    ):
        list(
            llm_grok_subscription.stream(
                [Message(role="user", content="hi")], "grok-4.5"
            )
        )
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["model"] == "grok-4.5"
    assert call_kwargs["json"]["stream"] is True
    assert call_kwargs["json"]["messages"][0]["role"] == "user"
    assert call_kwargs["json"]["messages"][0]["content"] == "hi"
    assert "Authorization" in call_kwargs["headers"]


def test_stream_sends_max_tokens_when_provided():
    auth = _make_auth()
    response = _FakeSSEStreamResponse([])
    with (
        patch("gptme.llm.llm_grok_subscription.get_auth", return_value=auth),
        patch(
            "gptme.llm.llm_grok_subscription.requests.post", return_value=response
        ) as mock_post,
    ):
        list(
            llm_grok_subscription.stream(
                [Message(role="user", content="hi")], "grok-4.5", max_tokens=512
            )
        )
    assert mock_post.call_args.kwargs["json"]["max_tokens"] == 512


def test_stream_omits_max_tokens_when_not_provided():
    auth = _make_auth()
    response = _FakeSSEStreamResponse([])
    with (
        patch("gptme.llm.llm_grok_subscription.get_auth", return_value=auth),
        patch(
            "gptme.llm.llm_grok_subscription.requests.post", return_value=response
        ) as mock_post,
    ):
        list(
            llm_grok_subscription.stream(
                [Message(role="user", content="hi")], "grok-4.5"
            )
        )
    assert "max_tokens" not in mock_post.call_args.kwargs["json"]


# ── message conversion tests ───────────────────────────────────────────────────


def test_messages_to_openai_basic():
    """Basic role mapping is correct."""
    msgs = [
        Message(role="system", content="You are helpful."),
        Message(role="user", content="Hi!"),
        Message(role="assistant", content="Hello!"),
    ]
    result = llm_grok_subscription._messages_to_openai(msgs)
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    assert result[2]["role"] == "assistant"


def test_messages_to_openai_tool_result_becomes_tool_role():
    """System messages with call_id should become role:tool for the API."""
    msgs = [
        Message(role="user", content="Run save."),
        Message(
            role="assistant",
            content='@save(call_1): {"path": "x.txt", "content": "hi"}',
        ),
        Message(role="system", content="Saved x.txt", call_id="call_1"),
    ]
    result = llm_grok_subscription._messages_to_openai(msgs)
    tool_msgs = [m for m in result if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].get("tool_call_id") == "call_1"


# ── credential store tests ─────────────────────────────────────────────────────


def test_load_grok_tokens_from_valid_auth_file(tmp_path):
    auth_file = tmp_path / ".grok" / "auth.json"
    auth_file.parent.mkdir()
    auth_file.write_text(json.dumps(_make_auth_json()))

    with patch.object(
        llm_grok_subscription, "_get_grok_auth_path", return_value=auth_file
    ):
        # Reset tracked key
        llm_grok_subscription._credential_key = None
        auth = _load_grok_tokens()

    assert auth is not None
    assert auth.access_token == "test-access-token"
    assert auth.refresh_token == "test-refresh-token"
    assert auth.expires_at > time.time()


def test_load_grok_tokens_missing_file(tmp_path):
    missing = tmp_path / ".grok" / "auth.json"
    with patch.object(
        llm_grok_subscription, "_get_grok_auth_path", return_value=missing
    ):
        assert _load_grok_tokens() is None


def test_load_grok_tokens_prefers_known_client_id_entry(tmp_path):
    """When multiple entries exist, prefer the one matching our client ID."""
    known_key = "https://auth.x.ai/oauth2/token::b1a00492-073a-47ea-816f-4c329264a828"
    other_key = "https://other.issuer/token::some-other-client"
    data = {
        other_key: {
            "key": "other-token",
            "refresh_token": None,
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        known_key: {
            "key": "correct-token",
            "refresh_token": "correct-refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    }
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(data))

    with patch.object(
        llm_grok_subscription, "_get_grok_auth_path", return_value=auth_file
    ):
        llm_grok_subscription._credential_key = None
        auth = _load_grok_tokens()

    assert auth is not None
    assert auth.access_token == "correct-token"
    assert llm_grok_subscription._credential_key == known_key


def test_write_grok_tokens_updates_correct_entry(tmp_path):
    """_write_grok_tokens must update the same entry that _load_grok_tokens read."""
    known_key = "https://auth.x.ai/oauth2/token::b1a00492-073a-47ea-816f-4c329264a828"
    other_key = "https://other.issuer/token::other-client"
    data = {
        other_key: {"key": "old-other", "expires_at": "2099-01-01T00:00:00+00:00"},
        known_key: {
            "key": "old-correct",
            "refresh_token": "old-refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    }
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(data))

    with patch.object(
        llm_grok_subscription, "_get_grok_auth_path", return_value=auth_file
    ):
        # Simulate a prior load that set _credential_key
        llm_grok_subscription._credential_key = known_key

        new_auth = GrokAuth(
            access_token="new-token",
            refresh_token="new-refresh",
            expires_at=9_999_999_999.0,
        )
        _write_grok_tokens(new_auth)

        written = json.loads(auth_file.read_text())

    # The known entry should be updated
    assert written[known_key]["key"] == "new-token"
    assert written[known_key]["refresh_token"] == "new-refresh"
    # The unrelated entry must be untouched
    assert written[other_key]["key"] == "old-other"


def test_write_grok_tokens_does_not_raise_on_error(tmp_path):
    """_write_grok_tokens logs a warning rather than raising when write fails."""
    # Point to a non-existent directory — write_text will raise OSError
    auth_file = tmp_path / "nonexistent_dir" / "auth.json"

    with patch.object(
        llm_grok_subscription, "_get_grok_auth_path", return_value=auth_file
    ):
        # Should NOT raise; logs a warning internally
        _write_grok_tokens(_make_auth())


# ── get_auth tests ─────────────────────────────────────────────────────────────


def test_get_auth_returns_cached_if_valid(tmp_path):
    auth = _make_auth()
    llm_grok_subscription._auth = auth
    result = llm_grok_subscription.get_auth()
    assert result is auth
    llm_grok_subscription._auth = None  # cleanup


def test_get_auth_raises_when_no_credentials(tmp_path):
    missing = tmp_path / "auth.json"
    llm_grok_subscription._auth = None
    with (
        patch.object(
            llm_grok_subscription, "_get_grok_auth_path", return_value=missing
        ),
        pytest.raises(ValueError, match="not authenticated"),
    ):
        llm_grok_subscription.get_auth()
