"""Tests for ToolSpec.read_only flag and cli_confirm_hook auto-approve behavior."""

from unittest.mock import patch

import pytest

from gptme.hooks.confirm import ConfirmAction
from gptme.tools.base import ToolFunction, ToolSpec, ToolUse


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
    from gptme.tools import get_available_tools

    spec = next(tool for tool in get_available_tools() if tool.name == tool_name)
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


@pytest.mark.parametrize("tool_name", ["rag", "vision", "screenshot"])
def test_ipython_registered_read_only_function_reaches_cli_auto_approval(
    tool_name: str, monkeypatch
):
    """Normal helper calls inherit their real read-only tool's CLI policy."""
    from gptme.hooks.cli_confirm import cli_confirm_hook
    from gptme.tools import _loaded_tools_var, get_available_tools
    from gptme.tools import python as python_tool

    spec = next(tool for tool in get_available_tools() if tool.name == tool_name)
    assert spec.functions
    function_name = spec.functions[0].name
    token = _loaded_tools_var.set([spec, python_tool.tool])
    old_owners = dict(python_tool.registered_function_tools)
    seen: list[ConfirmAction] = []
    try:
        python_tool.registered_function_tools[function_name] = tool_name

        def confirm(tool_use=None):
            result = cli_confirm_hook(tool_use, preview=None)
            seen.append(result.action)
            return result

        monkeypatch.setattr(python_tool, "get_confirmation", confirm)
        with (
            patch.object(
                python_tool,
                "_get_ipython",
                side_effect=RuntimeError("stop after confirmation"),
            ),
            pytest.raises(RuntimeError, match="stop after confirmation"),
        ):
            list(python_tool.execute_python(f"{function_name}()", [], None))
        assert seen == [ConfirmAction.CONFIRM]
    finally:
        python_tool.registered_function_tools.clear()
        python_tool.registered_function_tools.update(old_owners)
        _loaded_tools_var.reset(token)


@pytest.mark.parametrize(
    "code",
    [
        "inspect_data(); mutate()",
        "result = inspect_data()",
        "(lambda: inspect_data())()",
        "await inspect_data()",
        "inspect_data().mutate()",
        "inspect_data() + 1",
        "print(inspect_data())",
        "inspect_data(__import__('os').system('id'))",
        "inspect_data(open('/etc/passwd').read())",
        "inspect_data(obj.attr)",
        "inspect_data(*args)",
        "inspect_data(**kwargs)",
        "inspect_data(foo())",
        "inspect_data(path=foo())",
    ],
)
def test_ipython_mixed_code_keeps_ipython_confirmation(code: str):
    """Only a single direct registered call may inherit another tool's policy."""
    from gptme.tools import python as python_tool

    old_owners = dict(python_tool.registered_function_tools)
    try:
        python_tool.registered_function_tools["inspect_data"] = "observer"
        assert python_tool._single_registered_function_owner(code) is None
    finally:
        python_tool.registered_function_tools.clear()
        python_tool.registered_function_tools.update(old_owners)


@pytest.mark.parametrize(
    "code",
    [
        "inspect_data()",
        "inspect_data('notes.md')",
        "inspect_data(path)",
        "inspect_data(path='notes.md')",
    ],
)
def test_ipython_literal_or_name_args_inherit_owner(code: str):
    """Constants and names are the only argument forms that may inherit policy."""
    from gptme.tools import python as python_tool

    old_owners = dict(python_tool.registered_function_tools)
    try:
        python_tool.registered_function_tools["inspect_data"] = "observer"
        assert python_tool._single_registered_function_owner(code) == "observer"
    finally:
        python_tool.registered_function_tools.clear()
        python_tool.registered_function_tools.update(old_owners)


def test_ipython_registered_function_owner_is_recorded_by_init():
    """Python init records which ToolSpec supplied each registered function."""
    from gptme.tools import _loaded_tools_var
    from gptme.tools import python as python_tool

    def inspect_data() -> str:
        return "observed"

    observer = ToolSpec(
        name="observer",
        desc="test",
        functions=[ToolFunction.from_callable(inspect_data)],
        read_only=True,
    )
    token = _loaded_tools_var.set([observer])
    old_functions = dict(python_tool.registered_functions)
    old_owners = dict(python_tool.registered_function_tools)
    try:
        with patch.object(
            python_tool, "get_installed_python_libraries", return_value=[]
        ):
            python_tool.init()
        assert python_tool.registered_function_tools["inspect_data"] == "observer"
    finally:
        python_tool.registered_functions.clear()
        python_tool.registered_functions.update(old_functions)
        python_tool.registered_function_tools.clear()
        python_tool.registered_function_tools.update(old_owners)
        _loaded_tools_var.reset(token)


def test_cli_confirm_hook_does_not_auto_approve_mcp_read_only(monkeypatch):
    """MCP tools must not inherit the read_only confirmation bypass."""
    from gptme.hooks.cli_confirm import cli_confirm_hook
    from gptme.tools import _loaded_tools_var

    spec = ToolSpec(name="remote.read", desc="test", read_only=True, is_mcp=True)
    token = _loaded_tools_var.set([spec])
    try:
        import gptme.hooks.cli_confirm as _m

        monkeypatch.setattr(_m, "prompt_alert", lambda _: "n")
        monkeypatch.setattr(_m, "print_bell", lambda: None)
        monkeypatch.setattr(_m, "flush_stdin", lambda: None)

        tool_use = ToolUse(tool="remote.read", args=[], content="")
        result = cli_confirm_hook(tool_use, preview=None)
        assert result.action != ConfirmAction.CONFIRM, (
            "MCP read_only tools must not be auto-confirmed"
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
