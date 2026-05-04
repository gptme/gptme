"""Conversation log management for gptme.

Split into focused modules:
- manager: Log data structure, LogManager orchestrator, message processing
- conversations: ConversationMeta, conversation querying and management
"""

from .conversations import (
    ConversationMeta,
    delete_conversation,
    get_conversation_by_id,
    get_conversations,
    get_user_conversations,
    list_conversations,
    rename_conversation,
)
from .manager import (
    Log,
    LogManager,
    PathLike,
    RoleLiteral,
    _current_log_var,
    _gen_read_jsonl,
    check_for_modifications,
    ephemeral_cache_boundary,
    prepare_messages,
    prune_ephemeral_messages,
)

__all__ = [
    # Core types
    "Log",
    "LogManager",
    "PathLike",
    "RoleLiteral",
    # Module-level state
    "_current_log_var",
    # Message processing
    "prepare_messages",
    "prune_ephemeral_messages",
    "ephemeral_cache_boundary",
    "check_for_modifications",
    "_gen_read_jsonl",
    # Conversation management
    "ConversationMeta",
    "get_conversations",
    "get_user_conversations",
    "list_conversations",
    "get_conversation_by_id",
    "rename_conversation",
    "delete_conversation",
]
