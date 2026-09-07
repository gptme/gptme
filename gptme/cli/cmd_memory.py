"""
gptme-util memory — unified cross-runtime memory CLI.

One markdown+frontmatter schema, layered roots.
See: https://github.com/gptme/gptme/issues/3734
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    pass


@click.group("memory")
def memory():
    """Unified cross-runtime memory store (layered markdown roots)."""


@memory.command("save")
@click.argument("name")
@click.argument("content", required=False)
@click.option(
    "--description", "-d", help="One-line summary (defaults to first line of content)."
)
@click.option(
    "--type",
    "memory_type",
    default="general",
    show_default=True,
    help="Entry type (user, feedback, project, reference, general).",
)
@click.option(
    "--keyword",
    "-k",
    "keywords",
    multiple=True,
    help="Keyword for lesson-style matching.",
)
@click.option(
    "--scope",
    type=click.Choice(["project", "cc", "user", "env"]),
    default=None,
    help="Write target root (default: cc).",
)
@click.option("--json", "as_json", is_flag=True, help="Print saved entry as JSON.")
def memory_save_cmd(
    name: str,
    content: str | None,
    description: str | None,
    memory_type: str,
    keywords: tuple[str, ...],
    scope: str | None,
    as_json: bool,
):
    """Save a memory entry NAME with CONTENT.

    If CONTENT is omitted, it is read from stdin.

    Example:

    \b
        gptme-util memory save prefer-short \\
          "User prefers short, direct answers." \\
          --type user
    """
    from ..memory.ops import save_memory  # fmt: skip

    if content is None:
        if sys.stdin.isatty():
            click.echo("Reading content from stdin…", err=True)
        content = sys.stdin.read()

    if not content.strip():
        click.echo("Error: content is empty.", err=True)
        sys.exit(1)

    try:
        path = save_memory(
            name=name,
            content=content,
            description=description,
            memory_type=memory_type,
            keywords=list(keywords) if keywords else None,
            scope=scope,  # type: ignore[arg-type]
        )
    except (OSError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if as_json:
        from ..memory.schema import parse_entry  # fmt: skip

        entry = parse_entry(path)
        click.echo(
            json.dumps(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "path": str(path),
                    "type": entry.type,
                },
                indent=2,
            )
        )
    else:
        click.echo(f"Saved memory '{name}' to {path}")


@memory.command("list")
@click.option(
    "--scope",
    type=click.Choice(["project", "cc", "user", "env"]),
    default=None,
    help="Restrict to a single root.",
)
@click.option(
    "--all", "include_superseded", is_flag=True, help="Include superseded entries."
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def memory_list_cmd(scope: str | None, include_superseded: bool, as_json: bool):
    """List memory entries across all roots."""
    from ..memory.ops import list_entries  # fmt: skip
    from ..memory.roots import resolve_roots, write_root  # fmt: skip

    if scope is not None:
        roots = [write_root(scope=scope)]  # type: ignore[arg-type]
    else:
        roots = resolve_roots()

    entries = list_entries(roots=roots, include_superseded=include_superseded)

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "name": e.name,
                        "description": e.description,
                        "type": e.type,
                        "status": e.status,
                        "path": str(e.path) if e.path else None,
                    }
                    for e in entries
                ],
                indent=2,
            )
        )
        return

    if not entries:
        click.echo("No memory entries found.")
        return

    click.echo(f"Memory entries ({len(entries)}):\n")
    for entry in entries:
        status_tag = f" [{entry.status}]" if entry.status != "living" else ""
        click.echo(f"  {entry.name}{status_tag}")
        click.echo(f"    {entry.description[:80]}")


@memory.command("show")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def memory_show_cmd(name: str, as_json: bool):
    """Show a memory entry by name or slug."""
    from ..memory.ops import show_entry  # fmt: skip

    entry = show_entry(name)
    if entry is None:
        click.echo(f"No entry found for '{name}'.", err=True)
        sys.exit(1)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "type": entry.type,
                    "status": entry.status,
                    "keywords": entry.keywords,
                    "body": entry.body,
                    "path": str(entry.path) if entry.path else None,
                },
                indent=2,
            )
        )
        return

    click.echo(f"# {entry.name}")
    click.echo(f"Description: {entry.description}")
    click.echo(f"Type: {entry.type}  Status: {entry.status}")
    if entry.keywords:
        click.echo(f"Keywords: {', '.join(entry.keywords)}")
    if entry.path:
        click.echo(f"Path: {entry.path}")
    click.echo()
    click.echo(entry.body)


@memory.command("index")
@click.option("--budget", default=200, show_default=True, help="Max entries in index.")
@click.option(
    "--check", is_flag=True, help="Exit 0 (no output) if index is already byte-stable."
)
@click.option(
    "--write", "do_write", is_flag=True, help="Write MEMORY.md to the primary root."
)
def memory_index_cmd(budget: int, check: bool, do_write: bool):
    """Generate or verify MEMORY.md from all living entries.

    With --write, the primary root's MEMORY.md is updated in place.
    With --check, exits 0 if MEMORY.md is already up-to-date (nothing printed).
    """
    from ..memory.ops import index_memory  # fmt: skip
    from ..memory.roots import resolve_roots  # fmt: skip

    roots = resolve_roots()
    content = index_memory(roots=roots, budget=budget, check=check)

    if check and not content:
        # Already stable
        sys.exit(0)

    if do_write and roots:
        primary = roots[0]
        primary.mkdir(parents=True, exist_ok=True)
        (primary / "MEMORY.md").write_text(content, encoding="utf-8")
        click.echo(f"Wrote MEMORY.md to {primary / 'MEMORY.md'}")
    else:
        click.echo(content, nl=False)


@memory.command("supersede")
@click.argument("old_name")
@click.argument("new_name")
def memory_supersede_cmd(old_name: str, new_name: str):
    """Mark OLD_NAME as superseded by NEW_NAME."""
    from ..memory.ops import supersede  # fmt: skip

    result = supersede(old_name, new_name)
    if result is None:
        click.echo(
            f"Error: could not find both '{old_name}' and '{new_name}'.", err=True
        )
        sys.exit(1)
    old, new = result
    click.echo(f"Marked '{old.name}' as superseded by '{new.name}'.")


@memory.command("audit")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def memory_audit_cmd(as_json: bool):
    """Audit memory entries: show superseded, duplicates, and missing files."""
    from ..memory.ops import list_entries  # fmt: skip
    from ..memory.roots import resolve_roots  # fmt: skip

    roots = resolve_roots()
    all_entries = list_entries(roots=roots, include_superseded=True)

    superseded = [e for e in all_entries if e.status == "superseded"]
    historical = [e for e in all_entries if e.status == "historical"]
    living = [e for e in all_entries if e.is_living()]

    report = {
        "total": len(all_entries),
        "living": len(living),
        "superseded": len(superseded),
        "historical": len(historical),
        "superseded_entries": [e.name for e in superseded],
    }

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    click.echo(f"Memory audit: {len(all_entries)} entries total")
    click.echo(f"  Living:     {len(living)}")
    click.echo(f"  Superseded: {len(superseded)}")
    click.echo(f"  Historical: {len(historical)}")
    if superseded:
        click.echo("\nSuperseded entries:")
        for e in superseded:
            click.echo(f"  {e.name} → superseded_by: {e.superseded_by or '(unknown)'}")
