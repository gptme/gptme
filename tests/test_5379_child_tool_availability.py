"""Subprocess subagents decide optional-tool availability in the child (#5379)."""

from unittest.mock import patch

import pytest

from gptme.config.cli_setup import _normalize_tool_allowlist
from gptme.tools.base import ToolSpec
from gptme.tools.subagent.execution import _subprocess_tools_arg

MORPH_UNAVAILABLE = ToolSpec(name="morph", desc="edit", available=False)
SHELL = ToolSpec(name="shell", desc="shell")
PATCH = ToolSpec(name="patch", desc="patch")


def _discover(tools):
    def discover(include_mcp=True):
        return list(tools)

    return discover


def test_parent_sends_a_permitted_tool_it_cannot_run_itself():
    with patch(
        "gptme.tools.subagent.execution.get_available_tools",
        side_effect=_discover([SHELL, MORPH_UNAVAILABLE]),
    ):
        names = _subprocess_tools_arg(None, ["*"]).split(",")
    assert "morph" in names
    assert "shell" in names


def test_parent_still_applies_parent_and_profile_exclusions():
    with patch(
        "gptme.tools.subagent.execution.get_available_tools",
        side_effect=_discover([SHELL, PATCH, MORPH_UNAVAILABLE]),
    ):
        # The parent excludes morph; the profile excludes patch.
        names = _subprocess_tools_arg(["shell", "patch"], ["s*", "m*x"]).split(",")
    assert "morph" not in names
    assert "patch" not in names
    assert "shell" in names
    assert not any(name.startswith("+") for name in names)


@pytest.fixture
def child_tools():
    with patch(
        "gptme.config.cli_setup.get_available_tools",
        side_effect=_discover([SHELL, MORPH_UNAVAILABLE]),
    ):
        yield


def test_managed_child_skips_an_unavailable_permitted_tool(child_tools, monkeypatch):
    monkeypatch.setenv("GPTME_SUBAGENT_AGENT_ID", "abc")
    with patch(
        "gptme.config.cli_setup.get_toolchain",
        side_effect=lambda items, **_: [SHELL] if items == ["shell"] else [],
    ):
        assert _normalize_tool_allowlist(["shell", "morph"]) == ["shell"]


def test_interactive_session_still_rejects_an_unavailable_named_tool(
    child_tools, monkeypatch
):
    monkeypatch.delenv("GPTME_SUBAGENT_AGENT_ID", raising=False)

    def strict(items, **_):
        if items == ["morph"]:
            raise ValueError("Tool 'morph' is not available")
        return [SHELL]

    with (
        patch("gptme.config.cli_setup.get_toolchain", side_effect=strict),
        pytest.raises(ValueError, match="morph"),
    ):
        _normalize_tool_allowlist(["shell", "morph"])
