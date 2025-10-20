"""Unified auto-naming system for conversations."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..message import Message
from ..telemetry import trace_function
from .generate_name import generate_name

logger = logging.getLogger(__name__)

NamingStrategy = Literal["random", "llm", "auto"]


def generate_conversation_name(
    strategy: NamingStrategy = "auto",
    messages: list[Message] | None = None,
    model: str | None = None,
    dash_separated: bool = False,
    existing_names: set[str] | None = None,
    max_attempts: int = 3,
) -> str:
    """Generate a conversation name using the specified strategy."""
    # Determine strategy
    if strategy == "auto":
        strategy = "llm" if messages and model and len(messages) > 1 else "random"

    for attempt in range(max_attempts):
        if strategy == "random":
            name = generate_name()
        elif strategy == "llm":
            name = (
                _generate_llm_name(messages, model, dash_separated) or generate_name()
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        if existing_names is None or name not in existing_names:
            return name

        logger.debug(f"Name '{name}' exists, retrying (attempt {attempt + 1})")

    # Final fallback with timestamp
    timestamp = datetime.now().strftime("%H%M%S")
    return f"{name}-{timestamp}"


def generate_conversation_id(name: str | None, logs_dir: Path) -> str:
    """Generate a conversation ID for CLI usage with date prefix."""
    datestr = datetime.now().strftime("%Y-%m-%d")

    if name == "random":
        name = None
    if name and _starts_with_date(name):
        return name

    # Get existing names for uniqueness
    existing_names = {path.name for path in logs_dir.iterdir() if path.is_dir()}

    if not name:
        name = generate_conversation_name(
            strategy="random", existing_names=existing_names
        )

    # Ensure uniqueness
    full_id = f"{datestr}-{name}"
    attempt = 2
    while (logs_dir / full_id).exists():
        full_id = f"{datestr}-{name}-{attempt}"
        attempt += 1
        if attempt > 100:  # Safety valve
            timestamp = datetime.now().strftime("%H%M%S")
            full_id = f"{datestr}-{name}-{timestamp}"
            break

    return full_id


def _generate_llm_name(
    messages: list[Message] | None, model: str | None, dash_separated: bool = False
) -> str | None:
    """Generate an LLM-based contextual name."""
    if not messages or not model:
        return None

    try:
        from ..llm import _chat_complete
        from ..llm.models import get_model, get_summary_model

        # Try to use cheaper summary model
        naming_model = model
        try:
            current_model = get_model(model)
            if current_model.provider != "unknown":
                summary_model_name = get_summary_model(current_model.provider)
                naming_model = f"{current_model.provider}/{summary_model_name}"
        except Exception:
            logger.exception("exception during auto-name")

        # Create context from recent messages
        context = ""
        for msg in messages[-4:]:
            if msg.role in ["user", "assistant"]:
                content = (
                    msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
                )
                context += f"{msg.role.title()}: {content}\n"

        if not context.strip():
            logger.warning("no context for auto-name")
            return None

        # Create prompt based on format
        if dash_separated:
            prompt = f"""Generate a descriptive name for this conversation.

The name should be 3-6 words describing the conversation, separated by dashes. Examples:
- install-llama
- implement-game-of-life
- debug-python-script

Focus on the main and/or initial topic of the conversation. Avoid using names that are too generic or too specific.

IMPORTANT: output only the name, no preamble or postamble.

Conversation:
{context}

Name:"""
        else:
            prompt = f"""Your task: Create a 2-4 word title for this conversation.

Rules:
- Respond with ONLY the title
- No explanations or extra text
- Maximum 4 words
- Capture the main topic

Examples:
- "Python debugging help"
- "Website creation task"
- "CSS layout issue"
- "API integration guide"

Conversation:
{context}

Title:"""

        # Use summary model directly (no fallback)
        response = _chat_complete(
            [
                Message("system", "Generate concise conversation titles."),
                Message("user", prompt),
            ],
            naming_model,
            None,
        )

        # Strip think tags (Claude models may use these)
        response = response.strip()
        think_end = response.find("</think>")
        if think_end != -1:
            # Remove everything before and including </think>
            response = response[think_end + len("</think>") :]
        elif "<think>" in response:
            # Incomplete think tag, skip this response
            logger.warning("Incomplete think tag in response, skipping")
            return None

        name = response.strip().strip('"').strip("'").split("\n")[0][:50]
        if name:
            return name

    except Exception as e:
        logger.warning(f"LLM naming failed: {e}")

    return None


def _starts_with_date(name: str) -> bool:
    """Check if name starts with a date in YYYY-MM-DD format."""
    try:
        datetime.strptime(name[:10], "%Y-%m-%d")
        return True
    except (ValueError, IndexError):
        return False


# Backwards compatibility functions
def auto_generate_display_name(messages: list[Message], model: str) -> str | None:
    """Generate a display name for the conversation based on the messages."""
    return generate_conversation_name(strategy="llm", messages=messages, model=model)


@trace_function(
    name="auto_naming.generate_llm_name", attributes={"component": "auto_naming"}
)
def generate_llm_name(messages: list[Message]) -> str:
    """Generate a dash-separated LLM name for conversation renaming."""
    try:
        from ..llm.models import get_default_model_summary

        model = get_default_model_summary()
        if model:
            return generate_conversation_name(
                strategy="llm", messages=messages, model=model.full, dash_separated=True
            )
    except Exception:
        pass

    return generate_conversation_name(strategy="random")
