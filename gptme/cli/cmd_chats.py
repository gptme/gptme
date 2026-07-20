"""CLI commands for chat/conversation management."""

import json
import shutil
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
@click.option(
    "--judge",
    is_flag=True,
    default=False,
    help=(
        "Add advisory LLM-as-Judge annotation to safety check (implies --safety-check)."
        " Uses haiku-4.5 via OpenRouter. Requires OPENROUTER_API_KEY."
        " Advisory-only — never a gate. Post-cutoff arXiv citations may produce"
        " false positives; use live-source verification for recent citations."
    ),
)
def chats_export(
    id: str,
    fmt: str,
    output: str | None,
    safety_check: bool,
    safety_json: bool,
    judge: bool,
):
    """Export a conversation to HTML or markdown.

    Exports the conversation with the given ID to a file.
    Use --format to choose between HTML (self-contained) and markdown.

    Use --safety-check to run a local heuristic analysis of assistant messages
    for hedging/uncertainty signals and jailbreak bypass indicators before exporting.
    No network calls are made; the check is fully local.

    Use --judge to additionally annotate each segment with an advisory LLM-as-Judge
    score (haiku-4.5 via OpenRouter). This requires OPENROUTER_API_KEY and makes
    network calls. The annotation is advisory-only — it is never used as a gate.

    Examples:

        gptme-util chats export my-conversation

        gptme-util chats export my-conversation -f html -o chat.html

        gptme-util chats export my-conversation --safety-check

        gptme-util chats export my-conversation --safety-json

        gptme-util chats export my-conversation --judge
    """
    _ensure_tools()
    from ..util.export import export_chat_to_html, export_chat_to_markdown  # fmt: skip

    logdir = get_logs_dir() / id
    if not _is_valid_id(id) or not logdir.exists():
        click.echo(f"Chat '{id}' not found")
        raise SystemExit(1)

    log = LogManager.load(logdir)

    if safety_check or safety_json or judge:
        import json as _json  # fmt: skip

        from ..util.safety import CALIBRATED_JUDGE_MODEL, check_messages  # fmt: skip

        judge_model = CALIBRATED_JUDGE_MODEL if judge else None
        report = check_messages(log.log.messages, source=id, judge_model=judge_model)
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


def _slice_at_turn(messages: list, turn: int) -> list:
    """Return messages up to the end of the Nth user turn (1-indexed).

    Turn 0: system/pre-user messages only (no user input included).
    Turn N: through the Nth complete user+assistant exchange.

    If N exceeds the number of turns in the conversation, all messages
    are returned (no truncation).
    """
    if turn < 0:
        raise ValueError(f"Turn must be non-negative, got {turn}")

    if turn == 0:
        result = []
        for msg in messages:
            if msg.role == "user":
                break
            result.append(msg)
        return result

    user_count = 0
    for i, msg in enumerate(messages):
        if msg.role == "user":
            user_count += 1
            if user_count == turn:
                # Include all messages through the rest of this exchange
                # (up to but not including the next user message)
                for j in range(i + 1, len(messages)):
                    if messages[j].role == "user":
                        return list(messages[:j])
                # This was the last user turn — include all remaining
                return list(messages)

    # Requested turn exceeds available turns — include all messages
    return list(messages)


@chats.command("fork")
@click.argument("id")
@click.option(
    "--at-turn",
    "at_turn",
    required=True,
    type=click.IntRange(min=0),
    metavar="N",
    help="Fork at turn N. Turn 0 = system context only; turn N = through Nth user+assistant exchange.",
)
@click.option(
    "--name",
    "fork_name",
    default=None,
    metavar="NAME",
    help="Name for the forked session. Defaults to '<source>-fork-<timestamp>'.",
)
def chats_fork(id: str, at_turn: int, fork_name: str | None):
    """Fork a session at a specific turn into a new session.

    Creates a new session containing only the first N user turns from the
    source session. The source session is never modified.

    Turn 0 keeps only pre-user messages (e.g. system prompt).
    Turn N includes through the Nth complete user+assistant exchange.

    Examples:

    \b
        # Fork at turn 2
        gptme-util chats fork my-session --at-turn 2

    \b
        # Fork at turn 5 with a custom name
        gptme-util chats fork my-session --at-turn 5 --name retry-v2
    """
    from datetime import datetime, timezone  # fmt: skip

    from ..logmanager import conversation_name_error  # fmt: skip
    from ..logmanager.manager import Log  # fmt: skip

    if not _is_valid_id(id):
        raise click.UsageError(f"Invalid conversation ID: {id!r}")

    logs_dir = get_logs_dir()
    source_logdir = logs_dir / id
    source_logfile = source_logdir / "conversation.jsonl"

    if not source_logdir.exists():
        raise click.UsageError(f"Session not found: {id!r}")
    if not source_logfile.exists():
        raise click.UsageError(f"Session has no conversation: {id!r}")

    source_msgs = list(Log.read_jsonl(source_logfile).messages)

    user_turn_count = sum(1 for m in source_msgs if m.role == "user")
    if at_turn > user_turn_count:
        raise click.UsageError(
            f"Turn {at_turn} out of range — session '{id}' has {user_turn_count} user turn(s) "
            f"(valid range: 0–{user_turn_count})"
        )

    sliced = _slice_at_turn(source_msgs, at_turn)

    if fork_name:
        new_name = fork_name
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        new_name = f"{id}-fork-{ts}"

    name_err = conversation_name_error(new_name)
    if name_err:
        raise click.UsageError(name_err)

    new_logdir = logs_dir / new_name
    try:
        new_logdir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise click.UsageError(
            f"Session name '{new_name}' already exists. Choose a different name with --name."
        ) from None

    try:
        Log(sliced).write_jsonl(new_logdir / "conversation.jsonl")

        for subdir in ("files", "attachments"):
            src = source_logdir / subdir
            if src.exists():
                shutil.copytree(src, new_logdir / subdir, symlinks=True)
    except Exception:
        shutil.rmtree(new_logdir, ignore_errors=True)
        raise

    click.echo(
        f"Forked '{id}' at turn {at_turn} → '{new_name}' ({len(sliced)} messages kept)"
    )


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
