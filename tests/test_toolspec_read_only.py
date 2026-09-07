"""Tests for ToolSpec.read_only flag and cli_confirm_hook auto-approve behavior."""

import pytest

from gptme.hooks.confirm import ConfirmAction
from gptme.tools.base import ToolSpec, ToolUse


def _make_spec(name: str, read_only: bool = False) -> ToolSpec:
    return ToolSpec(name=name, desc="test", read_only=read_only)


def test_toolspec_read_only_default_false():
    spec = _make_spec("write")
    assert spec.read_only is False


def test_toolspec_read_only_true():
    spec = _make_spec("read", read_only=True)
    assert spec.read_only is True


@pytest.mark.parametrize("tool_name", ["read", "rag", "vision", "screenshot"])
def test_read_only_tools_are_flagged(tool_name):
    """Tools that cannot modify state must carry read_only=True."""
    from gptme import tools as _tools_module

    # Trigger tool loading; skip when tool is unavailable (missing optional deps)
    try:
        _tools_module.init_tools(allowlist=[tool_name])
    except ValueError as e:
        pytest.skip(f"{tool_name!r} not available in this environment: {e}")
    from gptme.tools import get_tool

    spec = get_tool(tool_name)
    if spec is None:
        pytest.skip(f"{tool_name!r} not available in this environment")
    assert spec.read_only, f"{tool_name!r} should have read_only=True"


def test_cli_confirm_hook_auto_approves_read_only(monkeypatch):
    """cli_confirm_hook must return ConfirmationResult.confirm() for read_only tools."""
    from gptme.hooks.cli_confirm import cli_confirm_hook
    from gptme.tools import _loaded_tools_var

    # Inject a minimal read_only tool into the loaded-tools context var
    spec = _make_spec("myreader", read_only=True)
    token = _loaded_tools_var.set([spec])
    try:
        tool_use = ToolUse(tool="myreader", args=[], content="")
        result = cli_confirm_hook(tool_use, preview=None)
        assert result.action == ConfirmAction.CONFIRM, (
            "read_only tool should be auto-confirmed"
        )
    finally:
        _loaded_tools_var.reset(token)


def test_cli_confirm_hook_does_not_auto_approve_write(monkeypatch):
    """cli_confirm_hook must NOT auto-approve tools without read_only=True."""
    from gptme.hooks.cli_confirm import cli_confirm_hook
    from gptme.tools import _loaded_tools_var

    spec = _make_spec("mywriter", read_only=False)
    token = _loaded_tools_var.set([spec])
    try:
        # Patch prompt_alert to return "n" so the hook doesn't hang waiting for input
        import gptme.hooks.cli_confirm as _m

        monkeypatch.setattr(_m, "prompt_alert", lambda _: "n")
        monkeypatch.setattr(_m, "print_bell", lambda: None)
        monkeypatch.setattr(_m, "flush_stdin", lambda: None)

        tool_use = ToolUse(tool="mywriter", args=[], content="")
        result = cli_confirm_hook(tool_use, preview=None)
        assert result.action != ConfirmAction.CONFIRM, (
            "non-read_only tool must not be auto-confirmed"
        )
    finally:
        _loaded_tools_var.reset(token)
