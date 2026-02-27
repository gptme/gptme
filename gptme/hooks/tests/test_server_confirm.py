"""Tests for server_confirm hook."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from gptme.hooks.confirm import ConfirmationResult
from gptme.hooks.server_confirm import (
    PendingConfirmation,
    _pending_confirmations,
    current_conversation_id,
    current_session_id,
    get_pending,
    register_pending,
    remove_pending,
    resolve_pending,
    server_confirm_hook,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset pending confirmations and context vars before each test."""
    _pending_confirmations.clear()
    current_conversation_id.set(None)
    current_session_id.set(None)
    yield
    _pending_confirmations.clear()
    current_conversation_id.set(None)
    current_session_id.set(None)


def _make_tool_use(tool: str = "shell", content: str = "echo hi"):
    """Create a mock ToolUse."""
    tu = MagicMock()
    tu.tool = tool
    tu.args = []
    tu.content = content
    return tu


class TestPendingConfirmationRegistry:
    """Tests for the pending confirmation registration system."""

    def test_register_pending(self):
        """Register creates a pending confirmation with event."""
        tool_use = _make_tool_use()
        pending = register_pending("tool-1", tool_use, "preview text")

        assert isinstance(pending, PendingConfirmation)
        assert pending.tool_use is tool_use
        assert pending.preview == "preview text"
        assert not pending.event.is_set()
        assert pending.result is None

    def test_register_without_preview(self):
        """Register works without preview."""
        tool_use = _make_tool_use()
        pending = register_pending("tool-2", tool_use, None)
        assert pending.preview is None

    def test_get_pending(self):
        """Get returns registered pending confirmation."""
        tool_use = _make_tool_use()
        register_pending("tool-3", tool_use, None)

        result = get_pending("tool-3")
        assert result is not None
        assert result.tool_use is tool_use

    def test_get_pending_not_found(self):
        """Get returns None for unknown tool ID."""
        assert get_pending("nonexistent") is None

    def test_resolve_pending(self):
        """Resolve sets result and signals event."""
        tool_use = _make_tool_use()
        pending = register_pending("tool-4", tool_use, None)

        result = ConfirmationResult.confirm()
        success = resolve_pending("tool-4", result)

        assert success is True
        assert pending.result is result
        assert pending.event.is_set()

    def test_resolve_pending_not_found(self):
        """Resolve returns False for unknown tool ID."""
        result = ConfirmationResult.confirm()
        success = resolve_pending("nonexistent", result)
        assert success is False

    def test_remove_pending(self):
        """Remove cleans up pending confirmation."""
        tool_use = _make_tool_use()
        register_pending("tool-5", tool_use, None)
        assert get_pending("tool-5") is not None

        remove_pending("tool-5")
        assert get_pending("tool-5") is None

    def test_remove_nonexistent(self):
        """Remove is safe for unknown tool IDs."""
        remove_pending("nonexistent")  # Should not raise

    def test_multiple_registrations(self):
        """Multiple pending confirmations can coexist."""
        tu1 = _make_tool_use("shell", "ls")
        tu2 = _make_tool_use("save", "file.txt")
        register_pending("t1", tu1, None)
        register_pending("t2", tu2, None)

        p1 = get_pending("t1")
        p2 = get_pending("t2")
        assert p1 is not None
        assert p2 is not None
        assert p1.tool_use.tool == "shell"
        assert p2.tool_use.tool == "save"


class TestServerConfirmHook:
    """Tests for server_confirm_hook function."""

    def test_auto_confirm_when_no_session_context(self):
        """Auto-confirms when not in a server session (no context vars set)."""
        tool_use = _make_tool_use()
        result = server_confirm_hook(tool_use)
        assert result.action.name == "CONFIRM"

    def test_auto_confirm_with_centralized_auto(self):
        """Auto-confirms when centralized auto-confirm is active."""
        with patch(
            "gptme.hooks.server_confirm.check_auto_confirm",
            return_value=(True, "auto mode"),
        ):
            tool_use = _make_tool_use()
            result = server_confirm_hook(tool_use)
            assert result.action.name == "CONFIRM"

    def test_auto_confirm_partial_context(self):
        """Auto-confirms when only conversation_id is set (no session_id)."""
        current_conversation_id.set("conv-1")
        # session_id is still None
        tool_use = _make_tool_use()
        result = server_confirm_hook(tool_use)
        assert result.action.name == "CONFIRM"

    def test_auto_confirm_only_session_id(self):
        """Auto-confirms when only session_id is set (no conversation_id)."""
        current_session_id.set("sess-1")
        # conversation_id is still None
        tool_use = _make_tool_use()
        result = server_confirm_hook(tool_use)
        assert result.action.name == "CONFIRM"

    def test_server_confirm_with_full_context(self):
        """With full context, emits SSE event and waits for resolution."""
        current_conversation_id.set("conv-1")
        current_session_id.set("sess-1")

        tool_use = _make_tool_use()

        # Mock SessionManager and ToolPendingEvent
        mock_session_mgr = MagicMock()
        mock_event_cls = MagicMock()

        with (
            patch(
                "gptme.hooks.server_confirm.check_auto_confirm",
                return_value=(False, None),
            ),
            patch.dict(
                "sys.modules",
                {
                    "gptme.server.api_v2_common": MagicMock(
                        ToolPendingEvent=mock_event_cls
                    ),
                    "gptme.server.api_v2_sessions": MagicMock(
                        SessionManager=mock_session_mgr
                    ),
                },
            ),
        ):
            # Resolve in a background thread
            def resolve_after_delay():
                import time

                time.sleep(0.1)
                # Find and resolve the pending confirmation
                for tool_id, _pending in list(_pending_confirmations.items()):
                    resolve_pending(tool_id, ConfirmationResult.confirm())

            t = threading.Thread(target=resolve_after_delay)
            t.start()

            result = server_confirm_hook(tool_use)
            t.join(timeout=5)

        assert result.action.name == "CONFIRM"

    def test_import_error_auto_confirms(self):
        """Auto-confirms when server modules are not available."""
        current_conversation_id.set("conv-1")
        current_session_id.set("sess-1")

        with patch(
            "gptme.hooks.server_confirm.check_auto_confirm",
            return_value=(False, None),
        ):
            # Simulate ImportError by making the import fail
            import sys

            # Temporarily hide server modules
            hidden = {}
            for mod_name in list(sys.modules.keys()):
                if "gptme.server" in mod_name:
                    hidden[mod_name] = sys.modules.pop(mod_name)

            try:
                with patch.dict("sys.modules", {"gptme.server.api_v2_common": None}):
                    tool_use = _make_tool_use()
                    result = server_confirm_hook(tool_use)
                    assert result.action.name == "CONFIRM"
            finally:
                sys.modules.update(hidden)


class TestConcurrency:
    """Tests for thread safety of pending confirmations."""

    def test_concurrent_register_resolve(self):
        """Multiple threads can register and resolve without corruption."""
        results = []
        errors = []

        def worker(i: int):
            try:
                tool_use = _make_tool_use("shell", f"cmd-{i}")
                tid = f"concurrent-{i}"
                pending = register_pending(tid, tool_use, None)
                resolve_pending(tid, ConfirmationResult.confirm())
                assert pending.event.is_set()
                remove_pending(tid)
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
