"""Unified auto-naming system for conversations."""

import logging
from datetime import datetime, timezone
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
) -> str | None:
    """Generate a conversation name using the specified strategy.

    Returns None if strategy is "llm" and LLM fails to generate a name
    (insufficient context, etc.). This allows callers to retry on subsequent
    turns when more context is available.
    """
    # Determine strategy
    if strategy == "auto":
        strategy = "llm" if messages and model and len(messages) > 1 else "random"

    for attempt in range(max_attempts):
        if strategy == "random":
            name = generate_name()
        elif strategy == "llm":
            # Don't fall back to random - return None so caller can retry
            # on subsequent turns when more context is available
            name = _generate_llm_name(messages, model, dash_separated)
            if not name:
                logger.info(
                    "LLM naming failed, returning None to allow retry on next turn"
                )
                return None
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        if existing_names is None or name not in existing_names:
            return name

        logger.debug(f"Name '{name}' exists, retrying (attempt {attempt + 1})")

    # Final fallback with timestamp
    timestamp = datetime.now(tz=timezone.utc).strftime("%H%M%S")
    return f"{name}-{timestamp}"


def generate_conversation_id(name: str | None, logs_dir: Path) -> str:
    """Generate a conversation ID for CLI usage with date prefix."""
    datestr = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

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
            timestamp = datetime.now(tz=timezone.utc).strftime("%H%M%S")
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
        from ..llm.models import ModelMeta, get_model, get_summary_model

        # Handle case where model is already a ModelMeta object
        if isinstance(model, ModelMeta):
            model = model.full

        # Try to use cheaper summary model (if available for provider)
        naming_model = model
        try:
            current_model = get_model(model)
            if current_model.provider != "unknown":
                summary_model_name = get_summary_model(current_model.provider)
                # Only switch models if a summary model is defined for this provider
                if summary_model_name is not None:
                    naming_model = f"{current_model.provider}/{summary_model_name}"
                # For local/unknown providers, keep using the original model
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

        # Check if context has enough substance for meaningful naming
        # Strip role prefixes and check actual content length
        content_only = context.replace("User:", "").replace("Assistant:", "").strip()
        min_context_length = (
            50  # Minimum characters needed for LLM to generate meaningful title
        )
        if len(content_only) < min_context_length:
            logger.info(
                f"Context too short for LLM naming ({len(content_only)} chars < {min_context_length}), "
                "using random name fallback"
            )
            return None

        # Sentinel string for when LLM cannot generate a meaningful title
        no_name_sentinel = "NO_NAME"

        # Create prompt based on format
        if dash_separated:
            prompt = f"""Generate a descriptive name for this conversation.

The name should be 3-6 words describing the conversation, separated by dashes. Examples:
- install-llama
- implement-game-of-life
- debug-python-script

Focus on the main and/or initial topic of the conversation. Avoid using names that are too generic or too specific.

CRITICAL RULES:
- Output ONLY the name itself, nothing else
- If there is truly insufficient context to generate a meaningful name, return exactly "{no_name_sentinel}" (nothing else)
- NEVER explain, apologize, or return error messages

Conversation:
{context}"""
        else:
            prompt = f"""Your task: Create a 2-4 word title for this conversation.

Rules:
- Respond with ONLY the title, nothing else
- Maximum 4 words
- Capture the main topic
- If there is truly insufficient context to generate a meaningful title, return exactly "{no_name_sentinel}" (nothing else)
- NEVER explain, apologize, or return error messages

Examples of GOOD titles:
- "Python debugging help"
- "Website creation task"
- "Quick greeting"
- "General assistance"

Conversation:
{context}"""

        # Use summary model directly (no fallback)
        response, _metadata = _chat_complete(
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
            # Check for explicit sentinel indicating LLM couldn't generate name
            if name.upper() == no_name_sentinel:
                logger.info("LLM indicated insufficient context for naming (NO_NAME)")
                return None
            # Validate that the response is a proper title, not an error message
            # (fallback safety net for models that don't follow sentinel instruction)
            if _is_invalid_title(name):
                logger.warning(f"LLM returned invalid title: {name}")
                return None
            return name

    except Exception as e:
        logger.warning(f"LLM naming failed: {e}")

    return None


def _is_invalid_title(name: str) -> bool:
    """Check if the generated title looks like an error message or explanation.

    Returns True if the title should be rejected.
    """
    # Reject empty or whitespace-only titles
    if not name or not name.strip():
        return True

    name_lower = name.lower()

    # Reject error-like patterns
    error_patterns = [
        "conversation content missing",
        "missing conversation",
        "content missing",
        "no conversation",
        "unable to",
        "cannot generate",
        "i don't have",
        "i cannot",
        "i'm sorry",
        "sorry,",
        "unfortunately",
        "not enough",
        "insufficient",
        "no information",
        "no context",
        "empty conversation",
        "missing details",
        "details missing",
        "conversation details",
        "n/a",
        "not applicable",
        "not available",
        "title:",  # Model repeating the prompt
        "name:",  # Model repeating the prompt
    ]

    for pattern in error_patterns:
        if pattern in name_lower:
            return True

    # Reject if it starts with common explanation prefixes
    explanation_prefixes = [
        "i ",
        "the conversation ",
        "this conversation ",
        "based on ",
        "here is ",
        "here's ",
    ]

    for prefix in explanation_prefixes:
        if name_lower.startswith(prefix):
            return True

    # Reject if it's too long to be a proper title (likely an explanation)
    return len(name.split()) > 8


def _starts_with_date(name: str) -> bool:
    """Check if name starts with a date in YYYY-MM-DD format."""
    try:
        datetime.strptime(name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
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
    """Generate a dash-separated LLM name for conversation renaming.

    Unlike auto_generate_display_name, this function always returns a string
    because it's used for explicit renaming where a name is required.
    Falls back to random name if LLM naming fails.
    """
    try:
        from ..llm.models import get_default_model_summary

        model = get_default_model_summary()
        if model:
            name = generate_conversation_name(
                strategy="llm", messages=messages, model=model.full, dash_separated=True
            )
            if name:
                return name
    except Exception:
        pass

    # Fallback to random name - always returns a string
    return generate_name()
