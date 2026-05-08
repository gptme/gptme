"""Tests for the OpenRouter PKCE OAuth flow."""

from __future__ import annotations

import base64
import hashlib
import http.client
import socket
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from gptme.oauth import openrouter as orouter


def _free_port() -> int:
    """Pick a free localhost port for tests so we don't conflict with port 3000."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_generate_pkce_produces_valid_s256_pair() -> None:
    verifier, challenge = orouter.generate_pkce()

    assert isinstance(verifier, str) and len(verifier) >= 43
    assert isinstance(challenge, str) and challenge

    # The challenge must be the URL-safe base64 (no padding) of SHA-256(verifier)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected


def test_generate_pkce_is_random() -> None:
    pairs = {orouter.generate_pkce() for _ in range(5)}
    assert len(pairs) == 5


def test_is_port_available_true_for_free_port() -> None:
    assert orouter.is_port_available(_free_port()) is True


def test_is_port_available_false_for_busy_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy_port = s.getsockname()[1]
        assert orouter.is_port_available(busy_port) is False


def test_authenticate_raises_when_port_busy() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy_port = s.getsockname()[1]
        with pytest.raises(orouter.OAuthError, match="not available"):
            orouter.authenticate(callback_port=busy_port, open_browser=lambda _u: True)


@contextmanager
def _run_flow_in_thread(
    callback_port: int,
    open_browser_fn,
    *,
    timeout: float = 5.0,
) -> Iterator[dict[str, object]]:
    """Drive ``authenticate`` in a background thread and yield its result."""
    holder: dict[str, object] = {}

    def target() -> None:
        try:
            holder["key"] = orouter.authenticate(
                callback_port=callback_port,
                open_browser=open_browser_fn,
                timeout=timeout,
            )
        except Exception as e:
            holder["error"] = e

    t = threading.Thread(target=target)
    t.start()
    try:
        yield holder
    finally:
        t.join(timeout=timeout + 2)


def _send_callback(port: int, query: str) -> None:
    """Hit the local callback server with a GET request."""
    # Retry briefly while the server thread starts up
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", f"/callback?{query}")
            conn.getresponse().read()
            conn.close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"Callback server on port {port} never came up")


def test_authenticate_full_flow_exchanges_code_for_key() -> None:
    port = _free_port()
    captured: dict[str, object] = {}

    def fake_open(url: str) -> bool:
        captured["url"] = url
        # Parse the state out of the auth URL so the callback can echo it back
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(url).query)
        state = params["state"][0]
        captured["state"] = state
        captured["code_challenge"] = params["code_challenge"][0]
        captured["code_challenge_method"] = params["code_challenge_method"][0]
        captured["callback_url"] = params["callback_url"][0]

        # Simulate the user signing in and OpenRouter redirecting to our server
        threading.Thread(
            target=_send_callback,
            args=(port, f"code=ABCDEF&state={state}"),
            daemon=True,
        ).start()
        return True

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"key": "sk-or-test-12345"}

    with (
        patch.object(orouter.requests, "post", return_value=FakeResponse()) as post,
        _run_flow_in_thread(port, fake_open) as holder,
    ):
        pass

    assert holder.get("error") is None, holder.get("error")
    assert holder["key"] == "sk-or-test-12345"
    assert captured["code_challenge_method"] == "S256"
    assert captured["callback_url"] == f"http://127.0.0.1:{port}/callback"

    # Token exchange was called with the same verifier that derived the challenge
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["code"] == "ABCDEF"
    assert payload["code_challenge_method"] == "S256"
    derived = (
        base64.urlsafe_b64encode(
            hashlib.sha256(payload["code_verifier"].encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert derived == captured["code_challenge"]


def test_authenticate_rejects_state_mismatch() -> None:
    port = _free_port()

    def fake_open(_url: str) -> bool:
        threading.Thread(
            target=_send_callback,
            args=(port, "code=ABC&state=tampered"),
            daemon=True,
        ).start()
        return True

    with _run_flow_in_thread(port, fake_open) as holder:
        pass

    err = holder.get("error")
    assert isinstance(err, orouter.OAuthError)
    assert "state" in str(err).lower()


def test_authenticate_surfaces_provider_error() -> None:
    port = _free_port()

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(url).query)["state"][0]
        threading.Thread(
            target=_send_callback,
            args=(
                port,
                f"error=access_denied&error_description=user+cancelled&state={state}",
            ),
            daemon=True,
        ).start()
        return True

    with _run_flow_in_thread(port, fake_open) as holder:
        pass

    err = holder.get("error")
    assert isinstance(err, orouter.OAuthError)
    assert "user cancelled" in str(err)


def test_authenticate_token_exchange_failure_raises() -> None:
    port = _free_port()

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(url).query)["state"][0]
        threading.Thread(
            target=_send_callback,
            args=(port, f"code=XYZ&state={state}"),
            daemon=True,
        ).start()
        return True

    class FakeResponse:
        status_code = 502
        text = "bad gateway"

        def json(self) -> dict[str, str]:  # pragma: no cover — not reached
            return {}

    with (
        patch.object(orouter.requests, "post", return_value=FakeResponse()),
        _run_flow_in_thread(port, fake_open) as holder,
    ):
        pass

    err = holder.get("error")
    assert isinstance(err, orouter.OAuthError)
    assert "502" in str(err)


def test_authenticate_rejects_response_without_key() -> None:
    port = _free_port()

    def fake_open(url: str) -> bool:
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(url).query)["state"][0]
        threading.Thread(
            target=_send_callback,
            args=(port, f"code=XYZ&state={state}"),
            daemon=True,
        ).start()
        return True

    class FakeResponse:
        status_code = 200
        text = '{"unexpected": "shape"}'

        def json(self) -> dict[str, str]:
            return {"unexpected": "shape"}

    with (
        patch.object(orouter.requests, "post", return_value=FakeResponse()),
        _run_flow_in_thread(port, fake_open) as holder,
    ):
        pass

    err = holder.get("error")
    assert isinstance(err, orouter.OAuthError)
    assert "key" in str(err).lower()
