"""Tests for server_elicit hook."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from gptme.hooks.elicitation import ElicitationRequest, ElicitationResponse
from gptme.hooks.server_confirm import current_conversation_id, current_session_id
from gptme.hooks.server_elicit import (
    PendingElicitation,
    _pending_elicitations,
    get_pending,
    register_pending,
    remove_pending,
    resolve_pending,
    server_elicit_hook,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset pending elicitations and context vars before each test."""
    _pending_elicitations.clear()
    current_conversation_id.set(None)
    current_session_id.set(None)
    yield
    _pending_elicitations.clear()
    current_conversation_id.set(None)
    current_session_id.set(None)


def _make_request(
    type: str = "text",
    prompt: str = "Enter value:",
    options: list[str] | None = None,
    default: str | None = None,
    description: str | None = None,
) -> ElicitationRequest:
    """Create an ElicitationRequest."""
    return ElicitationRequest(
        type=type,  # type: ignore[arg-type]
        prompt=prompt,
        options=options,
        default=default,
        description=description,
    )


class TestPendingElicitationRegistry:
    """Tests for the pending elicitation registration system."""

    def test_register_pending(self):
        """Register creates a pending elicitation with event."""
        request = _make_request()
        pending = register_pending("elicit-1", request)

        assert isinstance(pending, PendingElicitation)
        assert pending.request is request
        assert not pending.event.is_set()
        assert pending.result is None

    def test_get_pending(self):
        """Get returns registered pending elicitation."""
        request = _make_request()
        register_pending("elicit-2", request)

        result = get_pending("elicit-2")
        assert result is not None
        assert result.request is request

    def test_get_pending_not_found(self):
        """Get returns None for unknown elicitation ID."""
        assert get_pending("nonexistent") is None

    def test_resolve_pending(self):
        """Resolve sets result and signals event."""
        request = _make_request()
        pending = register_pending("elicit-3", request)

        response = ElicitationResponse(value="test-value")
        success = resolve_pending("elicit-3", response)

        assert success is True
        assert pending.result is response
        assert pending.event.is_set()

    def test_resolve_pending_not_found(self):
        """Resolve returns False for unknown elicitation ID."""
        response = ElicitationResponse(value="test")
        success = resolve_pending("nonexistent", response)
        assert success is False

    def test_remove_pending(self):
        """Remove cleans up pending elicitation."""
        request = _make_request()
        register_pending("elicit-4", request)
        assert get_pending("elicit-4") is not None

        remove_pending("elicit-4")
        assert get_pending("elicit-4") is None

    def test_remove_nonexistent(self):
        """Remove is safe for unknown elicitation IDs."""
        remove_pending("nonexistent")  # Should not raise

    def test_resolve_cancel(self):
        """Resolve with cancelled response."""
        request = _make_request()
        pending = register_pending("elicit-5", request)

        response = ElicitationResponse.cancel()
        resolve_pending("elicit-5", response)

        assert pending.result is not None
        assert pending.result.cancelled is True


class TestServerElicitHook:
    """Tests for server_elicit_hook function."""

    def test_no_context_returns_none(self):
        """Returns None when not in a server session (falls through to CLI)."""
        request = _make_request()
        result = server_elicit_hook(request)
        assert result is None

    def test_partial_context_conversation_only(self):
        """Returns None when only conversation_id is set."""
        current_conversation_id.set("conv-1")
        request = _make_request()
        result = server_elicit_hook(request)
        assert result is None

    def test_partial_context_session_only(self):
        """Returns None when only session_id is set."""
        current_session_id.set("sess-1")
        request = _make_request()
        result = server_elicit_hook(request)
        assert result is None

    def test_with_full_context_emits_sse_and_waits(self):
        """With full context, emits SSE event and waits for resolution."""
        current_conversation_id.set("conv-1")
        current_session_id.set("sess-1")

        request = _make_request(prompt="Pick a color:")

        mock_session_mgr = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "gptme.server.api_v2_sessions": MagicMock(
                    SessionManager=mock_session_mgr
                ),
            },
        ):

            def resolve_after_delay():
                import time

                time.sleep(0.1)
                for elicit_id, _pending in list(_pending_elicitations.items()):
                    resolve_pending(elicit_id, ElicitationResponse(value="blue"))

            t = threading.Thread(target=resolve_after_delay)
            t.start()

            result = server_elicit_hook(request)
            t.join(timeout=5)

        assert result is not None
        assert result.value == "blue"
        assert not result.cancelled

    def test_with_options(self):
        """SSE event includes options when present."""
        current_conversation_id.set("conv-1")
        current_session_id.set("sess-1")

        request = _make_request(
            type="choice",
            prompt="Pick:",
            options=["a", "b", "c"],
        )

        mock_session_mgr = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "gptme.server.api_v2_sessions": MagicMock(
                    SessionManager=mock_session_mgr
                ),
            },
        ):

            def resolve():
                import time

                time.sleep(0.1)
                for eid, _ in list(_pending_elicitations.items()):
                    resolve_pending(eid, ElicitationResponse(value="b"))

            t = threading.Thread(target=resolve)
            t.start()

            result = server_elicit_hook(request)
            t.join(timeout=5)

            # Verify SSE event was emitted with options
            call_args = mock_session_mgr.add_event.call_args
            assert call_args is not None
            event_data = call_args[0][1]
            assert event_data["options"] == ["a", "b", "c"]
            assert event_data["elicit_type"] == "choice"

        assert result is not None
        assert result.value == "b"

    def test_import_error_returns_none(self):
        """Returns None when server modules are not available."""
        current_conversation_id.set("conv-1")
        current_session_id.set("sess-1")

        import sys

        hidden = {}
        for mod_name in list(sys.modules.keys()):
            if "gptme.server" in mod_name:
                hidden[mod_name] = sys.modules.pop(mod_name)

        try:
            with patch.dict("sys.modules", {"gptme.server.api_v2_sessions": None}):
                request = _make_request()
                result = server_elicit_hook(request)
                # ImportError → falls through (returns None)
                assert result is None
        finally:
            sys.modules.update(hidden)


class TestConcurrency:
    """Tests for thread safety of pending elicitations."""

    def test_concurrent_register_resolve(self):
        """Multiple threads can register and resolve without corruption."""
        results = []
        errors = []

        def worker(i: int):
            try:
                request = _make_request(prompt=f"Q-{i}")
                eid = f"concurrent-{i}"
                pending = register_pending(eid, request)
                resolve_pending(eid, ElicitationResponse(value=f"answer-{i}"))
                assert pending.event.is_set()
                assert pending.result is not None
                assert pending.result.value == f"answer-{i}"
                remove_pending(eid)
                results.append(i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(results) == 10
