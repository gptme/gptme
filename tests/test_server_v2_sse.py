"""Tests for the Server-Sent Events (SSE) stream functionality in the V2 API."""

import threading
import time
from typing import TYPE_CHECKING, cast

import pytest
import requests

if TYPE_CHECKING:
    from gptme.server.api_v2_common import GenerationCompleteEvent

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


@pytest.mark.timeout(20)
def test_event_stream(event_listener, wait_for_event):
    """Test the event stream endpoint."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]

    # Send a message to trigger an event
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Test message"},
    )

    # Wait for events
    assert wait_for_event(event_listener, "connected")
    assert wait_for_event(event_listener, "message_added")

    # Verify message content
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
    )
    assert resp.status_code == 200
    messages = resp.json()["log"]

    # Find the user message
    user_messages = [m for m in messages if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "Test message"


def test_proxy_flush_pad_constants():
    """_PROXY_FLUSH_PAD must be a valid SSE comment of sufficient size to flush proxy buffers."""
    from gptme.server.api_v2_sessions import (
        _PROXY_FLUSH_PAD,
        _TERMINAL_EVENT_TYPES,
    )

    # Must be a valid SSE comment (starts with ":", browsers ignore it)
    assert _PROXY_FLUSH_PAD.startswith(": "), "Pad must be an SSE comment line"
    assert _PROXY_FLUSH_PAD.endswith("\n\n"), "Pad must end with SSE event separator"
    # Must be large enough to exceed typical proxy response buffers (~4KB for Traefik)
    assert len(_PROXY_FLUSH_PAD) >= 4096, (
        f"Pad is {len(_PROXY_FLUSH_PAD)} bytes; must be ≥4096 to flush proxy buffers"
    )

    # Terminal event types that need the flush
    assert "generation_complete" in _TERMINAL_EVENT_TYPES
    assert "error" in _TERMINAL_EVENT_TYPES
    assert "interrupted" in _TERMINAL_EVENT_TYPES


@pytest.mark.timeout(10)
def test_flush_pad_emitted_after_generation_complete(setup_conversation):
    """SSE stream must include proxy flush padding immediately after generation_complete.

    Regression test for the >5s Stop→Submit delay on Traefik-fronted staging:
    X-Accel-Buffering:no is nginx-only; small payloads sit in Traefik's ~4KB
    response buffer until an idle timeout fires.  The padding here ensures the
    payload exceeds the buffer and is forwarded immediately.
    """
    from gptme.server.api_v2_sessions import _PROXY_FLUSH_PAD
    from gptme.server.session_models import SessionManager

    port, conversation_id, session_id = setup_conversation

    raw_data = bytearray()
    flush_seen = threading.Event()

    def collect_stream():
        resp = requests.get(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}"
            f"/events?session_id={session_id}",
            headers={"Authorization": "Bearer test-token-for-server-thread"},
            stream=True,
            timeout=8,
        )
        try:
            for chunk in resp.iter_content(chunk_size=None):
                raw_data.extend(chunk)
                # The flush pad is 4KB+; stop collecting once we've seen
                # the generation_complete event AND enough data for the pad.
                if b"generation_complete" in bytes(raw_data) and len(raw_data) > 4096:
                    flush_seen.set()
                    break
        except Exception:
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass
            flush_seen.set()  # unblock wait() on error

    stream_thread = threading.Thread(target=collect_stream, daemon=True)
    stream_thread.start()

    # Let the SSE connection establish
    time.sleep(0.3)

    # Inject a terminal event directly — no LLM call needed.
    # cast() is required because mypy cannot disambiguate anonymous dict literals
    # from the union of TypedDicts accepted by add_event.
    terminal_event = cast(
        "GenerationCompleteEvent",
        {
            "type": "generation_complete",
            "message": {
                "role": "assistant",
                "content": "Hello",
                "timestamp": "2026-01-01T00:00:00+00:00",
            },
        },
    )
    SessionManager.add_event(conversation_id, terminal_event)

    flush_seen.wait(timeout=5)

    data = bytes(raw_data)
    assert b"generation_complete" in data, "generation_complete event not in stream"

    gen_pos = data.find(b"generation_complete")
    pad_prefix = _PROXY_FLUSH_PAD[:20].encode()
    pad_pos = data.find(pad_prefix, gen_pos)
    assert pad_pos > gen_pos, (
        "Proxy flush padding not found after generation_complete event — "
        "Traefik and other reverse proxies will delay the Stop→Submit transition"
    )


@pytest.mark.xfail(reason="Flaky test")
@pytest.mark.timeout(20)
@pytest.mark.slow
@pytest.mark.requires_api
def test_event_stream_with_generation(event_listener, wait_for_event):
    """Test that the event stream receives generation events."""
    port = event_listener["port"]
    conversation_id = event_listener["conversation_id"]
    session_id = event_listener["session_id"]

    # Add a user message
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Say hello"},
    )

    # Use a real model
    requests.post(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id},
    )

    # Wait for events
    assert wait_for_event(event_listener, "generation_started")
    assert wait_for_event(event_listener, "generation_progress")
    assert wait_for_event(event_listener, "generation_complete")

    # Verify the response
    resp = requests.get(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
    )
    assert resp.status_code == 200
    messages = resp.json()["log"]

    # Find the assistant's response
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert len(assistant_messages[0]["content"]) > 0
