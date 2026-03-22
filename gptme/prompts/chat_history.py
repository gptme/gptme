import logging
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_config
from ..message import Message
from ..util.content import extract_content_summary

logger = logging.getLogger(__name__)


def use_chat_history_context() -> bool:
    """Check if cross-conversation context is enabled."""
    config = get_config()
    flag: str = config.get_env("GPTME_CHAT_HISTORY", "")  # type: ignore[assignment]
    return flag.lower() in ("1", "true", "yes")


def prompt_chat_history() -> Generator[Message, None, None]:
    """
    Generate cross-conversation context from recent conversations.

    Provides continuity by including key information from recent conversations,
    helping the assistant understand context across conversation boundaries.
    """
    if not use_chat_history_context():
        return

    try:
        from ..logmanager import LogManager, list_conversations  # fmt: skip

        # Get recent conversations (we'll filter further)
        recent_conversations = list_conversations(limit=20, include_test=False)

        if not recent_conversations:
            return

        # Filter out very short conversations (likely tests or brief interactions)
        substantial_conversations = [
            conv
            for conv in recent_conversations
            if conv.messages >= 4  # At least 2 exchanges
        ]

        if not substantial_conversations:
            return

        # Take the 5 most recent substantial conversations
        conversations_to_summarize = substantial_conversations[:5]

        context_parts = []

        for _, conv in enumerate(conversations_to_summarize, 1):
            try:
                # Load the conversation
                log_manager = LogManager.load(Path(conv.path).parent, lock=False)
                messages = log_manager.log.messages

                # Extract key messages: first few user messages and last assistant message
                user_messages = [msg for msg in messages if msg.role == "user"]
                assistant_messages = [
                    msg for msg in messages if msg.role == "assistant"
                ]

                if not user_messages:
                    continue

                # Find the best assistant message to use as "last response"
                best_assistant_msg = None
                for msg in reversed(assistant_messages):
                    content = extract_content_summary(msg.content)
                    if content and len(content.split()) >= 10:  # At least 10 words
                        best_assistant_msg = msg
                        break

                # Create a concise summary
                summary_parts = []

                # Add conversation metadata
                summary_parts.append(f"## {conv.name}")
                summary_parts.append(
                    f"Modified: {datetime.fromtimestamp(conv.modified, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}"
                )

                # Always show first exchange to establish context
                first_user = user_messages[0]
                first_user_content = extract_content_summary(first_user.content)
                if first_user_content:
                    summary_parts.append(f"User: {first_user_content}")

                    # Find first assistant response after first user message
                    first_assistant = None
                    first_user_idx = messages.index(first_user)
                    for msg in messages[first_user_idx + 1 :]:
                        if msg.role == "assistant":
                            first_assistant = msg
                            break

                    if first_assistant:
                        # Use 30 words for first response - brief description of what was attempted
                        first_response = extract_content_summary(
                            first_assistant.content, max_words=30
                        )
                        if first_response:
                            summary_parts.append(f"Assistant: {first_response}")

                # Calculate omitted messages (all except first exchange and last assistant)
                messages_shown = 1  # first user
                if first_assistant:
                    messages_shown += 1  # first assistant
                if best_assistant_msg and best_assistant_msg != first_assistant:
                    messages_shown += 1  # last assistant (if different)

                omitted_count = len(messages) - messages_shown
                if omitted_count > 0:
                    summary_parts.append(f"... ({omitted_count} messages omitted) ...")

                # Add last assistant response if different from first
                if best_assistant_msg and best_assistant_msg != first_assistant:
                    # Use 60 words for last response - capture the outcome/conclusion
                    outcome = extract_content_summary(
                        best_assistant_msg.content, max_words=60
                    )
                    if outcome:
                        summary_parts.append(f"Assistant: {outcome}")

                if len(summary_parts) > 2:  # More than just metadata
                    context_parts.append("\n".join(summary_parts))

            except Exception as e:
                logger.debug(f"Failed to process conversation {conv.name}: {e}")
                continue

        sep = "\n---\n"
        if context_parts:
            context_content = f"""# Recent Conversation Context

The following is a summary of your recent conversations with the user to provide continuity:

```history
{sep.join(part for part in context_parts)}
```

Use this context to understand ongoing projects, preferences, and previous discussions.

*Tip: Use the `chats` tool to search past conversations or read their full history.*
"""
            yield Message("system", context_content)

    except Exception as e:
        logger.debug(f"Failed to generate chat history context: {e}")
