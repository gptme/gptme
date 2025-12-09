"""
Form auto-detection hook.

Automatically detects when the assistant presents options to the user
and converts them into an interactive form for easier selection.

Approach (per Erik's guidance in Issue #591):
1. Heuristics detect potential options (numbered lists, bullet points with choices)
2. Fast LLM (haiku/mini) parses into form fields if heuristics trigger
3. Auto-presents questionary form to user

Config: Set `FORM_AUTO_DETECT=true` environment variable to enable.
"""

import logging
import re
from collections.abc import Generator
from typing import Any

from ..config import get_config
from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message

logger = logging.getLogger(__name__)

# Patterns that suggest options are being presented
OPTION_PATTERNS = [
    # Numbered lists: "1.", "2.", "3." or "1)", "2)", "3)"
    r"^\s*(?:\d+[.)]\s+.+\n?){2,}",
    # Lettered lists: "a.", "b.", "c." or "a)", "b)", "c)"
    r"^\s*(?:[a-zA-Z][.)]\s+.+\n?){2,}",
    # Bullet points with similar structure
    r"^\s*(?:[-*•]\s+.+\n?){2,}",
    # "Please choose/select" patterns
    r"(?:please\s+)?(?:choose|select|pick)\s+(?:one|an?\s+option)",
    # Question with options pattern
    r"\?\s*\n\s*(?:[-*•\d]+[.)]\s+.+\n?){2,}",
    # "Which would you prefer" patterns
    r"which\s+(?:would\s+you\s+prefer|do\s+you\s+want|option)",
    # Explicit options header
    r"(?:options?|choices?|alternatives?):\s*\n",
]

# Compiled patterns for efficiency
COMPILED_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.MULTILINE) for p in OPTION_PATTERNS
]

# LLM prompt for parsing options
PARSE_PROMPT = """Extract the options from this assistant message and format them for a selection form.

<message>
{message}
</message>

If this message presents clear options for the user to choose from, output ONLY a JSON object with:
- "detected": true
- "question": the question being asked (if any)
- "options": list of option strings

If this is NOT a clear selection prompt (e.g., it's explanatory text, code examples, or doesn't require user choice), output:
- "detected": false

Examples of what IS a selection prompt:
- "Would you like option A or option B?"
- "1. First choice\n2. Second choice\n3. Third choice"
- "Please select: - React - Vue - Svelte"

Examples of what is NOT a selection prompt:
- Code with numbered comments
- Explanatory lists (e.g., "Here are some features: 1. Feature A 2. Feature B")
- Error messages with numbered steps
- General bullet point documentation

Output ONLY valid JSON, no explanation."""


def _detect_options_heuristic(content: str) -> bool:
    """Use heuristics to detect if message might contain selectable options."""
    for pattern in COMPILED_PATTERNS:
        if pattern.search(content):
            return True
    return False


def _parse_options_with_llm(content: str) -> dict | None:
    """Use fast LLM to parse detected options into form fields."""
    try:
        from ..llm import _chat_complete
    except ImportError:
        logger.warning("Cannot import _chat_complete for form auto-detection")
        return None

    # Use a fast model for parsing
    # Prefer haiku/mini for speed and cost
    fast_models = [
        "anthropic/claude-haiku-4-5",  # ~0.25s response
        "openai/gpt-4o-mini",  # ~0.3s response
        "anthropic/claude-3-haiku-20240307",  # fallback
    ]

    # Try to find an available fast model
    model: str | None = None
    try:
        from ..llm.models import get_default_model

        default_meta = get_default_model()
        if default_meta:
            default = default_meta.model
            # If the default model is already fast, use it
            if any(fast in default for fast in ["haiku", "mini", "flash"]):
                model = default
    except Exception:
        pass

    if model is None:
        # Fall back to first fast model (assumes API key is set)
        model = fast_models[0]

    try:
        messages = [
            Message(
                "user", PARSE_PROMPT.format(message=content[:2000])
            )  # Limit context
        ]
        response, _metadata = _chat_complete(messages, model=model, tools=None)

        # Parse JSON from response
        import json

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            response = json_match.group(1)
        else:
            # Try to find raw JSON
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                response = json_match.group(0)

        result = json.loads(response)
        return result if result.get("detected") else None

    except Exception as e:
        logger.debug(f"Form auto-detection LLM parsing failed: {e}")
        return None


def _create_form_message(parsed: dict | None) -> Message | None:
    """Create a form tool message from parsed options."""
    if not parsed or not parsed.get("options"):
        return None

    options = parsed["options"]
    question = parsed.get("question", "Please select an option")

    # Format as form tool content
    # Single select field with the detected options
    options_str = ", ".join(options)
    form_content = f"selection: {question} [{options_str}]"

    return Message(
        "assistant",
        f"```form\n{form_content}\n```",
        call_id=None,  # Will be processed as tool use
    )


def form_autodetect_hook(
    message: Message,
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Hook to auto-detect options and present form.

    Triggered after assistant message generation.
    """
    # Only process assistant messages
    if message.role != "assistant":
        return

    # Check if form tool is loaded (must be explicitly enabled)
    try:
        from ..tools import has_tool

        if not has_tool("form"):
            return
    except ImportError:
        return

    # Check if auto-detection is enabled via environment variable
    # Set FORM_AUTO_DETECT=true to enable
    config = get_config()
    auto_detect = config.get_env_bool("FORM_AUTO_DETECT") or False
    if not auto_detect:
        return

    content = message.content

    # Skip if message already contains a form tool
    if "```form" in content:
        return

    # Skip very short or very long messages
    if len(content) < 50 or len(content) > 5000:
        return

    # Step 1: Heuristic detection
    if not _detect_options_heuristic(content):
        return

    logger.debug("Form auto-detection: heuristics triggered, running LLM parse")

    # Step 2: LLM parsing
    parsed = _parse_options_with_llm(content)
    if not parsed:
        logger.debug("Form auto-detection: LLM did not detect valid options")
        return

    # Step 3: Create and yield form message
    form_msg = _create_form_message(parsed)
    if form_msg:
        logger.info(f"Form auto-detection: presenting {len(parsed['options'])} options")
        yield Message(
            "system",
            f"[Form auto-detected {len(parsed['options'])} options - presenting interactive selection]",
            hide=True,
        )
        yield form_msg


def register() -> None:
    """Register the form auto-detection hook."""
    register_hook(
        name="form_autodetect",
        hook_type=HookType.GENERATION_POST,
        func=form_autodetect_hook,
        priority=10,  # Run after other generation hooks
        enabled=True,
    )
