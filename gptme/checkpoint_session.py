"""Durable, user-visible checkpoints for gptme conversations.

Conversation checkpoints capture enough state to hand a session off without
copying or mutating its lossless trajectory.  They complement ``/backtrack``
(message-index rewind) and workspace checkpoints (Git state recovery).
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .config import ChatConfig
from .llm.models import get_model
from .logmanager import Log
from .tools import get_available_tools, get_tools
from .util.git_cmd import git_inspect_cmd
from .util.tokens import len_tokens

SCHEMA_VERSION = 1
_CHECKPOINT_DIR = "checkpoints"
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_FILE_KEYS = ("path", "file", "filename")
_WRITE_TOOLS = frozenset({"append", "morph", "patch", "save"})
_TOOL_BLOCK_RE = re.compile(
    r"```(?P<tool>[A-Za-z0-9_.-]+)(?P<args>[^\n]*)\n(?P<content>.*?)```",
    re.DOTALL,
)
_NATIVE_TOOL_RE = re.compile(
    r"^@(?P<tool>[\w.]+)\([^)]+\):\s*(?P<payload>\{[^\n]*\})$",
    re.MULTILINE,
)

if TYPE_CHECKING:
    from .message import Message


class ConversationCheckpointError(ValueError):
    """Raised when a conversation checkpoint cannot be created or loaded."""


@dataclass(frozen=True)
class ContextBoundary:
    """Token usage at the point where a checkpoint was saved."""

    total_tokens: int
    model_limit: int | None = None
    pct_used: float | None = None


@dataclass(frozen=True)
class ToolCallSnapshot:
    """Compact description of one recent tool invocation."""

    tool: str
    description: str
    file: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class FileChange:
    """One working-tree path changed when the checkpoint was saved."""

    path: str
    action: str
    lines: str | None = None


@dataclass(frozen=True)
class ConversationCheckpoint:
    """Versioned on-disk checkpoint schema."""

    version: int
    label: str
    timestamp: str
    model: str | None
    context_boundary: ContextBoundary
    summary: str
    last_tool_calls: list[ToolCallSnapshot]
    file_changes: list[FileChange]
    message_count: int
    conversation_id: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationCheckpoint:
        """Validate and decode a checkpoint dictionary."""
        version = data.get("version")
        if version != SCHEMA_VERSION:
            raise ConversationCheckpointError(
                f"Unsupported conversation checkpoint version {version!r}; "
                f"this gptme supports version {SCHEMA_VERSION}."
            )
        try:
            label = validate_label(data["label"])
            timestamp = _required_str(data, "timestamp")
            datetime.fromisoformat(timestamp)
            model = data.get("model")
            if model is not None and not isinstance(model, str):
                raise TypeError("model must be a string or null")
            context_boundary = _decode_context_boundary(data["context_boundary"])
            tool_calls = _decode_records(
                data["last_tool_calls"], ToolCallSnapshot, "last_tool_calls"
            )
            file_changes = _decode_records(
                data["file_changes"], FileChange, "file_changes"
            )
            message_count = data["message_count"]
            if not _is_int(message_count) or message_count < 0:
                raise TypeError("message_count must be a non-negative integer")
            return cls(
                version=version,
                label=label,
                timestamp=timestamp,
                model=model,
                context_boundary=context_boundary,
                summary=_required_str(data, "summary"),
                last_tool_calls=tool_calls,
                file_changes=file_changes,
                message_count=message_count,
                conversation_id=_required_str(data, "conversation_id"),
            )
        except (KeyError, OverflowError, TypeError, ValueError) as exc:
            raise ConversationCheckpointError(
                f"Invalid conversation checkpoint: {exc}"
            ) from exc


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _decode_context_boundary(value: object) -> ContextBoundary:
    if not isinstance(value, dict):
        raise TypeError("context_boundary must be an object")
    if set(value) - {"total_tokens", "model_limit", "pct_used"}:
        raise TypeError("context_boundary has unknown fields")
    raw_total_tokens = value.get("total_tokens")
    raw_model_limit = value.get("model_limit")
    raw_pct_used = value.get("pct_used")
    if not _is_int(raw_total_tokens) or cast(int, raw_total_tokens) < 0:
        raise TypeError("context_boundary.total_tokens must be a non-negative integer")
    if raw_model_limit is not None and (
        not _is_int(raw_model_limit) or cast(int, raw_model_limit) <= 0
    ):
        raise TypeError(
            "context_boundary.model_limit must be a positive integer or null"
        )
    if raw_pct_used is not None and (
        isinstance(raw_pct_used, bool)
        or not isinstance(raw_pct_used, (int, float))
        or not math.isfinite(raw_pct_used)
        or raw_pct_used < 0
    ):
        raise TypeError("context_boundary.pct_used must be a finite number or null")
    total_tokens = cast(int, raw_total_tokens)
    model_limit = cast(int | None, raw_model_limit)
    pct_used = cast(int | float | None, raw_pct_used)
    return ContextBoundary(
        total_tokens, model_limit, float(pct_used) if pct_used is not None else None
    )


_RecordT = TypeVar("_RecordT")


def _decode_records(
    value: object, record_type: type[_RecordT], field_name: str
) -> list[_RecordT]:
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    records: list[_RecordT] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError(f"{field_name} entries must be objects")
        record = record_type(**item)
        for field in dataclasses.fields(cast(Any, record)):
            field_value = getattr(record, field.name)
            if field_value is not None and not isinstance(field_value, str):
                raise TypeError(f"{field_name}.{field.name} must be a string or null")
        records.append(record)
    return records


def validate_label(label: str) -> str:
    """Return a normalized safe filename label or raise."""
    normalized = label.strip()
    if not _LABEL_RE.fullmatch(normalized):
        raise ConversationCheckpointError(
            "Checkpoint labels must be 1-80 characters and contain only "
            "letters, numbers, '.', '_', or '-'."
        )
    return normalized


def checkpoint_dir(logdir: Path) -> Path:
    """Return the checkpoint directory for a conversation."""
    return logdir / _CHECKPOINT_DIR


def checkpoint_path(logdir: Path, label: str) -> Path:
    """Return the path for *label*, rejecting path traversal."""
    return checkpoint_dir(logdir) / f"{validate_label(label)}.json"


def load_messages(logdir: Path) -> list[Message]:
    """Load the lossless master trajectory for *logdir*."""
    conversation = logdir / "conversation.jsonl"
    if not conversation.exists():
        raise ConversationCheckpointError(
            f"Not a gptme conversation (missing conversation.jsonl): {logdir}"
        )
    return Log.read_jsonl(conversation).messages


def _model_from_messages(messages: list[Message], logdir: Path) -> str | None:
    for message in reversed(messages):
        metadata = message.metadata or {}
        if model := metadata.get("model"):
            return str(model)
    try:
        return ChatConfig.from_logdir(logdir).model
    except (OSError, ValueError):
        return None


def _context_boundary(
    messages: list[Message], model: str | None, model_limit: int | None
) -> ContextBoundary:
    token_model = model or "gpt-4"
    total_tokens = len_tokens(messages, token_model)
    if model_limit is None and model is not None:
        try:
            resolved_model = get_model(model)
        except (AssertionError, ValueError):
            resolved_model = None
        if resolved_model is not None:
            model_limit = resolved_model.context
    pct_used = total_tokens / model_limit if model_limit else None
    return ContextBoundary(
        total_tokens=total_tokens,
        model_limit=model_limit,
        pct_used=pct_used,
    )


def _tool_block_names() -> frozenset[str]:
    tools = [*get_tools(), *get_available_tools(include_mcp=False)]
    return frozenset(block_type for tool in tools for block_type in tool.block_types)


def _strip_tool_payloads(content: str) -> str:
    block_names = _tool_block_names()
    content = _TOOL_BLOCK_RE.sub(
        lambda match: (
            f"[tool call: {match.group('tool')}]"
            if match.group("tool") in block_names
            else match.group(0)
        ),
        content,
    )
    return _NATIVE_TOOL_RE.sub(
        lambda match: f"[tool call: {match.group('tool')}]", content
    )


def default_summary(messages: list[Message], max_chars: int = 4000) -> str:
    """Build a deterministic summary from the task and recent conversation tail.

    Checkpoint saving must remain useful offline.  The result deliberately avoids
    another model call; callers that already have a model-written compaction
    summary can pass it through ``summary=``. Tool payloads are replaced with
    markers because ``last_tool_calls`` carries their compact metadata separately.
    """
    visible = [message for message in messages if message.role in {"user", "assistant"}]
    if not visible:
        return "No user or assistant messages were recorded."

    first_user = next((message for message in visible if message.role == "user"), None)
    recent = visible[-6:]
    parts: list[str] = []
    if first_user is not None:
        parts.extend(
            ("Original task:", _strip_tool_payloads(first_user.content).strip())
        )
    parts.append("Recent conversation:")
    parts.extend(
        f"{message.role}: {_strip_tool_payloads(message.content).strip()}"
        for message in recent
    )
    summary = "\n\n".join(part for part in parts if part)
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 24].rstrip() + "\n\n[summary truncated]"


def _snapshot_tool_call(
    tool: str,
    *,
    args: list[str] | None = None,
    content: str | None = None,
    kwargs: dict[str, Any] | None = None,
) -> ToolCallSnapshot:
    def value(key: str) -> str | None:
        if not kwargs or key not in kwargs or kwargs[key] is None:
            return None
        return str(kwargs[key])

    file_path = next((value(key) for key in _FILE_KEYS if value(key)), None)
    if file_path is None and tool in _WRITE_TOOLS and args:
        file_path = args[0]
    command = value("command")
    if command is None and tool in {"shell", "shell_compact"}:
        command = content
    target = file_path or command
    description = tool if not target else f"{tool}: {target}"
    return ToolCallSnapshot(
        tool=tool,
        description=description[:500],
        file=file_path,
        command=command,
    )


def _tool_calls_from_content(content: str) -> list[ToolCallSnapshot]:
    calls: list[tuple[int, ToolCallSnapshot]] = []
    block_names = _tool_block_names()
    for match in _TOOL_BLOCK_RE.finditer(content):
        tool = match.group("tool")
        if tool not in block_names:
            continue
        args = match.group("args").strip().split()
        calls.append(
            (
                match.start(),
                _snapshot_tool_call(
                    tool, args=args or None, content=match.group("content").rstrip()
                ),
            )
        )
    for match in _NATIVE_TOOL_RE.finditer(content):
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            calls.append(
                (
                    match.start(),
                    _snapshot_tool_call(match.group("tool"), kwargs=payload),
                )
            )
    return [snapshot for _, snapshot in sorted(calls, key=lambda item: item[0])]


def recent_tool_calls(
    messages: list[Message], limit: int = 5
) -> list[ToolCallSnapshot]:
    """Extract recent tool calls without depending on the loaded tool registry."""
    calls: list[ToolCallSnapshot] = []
    for message in messages:
        if message.role == "assistant":
            calls.extend(_tool_calls_from_content(message.content))
    return calls[-limit:]


def _git_output(workspace: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            [*git_inspect_cmd(), *args],
            cwd=workspace,
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _decode_git_path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def working_tree_changes(workspace: Path) -> list[FileChange]:
    """Return tracked and untracked workspace changes without executing Git hooks."""
    status = _git_output(
        workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    if status is None:
        return []

    numstat = _git_output(workspace, "diff", "--numstat", "-z", "HEAD") or b""
    line_counts: dict[str, str] = {}
    for record in numstat.split(b"\0"):
        if not record:
            continue
        fields = record.split(b"\t", maxsplit=2)
        if len(fields) == 3:
            added, removed, raw_path = fields
            line_counts[_decode_git_path(raw_path)] = (
                f"+{added.decode('ascii')}/-{removed.decode('ascii')}"
            )

    changes: list[FileChange] = []
    records = status.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        code = record[:2].decode("ascii")
        path = _decode_git_path(record[3:])
        if "R" in code or "C" in code:
            if index >= len(records):
                break
            # In porcelain v1 -z, the first path is the destination and the
            # following NUL record is the source.
            index += 1
        if code == "??":
            action = "untracked"
        elif "D" in code:
            action = "deleted"
        elif "R" in code:
            action = "renamed"
        elif "A" in code:
            action = "added"
        else:
            action = "modified"
        changes.append(
            FileChange(path=path, action=action, lines=line_counts.get(path))
        )
    return changes


def save_conversation_checkpoint(
    logdir: Path,
    label: str,
    *,
    summary: str | None = None,
    model_limit: int | None = None,
    overwrite: bool = False,
    messages: list[Message] | None = None,
) -> tuple[ConversationCheckpoint, Path]:
    """Create and atomically persist a checkpoint for one conversation."""
    label = validate_label(label)
    messages = list(messages) if messages is not None else load_messages(logdir)
    model = _model_from_messages(messages, logdir)
    try:
        workspace = ChatConfig.from_logdir(logdir).workspace
    except (OSError, ValueError):
        workspace = logdir

    checkpoint = ConversationCheckpoint(
        version=SCHEMA_VERSION,
        label=label,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=model,
        context_boundary=_context_boundary(messages, model, model_limit),
        summary=summary if summary is not None else default_summary(messages),
        last_tool_calls=recent_tool_calls(messages),
        file_changes=working_tree_changes(workspace),
        message_count=len(messages),
        conversation_id=logdir.name,
    )

    path = checkpoint_path(logdir, label)
    if path.exists() and not overwrite:
        raise ConversationCheckpointError(
            f"Checkpoint {label!r} already exists; pass --overwrite to replace it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{label}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(checkpoint.to_dict(), output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise ConversationCheckpointError(
                    f"Checkpoint {label!r} already exists; "
                    "pass --overwrite to replace it."
                ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return checkpoint, path


def load_conversation_checkpoint(logdir: Path, label: str) -> ConversationCheckpoint:
    """Load and validate a named checkpoint."""
    path = checkpoint_path(logdir, label)
    if not path.exists():
        raise ConversationCheckpointError(
            f"No checkpoint named {label!r} in conversation {logdir.name!r}."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConversationCheckpointError(
            f"Could not read checkpoint {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ConversationCheckpointError(f"Invalid conversation checkpoint: {path}")
    return ConversationCheckpoint.from_dict(data)


def list_conversation_checkpoints(logdir: Path) -> list[ConversationCheckpoint]:
    """Return valid checkpoints for a conversation, newest first."""
    directory = checkpoint_dir(logdir)
    if not directory.exists():
        return []
    checkpoints: list[ConversationCheckpoint] = []
    for path in directory.glob("*.json"):
        try:
            checkpoints.append(load_conversation_checkpoint(logdir, path.stem))
        except ConversationCheckpointError:
            continue
    return sorted(checkpoints, key=lambda item: item.timestamp, reverse=True)


def checkpoint_resume_prompt(checkpoint: ConversationCheckpoint) -> str:
    """Render a durable checkpoint as a ``<<RESUMED SESSION>>`` prompt."""
    boundary = checkpoint.context_boundary
    context = f"{boundary.total_tokens:,} tokens"
    if boundary.model_limit:
        context += f" / {boundary.model_limit:,} ({(boundary.pct_used or 0):.1%})"

    parts = [
        "<<RESUMED SESSION>>",
        "",
        f"[CHECKPOINT: {checkpoint.label}]",
        f"[SOURCE CONVERSATION: {checkpoint.conversation_id}]",
        f"[SAVED: {checkpoint.timestamp}]",
        f"[MODEL: {checkpoint.model or 'unknown'}]",
        f"[CONTEXT AT SAVE: {context}]",
        "",
        "## Checkpoint summary",
        checkpoint.summary,
    ]
    if checkpoint.last_tool_calls:
        parts.extend(("", "## Recent tool calls"))
        parts.extend(f"- {call.description}" for call in checkpoint.last_tool_calls)
    if checkpoint.file_changes:
        parts.extend(("", "## Working tree changes at save time"))
        for change in checkpoint.file_changes:
            lines = f" ({change.lines})" if change.lines else ""
            parts.append(f"- {change.path}: {change.action}{lines}")
    parts.extend(
        (
            "",
            "--- End of checkpoint ---",
            "Continue from this saved state. Verify the working tree before modifying files.",
        )
    )
    return "\n".join(parts)
