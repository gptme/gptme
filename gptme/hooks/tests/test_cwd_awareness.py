"""Tests for cwd_awareness hook.

Updated for CWD_CHANGED hook type (Issue #1521): the hook now receives
(old_cwd, new_cwd) directly from the centralized detector instead of
storing/reading its own ContextVar.
"""

from pathlib import Path
from unittest.mock import MagicMock

from gptme.hooks.cwd_awareness import on_cwd_changed
from gptme.message import Message


class TestOnCwdChanged:
    """Tests for on_cwd_changed — the CWD notification handler."""

    def test_yields_system_message(self, tmp_path: Path) -> None:
        """Should yield a system message when CWD changes."""
        msgs = list(
            on_cwd_changed(
                log=MagicMock(),
                workspace=None,
                old_cwd="/old/path",
                new_cwd=str(tmp_path),
                tool_use=None,
            )
        )
        messages = [m for m in msgs if isinstance(m, Message)]
        assert len(messages) == 1

    def test_message_contains_new_cwd(self, tmp_path: Path) -> None:
        """Message content should include the new working directory."""
        new_cwd = str(tmp_path)
        msgs = list(
            on_cwd_changed(
                log=MagicMock(),
                workspace=None,
                old_cwd="/old/path",
                new_cwd=new_cwd,
                tool_use=None,
            )
        )
        messages = [m for m in msgs if isinstance(m, Message)]
        assert len(messages) == 1
        assert new_cwd in messages[0].content

    def test_message_is_system_role(self) -> None:
        """Message should be a system-role message."""
        msgs = list(
            on_cwd_changed(
                log=MagicMock(),
                workspace=None,
                old_cwd="/old/path",
                new_cwd="/new/path",
                tool_use=None,
            )
        )
        messages = [m for m in msgs if isinstance(m, Message)]
        assert messages[0].role == "system"

    def test_message_has_system_info_tag(self) -> None:
        """Message should use system_info XML tag."""
        msgs = list(
            on_cwd_changed(
                log=MagicMock(),
                workspace=None,
                old_cwd="/old/path",
                new_cwd="/new/path",
                tool_use=None,
            )
        )
        messages = [m for m in msgs if isinstance(m, Message)]
        assert "system_info" in messages[0].content


class TestRegister:
    """Tests for register() — registering CWD_CHANGED hook."""

    def test_register_adds_hook(self) -> None:
        """register() should add a CWD_CHANGED hook."""
        from gptme.hooks import (
            HookRegistry,
            HookType,
            get_hooks,
            get_registry,
            set_registry,
        )
        from gptme.hooks.cwd_awareness import register

        old = get_registry()
        set_registry(HookRegistry())
        try:
            register()
            hooks = get_hooks(HookType.CWD_CHANGED)
            names = [h.name for h in hooks]
            assert "cwd_awareness.notification" in names
        finally:
            set_registry(old)


def test_cwd_change_message_is_hidden_from_output(tmp_path):
    """The notice is for the model; the user already sees the cd in the tool call."""
    from gptme.hooks.cwd_awareness import on_cwd_changed
    from gptme.logmanager import Log

    msgs = list(
        on_cwd_changed(
            Log(), workspace=tmp_path, old_cwd="/a", new_cwd="/b", tool_use=None
        )
    )
    assert msgs and all(isinstance(m, Message) and m.hide for m in msgs)
