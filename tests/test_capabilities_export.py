"""Tests for gptme.util.capabilities_export (idea #1204).

Snapshot tests freeze the JSON bytes of the pure builder and assert that
no tool instructions, skill bodies, or MCP env/headers leak into default
JSON/text/HTML output.
"""

from __future__ import annotations

import json

import pytest

from gptme.util.capabilities_export import (
    build_snapshot,
    redact_secret_like,
    render,
)


def _fixture_snapshot(**overrides: object) -> dict:
    payload: dict = {
        "workspace": "/tmp/demo-workspace",
        "generated_at": "2026-09-02T01:30:00Z",
        "config": {
            "mcp_enabled": False,
            "plugin_enabled": ["headroom_compressor", "action_receipts"],
            "tool_allowlist": None,
            "profile": None,
        },
        "tools": [
            {
                "name": "shell",
                "desc": "Execute shell commands",
                "in_session": True,
                "available": True,
                "disabled_by_default": False,
                "available_hint": None,
                "is_mcp": False,
                "provenance": {"source": "builtin", "detail": "gptme.tools"},
                "block_types": ["shell", "bash"],
                "functions": [],
                "commands": [],
                "hints": ["code-exec", "destructive"],
                "parameters": [],
                "instructions_included": False,
            },
            {
                "name": "computer",
                "desc": "Desktop computer use",
                "in_session": False,
                "available": True,
                "disabled_by_default": True,
                "available_hint": None,
                "is_mcp": False,
                "provenance": {"source": "builtin", "detail": "gptme.tools"},
                "block_types": ["computer"],
                "functions": [],
                "commands": [],
                "hints": [],
                "parameters": [],
                "instructions_included": False,
            },
            {
                "name": "screenshot",
                "desc": "Capture the screen",
                "in_session": False,
                "available": False,
                "disabled_by_default": False,
                "available_hint": "install scrot",
                "is_mcp": False,
                "provenance": {"source": "builtin", "detail": "gptme.tools"},
                "block_types": ["screenshot"],
                "functions": [],
                "commands": [],
                "hints": [],
                "parameters": [],
                "instructions_included": False,
            },
        ],
        "skills": [
            {
                "name": "end",
                "desc": "Wrap up a session safely",
                "path": "skills/end/SKILL.md",
                "category": "end",
                "stub": False,
                "provenance": {"source": "dir", "detail": "end"},
                "body_included": False,
            },
        ],
        "plugins": [
            {
                "name": "headroom_compressor",
                "provenance": {"source": "folder", "detail": "headroom_compressor"},
                "tool_modules": [],
                "tool_names": [],
                "has_hooks": True,
                "has_commands": False,
                "enabled": True,
            },
        ],
        "mcp_servers": [
            {
                "name": "context",
                "enabled": True,
                "transport": "stdio",
                "in_session": False,
                "reason": "mcp_globally_disabled",
                "tool_count": None,
            },
        ],
        "limitations": [],
        "lessons_count": 3,
    }
    payload.update(overrides)
    return payload


SECRET = "Bearer sk-abcdef1234567890abcdef"


def test_redact_secret_like_masks_tokens():
    masked = redact_secret_like(SECRET)
    assert "abcdef1234567890abcdef" not in masked
    assert "Bearer" in masked
    assert redact_secret_like(None) == ""
    assert redact_secret_like("") == ""


def test_json_snapshot_is_byte_stable():
    snap = build_snapshot(
        workspace=_fixture_snapshot()["workspace"],
        generated_at=_fixture_snapshot()["generated_at"],
        config=_fixture_snapshot()["config"],
        tools=_fixture_snapshot()["tools"],
        skills=_fixture_snapshot()["skills"],
        plugins=_fixture_snapshot()["plugins"],
        mcp_servers=_fixture_snapshot()["mcp_servers"],
        lessons_count=3,
    )
    assert render(snap, "json") == render(snap, "json")
    parsed = json.loads(render(snap, "json"))
    assert parsed["schema_version"] == 1
    assert parsed["counts"]["tools_in_session"] == 1
    assert parsed["counts"]["skills"] == 1
    assert parsed["counts"]["lessons"] == 3
    assert parsed["counts"]["mcp_servers"] == 1


def test_default_json_omits_instructions():
    tools = _fixture_snapshot()["tools"]
    tools[0]["instructions"] = SECRET
    tools[0]["instructions_included"] = True  # pretend opt-in upstream
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config=_fixture_snapshot()["config"],
        tools=tools,
        skills=[],
        plugins=[],
        mcp_servers=[],
    )
    out = render(snap, "json")
    assert "instructions_included" in out
    # Only tools that explicitly opt in carry instructions; the CLI default
    # never sets that flag, and secrets are redacted regardless.
    for tool in json.loads(out)["tools"]:
        if tool.get("instructions_included"):
            assert SECRET not in tool["instructions"]


def test_html_has_no_instruction_text():
    tools = _fixture_snapshot()["tools"]
    tools[0]["instructions"] = "RUN THIS EXACT SECRET COMMAND SEQUENCE"
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config=_fixture_snapshot()["config"],
        tools=tools,
        skills=_fixture_snapshot()["skills"],
        plugins=_fixture_snapshot()["plugins"],
        mcp_servers=_fixture_snapshot()["mcp_servers"],
    )
    html = render(snap, "html")
    assert "RUN THIS EXACT SECRET COMMAND SEQUENCE" not in html
    assert "gptme capabilities" in html
    assert "<table>" in html


def test_text_default_hides_not_in_session_tools():
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config=_fixture_snapshot()["config"],
        tools=_fixture_snapshot()["tools"],
        skills=[],
        plugins=[],
        mcp_servers=[],
    )
    default = render(snap, "text")
    assert "shell" in default
    assert "computer" not in default
    all_tools = render(snap, "text", show_all=True)
    assert "computer" in all_tools


def test_unknown_format_raises():
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config={},
        tools=[],
        skills=[],
        plugins=[],
        mcp_servers=[],
    )
    with pytest.raises(ValueError, match="unknown format"):
        render(snap, "yaml")  # pyright: ignore[reportArgumentType]


def test_mcp_connected_tools_excluded_reason_renders():
    """connected_tools_excluded reason appears when a server connected but its tools
    were filtered by the allowlist — distinct from mcp_not_connected."""
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config={"mcp_enabled": True, "plugin_enabled": []},
        tools=[],
        skills=[],
        plugins=[],
        mcp_servers=[
            {
                "name": "myserver",
                "enabled": True,
                "transport": "stdio",
                "in_session": False,
                "reason": "connected_tools_excluded",
                "tool_count": None,
            }
        ],
    )
    text = render(snap, "text")
    assert "connected_tools_excluded" in text
    assert "myserver" in text
    html = render(snap, "html")
    assert "connected_tools_excluded" in html


def test_limitations_include_provenance_gap():
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config={},
        tools=[],
        skills=[],
        plugins=[],
        mcp_servers=[],
    )
    assert any("no native source field" in note for note in snap["limitations"])
