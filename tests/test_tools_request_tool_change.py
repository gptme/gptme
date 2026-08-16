from __future__ import annotations

from unittest.mock import patch

from gptme.tools import get_available_tools
from gptme.tools.base import ToolSpec
from gptme.tools.request_tool_change import execute_request_tool_change, tool


def _execute(**kwargs: str):
    return execute_request_tool_change(None, None, kwargs)


def test_request_tool_change_is_opt_in():
    assert tool.disabled_by_default is True


def test_request_tool_change_is_discoverable():
    available_tools = get_available_tools(include_mcp=False)
    assert tool in available_tools


@patch("gptme.tools.request_tool_change.get_available_tools")
def test_request_tool_change_records_valid_request(mock_get_available_tools):
    mock_get_available_tools.return_value = [
        ToolSpec(name="shell", desc="Run commands")
    ]

    result = _execute(
        change_type="enable_tool",
        tool_name="shell",
        reason="Need to inspect the workspace",
        urgency="medium",
    )

    mock_get_available_tools.assert_called_once_with(include_mcp=False)
    assert result.content == (
        "Tool change request recorded: enable_tool shell. "
        "No tool configuration was changed."
    )
    assert result.quiet is True


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
