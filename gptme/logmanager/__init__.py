"""Conversation log management for gptme.

Split into focused modules:
- manager: Log data structure, LogManager orchestrator, message processing
- conversations: ConversationMeta, conversation querying and management
"""

# Re-export Message for backward compatibility (tests and tools import it from here)
from ..message import Message
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
    prepare_messages,
)

__all__ = [
    # Core types
    "Log",
    "LogManager",
    "Message",
    "PathLike",
    "RoleLiteral",
    # Module-level state
    "_current_log_var",
    # Message processing
    "prepare_messages",
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
