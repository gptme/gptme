"""Tests for the centralized CWD change detection (gptme/hooks/cwd_changed.py)
and CWD awareness notification (gptme/hooks/cwd_awareness.py).

Tests the CWD_CHANGED hook type added in Issue #1521.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gptme.hooks import (
    HookRegistry,
    HookType,
    get_hooks,
    get_registry,
    set_registry,
)
from gptme.hooks.cwd_awareness import on_cwd_changed
from gptme.hooks.cwd_changed import _cwd_before_var, _detect_change, _store_cwd
from gptme.message import Message


def _messages_only(items: list) -> list[Message]:
    """Filter hook results to only Message objects."""
    return [m for m in items if isinstance(m, Message)]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Provide a fresh hook registry for each test."""
    old = get_registry()
    set_registry(HookRegistry())
    _cwd_before_var.set(None)
    yield
    set_registry(old)


class TestCwdChangedDetector:
    """Tests for cwd_changed.py — the centralized CWD change detection."""

    def test_store_cwd(self, tmp_path: Path):
        """_store_cwd should save the current directory."""
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            list(_store_cwd(log=MagicMock(), workspace=None, tool_use=MagicMock()))
            assert _cwd_before_var.get() == str(tmp_path)
        finally:
            os.chdir(orig_cwd)

    def test_detect_no_change(self, tmp_path: Path):
        """_detect_change should yield nothing when CWD hasn't changed."""
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            _cwd_before_var.set(str(tmp_path))
            msgs = list(
                _detect_change(log=MagicMock(), workspace=None, tool_use=MagicMock())
            )
            assert msgs == []
        finally:
            os.chdir(orig_cwd)

    def test_detect_change_triggers_cwd_changed(self, tmp_path: Path):
        """_detect_change should trigger CWD_CHANGED hooks when CWD changes."""
        subdir = tmp_path / "sub"
        subdir.mkdir()

        captured = []

        def capture_hook(log, workspace, old_cwd, new_cwd, tool_use):
            captured.append({"old": old_cwd, "new": new_cwd})
            return
            yield  # make generator

        from gptme.hooks import register_hook

        register_hook("test_capture", HookType.CWD_CHANGED, capture_hook)

        orig_cwd = os.getcwd()
        try:
            _cwd_before_var.set(str(tmp_path))
            os.chdir(subdir)
            list(_detect_change(log=MagicMock(), workspace=None, tool_use=MagicMock()))
            assert len(captured) == 1
            assert captured[0]["old"] == str(tmp_path)
            assert captured[0]["new"] == str(subdir)
        finally:
            os.chdir(orig_cwd)

    def test_detect_no_stored_cwd(self, tmp_path: Path):
        """_detect_change should yield nothing when no CWD was stored."""
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            _cwd_before_var.set(None)
            msgs = list(
                _detect_change(log=MagicMock(), workspace=None, tool_use=MagicMock())
            )
            assert msgs == []
        finally:
            os.chdir(orig_cwd)

    def test_full_pre_post_cycle(self, tmp_path: Path):
        """Full cycle: _store_cwd + cd + _detect_change should trigger CWD_CHANGED."""
        subdir = tmp_path / "project"
        subdir.mkdir()

        triggered = []

        def on_change(log, workspace, old_cwd, new_cwd, tool_use):
            triggered.append(new_cwd)
            return
            yield

        from gptme.hooks import register_hook

        register_hook("test_on_change", HookType.CWD_CHANGED, on_change)

        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            list(_store_cwd(log=MagicMock(), workspace=None, tool_use=MagicMock()))

            os.chdir(subdir)
            list(_detect_change(log=MagicMock(), workspace=None, tool_use=MagicMock()))

            assert len(triggered) == 1
            assert triggered[0] == str(subdir)
        finally:
            os.chdir(orig_cwd)

    def test_register(self):
        """register() should add two hooks: store (PRE) and detect (POST)."""
        from gptme.hooks.cwd_changed import register

        register()

        pre_hooks = get_hooks(HookType.TOOL_EXECUTE_PRE)
        post_hooks = get_hooks(HookType.TOOL_EXECUTE_POST)

        pre_names = [h.name for h in pre_hooks]
        post_names = [h.name for h in post_hooks]

        assert "cwd_changed.store" in pre_names
        assert "cwd_changed.detect" in post_names

        # Both should have high priority
        store_hook = next(h for h in pre_hooks if h.name == "cwd_changed.store")
        detect_hook = next(h for h in post_hooks if h.name == "cwd_changed.detect")
        assert store_hook.priority == 100
        assert detect_hook.priority == 100


class TestCwdTrackingNotification:
    """Tests for cwd_awareness.py — the simplified CWD notification hook."""

    def test_yields_notification(self):
        """on_cwd_changed should yield a system message with the new CWD."""
        msgs = _messages_only(
            list(
                on_cwd_changed(
                    log=MagicMock(),
                    workspace=None,
                    old_cwd="/old/path",
                    new_cwd="/new/path",
                    tool_use=MagicMock(),
                )
            )
        )
        assert len(msgs) == 1
        assert msgs[0].role == "system"
        assert "/new/path" in msgs[0].content
        assert "system_info" in msgs[0].content

    def test_register(self):
        """register() should add a CWD_CHANGED hook."""
        from gptme.hooks.cwd_awareness import register

        register()

        hooks = get_hooks(HookType.CWD_CHANGED)
        names = [h.name for h in hooks]
        assert "cwd_awareness.notification" in names


class TestHookTypeEnum:
    """Test CWD_CHANGED hook type in the enum."""

    def test_cwd_changed_exists(self):
        """HookType should have CWD_CHANGED."""
        assert HookType.CWD_CHANGED == "cwd.changed"
        assert HookType.CWD_CHANGED.value == "cwd.changed"

    def test_cwd_changed_distinct(self):
        """CWD_CHANGED should be distinct from tool execution hooks."""
        assert HookType.CWD_CHANGED != HookType.TOOL_EXECUTE_PRE
        assert HookType.CWD_CHANGED != HookType.TOOL_EXECUTE_POST


class TestIntegration:
    """Integration tests: centralized detector + subscriber hooks working together."""

    def test_detector_triggers_tracking(self, tmp_path: Path):
        """cwd_changed detector + cwd_awareness subscriber should produce notification."""
        from gptme.hooks.cwd_awareness import register as register_tracking
        from gptme.hooks.cwd_changed import register as register_detector

        register_detector()
        register_tracking()

        subdir = tmp_path / "project"
        subdir.mkdir()

        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            list(_store_cwd(log=MagicMock(), workspace=None, tool_use=MagicMock()))

            os.chdir(subdir)
            msgs = _messages_only(
                list(
                    _detect_change(
                        log=MagicMock(), workspace=None, tool_use=MagicMock()
                    )
                )
            )

            assert len(msgs) >= 1
            assert any("/project" in m.content for m in msgs)
        finally:
            os.chdir(orig_cwd)

    def test_no_notification_without_change(self, tmp_path: Path):
        """No notification when CWD stays the same."""
        from gptme.hooks.cwd_awareness import register as register_tracking
        from gptme.hooks.cwd_changed import register as register_detector

        register_detector()
        register_tracking()

        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            list(_store_cwd(log=MagicMock(), workspace=None, tool_use=MagicMock()))

            # CWD stays the same
            msgs = list(
                _detect_change(log=MagicMock(), workspace=None, tool_use=MagicMock())
            )
            assert msgs == []
        finally:
            os.chdir(orig_cwd)
