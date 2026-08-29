"""Tests for the request_tool_change tool (Phase 1 + Phase 2)."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools import (
    clear_tools,
    execute_msg,
    get_available_tools,
    get_tool_format,
    get_tools,
    has_tool,
    init_tools,
    set_session_allowlist,
    set_tool_format,
    set_tools,
    unload_tool,
)
from gptme.tools.base import ToolSpec, ToolUse
from gptme.tools.request_tool_change import (
    _SELF_NAME,
    execute_request_tool_change,
    tool,
)


def _execute(**kwargs: str):
    return execute_request_tool_change(None, None, kwargs)


# ---------------------------------------------------------------------------
# Phase 1 opt-in and discoverability tests (unchanged)
# ---------------------------------------------------------------------------


def test_request_tool_change_is_opt_in():
    assert tool.disabled_by_default is True


def test_request_tool_change_is_discoverable():
    available_tools = get_available_tools(include_mcp=False)
    assert tool in available_tools


# ---------------------------------------------------------------------------
# Validation tests (unchanged from Phase 1)
# ---------------------------------------------------------------------------


@patch("gptme.tools.request_tool_change.get_available_tools")
def test_request_tool_change_rejects_unknown_tool(mock_get_available_tools):
    mock_get_available_tools.return_value = [
        ToolSpec(name="shell", desc="Run commands")
    ]

    result = _execute(
        change_type="enable_tool",
        tool_name="made_up_tool",
        reason="Need it",
        urgency="medium",
    )

    assert result.content == "request_tool_change: unknown tool 'made_up_tool'"


def test_request_tool_change_rejects_invalid_fields():
    cases = [
        (
            {
                "change_type": "replace_tool",
                "tool_name": "shell",
                "reason": "Need it",
                "urgency": "medium",
            },
            "change_type must be one of",
        ),
        (
            {
                "change_type": "enable_tool",
                "tool_name": "shell",
                "reason": "  ",
                "urgency": "medium",
            },
            "reason must not be empty",
        ),
        (
            {
                "change_type": "enable_tool",
                "tool_name": "shell",
                "reason": "Need it",
                "urgency": "critical",
            },
            "urgency must be one of",
        ),
    ]

    for kwargs, expected in cases:
        with patch(
            "gptme.tools.request_tool_change.get_available_tools",
            return_value=[ToolSpec(name="shell", desc="Run commands")],
        ):
            assert expected in _execute(**kwargs).content


def test_request_tool_change_rejects_non_string_fields():
    cases = [
        {
            "change_type": "enable_tool",
            "tool_name": "shell",
            "reason": None,
            "urgency": "medium",
        },
        {
            "change_type": "enable_tool",
            "tool_name": "shell",
            "reason": 123,
            "urgency": "medium",
        },
        {
            "change_type": "enable_tool",
            "tool_name": ["shell"],
            "reason": "Need it",
            "urgency": "medium",
        },
    ]

    for kwargs in cases:
        result = execute_request_tool_change(None, None, kwargs)  # type: ignore[arg-type]
        assert result.content == "request_tool_change: all arguments must be strings"
        assert result.quiet is True


# ---------------------------------------------------------------------------
# Phase 2 — actuation tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_tools():
    """Provide a clean tool context with a minimal known set."""
    previous_format = get_tool_format()
    clear_tools()
    # Load only request_tool_change itself so each test starts from a known state.
    init_tools(allowlist=["request_tool_change"])
    # The fixture allowlist isolates the loaded set; it is not an operator
    # restriction. Unrestricted sessions may still enable additional tools.
    set_session_allowlist(None)
    yield
    clear_tools()
    set_tool_format(previous_format)


_FAKE_SHELL = ToolSpec(
    name="shell",
    desc="Run shell commands",
    execute=lambda _code, _args, _kwargs: Message("system", "shell executed"),
)


@contextmanager
def _patch_available(tools: list[ToolSpec]):
    """Patch discovery for request validation and load_tool()."""
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "gptme.tools.request_tool_change.get_available_tools",
                return_value=tools,
            )
        )
        stack.enter_context(
            patch("gptme.tools.get_available_tools", return_value=tools)
        )
        yield


class TestEnableTool:
    def test_enable_adds_tool_to_session(self, isolated_tools):
        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="shell",
                reason="Need to inspect workspace",
                urgency="medium",
            )

        assert "enabled" in result.content
        assert "shell" in result.content
        assert any(t.name == "shell" for t in get_tools())

    def test_enable_initialization_error_is_reported(self, isolated_tools):
        def fail_initialization() -> ToolSpec:
            raise RuntimeError("broken initialization")

        broken_tool = ToolSpec(
            name="broken_tool",
            desc="Fails during initialization",
            init=fail_initialization,
        )

        with _patch_available([broken_tool, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="broken_tool",
                reason="Need it",
                urgency="medium",
            )

        assert "could not enable" in result.content
        assert "broken initialization" in result.content
        assert not any(t.name == "broken_tool" for t in get_tools())

    def test_enable_runs_tool_initialization(self, isolated_tools):
        initialized: list[str] = []

        def initialize_tool() -> ToolSpec:
            initialized.append("initialized")
            return ToolSpec(
                name="initializing_tool",
                desc="Initialized",
                execute=lambda _code, _args, _kwargs: Message(
                    "system", "initialized tool executed"
                ),
            )

        raw_tool = ToolSpec(
            name="initializing_tool",
            desc="Needs initialization",
            init=initialize_tool,
        )

        with (
            _patch_available([raw_tool, tool]),
            patch(
                "gptme.tools.get_available_tools",
                return_value=[raw_tool, tool],
            ),
        ):
            result = _execute(
                change_type="enable_tool",
                tool_name="initializing_tool",
                reason="Need initialized behavior",
                urgency="medium",
            )

        assert "enabled" in result.content
        assert initialized == ["initialized"]
        loaded = next(t for t in get_tools() if t.name == "initializing_tool")
        assert loaded.execute is not None

    def test_enable_rejects_unavailable_tool(self, isolated_tools):
        unavailable = ToolSpec(
            name="unavailable_tool",
            desc="Unavailable",
            available=False,
            available_hint="install its dependency",
        )

        with (
            _patch_available([unavailable, tool]),
            patch(
                "gptme.tools.get_available_tools",
                return_value=[unavailable, tool],
            ),
        ):
            result = _execute(
                change_type="enable_tool",
                tool_name="unavailable_tool",
                reason="Need it",
                urgency="medium",
            )

        assert "could not enable" in result.content
        assert "unavailable" in result.content.lower()
        assert not any(t.name == "unavailable_tool" for t in get_tools())

    def test_enable_already_loaded_is_noop(self, isolated_tools):
        # Pre-load shell
        set_tools([*get_tools(), _FAKE_SHELL])

        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="shell",
                reason="Make sure shell is here",
                urgency="low",
            )

        assert "already enabled" in result.content
        # Still exactly one shell in the list
        assert sum(1 for t in get_tools() if t.name == "shell") == 1

    def test_enable_unknown_tool_rejected(self, isolated_tools):
        with _patch_available([tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="nonexistent_xyz",
                reason="I want it",
                urgency="low",
            )

        assert "unknown tool" in result.content
        assert not any(t.name == "nonexistent_xyz" for t in get_tools())

    def test_enable_rejects_tool_outside_allowlist(self, isolated_tools):
        set_session_allowlist(["request_tool_change"])
        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="shell",
                reason="Need to inspect workspace",
                urgency="medium",
            )

        assert "allowlist" in result.content
        assert not any(t.name == "shell" for t in get_tools())

    def test_enable_allows_allowlisted_tool(self, isolated_tools):
        set_session_allowlist(["request_tool_change", "shell"])
        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="shell",
                reason="Need to inspect workspace",
                urgency="medium",
            )

        assert "enabled" in result.content
        assert any(t.name == "shell" for t in get_tools())

    def test_enable_allows_unloaded_mcp_tool(self, isolated_tools):
        """Validation must not drop MCP tools via include_mcp=False."""
        mcp_tool = ToolSpec(
            name="docs-server.read",
            desc="MCP docs tool",
            execute=lambda _code, _args, _kwargs: Message("system", "mcp executed"),
        )

        def fake_available(include_mcp: bool = True):
            return [mcp_tool, tool] if include_mcp else [tool]

        with (
            patch(
                "gptme.tools.request_tool_change.get_available_tools",
                side_effect=fake_available,
            ),
            patch("gptme.tools.get_available_tools", side_effect=fake_available),
        ):
            result = _execute(
                change_type="enable_tool",
                tool_name="docs-server.read",
                reason="Need MCP docs",
                urgency="medium",
            )

        assert "enabled" in result.content
        assert any(t.name == "docs-server.read" for t in get_tools())

    def test_enable_honors_hint_allowlist(self, isolated_tools):
        hinted = ToolSpec(
            name="file_tool",
            desc="Loaded from a user file",
            hints=frozenset({"read-only"}),
            execute=lambda _code, _args, _kwargs: Message("system", "file tool"),
        )
        set_session_allowlist(["hint:read-only"])
        with _patch_available([hinted, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="file_tool",
                reason="Need the file tool",
                urgency="medium",
            )

        assert "enabled" in result.content
        assert any(t.name == "file_tool" for t in get_tools())

    def test_enable_rejects_when_hints_do_not_match_allowlist(self, isolated_tools):
        set_session_allowlist(["hint:read-only"])
        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="enable_tool",
                tool_name="shell",
                reason="Need to inspect workspace",
                urgency="medium",
            )

        assert "allowlist" in result.content
        assert not any(t.name == "shell" for t in get_tools())


class TestDisableTool:
    def test_disable_removes_tool_from_session(self, isolated_tools):
        set_tools([*get_tools(), _FAKE_SHELL])

        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="disable_tool",
                tool_name="shell",
                reason="Pure-coding phase — no shell needed",
                urgency="low",
            )

        assert "disabled" in result.content
        assert not any(t.name == "shell" for t in get_tools())

    def test_disable_unregisters_owned_hooks(self, isolated_tools):
        hook = ("session_start", cast(Any, lambda: None), 0)
        side_effect_tool = ToolSpec(
            name="side_effect_tool",
            desc="Registers session side effects",
            execute=lambda _code, _args, _kwargs: Message("system", "executed"),
            hooks={"watch": hook},
        )
        loaded_tool = ToolSpec(
            name="side_effect_tool",
            desc="Registers session side effects",
            execute=side_effect_tool.execute,
            hooks=side_effect_tool.hooks,
        )

        with (
            _patch_available([side_effect_tool, tool]),
            patch("gptme.tools._init_single_tool", return_value=loaded_tool),
            patch("gptme.hooks.unregister_hook") as unregister_hook,
        ):
            enabled = _execute(
                change_type="enable_tool",
                tool_name="side_effect_tool",
                reason="Need it",
                urgency="medium",
            )
            disabled = _execute(
                change_type="disable_tool",
                tool_name="side_effect_tool",
                reason="Done with it",
                urgency="low",
            )

        assert "enabled" in enabled.content
        assert "disabled" in disabled.content
        unregister_hook.assert_called_once_with("side_effect_tool.watch")

    def test_disable_does_not_drop_process_global_commands(self, isolated_tools):
        """Slash commands live in a process-global registry.

        Unloading a command-providing tool in this session must not yank the
        command out from under a sibling session in the same process
        (gptme-server multi-conversation). Register via ToolSpec.register_commands()
        so the command is owned; an unowned registration would survive even if
        unload_tool incorrectly unregistered owned commands.
        """
        from gptme.commands import get_registered_commands, unregister_command

        def handler(_ctx):
            return
            yield  # pragma: no cover — CommandHandler is a generator type

        side_effect_tool = ToolSpec(
            name="side_effect_tool",
            desc="Registers a process-global slash command",
            execute=lambda _code, _args, _kwargs: Message("system", "executed"),
            commands={"side-effect": handler},
        )
        side_effect_tool.register_commands()
        set_tools([*get_tools(), side_effect_tool])
        try:
            with _patch_available([side_effect_tool, tool]):
                result = _execute(
                    change_type="disable_tool",
                    tool_name="side_effect_tool",
                    reason="Done with it",
                    urgency="low",
                )

            assert "disabled" in result.content
            assert not any(t.name == "side_effect_tool" for t in get_tools())
            assert "side-effect" in get_registered_commands()
        finally:
            unregister_command("side-effect")

    def test_disable_blocks_session_local_command_dispatch(self, isolated_tools):
        """Keeping the process-global handler must not leave /cmd executable here."""
        from gptme.commands import (
            get_registered_commands,
            get_user_commands,
            handle_cmd,
            unregister_command,
        )

        called: list[bool] = []

        def handler(_ctx):
            called.append(True)
            return
            yield  # pragma: no cover — CommandHandler is a generator type

        side_effect_tool = ToolSpec(
            name="side_effect_tool",
            desc="Registers a process-global slash command",
            execute=lambda _code, _args, _kwargs: Message("system", "executed"),
            commands={"side-effect": handler},
        )
        side_effect_tool.register_commands()
        set_tools([*get_tools(), side_effect_tool])
        mock_manager = MagicMock()
        try:
            list(handle_cmd("/side-effect", mock_manager))
            assert called == [True]
            assert "/side-effect" in get_user_commands()

            with _patch_available([side_effect_tool, tool]):
                result = _execute(
                    change_type="disable_tool",
                    tool_name="side_effect_tool",
                    reason="Done with it",
                    urgency="low",
                )

            assert "disabled" in result.content
            assert not has_tool("side_effect_tool")
            assert "side-effect" in get_registered_commands()

            called.clear()
            list(handle_cmd("/side-effect", mock_manager))
            assert called == []
            assert "/side-effect" not in get_user_commands()
        finally:
            unregister_command("side-effect")

    def test_disable_removes_tool_if_cleanup_fails(self, isolated_tools):
        side_effect_tool = ToolSpec(
            name="side_effect_tool",
            desc="Registers session side effects",
            execute=lambda _code, _args, _kwargs: Message("system", "executed"),
            hooks={"watch": ("session_start", cast(Any, lambda: None), 0)},
        )
        set_tools([*get_tools(), side_effect_tool])

        with (
            _patch_available([side_effect_tool, tool]),
            patch(
                "gptme.hooks.unregister_hook",
                side_effect=RuntimeError("cleanup failed"),
            ),
        ):
            result = _execute(
                change_type="disable_tool",
                tool_name="side_effect_tool",
                reason="Stop it",
                urgency="medium",
            )

        assert "disabled" in result.content
        assert not any(t.name == "side_effect_tool" for t in get_tools())

    def test_disable_not_loaded_is_noop(self, isolated_tools):
        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="disable_tool",
                tool_name="shell",
                reason="Remove shell",
                urgency="low",
            )

        assert "not currently enabled" in result.content

    def test_disable_self_is_refused(self, isolated_tools):
        with _patch_available([tool]):
            result = _execute(
                change_type="disable_tool",
                tool_name=_SELF_NAME,
                reason="Clean up",
                urgency="low",
            )

        assert "cannot disable itself" in result.content
        # request_tool_change must still be present
        assert any(t.name == _SELF_NAME for t in get_tools())

    def test_disable_loaded_custom_tool(self, isolated_tools):
        custom_tool = ToolSpec(
            name="custom_tool",
            desc="Loaded from a user file",
            execute=lambda _code, _args, _kwargs: Message("system", "executed"),
        )
        set_tools([*get_tools(), custom_tool])

        with _patch_available([tool]):
            result = _execute(
                change_type="disable_tool",
                tool_name="custom_tool",
                reason="Stop custom behavior",
                urgency="medium",
            )

        assert "disabled" in result.content
        assert not any(t.name == "custom_tool" for t in get_tools())


class TestUnloadTool:
    def test_unload_by_block_type_removes_tool(self, isolated_tools):
        alias_tool = ToolSpec(
            name="alias_tool",
            desc="Has a distinct block type",
            block_types=["alias_block"],
            execute=lambda _code, _args, _kwargs: Message("system", "executed"),
        )
        set_tools([*get_tools(), alias_tool])
        assert has_tool("alias_tool")

        unloaded = unload_tool("alias_block")
        assert unloaded is alias_tool
        assert not has_tool("alias_tool")
        assert not any(t is alias_tool for t in get_tools())


class TestConfigureTool:
    def test_configure_is_audit_only(self, isolated_tools):
        before = list(get_tools())
        with _patch_available([_FAKE_SHELL, tool]):
            result = _execute(
                change_type="configure_tool",
                tool_name="shell",
                reason="Change timeout",
                urgency="low",
            )

        assert "configure" in result.content.lower() or "recorded" in result.content
        # Tool list must be unchanged
        assert get_tools() == before


class TestEnableDisableIntegration:
    """Integration: enable → tool is callable; disable → tool is not callable."""

    def test_concurrent_unload_still_pairs_structured_call(self, isolated_tools):
        """An unload between runnability check and execution must not dangle a call."""
        set_tools([*get_tools(), _FAKE_SHELL])
        tooluse = ToolUse(
            "shell",
            [],
            "echo raced",
            call_id="shell_race",
            _format="tool",
        )

        # Deterministically model another execution context unloading the selected
        # tool after execute_msg's runnability check but before ToolUse.execute's
        # lookup. The structured call must still receive exactly one result.
        assert tooluse.is_runnable
        set_tools([loaded for loaded in get_tools() if loaded.name != "shell"])

        results = list(tooluse.execute())

        assert len(results) == 1
        assert results[0].call_id == "shell_race"
        assert "not available for execution" in results[0].content

    def test_enable_call_disable_call_fails(self, isolated_tools):
        """
        Requirement 3.1: a focused integration test exercises
        enable → call → disable → call-fails.

        We simulate 'call' at the tool-availability level (is_runnable)
        rather than doing a full LLM round-trip.
        """
        from gptme.tools import has_tool

        # Phase A: shell is not yet in the session
        assert not has_tool("shell"), "shell should not be loaded initially"

        # Phase B: enable shell
        with _patch_available([_FAKE_SHELL, tool]):
            r_enable = _execute(
                change_type="enable_tool",
                tool_name="shell",
                reason="Need to run tests",
                urgency="high",
            )
        assert "enabled" in r_enable.content
        assert has_tool("shell"), "shell should be loaded after enable"

        # Phase C: verify the loaded ToolSpec is the one from available tools
        loaded_shell = next(t for t in get_tools() if t.name == "shell")
        assert loaded_shell.name == "shell"

        # Phase D: disable shell
        with _patch_available([_FAKE_SHELL, tool]):
            r_disable = _execute(
                change_type="disable_tool",
                tool_name="shell",
                reason="Done with shell phase",
                urgency="low",
            )
        assert "disabled" in r_disable.content
        assert not has_tool("shell"), "shell should be removed after disable"

    def test_enable_allows_later_call_in_same_response(self, isolated_tools):
        set_tool_format("tool")
        content = (
            '@request_tool_change(change_1): {"change_type": "enable_tool", '
            '"tool_name": "shell", "reason": "Need shell", '
            '"urgency": "medium"}\n'
            '@shell(shell_2): {"cmd": "echo now-runnable"}'
        )

        with _patch_available([_FAKE_SHELL, tool]):
            results = list(execute_msg(Message("assistant", content)))

        assert any("enabled" in result.content for result in results)
        shell_results = [result for result in results if result.call_id == "shell_2"]
        assert len(shell_results) == 1
        assert "shell executed" in shell_results[0].content

    def test_disable_prevents_later_call_in_same_response(self, isolated_tools):
        set_tools([*get_tools(), _FAKE_SHELL])
        set_tool_format("tool")
        content = (
            '@request_tool_change(change_1): {"change_type": "disable_tool", '
            '"tool_name": "shell", "reason": "Done with shell", '
            '"urgency": "low"}\n'
            '@shell(shell_2): {"cmd": "echo should-not-run"}'
        )

        with _patch_available([_FAKE_SHELL, tool]):
            results = list(execute_msg(Message("assistant", content)))

        assert any("disabled" in result.content for result in results)
        shell_results = [result for result in results if result.call_id == "shell_2"]
        assert len(shell_results) == 1
        assert "not available for execution" in shell_results[0].content
        assert all("shell executed" not in result.content for result in results)
