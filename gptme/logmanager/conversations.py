"""Conversation metadata, querying, and management.

Provides read-only conversation discovery (get_conversations, list_conversations)
and mutation operations (rename, delete) that work on persisted conversation logs.
"""

import json
import logging
import re
import shutil
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

import tomlkit

from ..config import ChatConfig, get_project_config
from ..dirs import get_logs_dir
from .manager import Log

logger = logging.getLogger(__name__)


def _conversation_files() -> list[Path]:
    # NOTE: only returns the main conversation, not branches (to avoid duplicates)
    # returns the conversation files sorted by modified time (newest first)
    logsdir = get_logs_dir()
    return sorted(
        logsdir.glob("*/conversation.jsonl"), key=lambda f: -f.stat().st_mtime
    )


@dataclass(frozen=True)
class ConversationMeta:
    """Metadata about a conversation."""

    id: str
    name: str
    path: str
    created: float
    modified: float
    messages: int
    branches: int
    workspace: str
    agent_name: str | None = None
    agent_path: str | None = None
    agent_avatar: str | None = None
    agent_urls: dict[str, str] | None = None
    model: str | None = None
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    last_message_role: str | None = None
    last_message_preview: str | None = None

    def format(self, metadata=False) -> str:
        """Format conversation metadata for display."""
        output = f"{self.name} (id: {self.id})"
        if metadata:
            output += f"\nMessages: {self.messages}"
            output += (
                f"\nCreated:  {datetime.fromtimestamp(self.created, tz=timezone.utc)}"
            )
            output += (
                f"\nModified: {datetime.fromtimestamp(self.modified, tz=timezone.utc)}"
            )
            if self.branches > 1:
                output += f"\n({self.branches} branches)"
        return output


def _parse_preview(last_msg_line: bytes) -> tuple[str | None, str | None]:
    """Parse a JSONL line into (role, preview) for display."""
    try:
        msg = json.loads(last_msg_line)
        role = msg.get("role")
        if role in ("user", "assistant"):
            content = msg.get("content", "")
            if content:
                # Strip <think>/<thinking> tags and their content
                content = re.sub(
                    r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>",
                    "",
                    content,
                )
                # Also strip unclosed opening tags and any trailing content
                content = re.sub(r"<think(?:ing)?>[\s\S]*$", "", content)
                content = content.strip()
            if content:
                # Collapse whitespace first, then truncate to 100 chars
                collapsed = " ".join(content.split())
                if len(collapsed) > 100:
                    return role, collapsed[:100] + "..."
                return role, collapsed
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return None, None


# Tail size for fast metadata extraction.
# 8KB covers ~20-40 chat messages, enough to find last user/assistant msg + model.
_TAIL_BYTES = 8192


def _fast_scan_tail(
    conv_fn: Path, file_size: int
) -> tuple[int, str | None, bytes | None]:
    """Read only the tail of a JSONL file to extract preview and model.

    For the message count, does a fast newline count over the full file
    (much cheaper than JSON-parsing every line).

    Returns (message_count, model, last_user_or_assistant_line).
    """
    # Fast line count: count non-empty lines without JSON parsing.
    # The gain is from avoiding json.loads() on every metadata line;
    # the full file is still read for the line count (I/O unchanged).
    len_msgs = 0
    with open(conv_fn, "rb") as f:
        for line in f:
            if line.strip():
                len_msgs += 1

    # Read tail for preview + model
    last_msg_line: bytes | None = None
    conv_model: str | None = None
    with open(conv_fn, "rb") as f:
        if file_size > _TAIL_BYTES:
            f.seek(file_size - _TAIL_BYTES)
            f.readline()  # skip partial first line
        tail_lines = f.readlines()

    # Scan tail lines in reverse for last user/assistant message + model
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        if last_msg_line is None and (b'"user"' in line or b'"assistant"' in line):
            last_msg_line = line
        if conv_model is None and b'"metadata"' in line:
            try:
                msg = json.loads(line)
                meta = msg.get("metadata")
                if meta and meta.get("model"):
                    conv_model = meta["model"]
            except (json.JSONDecodeError, TypeError):
                pass
        if last_msg_line is not None and conv_model is not None:
            break

    return len_msgs, conv_model, last_msg_line


