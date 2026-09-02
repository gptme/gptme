"""CLI command: export a session-resolved capability snapshot (idea #1204)."""

from __future__ import annotations

import json
from pathlib import Path

import click


@click.command("capabilities")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(("text", "json", "html")),
    default="text",
    help="Output format (default: text)",
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    default=None,
    help="Workspace whose gptme.toml to resolve (default: cwd)",
)
@click.option(
    "--from-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Render an existing snapshot instead of collecting live",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Include discovered-but-not-loaded tools in text output",
)
@click.option(
    "--include-instructions",
    is_flag=True,
    help="Opt in to redacted tool instructions in JSON",
)
@click.option(
    "--connect-mcp",
    is_flag=True,
    help="Connect to configured MCP servers and live-enumerate their tools "
    "(off by default; the default path never connects)",
)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
def capabilities(
    fmt: str,
    workspace: Path | None,
    from_json: Path | None,
    show_all: bool,
    include_instructions: bool,
    connect_mcp: bool,
    output: Path | None,
) -> None:
    """Export a session-resolved snapshot of tools, skills, plugins, and MCP servers.

    Default output redacts tool instructions, skill bodies, and MCP env/headers.
    This is a snapshot of one workspace's resolved configuration, not a
    guarantee of the next session's toolset.
    """
    from ..util.capabilities_export import collect_live, render

    if from_json:
        snapshot = json.loads(from_json.read_text(encoding="utf-8"))
    else:
        snapshot = collect_live(
            (workspace or Path.cwd()).resolve(),
            include_instructions=include_instructions,
            connect_mcp=connect_mcp,
        )

    text = render(snapshot, fmt, show_all=show_all)
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        click.echo(text, nl=False)
