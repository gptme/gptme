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
    collect_live,
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


def test_html_escapes_untrusted_snapshot_scalars():
    """--from-json HTML must not interpolate raw snapshot fields (XSS)."""
    snap = build_snapshot(
        workspace="/tmp/w",
        generated_at="2026-09-02T01:30:00Z",
        config=_fixture_snapshot()["config"],
        tools=_fixture_snapshot()["tools"],
        skills=_fixture_snapshot()["skills"],
        plugins=_fixture_snapshot()["plugins"],
        mcp_servers=_fixture_snapshot()["mcp_servers"],
    )
    snap["schema_version"] = "<script>alert(1)</script>"
    snap["generated_at"] = "<b>xss</b>"
    snap["workspace"] = '" onload="alert(1)'
    snap["counts"]["tools_in_session"] = "<svg/onload=alert(1)>"
    html = render(snap, "html")
    assert "<script>" not in html
    assert "<b>xss</b>" not in html
    assert "<svg/onload=alert(1)>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;xss&lt;/b&gt;" in html


def test_collect_live_default_does_not_connect_mcp(tmp_path, monkeypatch):
    """Default collect_live must not call create_mcp_tools even if MCP is enabled."""
    (tmp_path / "gptme.toml").write_text(
        "[mcp]\n"
        "enabled = true\n"
        "\n"
        "[[mcp.servers]]\n"
        'name = "fake-stdio"\n'
        "enabled = true\n"
        'command = "true"\n'
    )
    calls: list[object] = []

    def fake_create(config: object) -> list:
        calls.append(config)
        return []

    monkeypatch.setattr("gptme.tools.mcp_adapter.create_mcp_tools", fake_create)
    snap = collect_live(
        tmp_path, connect_mcp=False, generated_at="2026-09-02T00:00:00Z"
    )
    assert calls == []
    servers = {s["name"]: s for s in snap["mcp_servers"]}
    assert "fake-stdio" in servers
    assert servers["fake-stdio"]["reason"] == "connect_mcp_not_requested"
    assert servers["fake-stdio"]["in_session"] is False
    assert any("pass --connect-mcp" in note for note in snap["limitations"])


def test_collect_live_mcp_allowlist_does_not_abort_default_export(
    tmp_path, monkeypatch
):
    """MCP-qualified allowlist entries must not raise when connect_mcp=False."""
    monkeypatch.setenv("TOOL_ALLOWLIST", "shell,discord.read_channel,discord.*")
    snap = collect_live(
        tmp_path, connect_mcp=False, generated_at="2026-09-02T00:00:00Z"
    )
    assert snap["schema_version"] == 1
    in_session = [t["name"] for t in snap["tools"] if t["in_session"]]
    assert "shell" in in_session
    assert "discord.read_channel" not in in_session


def test_collect_live_mcp_only_allowlist_does_not_abort_default_export(
    tmp_path, monkeypatch
):
    """MCP-only allowlist must not raise when connect_mcp=False.

    Filtering every MCP-qualified entry must leave [], not None — None makes
    init_tools reload the original allowlist and ValueError with include_mcp=False.
    """
    monkeypatch.setenv("TOOL_ALLOWLIST", "discord.read_channel,discord.*")
    snap = collect_live(
        tmp_path, connect_mcp=False, generated_at="2026-09-02T00:00:00Z"
    )
    assert snap["schema_version"] == 1
    in_session = [t["name"] for t in snap["tools"] if t["in_session"]]
    assert "discord.read_channel" not in in_session


def test_collect_live_clears_stale_mcp_clients_before_fresh_collection(
    tmp_path, monkeypatch
):
    """Stale MCP clients from a prior connection must not fabricate connectivity."""
    from unittest.mock import MagicMock

    import gptme.tools.mcp_adapter as mcp_mod

    (tmp_path / "gptme.toml").write_text(
        "[mcp]\n"
        "enabled = true\n"
        "\n"
        "[[mcp.servers]]\n"
        'name = "fake-server"\n'
        "enabled = true\n"
        'command = "true"\n'
    )
    # Inject a stale client as if a prior session connected this server
    mcp_mod._mcp_clients["fake-server"] = MagicMock()

    def fake_create(config: object) -> list:
        # Reconnection fails — returns no tools, does not update _mcp_clients
        return []

    monkeypatch.setattr("gptme.tools.mcp_adapter.create_mcp_tools", fake_create)
    snap = collect_live(tmp_path, connect_mcp=True, generated_at="2026-09-02T00:00:00Z")

    # The stale client must have been cleared; connection failure → mcp_not_connected
    servers = {s["name"]: s for s in snap["mcp_servers"]}
    assert "fake-server" in servers
    assert servers["fake-server"]["reason"] == "mcp_not_connected"
    assert servers["fake-server"]["in_session"] is False


def test_connect_mcp_not_requested_reason_renders():
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
                "reason": "connect_mcp_not_requested",
                "tool_count": None,
            }
        ],
    )
    text = render(snap, "text")
    assert "connect_mcp_not_requested" in text
    html = render(snap, "html")
    assert "connect_mcp_not_requested" in html
