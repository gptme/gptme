"""Tests for gptme.hooks.server_confirm module.

Tests the server-based tool confirmation system that integrates with
the SSE event system for client-side tool approval/rejection.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gptme.hooks.server_confirm import (
    PendingConfirmation,
    _lock,
    _pending_confirmations,
    current_conversation_id,
    current_session_id,
    get_pending,
    register_pending,
    remove_pending,
    resolve_pending,
    server_confirm_hook,
)
from gptme.tools.base import ToolUse


@pytest.fixture(autouse=True)
def _clean_pending():
    """Clear pending confirmations before and after each test."""
    with _lock:
        _pending_confirmations.clear()
    yield
    with _lock:
        _pending_confirmations.clear()


@pytest.fixture
def mock_tool_use():
    """Create a mock ToolUse for testing."""
    return ToolUse(tool="shell", args=[], content="echo hello")


# === PendingConfirmation dataclass ===


class TestPendingConfirmation:
    def test_creation(self, mock_tool_use):
        event = threading.Event()
        pending = PendingConfirmation(
            tool_use=mock_tool_use,
            preview="echo hello",
            event=event,
        )
        assert pending.tool_use is mock_tool_use
        assert pending.preview == "echo hello"
        assert pending.event is event
        assert pending.result is None

    def test_default_result_none(self, mock_tool_use):
        pending = PendingConfirmation(
            tool_use=mock_tool_use,
            preview=None,
            event=threading.Event(),
        )
        assert pending.result is None


# === Pending confirmation registry ===


class TestRegisterPending:
    def test_basic_registration(self, mock_tool_use):
        pending = register_pending("tool-1", mock_tool_use, "preview text")
        assert isinstance(pending, PendingConfirmation)
        assert pending.tool_use is mock_tool_use
        assert pending.preview == "preview text"
        assert not pending.event.is_set()
        assert pending.result is None

    def test_none_preview(self, mock_tool_use):
        pending = register_pending("tool-2", mock_tool_use, None)
        assert pending.preview is None

    def test_retrievable_after_registration(self, mock_tool_use):
        register_pending("tool-3", mock_tool_use, "test")
        retrieved = get_pending("tool-3")
        assert retrieved is not None
        assert retrieved.tool_use is mock_tool_use

    def test_multiple_registrations(self, mock_tool_use):
        register_pending("tool-a", mock_tool_use, "a")
        register_pending("tool-b", mock_tool_use, "b")
        register_pending("tool-c", mock_tool_use, "c")

        assert get_pending("tool-a") is not None
        assert get_pending("tool-b") is not None
        assert get_pending("tool-c") is not None

    def test_overwrite_existing(self, mock_tool_use):
        """Registering same ID overwrites the previous entry."""
        pending1 = register_pending("tool-x", mock_tool_use, "first")
        pending2 = register_pending("tool-x", mock_tool_use, "second")
        assert pending1 is not pending2
        retrieved = get_pending("tool-x")
        assert retrieved is not None
        assert retrieved.preview == "second"


class TestResolvePending:
    def test_resolve_existing(self, mock_tool_use):
        from gptme.hooks.confirm import ConfirmationResult

        pending = register_pending("tool-1", mock_tool_use, None)
        result = ConfirmationResult.confirm()

        success = resolve_pending("tool-1", result)
        assert success is True
        assert pending.result is result
        assert pending.event.is_set()

    def test_resolve_nonexistent_returns_false(self):
        from gptme.hooks.confirm import ConfirmationResult

        success = resolve_pending("nonexistent", ConfirmationResult.confirm())
        assert success is False

    def test_resolve_sets_event(self, mock_tool_use):
        """Resolving should signal the waiting thread via Event."""
        from gptme.hooks.confirm import ConfirmationResult

        pending = register_pending("tool-1", mock_tool_use, None)
        assert not pending.event.is_set()

        resolve_pending("tool-1", ConfirmationResult.confirm())
        assert pending.event.is_set()

    def test_resolve_with_skip(self, mock_tool_use):
        from gptme.hooks.confirm import ConfirmationResult

        pending = register_pending("tool-1", mock_tool_use, None)
        skip_result = ConfirmationResult.skip("user declined")

        resolve_pending("tool-1", skip_result)
        assert pending.result is not None
        assert pending.result.action == "skip"

    def test_resolve_with_edit(self, mock_tool_use):
        from gptme.hooks.confirm import ConfirmationResult

        pending = register_pending("tool-1", mock_tool_use, None)
        edit_result = ConfirmationResult.edit("echo goodbye")

        resolve_pending("tool-1", edit_result)
        assert pending.result is not None
        assert pending.result.action == "edit"


class TestRemovePending:
    def test_remove_existing(self, mock_tool_use):
        register_pending("tool-1", mock_tool_use, None)
        assert get_pending("tool-1") is not None

        remove_pending("tool-1")
        assert get_pending("tool-1") is None

    def test_remove_nonexistent_no_error(self):
        """Removing a non-existent ID should not raise."""
        remove_pending("nonexistent")

    def test_remove_idempotent(self, mock_tool_use):
        register_pending("tool-1", mock_tool_use, None)
        remove_pending("tool-1")
        remove_pending("tool-1")  # Should not raise
        assert get_pending("tool-1") is None


class TestGetPending:
    def test_get_existing(self, mock_tool_use):
        register_pending("tool-1", mock_tool_use, "preview")
        pending = get_pending("tool-1")
        assert pending is not None
        assert pending.preview == "preview"

    def test_get_nonexistent(self):
        assert get_pending("nonexistent") is None


# === Thread safety ===


class TestThreadSafety:
    def test_concurrent_register_resolve(self, mock_tool_use):
        """Test that register and resolve work correctly under concurrent access."""
        from gptme.hooks.confirm import ConfirmationResult

        n_tools = 50
        results = {}
        errors = []

        # Barrier ensures all n_tools registrations complete before resolver starts
        barrier = threading.Barrier(n_tools + 1)

        def register_and_wait(tool_id):
            try:
                pending = register_pending(tool_id, mock_tool_use, None)
                barrier.wait()
                # Wait with timeout
                pending.event.wait(timeout=5)
                results[tool_id] = pending.result
            except Exception as e:
                errors.append(e)

        def resolve_all():
            barrier.wait()  # Wait until all tools are registered
            for i in range(n_tools):
                try:
                    resolve_pending(f"tool-{i}", ConfirmationResult.confirm())
                except Exception as e:
                    errors.append(e)

        threads = []
        for i in range(n_tools):
            t = threading.Thread(target=register_and_wait, args=(f"tool-{i}",))
            threads.append(t)
            t.start()

        resolver = threading.Thread(target=resolve_all)
        resolver.start()

        for t in threads:
            t.join(timeout=10)
        resolver.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == n_tools
        for result in results.values():
            assert result is not None
            assert result.action == "confirm"


# === server_confirm_hook ===


class TestServerConfirmHook:
    def test_auto_confirm_when_no_session_context(self, mock_tool_use):
        """Without conversation/session context, hook should auto-confirm."""
        # Ensure no session context is set
        token1 = current_conversation_id.set(None)
        token2 = current_session_id.set(None)
        try:
            with patch(
                "gptme.hooks.server_confirm.check_auto_confirm",
                return_value=(False, None),
            ):
                result = server_confirm_hook(mock_tool_use, preview=None)
                assert result.action == "confirm"
        finally:
            current_conversation_id.reset(token1)
            current_session_id.reset(token2)

    def test_auto_confirm_when_centralized_flag(self, mock_tool_use):
        """When centralized auto-confirm is active, should auto-confirm."""
        with patch(
            "gptme.hooks.server_confirm.check_auto_confirm",
            return_value=(True, "auto-confirm active"),
        ):
            result = server_confirm_hook(mock_tool_use, preview=None)
            assert result.action == "confirm"

    def test_auto_confirm_missing_conversation_id(self, mock_tool_use):
        """Auto-confirm if conversation_id is None."""
        token1 = current_conversation_id.set(None)
        token2 = current_session_id.set("session-123")
        try:
            with patch(
                "gptme.hooks.server_confirm.check_auto_confirm",
                return_value=(False, None),
            ):
                result = server_confirm_hook(mock_tool_use, preview=None)
                assert result.action == "confirm"
        finally:
            current_conversation_id.reset(token1)
            current_session_id.reset(token2)

    def test_auto_confirm_missing_session_id(self, mock_tool_use):
        """Auto-confirm if session_id is None."""
        token1 = current_conversation_id.set("conv-123")
        token2 = current_session_id.set(None)
        try:
            with patch(
                "gptme.hooks.server_confirm.check_auto_confirm",
                return_value=(False, None),
            ):
                result = server_confirm_hook(mock_tool_use, preview=None)
                assert result.action == "confirm"
        finally:
            current_conversation_id.reset(token1)
            current_session_id.reset(token2)

    def test_emits_sse_event_when_session_context(self, mock_tool_use):
        """With session context, should register pending and emit SSE event."""
        from gptme.hooks.confirm import ConfirmationResult

        mock_session_mgr = MagicMock()
        mock_event_cls = MagicMock()

        token1 = current_conversation_id.set("conv-123")
        token2 = current_session_id.set("session-456")
        try:
            with (
                patch(
                    "gptme.hooks.server_confirm.check_auto_confirm",
                    return_value=(False, None),
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "gptme.server.api_v2_sessions": MagicMock(
                            SessionManager=mock_session_mgr
                        ),
                        "gptme.server.api_v2_common": MagicMock(
                            ToolPendingEvent=mock_event_cls
                        ),
                    },
                ),
            ):
                # Resolve the pending confirmation in a background thread
                def resolve_soon():
                    # Poll until the hook registers its pending entry, then resolve
                    for _ in range(200):  # up to 2 seconds
                        time.sleep(0.01)
                        with _lock:
                            if _pending_confirmations:
                                for pending in _pending_confirmations.values():
                                    pending.result = ConfirmationResult.confirm()
                                    pending.event.set()
                                return

                t = threading.Thread(target=resolve_soon)
                t.start()

                result = server_confirm_hook(mock_tool_use, preview="echo hello")
                t.join(timeout=5)

                assert result.action == "confirm"
                # Verify SSE event was emitted
                mock_session_mgr.add_event.assert_called_once()
                call_args = mock_session_mgr.add_event.call_args
                assert call_args[0][0] == "conv-123"
        finally:
            current_conversation_id.reset(token1)
            current_session_id.reset(token2)

    def test_handles_import_error_gracefully(self, mock_tool_use):
        """If server modules can't be imported, auto-confirm (not skip)."""
        token1 = current_conversation_id.set("conv-123")
        token2 = current_session_id.set("session-456")
        try:
            with (
                patch(
                    "gptme.hooks.server_confirm.check_auto_confirm",
                    return_value=(False, None),
                ),
                patch.dict("sys.modules", {"gptme.server.api_v2_common": None}),
            ):
                result = server_confirm_hook(mock_tool_use, preview=None)
                # ImportError handler should auto-confirm, not skip
                assert result.action == "confirm"
        finally:
            current_conversation_id.reset(token1)
            current_session_id.reset(token2)


# === register/unregister ===


class TestRegisterUnregister:
    def test_register(self):
        """register() should not raise."""
        from gptme.hooks.server_confirm import register

        register()

    def test_unregister(self):
        """unregister() should not raise even if not registered."""
        # Register first, then unregister
        from gptme.hooks.server_confirm import register, unregister

        register()
        unregister()
