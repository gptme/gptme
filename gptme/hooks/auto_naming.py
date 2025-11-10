"""
Auto-naming hook that generates conversation names after the first assistant response.

This hook automatically generates a display name for conversations after the first
assistant message is received. The name is generated using the auto-naming utility
which can use either LLM-based naming or random name generation.

Note: This hook stores the generated name in a context variable that can be accessed
by the server to send config change events to clients.
"""

import logging
from collections.abc import Generator
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ..config import ChatConfig
from ..message import Message
from ..util.auto_naming import auto_generate_display_name
from . import HookType, StopPropagation, register_hook

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

# Context variable to track if name was generated (for server notification)
_name_generated: ContextVar[bool] = ContextVar("auto_naming_name_generated", default=False)


def register() -> None:
    """Setup function to register the auto-naming hook."""
    register_hook(
        "auto_naming",
        HookType.MESSAGE_POST_PROCESS,
        auto_name_on_first_assistant_message,
        priority=5,  # Medium-low priority to run after validation hooks
    )


def was_name_generated() -> bool:
    """Check if a name was generated in this context."""
    return _name_generated.get()


def reset_name_generated() -> None:
    """Reset the name generated flag."""
    _name_generated.set(False)


def auto_name_on_first_assistant_message(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook that auto-generates conversation name after first assistant response.

    This hook runs after a message is processed and checks if:
    1. This is the first assistant message
    2. No name is already set for the conversation
    3. A model is available for LLM-based naming

    If all conditions are met, it generates a display name and saves it to the config.

    Args:
        manager: The log manager containing conversation history

    Yields:
        None (hook performs side effects but doesn't add messages)
    """
    # Reset flag
    _name_generated.set(False)

    # Get the last message
    if not manager.log.messages:
        return

    # Check if we have exactly one assistant message
    assistant_messages = [m for m in manager.log.messages if m.role == "assistant"]
    if len(assistant_messages) != 1:
        return

    # Load chat config
    chat_config = ChatConfig.from_logdir(manager.logdir)

    # Only run if name is not already set
    if chat_config.name:
        return

    # Get model from chat config
    model = chat_config.model

    # Generate and save the display name
    try:
        display_name = auto_generate_display_name(manager.log.messages, model)
        if display_name:
            chat_config.name = display_name
            chat_config.save()
            _name_generated.set(True)
            logger.info(f"Auto-generated conversation name: {display_name}")
        else:
            logger.info(
                "Auto-naming failed, leaving conversation name unset for future retry"
            )
    except Exception as e:
        logger.warning(f"Failed to auto-generate display name: {e}")
    # Don't yield any messages - this hook only performs side effects
    yield from []
    yield  # Make this a generator function
