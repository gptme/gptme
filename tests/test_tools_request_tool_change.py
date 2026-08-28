"""Tests for the request_tool_change tool (Phase 1 + Phase 2)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gptme.tools import (
    clear_tools,
    get_available_tools,
    get_tools,
    init_tools,
    set_tools,
)
from gptme.tools.base import ToolSpec
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
    clear_tools()
    # Load only request_tool_change itself so each test starts from a known state.
    init_tools(allowlist=["request_tool_change"])
    yield
    clear_tools()


_FAKE_SHELL = ToolSpec(name="shell", desc="Run shell commands")


def _patch_available(tools: list[ToolSpec]):
    """Patch get_available_tools in the module under test."""
    return patch(
        "gptme.tools.request_tool_change.get_available_tools",
        return_value=tools,
    )


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
