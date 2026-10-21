"""Tests for native (structured kwargs) invocation of the memory tool."""

from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmAction, ConfirmationResult
from gptme.tools import memory as memory_mod
from gptme.tools.memory import execute_memory


@pytest.fixture
def memory_workspace(tmp_path, monkeypatch):
    """Redirect the memory workspace and CC memory dir into tmp_path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mem_dir = tmp_path / "cc-memory"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: workspace)
    monkeypatch.setattr(memory_mod, "get_cc_memory_dir", lambda _ws: mem_dir)
    return mem_dir


def test_native_call_reaches_confirmation_with_content(memory_workspace):
    decision = ConfirmationResult(action=ConfirmAction.SKIP)
    with (
        patch(
            "gptme.tools.memory.execute_with_confirmation",
            wraps=memory_mod.execute_with_confirmation,
        ) as spy,
        patch("gptme.hooks.get_confirmation", return_value=decision) as confirm,
    ):
        messages = list(
            execute_memory(
                None, None, {"name": "native-probe", "content": "Test content"}
            )
        )

    assert spy.call_count == 1
    assert confirm.call_count == 1
    assert confirm.call_args.kwargs["preview"] == "Test content"
    assert not any("requires a name" in m.content for m in messages)


def test_native_skip_writes_no_file(memory_workspace):
    decision = ConfirmationResult(action=ConfirmAction.SKIP)
    with patch("gptme.hooks.get_confirmation", return_value=decision):
        list(
            execute_memory(
                None, None, {"name": "native-probe", "content": "Test content"}
            )
        )

    assert not (memory_workspace / "native-probe.md").exists()
    assert list(memory_workspace.glob("*.md")) == []


def test_legacy_call_still_reaches_confirmation(memory_workspace):
    decision = ConfirmationResult(action=ConfirmAction.SKIP)
    with patch("gptme.hooks.get_confirmation", return_value=decision) as confirm:
        list(execute_memory("Legacy body", ["legacy-name"], None))

    assert confirm.call_count == 1
    assert confirm.call_args.kwargs["preview"] == "Legacy body"
