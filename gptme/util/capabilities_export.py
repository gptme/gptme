#!/usr/bin/env python3
"""Export a session-resolved gptme capability snapshot (idea #1204).

Dumps tools, skills, plugins, and MCP servers for one configured workspace as
JSON, static HTML, or text. Default output redacts instructions, skill bodies,
and MCP env/headers.

The builder/render half of this module is pure (no gptme imports at module
scope); ``collect_live`` resolves the workspace config lazily.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SECRET_LIKE = re.compile(r"(?i)(\b(sk-|Bearer\s+|api[_-]?key\s*[=:]\s*))([^\s\"']{8,})")

ToolStatus = str  # loaded | available | unavailable | disabled_by_default


def redact_secret_like(text: str | None) -> str:
    """Mask secret-like tokens while keeping a short prefix for debugging."""
    if not text:
        return ""

    def _sub(match: re.Match[str]) -> str:
        prefix = match.group(1)
        rest = match.group(3)
        return f"{prefix}{rest[:2]}***"

    return SECRET_LIKE.sub(_sub, text)


def _sorted_names(
    items: list[dict[str, Any]], key: str = "name"
) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: str(item.get(key, "")).lower())


def _tool_status(tool: dict[str, Any]) -> ToolStatus:
    if tool.get("in_session"):
        return "loaded"
    if not tool.get("available", True):
        return "unavailable"
    if tool.get("disabled_by_default"):
        return "disabled_by_default"
    return "available"


def build_snapshot(
    *,
    workspace: str,
    generated_at: str,
    config: dict[str, Any],
    tools: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    plugins: list[dict[str, Any]],
    mcp_servers: list[dict[str, Any]],
    limitations: list[str] | None = None,
    lessons_count: int = 0,
) -> dict[str, Any]:
    """Assemble a schema-v1 snapshot from already-resolved records.

    Pure: no gptme imports. Live collection is ``collect_live``.
    """
    # Defense in depth: redact instructions at the builder too, not only in
    # collect_live, so a hand-assembled snapshot cannot leak secrets.
    tools_sorted = _sorted_names(tools)
    for tool in tools_sorted:
        if tool.get("instructions_included") and tool.get("instructions"):
            tool["instructions"] = redact_secret_like(tool["instructions"])
    skills_sorted = _sorted_names(skills)
    plugins_sorted = _sorted_names(plugins)
    mcp_sorted = _sorted_names(mcp_servers)
    plugin_enabled = sorted(config.get("plugin_enabled") or [])
    mcp_enabled = bool(config.get("mcp_enabled"))

    notes = list(limitations or [])
    if not any("ToolSpec has no native source field" in note for note in notes):
        notes.append("ToolSpec has no native source field; tool provenance is inferred")
    if not mcp_enabled and mcp_sorted:
        if not any("config.mcp.enabled is false" in note for note in notes):
            notes.append("MCP tools not enumerated because config.mcp.enabled is false")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "workspace": workspace,
        "config": {
            "mcp_enabled": mcp_enabled,
            "plugin_enabled": plugin_enabled,
            "tool_allowlist": config.get("tool_allowlist"),
            "profile": config.get("profile"),
        },
        "counts": {
            "tools_in_session": sum(1 for t in tools_sorted if t.get("in_session")),
            "tools_available": len(tools_sorted),
            "skills": len(skills_sorted),
            "lessons": lessons_count,
            "plugins": len(plugins_sorted),
            "mcp_servers": len(mcp_sorted),
        },
        "tools": tools_sorted,
        "skills": skills_sorted,
        "plugins": plugins_sorted,
        "mcp_servers": mcp_sorted,
        "limitations": notes,
    }


def snapshot_to_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


def snapshot_to_text(snapshot: dict[str, Any], *, show_all: bool = False) -> str:
    counts = snapshot["counts"]
    cfg = snapshot["config"]
    lines = [
        f"gptme capabilities  schema={snapshot['schema_version']}  {snapshot['generated_at']}",
        f"workspace: {snapshot['workspace']}",
        (
            f"in-session tools {counts['tools_in_session']}/{counts['tools_available']}  "
            f"skills {counts['skills']}  plugins {counts['plugins']}  "
            f"mcp {counts['mcp_servers']} (enabled={cfg['mcp_enabled']})"
        ),
        "",
        "Tools:",
    ]
    for tool in snapshot["tools"]:
        if not show_all and not tool.get("in_session"):
            continue
        status = _tool_status(tool)
        prov = tool.get("provenance") or {}
        lines.append(
            f"  [{status:<20}] {tool['name']:<22} {tool.get('desc', '')} "
            f"({prov.get('source', '?')}:{prov.get('detail', '')})"
        )
    lines.append("")
    lines.append("Skills:")
    lines.extend(
        f"  {skill['name']:<28} {skill.get('desc', '')}" for skill in snapshot["skills"]
    )
    if snapshot["plugins"]:
        lines.append("")
        lines.append("Plugins:")
        for plugin in snapshot["plugins"]:
            enabled = "on" if plugin.get("enabled") else "off"
            lines.append(f"  [{enabled}] {plugin['name']}")
    if snapshot["mcp_servers"]:
        lines.append("")
        lines.append("MCP servers:")
        for server in snapshot["mcp_servers"]:
            reason = server.get("reason") or (
                "in_session" if server.get("in_session") else "configured"
            )
            lines.append(
                f"  {server['name']:<24} enabled={server.get('enabled')} "
                f"{server.get('transport')} ({reason})"
            )
    if snapshot["limitations"]:
        lines.append("")
        lines.append("Limitations:")
        lines.extend(f"  - {note}" for note in snapshot["limitations"])
    lines.append("")
    return "\n".join(lines)


def snapshot_to_html(snapshot: dict[str, Any]) -> str:
    counts = snapshot["counts"]
    cfg = snapshot["config"]
    loaded = [t for t in snapshot["tools"] if t.get("in_session")]
    other = [t for t in snapshot["tools"] if not t.get("in_session")]

    def _rows(items: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
        if not items:
            return "<p><em>None</em></p>"
        head = "".join(f"<th>{html_escape(label)}</th>" for _, label in columns)
        body_rows = []
        for item in items:
            cells = []
            for key, _label in columns:
                if key == "status":
                    value = _tool_status(item)
                elif key == "provenance":
                    prov = item.get("provenance") or {}
                    value = f"{prov.get('source', '')}:{prov.get('detail', '')}"
                else:
                    value = item.get(key, "")
                cells.append(f"<td>{html_escape(str(value))}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")
        return (
            "<table><thead><tr>"
            + head
            + "</tr></thead><tbody>"
            + "".join(body_rows)
            + "</tbody></table>"
        )

    warning = (
        "Availability depends on this workspace's gptme.toml, enabled plugins, "
        "MCP flag, tool allowlist, and permissions. This is a snapshot, not a "
        "guarantee of the next session."
    )
    limitations = "".join(
        f"<li>{html_escape(note)}</li>" for note in snapshot["limitations"]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>gptme capabilities — {html_escape(Path(snapshot["workspace"]).name)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.5rem; }}
    th, td {{ border-bottom: 1px solid #ddd; text-align: left; padding: 0.35rem 0.5rem; vertical-align: top; }}
    th {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em; }}
    .warn {{ background: #fff6d6; padding: 0.75rem 1rem; border-left: 4px solid #e6b800; }}
    code {{ font-size: 0.9em; }}
  </style>
</head>
<body>
  <h1>gptme capabilities</h1>
  <p>Schema {html_escape(str(snapshot["schema_version"]))} · {html_escape(str(snapshot["generated_at"]))}</p>
  <p><code>{html_escape(str(snapshot["workspace"]))}</code></p>
  <p class="warn">{html_escape(warning)}</p>
  <p>
    In-session tools {html_escape(str(counts["tools_in_session"]))}/{html_escape(str(counts["tools_available"]))}
    · skills {html_escape(str(counts["skills"]))}
    · plugins {html_escape(str(counts["plugins"]))}
    · MCP servers {html_escape(str(counts["mcp_servers"]))}
    (mcp.enabled={html_escape(str(cfg["mcp_enabled"]).lower())})
  </p>
  <h2>In-session tools</h2>
  {_rows(loaded, [("name", "Name"), ("desc", "Description"), ("provenance", "Provenance"), ("status", "Status")])}
  <h2>Discovered, not in session</h2>
  {_rows(other, [("name", "Name"), ("desc", "Description"), ("provenance", "Provenance"), ("status", "Status")])}
  <h2>Skills</h2>
  {_rows(snapshot["skills"], [("name", "Name"), ("desc", "Description"), ("path", "Path"), ("provenance", "Provenance")])}
  <h2>Plugins</h2>
  {_rows(snapshot["plugins"], [("name", "Name"), ("enabled", "Enabled"), ("provenance", "Provenance")])}
  <h2>MCP servers</h2>
  {_rows(snapshot["mcp_servers"], [("name", "Name"), ("enabled", "Enabled"), ("transport", "Transport"), ("reason", "Reason")])}
  <h2>Limitations</h2>
  <ul>{limitations}</ul>
</body>
</html>
"""


