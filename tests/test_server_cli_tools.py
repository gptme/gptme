"""Tests for gptme-server --tools parsing (parity with the main CLI's `none`)."""

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.cli import _parse_tools_allowlist


def test_none_flag_returns_none():
    # Flag not passed -> use default tools.
    assert _parse_tools_allowlist(None) is None


def test_tools_none_disables_all_tools():
    # `--tools none` must disable all tools, like the main `gptme` CLI,
    # instead of crashing with "Tool 'none' not found".
    assert _parse_tools_allowlist("none") == []
    assert _parse_tools_allowlist("NONE") == []
    assert _parse_tools_allowlist(" none ") == []


def test_regular_tool_list_is_split_and_stripped():
    assert _parse_tools_allowlist("read,shell") == ["read", "shell"]
    assert _parse_tools_allowlist(" read , shell ") == ["read", "shell"]
    assert _parse_tools_allowlist("read,,shell,") == ["read", "shell"]


def test_none_cannot_be_combined_with_other_tools():
    import click

    with pytest.raises(click.UsageError):
        _parse_tools_allowlist("read,none")