def _full_scan(
    conv_fn: Path,
) -> tuple[int, str | None, float, int, int, int, bytes | None]:
    """Full JSONL scan: counts messages and accumulates cost/token metadata.

    Both scan modes return the most recently used model (last model wins),
    which is more useful for display than the first model used.

    Returns (len_msgs, model, cost, input_tokens, output_tokens,
             cache_read_tokens, last_user_or_assistant_line).
    """
    len_msgs = 0
    conv_model: str | None = None
    conv_cost = 0.0
    conv_input_tokens = 0
    conv_output_tokens = 0
    conv_cache_read_tokens = 0
    last_msg_line: bytes | None = None
    with open(conv_fn, "rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            len_msgs += 1
            if b'"user"' in line or b'"assistant"' in line:
                last_msg_line = line
            if b'"metadata"' in line:
                try:
                    msg = json.loads(line)
                    meta = msg.get("metadata")
                    if meta:
                        if meta.get("model"):
                            conv_model = meta["model"]
                        conv_cost += meta.get("cost", 0) or 0
                        usage = meta.get("usage", {})
                        src = usage or meta
                        cache_read = src.get("cache_read_tokens", 0) or 0
                        conv_input_tokens += (
                            (src.get("input_tokens", 0) or 0)
                            + cache_read
                            + (src.get("cache_creation_tokens", 0) or 0)
                        )
                        conv_output_tokens += src.get("output_tokens", 0) or 0
                        conv_cache_read_tokens += cache_read
                except (json.JSONDecodeError, TypeError):
                    pass
    return (
        len_msgs,
        conv_model,
        conv_cost,
        conv_input_tokens,
        conv_output_tokens,
        conv_cache_read_tokens,
        last_msg_line,
    )


def get_conversations(
    *, detail: bool = True
) -> Generator[ConversationMeta, None, None]:
    """Returns all conversations, excluding ones used for testing, evals, etc.

    Args:
        detail: If True (default), performs a full JSONL scan to compute exact
            costs, token counts, and model info. If False, reads only the tail
            of each file for a faster scan — suitable for list/search endpoints
            where cost/token aggregates are not displayed.
    """
    for conv_fn in _conversation_files():
        log = Log.read_jsonl(conv_fn, limit=1)

        file_size = conv_fn.stat().st_size

        if detail or file_size <= _TAIL_BYTES:
            # Full scan: exact counts + cost/token aggregation
            (
                len_msgs,
                conv_model,
                conv_cost,
                conv_input_tokens,
                conv_output_tokens,
                conv_cache_read_tokens,
                last_msg_line,
            ) = _full_scan(conv_fn)
        else:
            # Fast scan: tail-only for preview + model, fast line count
            len_msgs, conv_model, last_msg_line = _fast_scan_tail(conv_fn, file_size)
            conv_cost = 0.0
            conv_input_tokens = 0
            conv_output_tokens = 0
            conv_cache_read_tokens = 0

        last_msg_role, last_msg_preview = (
            _parse_preview(last_msg_line) if last_msg_line else (None, None)
        )

        assert len(log) <= 1
        modified = conv_fn.stat().st_mtime
        first_timestamp = log[0].timestamp.timestamp() if log else modified
        # Try to get display name from ChatConfig, fallback to folder name
        conv_id = conv_fn.parent.name
        chat_config = ChatConfig.from_logdir(conv_fn.parent)
        display_name = chat_config.name or conv_id

        agent_path = chat_config.agent
        agent_project_config = (
            get_project_config(agent_path, quiet=True) if agent_path else None
        )
        agent_name = (
            agent_project_config.agent.name
            if agent_project_config and agent_project_config.agent
            else None
        )
        agent_avatar = (
            agent_project_config.agent.avatar
            if agent_project_config and agent_project_config.agent
            else None
        )
        agent_urls = (
            agent_project_config.agent.urls
            if agent_project_config and agent_project_config.agent
            else None
        )

        yield ConversationMeta(
            id=conv_id,
            name=display_name,
            path=str(conv_fn),
            created=first_timestamp,
            modified=modified,
            messages=len_msgs,
            branches=1 + len(list(conv_fn.parent.glob("branches/*.jsonl"))),
            workspace=str(chat_config.workspace),
            agent_name=agent_name,
            agent_path=str(agent_path) if agent_path else None,
            agent_avatar=agent_avatar,
            agent_urls=agent_urls,
            model=conv_model,
            total_cost=conv_cost,
            total_input_tokens=conv_input_tokens,
            total_output_tokens=conv_output_tokens,
            total_cache_read_tokens=conv_cache_read_tokens,
            last_message_role=last_msg_role,
            last_message_preview=last_msg_preview,
        )


def get_user_conversations(
    *, detail: bool = True
) -> Generator[ConversationMeta, None, None]:
    """Returns all user conversations, excluding ones used for testing, evals, etc."""
    for conv in get_conversations(detail=detail):
        if any(conv.id.startswith(prefix) for prefix in ["tmp", "test-"]) or any(
            substr in conv.id for substr in ["gptme-evals-"]
        ):
            continue
        yield conv


def list_conversations(
    limit: int = 20,
    include_test: bool = False,
    *,
    detail: bool = True,
) -> list[ConversationMeta]:
    """
    List conversations with a limit.

    Args:
        limit: Maximum number of conversations to return
        include_test: Whether to include test conversations
        detail: If True, performs full JSONL scan for costs/tokens.
            If False, uses fast tail-only scan.
    """
    conversation_iter = (
        get_conversations(detail=detail)
        if include_test
        else get_user_conversations(detail=detail)
    )
    return list(islice(conversation_iter, limit))


def get_conversation_by_id(conv_id: str) -> ConversationMeta | None:
    """
    Get a conversation by its ID.

    Args:
        conv_id: The conversation ID to find

    Returns:
        ConversationMeta if found, None otherwise
    """
    for conv in get_conversations():
        if conv.id == conv_id:
            return conv
    return None


def rename_conversation(conv_id: str, new_name: str) -> bool:
    """
    Rename a conversation by updating its display name in the chat config.

    Args:
        conv_id: The conversation ID to rename
        new_name: The new display name for the conversation

    Returns:
        True if renamed successfully, False if not found
    """
    conv = get_conversation_by_id(conv_id)
    if conv is None:
        return False

    conv_path = Path(conv.path)
    conv_dir = conv_path.parent
    config_path = conv_dir / "config.toml"

    # Load existing config or create fresh — update only the name field.
    # We avoid ChatConfig.save() here because it also manages the workspace
    # symlink, which would create an unintended symlink pointing to cwd for
    # conversations that have no pre-existing workspace configuration.
    if config_path.exists():
        with open(config_path) as f:
            config_data = tomlkit.load(f)
    else:
        config_data = tomlkit.document()

    if "chat" not in config_data:
        config_data.add("chat", tomlkit.table())
    chat_section = config_data["chat"]
    assert isinstance(chat_section, dict)
    chat_section["name"] = new_name

    with open(config_path, "w") as f:
        tomlkit.dump(config_data, f)

    return True


def delete_conversation(conv_id: str) -> bool:
    """
    Delete a conversation by its ID.

    Args:
        conv_id: The conversation ID to delete

    Returns:
        True if deleted successfully, False if not found

    Raises:
        PermissionError: If the conversation directory cannot be deleted
    """
    conv = get_conversation_by_id(conv_id)
    if conv is None:
        return False

    # Get the conversation directory (parent of conversation.jsonl)
    conv_path = Path(conv.path)
    conv_dir = conv_path.parent

    # Delete the entire conversation directory
    shutil.rmtree(conv_dir)
    return True