def _rel_or_str(path: Path, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def collect_live(
    workspace: Path,
    *,
    include_instructions: bool = False,
    connect_mcp: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Resolve the workspace gptme config and project a v1 snapshot."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from . import console

    workspace = workspace.resolve()
    prev_quiet = console.quiet
    console.quiet = True
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return _collect_live_impl(
                workspace,
                include_instructions=include_instructions,
                connect_mcp=connect_mcp,
                generated_at=generated_at,
            )
    finally:
        console.quiet = prev_quiet


def _collect_live_impl(
    workspace: Path,
    *,
    include_instructions: bool,
    connect_mcp: bool,
    generated_at: str | None,
) -> dict[str, Any]:
    from ..config import get_config, set_config_from_workspace  # fmt: skip
    from ..lessons.index import LessonIndex  # fmt: skip
    from ..plugins.registry import (  # fmt: skip
        clear_registry,
        discover_all_plugins,
        get_all_plugins,
    )
    from ..tools import (  # fmt: skip
        clear_tools,
        get_available_tools,
        get_tools,
        init_tools,
    )
    from ..tools._allowlist import is_mcp_allowlist_entry  # fmt: skip

    set_config_from_workspace(workspace)
    cfg = get_config()
    plugin_paths, enabled_plugins = cfg.get_plugin_config()
    clear_registry()
    discover_all_plugins(plugin_paths, enabled_plugins)
    plugins = get_all_plugins()

    plugin_tool_names: dict[str, str] = {}
    plugin_records: list[dict[str, Any]] = []
    seen_plugin_names: set[str] = set()
    for plugin in plugins:
        if plugin.name in seen_plugin_names:
            continue
        seen_plugin_names.add(plugin.name)
        for tool in plugin.tools:
            plugin_tool_names[tool.name] = plugin.name
        plugin_records.append(
            {
                "name": plugin.name,
                "provenance": {
                    "source": "folder",
                    "detail": plugin.name,
                },
                "tool_modules": list(plugin.tool_modules),
                "tool_names": [t.name for t in plugin.tools],
                "has_hooks": plugin.register_hooks is not None,
                "has_commands": plugin.register_commands is not None,
                "enabled": enabled_plugins is None or plugin.name in enabled_plugins,
            }
        )

    # Resolve the same allowlist init_tools would (TOOL_ALLOWLIST, then
    # chat.tools) so the config-only path can drop MCP-qualified names
    # before validation. Keep [] when every entry was MCP-qualified —
    # `or None` would reload the original MCP-only list and abort.
    chat_cfg = getattr(cfg, "chat", None)
    env_allowlist = cfg.get_env("TOOL_ALLOWLIST")
    raw_allowlist: list[str] | None
    if env_allowlist:
        raw_allowlist = env_allowlist.split(",")
    elif chat_cfg is not None and chat_cfg.tools:
        raw_allowlist = list(chat_cfg.tools)
    else:
        raw_allowlist = None
    if not connect_mcp and raw_allowlist:
        raw_allowlist = [t for t in raw_allowlist if not is_mcp_allowlist_entry(t)]

    clear_tools()
    # Honor --connect-mcp: init_tools() used to call get_available_tools() with
    # the default include_mcp=True, which connected every configured MCP server
    # before this flag was consulted.
    if connect_mcp:
        # Clear stale clients so a reconnect failure is reported as
        # mcp_not_connected, not the misleading connected_tools_excluded.
        from ..tools.mcp_adapter import clear_mcp_clients  # fmt: skip

        clear_mcp_clients()
    init_tools(raw_allowlist, include_mcp=connect_mcp)
    connected_mcp_servers: set[str] = set()
    if connect_mcp:
        from ..tools.mcp_adapter import get_mcp_clients  # fmt: skip

        connected_mcp_servers = set(get_mcp_clients().keys())

    loaded = {t.name: t for t in get_tools()}
    discovered = get_available_tools(include_mcp=connect_mcp)

    tools: list[dict[str, Any]] = []
    for tool in discovered:
        if tool.is_mcp:
            server = tool.name.split(".", 1)[0] if "." in tool.name else "unknown"
            provenance = {"source": "mcp", "detail": server}
        elif tool.name in plugin_tool_names:
            provenance = {
                "source": "plugin",
                "detail": plugin_tool_names[tool.name],
            }
        else:
            provenance = {"source": "builtin", "detail": "gptme.tools"}
        record: dict[str, Any] = {
            "name": tool.name,
            "desc": redact_secret_like(tool.desc),
            "in_session": tool.name in loaded,
            "available": bool(tool.is_available),
            "disabled_by_default": bool(tool.disabled_by_default),
            "available_hint": tool.available_hint,
            "is_mcp": bool(tool.is_mcp),
            "provenance": provenance,
            "block_types": list(tool.block_types or []),
            "functions": [fn.name for fn in (tool.functions or [])],
            "commands": list(tool.commands.keys()) if tool.commands else [],
            "hints": sorted(tool.hints) if tool.hints else [],
            "parameters": [
                {
                    "name": p.name,
                    "type": str(p.type or "string"),
                    "required": bool(getattr(p, "required", False)),
                    "description": redact_secret_like(p.description or ""),
                }
                for p in (tool.parameters or [])
            ],
            "instructions_included": False,
        }
        if include_instructions:
            record["instructions"] = redact_secret_like(tool.instructions or "")
            record["instructions_included"] = True
        tools.append(record)

    prev_cwd = Path.cwd()
    os.chdir(workspace)
    try:
        index = LessonIndex()
    finally:
        os.chdir(prev_cwd)
    skills: list[dict[str, Any]] = []
    lessons_count = 0
    for item in index.lessons:
        if not item.metadata.name:
            lessons_count += 1
            continue
        path = Path(item.path)
        skills.append(
            {
                "name": item.metadata.name,
                "desc": redact_secret_like(
                    (item.metadata.description or item.description or "")[:200]
                ),
                "path": _rel_or_str(path, workspace),
                "category": item.category,
                "stub": bool(getattr(item, "is_stub", False)),
                "provenance": {
                    "source": "dir",
                    "detail": path.parent.name,
                },
                "body_included": False,
            }
        )

    from ..config.models import MCPConfig, MCPServerConfig  # fmt: skip

    mcp_cfg = getattr(cfg, "mcp", None)
    mcp_typed: MCPConfig | None = mcp_cfg if isinstance(mcp_cfg, MCPConfig) else None
    mcp_enabled = bool(mcp_typed.enabled) if mcp_typed else False
    mcp_servers: list[dict[str, Any]] = []
    mcp_server_list: list[MCPServerConfig] = (
        list(mcp_typed.servers) if mcp_typed else []
    )
    mcp_servers_in_session = {
        t["provenance"]["detail"] for t in tools if t["is_mcp"] and t["in_session"]
    }
    for srv in mcp_server_list:
        if mcp_enabled and srv.enabled:
            if srv.name in mcp_servers_in_session:
                # Connected and has tools active in this session
                reason = "configured"
                in_session = True
            elif srv.name in connected_mcp_servers:
                # Connected but all tools excluded by the allowlist
                reason = "connected_tools_excluded"
                in_session = False
            elif not connect_mcp:
                # Default path never connects; listing is config-only
                reason = "connect_mcp_not_requested"
                in_session = False
            else:
                # Not connected (connection failed or server not yet reached)
                reason = "mcp_not_connected"
                in_session = False
        elif not mcp_enabled:
            reason = "mcp_globally_disabled"
            in_session = False
        else:
            reason = "server_disabled"
            in_session = False
        mcp_servers.append(
            {
                "name": srv.name,
                "enabled": bool(srv.enabled),
                "transport": "http" if srv.is_http else "stdio",
                "in_session": in_session,
                "reason": reason,
                "tool_count": None,
            }
        )

    chat = getattr(cfg, "chat", None)
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    limitations: list[str] = []
    if mcp_enabled and not connect_mcp:
        limitations.append(
            "MCP servers listed from config only; pass --connect-mcp to connect "
            "and live-enumerate tools"
        )
    return build_snapshot(
        workspace=str(workspace),
        generated_at=stamp,
        config={
            "mcp_enabled": mcp_enabled,
            "plugin_enabled": list(enabled_plugins or [p.name for p in plugins]),
            "tool_allowlist": getattr(chat, "tools", None),
            "profile": getattr(chat, "agent", None) if chat else None,
        },
        tools=tools,
        skills=skills,
        plugins=plugin_records,
        mcp_servers=mcp_servers,
        limitations=limitations,
        lessons_count=lessons_count,
    )


def render(snapshot: dict[str, Any], fmt: str, *, show_all: bool = False) -> str:
    if fmt == "json":
        return snapshot_to_json(snapshot)
    if fmt == "html":
        return snapshot_to_html(snapshot)
    if fmt == "text":
        return snapshot_to_text(snapshot, show_all=show_all)
    raise ValueError(f"unknown format: {fmt}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a session-resolved gptme capability snapshot (idea #1204)."
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "html"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace whose gptme.toml to resolve (default: cwd)",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="Render an existing snapshot instead of collecting live",
    )
    parser.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Include discovered-but-not-loaded tools in text output",
    )
    parser.add_argument(
        "--include-instructions",
        action="store_true",
        help="Opt in to redacted tool instructions in JSON",
    )
    parser.add_argument(
        "--connect-mcp",
        action="store_true",
        help="Connect to configured MCP servers and live-enumerate their tools "
        "(off by default; the default path never connects)",
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.from_json:
        snapshot = json.loads(args.from_json.read_text(encoding="utf-8"))
    else:
        workspace = (args.workspace or Path.cwd()).resolve()
        snapshot = collect_live(
            workspace,
            include_instructions=args.include_instructions,
            connect_mcp=args.connect_mcp,
        )

    text = render(snapshot, args.format, show_all=args.show_all)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
