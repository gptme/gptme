"""CLI commands for chat/conversation management."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..logmanager.conversations import ConversationMeta
from ..prompt_queue import queue_prompt
from ..tools import get_tools, init_tools
from ..tools.chats import (
    find_empty_conversations,
    list_chats,
    search_chats,
    search_external_chats,
)
from ..util.conversation_ids import is_valid_conversation_id


def _is_valid_id(id: str) -> bool:
    """Return True if id is a plausible conversation identifier.

    Rejects IDs that are too long for the filesystem (Linux NAME_MAX is 255
    UTF-8 bytes per path component) or contain path-traversal sequences.
    These would otherwise raise ``OSError: [Errno 36] File name too long``
    deep inside ``Path.exists()`` or ``os.stat()``.
    """
    return is_valid_conversation_id(id)


def _ensure_tools():
    """Lazily initialize tools only when needed."""
    if not get_tools():
        init_tools()


def _conv_to_dict(conv: ConversationMeta) -> dict:
    """Serialize a ConversationMeta to a JSON-friendly dict."""
    return {
        "id": conv.id,
        "name": conv.name,
        "path": conv.path,
        "created": datetime.fromtimestamp(conv.created, tz=timezone.utc).isoformat(),
        "modified": datetime.fromtimestamp(conv.modified, tz=timezone.utc).isoformat(),
        "messages": conv.messages,
        "branches": conv.branches,
        "workspace": conv.workspace,
        "agent_name": conv.agent_name,
        "model": conv.model,
        "total_cost": round(conv.total_cost, 4),
        "total_input_tokens": conv.total_input_tokens,
        "total_output_tokens": conv.total_output_tokens,
    }


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
    """Commands for managing chat logs and queued follow-ups."""


@chats.command("list")
@click.option(
    "-n",
    "--limit",
    default=20,
    type=click.IntRange(min=1),
    help="Maximum number of chats to show.",
)
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.option(
    "--metadata", is_flag=True, help="Show full metadata (ID, model, cost, tokens)."
)
def chats_list(limit: int, summarize: bool, output_json: bool, metadata: bool):
    """List conversation logs."""
    _ensure_tools()

    if output_json:
        from ..logmanager import list_conversations  # fmt: skip

        conversations = list_conversations(limit)
        click.echo(json.dumps([_conv_to_dict(c) for c in conversations], indent=2))
        return

    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init(
            "openai/gpt-4o",
            interactive=False,
            tool_allowlist=[],
            tool_format="markdown",
        )
    list_chats(max_results=limit, metadata=metadata, include_summary=summarize)


@chats.command("search")
@click.argument("query")
@click.option(
    "-n",
    "--limit",
    default=20,
    type=click.IntRange(min=1),
    help="Maximum number of chats to show.",
)
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.option(
    "-c", "--context", default=1, help="Lines of context to show around each match."
)
@click.option(
    "-m", "--matches", default=1, help="Maximum matches to show per conversation."
)
@click.option(
    "--all-agents",
    is_flag=True,
    help="Also search Cursor and Codex CLI sessions (reads ~/.cursor/ and ~/.codex/).",
)
def chats_search(
    query: str,
    limit: int,
    summarize: bool,
    output_json: bool,
    context: int,
    matches: int,
    all_agents: bool,
):
    """Search conversation logs."""
    if not query.strip():
        raise click.UsageError("search query cannot be empty")
    _ensure_tools()

    if output_json:
        if all_agents:
            click.echo(
                "Warning: --all-agents is not supported with --json; "
                "only gptme native sessions will be included.",
                err=True,
            )
        from ..logmanager import LogManager, list_conversations  # fmt: skip
        from ..tools.chats import _get_matching_messages  # fmt: skip

        results = []
        for conv in list_conversations(10 * limit):
            log_path = Path(conv.path)
            log_manager = LogManager.load(log_path, lock=False)
            matching = _get_matching_messages(log_manager, query)
            if matching:
                entry = _conv_to_dict(conv)
                entry["matches"] = len(matching)
                entry["snippets"] = [
                    {
                        "index": idx,
                        "role": msg.role,
                        "content": msg.content[:200],
                    }
                    for idx, msg in matching[:3]
                ]
                results.append(entry)
                if len(results) >= limit:
                    break

        click.echo(json.dumps(results, indent=2))
        return

    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init(
            "openai/gpt-4o",
            interactive=False,
            tool_allowlist=[],
            tool_format="markdown",
        )
    search_chats(query, max_results=limit, context_lines=context, max_matches=matches)
    if all_agents:
        search_external_chats(query, max_results=limit)


@chats.command("read")
@click.argument("id")
@click.option(
    "-n",
    "--limit",
    default=20,
    type=click.IntRange(min=1),
    help="Maximum number of messages to show.",
)
@click.option("--system", is_flag=True, help="Include system messages.")
@click.option(
    "-c", "--context", default=0, help="Messages of context before start message."
)
@click.option(
    "--start",
    type=int,
    default=None,
    help="Start from this message number (1-indexed).",
)
def chats_read(id: str, limit: int, system: bool, context: int, start: int | None):
    """Read a specific chat log."""
    _ensure_tools()

    from ..tools.chats import read_chat  # fmt: skip

    if (
        not _is_valid_id(id)
        or not (get_logs_dir() / id / "conversation.jsonl").exists()
    ):
        click.echo(f"Conversation '{id}' not found.")
        raise SystemExit(1)

    read_chat(
        id,
        max_results=limit,
        incl_system=system,
        context_messages=context,
        start_message=start,
    )


@chats.command("rename")
@click.argument("id")
@click.argument("name")
def chats_rename(id: str, name: str):
    """Rename a conversation's display name.

    Updates the conversation's display name without moving files.
    The conversation ID remains unchanged.
    """
    from ..logmanager import rename_conversation  # fmt: skip

    if not _is_valid_id(id) or not rename_conversation(id, name):
        print(f"Chat '{id}' not found")
        sys.exit(1)
    else:
        print(f"Renamed '{id}' to '{name}'")


@chats.command("send")
@click.argument("id")
@click.argument("message", nargs=-1, required=True)
def chats_send(id: str, message: tuple[str, ...]):
    """Queue a prompt for the next turn of a running conversation.

    This is useful when another gptme process is busy in the same chat and you
    already know the next instruction you want to send.
    """
    if not _is_valid_id(id):
        click.echo(f"Chat '{id}' not found")
        raise SystemExit(1)
    logdir = get_logs_dir() / id
    if not logdir.exists():
        click.echo(f"Chat '{id}' not found")
        raise SystemExit(1)

    content = " ".join(message).strip()
    if not content:
        raise click.UsageError("Queued message must not be empty.")

    queue_prompt(logdir, content)
    click.echo(f"Queued prompt for '{id}'")


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
@click.option(
    "--safety-check",
    is_flag=True,
    default=False,
    help="Run local heuristic safety analysis on assistant messages before exporting.",
)
@click.option(
    "--safety-json",
    is_flag=True,
    default=False,
    help="Output safety analysis as JSON (implies --safety-check, skips export).",
)
def chats_export(
    id: str, fmt: str, output: str | None, safety_check: bool, safety_json: bool
):
    """Export a conversation to HTML or markdown.

    Exports the conversation with the given ID to a file.
    Use --format to choose between HTML (self-contained) and markdown.

    Use --safety-check to run a local heuristic analysis of assistant messages
    for hedging/uncertainty signals and jailbreak bypass indicators before exporting.
    No network calls are made; the check is fully local.

    Examples:

        gptme-util chats export my-conversation

        gptme-util chats export my-conversation -f html -o chat.html

        gptme-util chats export my-conversation --safety-check

        gptme-util chats export my-conversation --safety-json
    """
    _ensure_tools()
    from ..util.export import export_chat_to_html, export_chat_to_markdown  # fmt: skip

    logdir = get_logs_dir() / id
    if not _is_valid_id(id) or not logdir.exists():
        click.echo(f"Chat '{id}' not found")
        raise SystemExit(1)

    log = LogManager.load(logdir)

    if safety_check or safety_json:
        import json as _json  # fmt: skip

        from ..util.safety import check_messages  # fmt: skip

        report = check_messages(log.log.messages, source=id)
        if safety_json:
            click.echo(_json.dumps(report.to_dict(), indent=2))
            return
        click.echo(report.to_text())
        click.echo()
        if report.flags:
            click.echo(f"⚠  Safety flags: {', '.join(report.flags)}")

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
@click.argument("id", required=False)
@click.option(
    "--since",
    default=None,
    help="Only include conversations since this date (YYYY-MM-DD or Nd for N days ago).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def chats_stats(id: str | None, since: str | None, as_json: bool):
    """Show conversation statistics.

    Without an ID, displays overview of conversation history including counts,
    date ranges, message totals, and activity breakdown.

    With an ID, shows detailed stats for one conversation including role counts,
    tool calls, token usage, and duration.
    """
    from ..tools.chats import conversation_stats  # fmt: skip

    if id and since:
        raise click.UsageError("Cannot use --since when inspecting a specific chat.")

    try:
        conversation_stats(since=since, as_json=as_json, conversation_id=id)
    except ValueError as e:
        raise click.UsageError(str(e)) from e
