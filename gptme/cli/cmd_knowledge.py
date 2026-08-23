"""CLI commands for the cross-session knowledge base (``gptme-util knowledge``)."""

from __future__ import annotations

import json

import click

from ..knowledge import (
    KnowledgeValidationError,
    load_entries,
    save_entry,
    search_entries,
)


@click.group()
def knowledge() -> None:
    """Save and search your personal cross-session knowledge base."""


@knowledge.command("save")
@click.option(
    "--problem", required=True, help="Concise problem statement (max 200 chars)."
)
@click.option("--resolution", required=True, help="Step-by-step fix or workaround.")
@click.option(
    "--context", default="", help="Longer context (max 2000 chars, optional)."
)
@click.option("--tag", "problem_tags", multiple=True, help="Machine-readable tag.")
@click.option("--keyword", "keywords", multiple=True, help="Search hint keyword.")
@click.option("--session-id", default="", help="gptme session that resolved this.")
@click.option("--model", default="", help="Model that made the fix.")
def save_cmd(
    problem: str,
    resolution: str,
    context: str,
    problem_tags: tuple[str, ...],
    keywords: tuple[str, ...],
    session_id: str,
    model: str,
) -> None:
    """Save a resolved problem to the knowledge base."""
    try:
        entry = save_entry(
            problem=problem,
            resolution=resolution,
            context=context,
            problem_tags=list(problem_tags),
            keywords=list(keywords),
            session_id=session_id,
            model=model,
        )
    except KnowledgeValidationError as e:
        raise click.UsageError(str(e)) from e
    click.echo(f"Saved knowledge entry {entry.id}")
    click.echo(f"  {entry.problem}")


@knowledge.command("search")
@click.argument("query")
@click.option(
    "-k",
    "--top-k",
    default=5,
    type=click.IntRange(min=1, max=50),
    help="Number of results to return.",
)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON array.")
def search_cmd(query: str, top_k: int, output_json: bool) -> None:
    """Search the knowledge base for relevant entries."""
    results = search_entries(query, top_k=top_k)

    if output_json:
        click.echo(json.dumps([e.__dict__ for e in results], indent=2))
        return

    if not results:
        click.echo("No knowledge entries match that query.")
        return

    for i, entry in enumerate(results, 1):
        click.echo(f"[{i}] {entry.problem}")
        if entry.problem_tags:
            click.echo(f"    tags: {', '.join(entry.problem_tags)}")
        click.echo(f"    {entry.resolution}")


@knowledge.command("list")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON array.")
def list_cmd(output_json: bool) -> None:
    """List all entries in the knowledge base."""
    entries = load_entries()
    if output_json:
        click.echo(json.dumps([e.__dict__ for e in entries], indent=2))
        return
    if not entries:
        click.echo("Knowledge base is empty.")
        return
    for entry in entries:
        click.echo(f"{entry.id[:8]}  {entry.problem}")
