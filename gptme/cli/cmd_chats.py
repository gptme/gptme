"""CLI commands for chat/conversation management."""

import sys
from pathlib import Path

import click

from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..message import Message
from ..tools import get_tools, init_tools
from ..tools.chats import find_empty_conversations, list_chats, search_chats


def _ensure_tools():
    """Lazily initialize tools only when needed."""
    if not get_tools():
        init_tools()


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


@click.group()
def chats():
    """Commands for managing chat logs."""


@chats.command("list")
@click.option("-n", "--limit", default=20, help="Maximum number of chats to show.")
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
def chats_list(limit: int, summarize: bool):
    """List conversation logs."""
    _ensure_tools()
    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init(
            "openai/gpt-4o",
            interactive=False,
            tool_allowlist=[],
            tool_format="markdown",
        )
    list_chats(max_results=limit, include_summary=summarize)


@chats.command("search")
@click.argument("query")
@click.option("-n", "--limit", default=20, help="Maximum number of chats to show.")
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
def chats_search(query: str, limit: int, summarize: bool):
    """Search conversation logs."""
    _ensure_tools()
    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init(
            "openai/gpt-4o",
            interactive=False,
            tool_allowlist=[],
            tool_format="markdown",
        )
    search_chats(query, max_results=limit)


@chats.command("read")
@click.argument("id")
def chats_read(id: str):
    """Read a specific chat log."""
    _ensure_tools()

    logdir = get_logs_dir() / id
    if not logdir.exists():
        print(f"Chat '{id}' not found")
        return

    log = LogManager.load(logdir)
    for msg in log.log:
        if isinstance(msg, Message):
            print(f"{msg.role}: {msg.content}")


@chats.command("rename")
@click.argument("id")
@click.argument("name")
def chats_rename(id: str, name: str):
    """Rename a conversation's display name.

    Updates the conversation's display name without moving files.
    The conversation ID remains unchanged.
    """
    from ..logmanager import rename_conversation  # fmt: skip

    if rename_conversation(id, name):
        print(f"Renamed '{id}' to '{name}'")
    else:
        print(f"Chat '{id}' not found")
        sys.exit(1)


@chats.command("export")
@click.argument("id")
@click.option(
    "-f",
    "--format",
    "fmt",
    type=click.Choice(["html", "markdown"]),
    default="markdown",
    help="Export format (default: markdown).",
)
@click.option(
    "-o", "--output", type=click.Path(), default=None, help="Output file path."
)
def chats_export(id: str, fmt: str, output: str | None):
    """Export a conversation to HTML or markdown.

    Exports the conversation with the given ID to a file.
    Use --format to choose between HTML (self-contained) and markdown.

    Examples:

        gptme-util chats export my-conversation

        gptme-util chats export my-conversation -f html -o chat.html
    """
    _ensure_tools()
    from ..util.export import export_chat_to_html, export_chat_to_markdown  # fmt: skip

    logdir = get_logs_dir() / id
    if not logdir.exists():
        click.echo(f"Chat '{id}' not found")
        raise SystemExit(1)

    log = LogManager.load(logdir)

    ext = "html" if fmt == "html" else "md"
    output_path = Path(output) if output else Path(f"{id}.{ext}")

    if fmt == "html":
        export_chat_to_html(id, log.log, output_path)
    else:
        export_chat_to_markdown(id, log.log, output_path)

    click.echo(f"Exported conversation to {output_path}")


@chats.command("clean")
@click.option(
    "-n",
    "--max-messages",
    default=1,
    help="Treat conversations with at most N messages as empty (default: 1).",
)
@click.option(
    "--include-test",
    is_flag=True,
    help="Include test/eval conversations in scan.",
)
@click.option(
    "--delete",
    is_flag=True,
    help="Actually delete empty conversations (default is dry-run).",
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def chats_clean(max_messages: int, include_test: bool, delete: bool, json_output: bool):
    """Find and remove empty or trivial conversations.

    By default, lists conversations with 0-1 messages (dry-run).
    Use --delete to actually remove them.

    \b
    Examples:
        gptme-util chats clean                  # List empty conversations
        gptme-util chats clean -n 2             # Include conversations with <=2 messages
        gptme-util chats clean --delete         # Delete empty conversations
        gptme-util chats clean --include-test   # Include test/eval conversations
    """
    from ..logmanager import delete_conversation  # fmt: skip

    _ensure_tools()

    results = find_empty_conversations(
        max_messages=max_messages,
        include_test=include_test,
    )

    if not results:
        if json_output:
            import json

            click.echo(
                json.dumps(
                    {
                        "found": 0,
                        "deleted": 0,
                        "freed_bytes": 0,
                        "total_bytes": 0,
                        "conversations": [],
                    }
                )
            )
        else:
            click.echo("No empty conversations found.")
        return

    total_size = sum(r["size_bytes"] for r in results)

    if json_output:
        import json

        deleted_count = 0
        freed_bytes = 0
        if delete:
            for r in results:
                try:
                    if delete_conversation(r["conversation"].id):
                        deleted_count += 1
                        freed_bytes += r["size_bytes"]
                except PermissionError as e:
                    click.echo(
                        f"Warning: could not delete {r['conversation'].id}: {e}",
                        err=True,
                    )

        output = {
            "found": len(results),
            "deleted": deleted_count,
            "freed_bytes": freed_bytes,
            "total_bytes": total_size,
            "conversations": [
                {
                    "id": r["conversation"].id,
                    "name": r["conversation"].name,
                    "messages": r["conversation"].messages,
                    "size_bytes": r["size_bytes"],
                }
                for r in results
            ],
        }
        click.echo(json.dumps(output, indent=2))
        return

    click.echo(
        f"Found {len(results)} conversation(s) with <={max_messages} messages "
        f"({_format_size(total_size)} total):\n"
    )

    for r in results:
        conv = r["conversation"]
        size = _format_size(r["size_bytes"])
        click.echo(f"  {conv.id}  {conv.messages} msg  {size}")

    if delete:
        click.echo()
        deleted = 0
        freed_bytes = 0
        for r in results:
            conv_id = r["conversation"].id
            try:
                if delete_conversation(conv_id):
                    deleted += 1
                    freed_bytes += r["size_bytes"]
            except PermissionError as e:
                click.echo(f"Warning: could not delete {conv_id}: {e}", err=True)

        click.echo(
            f"Deleted {deleted} conversation(s), freed {_format_size(freed_bytes)}."
        )
    else:
        click.echo("\nDry run. Use --delete to remove these conversations.")


@chats.command("stats")
@click.option(
    "--since",
    default=None,
    help="Only include conversations since this date (YYYY-MM-DD or Nd for N days ago).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def chats_stats(since: str | None, as_json: bool):
    """Show conversation statistics.

    Displays overview of conversation history including counts,
    date ranges, message totals, and activity breakdown.
    """
    from ..tools.chats import conversation_stats  # fmt: skip

    try:
        conversation_stats(since=since, as_json=as_json)
    except ValueError as e:
        raise click.UsageError(str(e)) from e
