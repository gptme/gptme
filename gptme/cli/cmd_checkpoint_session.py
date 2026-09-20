"""``gptme-util checkpoint-session`` — durable conversation save points."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from ..checkpoint_session import (
    ConversationCheckpointError,
    checkpoint_resume_prompt,
    list_conversation_checkpoints,
    load_conversation_checkpoint,
    save_conversation_checkpoint,
)
from ..dirs import get_logs_dir
from .cmd_resume import _list_sessions


def _resolve_session(session: str | None) -> Path:
    """Resolve an explicit conversation ID/path or select the active/latest one."""
    if session is None:
        active = os.environ.get("GPTME_LOGDIR")
        if active and (Path(active).expanduser() / "conversation.jsonl").exists():
            candidate = Path(active).expanduser()
        else:
            sessions = _list_sessions(get_logs_dir(), n=1)
            if not sessions:
                raise click.ClickException("No gptme conversations found.")
            candidate = sessions[0]
    else:
        candidate = Path(session).expanduser()
        if not candidate.is_absolute():
            candidate = get_logs_dir() / candidate

    if not (candidate / "conversation.jsonl").exists():
        raise click.ClickException(
            f"Not a gptme conversation (missing conversation.jsonl): {candidate}"
        )
    return candidate


@click.group("checkpoint-session")
def checkpoint_session() -> None:
    """Save, list, and resume durable conversation checkpoints.

    This is separate from ``gptme-checkpoint``, which records Git workspace
    state, and ``/backtrack``, which rewinds the current conversation.
    """


@checkpoint_session.command("save")
@click.argument("label")
@click.option(
    "--session",
    metavar="ID_OR_DIR",
    help="Conversation ID or directory (default: active or latest conversation).",
)
@click.option(
    "--summary",
    help="Checkpoint summary (default: deterministic task + recent-message summary).",
)
@click.option(
    "--model-limit",
    type=click.IntRange(min=1),
    help="Model context limit used to record percent consumed.",
)
@click.option("--overwrite", is_flag=True, help="Replace a checkpoint with this label.")
def checkpoint_save(
    label: str,
    session: str | None,
    summary: str | None,
    model_limit: int | None,
    overwrite: bool,
) -> None:
    """Save LABEL for a conversation."""
    logdir = _resolve_session(session)
    try:
        checkpoint, path = save_conversation_checkpoint(
            logdir,
            label,
            summary=summary,
            model_limit=model_limit,
            overwrite=overwrite,
        )
    except ConversationCheckpointError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Saved checkpoint {checkpoint.label!r} for {checkpoint.conversation_id} "
        f"({checkpoint.message_count} messages)\n{path}"
    )


@checkpoint_session.command("list")
@click.option(
    "--session",
    metavar="ID_OR_DIR",
    help="Conversation ID or directory (default: active or latest conversation).",
)
@click.option("--json", "as_json", is_flag=True, help="Output checkpoint JSON.")
def checkpoint_list(session: str | None, as_json: bool) -> None:
    """List checkpoints for a conversation."""
    logdir = _resolve_session(session)
    try:
        checkpoints = list_conversation_checkpoints(logdir)
    except ConversationCheckpointError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps([item.to_dict() for item in checkpoints], indent=2))
        return
    if not checkpoints:
        click.echo(f"No checkpoints for {logdir.name}.")
        return
    click.echo(f"Conversation checkpoints for {logdir.name}:")
    for checkpoint in checkpoints:
        click.echo(
            f"  {checkpoint.label:<24} {checkpoint.timestamp}  "
            f"{checkpoint.message_count} messages  "
            f"{checkpoint.context_boundary.total_tokens:,} tokens"
        )


@checkpoint_session.command("resume")
@click.argument("label")
@click.option(
    "--session",
    metavar="ID_OR_DIR",
    help="Conversation ID or directory (default: active or latest conversation).",
)
def checkpoint_resume(label: str, session: str | None) -> None:
    """Print a ``<<RESUMED SESSION>>`` prompt for LABEL."""
    logdir = _resolve_session(session)
    try:
        checkpoint = load_conversation_checkpoint(logdir, label)
    except ConversationCheckpointError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(checkpoint_resume_prompt(checkpoint))
