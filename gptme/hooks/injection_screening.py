"""Injection screening hook for untrusted tool outputs.

Screens tool outputs from web/email/GitHub sources for prompt injection
patterns. When detected, appends an [UNTRUSTED] warning to the model context
so the model treats the preceding tool output as untrusted rather than instruction.

Hook type: TOOL_EXECUTE_POST
"""

import logging
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, register_hook
from ..logmanager import Log
from ..message import Message
from ..tools.base import ToolUse

logger = logging.getLogger(__name__)

# Tools whose outputs may contain untrusted external content
_UNTRUSTED_SOURCE_TOOLS = frozenset(
    {
        "browser",  # Web page fetches
        "read",  # URL reads (not local file reads — checked via content)
        "gh",  # GitHub issue/PR bodies
        "elicit",  # Web research
    }
)

# Common prompt injection patterns checked against tool output content.
# Ordered from most specific to most general to reduce false positives.
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"ignore\s+(all\s+)?previous\s+(instructions|commands|directions)",
        re.IGNORECASE,
    ),
    re.compile(r"ignore\s+(everything\s+)?(above|below|before)", re.IGNORECASE),
    re.compile(
        r"(forget|discard)\s+(all\s+)?previous\s+(instructions|context)", re.IGNORECASE
    ),
    re.compile(r"your\s+new\s+(task|role|mission|purpose)\s+is", re.IGNORECASE),
    re.compile(
        r"(override|overwrite)\s+(system\s+)?(prompt|instructions)", re.IGNORECASE
    ),
    re.compile(
        r"do\s+not\s+(follow|obey|listen\s+to)\s+(any\s+)?(instructions|commands)",
        re.IGNORECASE,
    ),
    re.compile(r"you\s+must\s+(now\s+)?ignore", re.IGNORECASE),
    re.compile(r"##\s*(system\s+prompt|instructions|override)", re.IGNORECASE),
    re.compile(r"<\|im_start\|>\s*system", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+\w+", re.IGNORECASE),
]


def _is_untrusted_source(tool_name: str, tool_content: str | None) -> bool:
    """Return True if the tool retrieves untrusted external content."""
    if tool_name in _UNTRUSTED_SOURCE_TOOLS:
        # For "read" tool: only flag URL reads, not local file reads.
        # No content means we can't determine the target — treat as trusted.
        if tool_name == "read":
            if not tool_content:
                return False
            return tool_content.strip().startswith(("http://", "https://"))
        return True
    return False


def _has_injection_pattern(text: str | None) -> tuple[bool, str]:
    """Check if text contains common injection patterns.

    Returns (detected, matched_pattern_description).
    """
    if not text:
        return False, ""
    for pattern in _INJECTION_PATTERNS:
        if match := pattern.search(text):
            return True, match.group()
    return False, ""


def injection_screening(
    log: Log | None = None,
    workspace: Path | None = None,
    tool_use: ToolUse | None = None,
    result_msgs: list[Message] | None = None,
    **kwargs: Any,
) -> Generator[Message, None, None]:
    """TOOL_EXECUTE_POST hook that flags untrusted external content in tool output.

    When a web/read/gh/elicit tool returns content containing prompt injection
    patterns, this hook yields a system warning that appears before the model
    processes the tool results.
    """
    if tool_use is None or not result_msgs:
        return

    tool_name = tool_use.tool

    # Only screen tools that fetch untrusted external content
    if not _is_untrusted_source(tool_name, tool_use.content):
        return

    # Concatenate text content from all result messages
    output_text = "\n".join(
        msg.content for msg in result_msgs if isinstance(msg.content, str)
    )

    detected, match_text = _has_injection_pattern(output_text)
    if detected:
        logger.warning(
            "Injection pattern detected in %s output: %r", tool_name, match_text
        )
        yield Message(
            role="system",
            content=(
                f"[UNTRUSTED: possible prompt injection detected in {tool_name} "
                f"output, matching pattern: {match_text!r}]"
            ),
        )


def register() -> None:
    """Register the injection screening hook."""
    register_hook(
        "injection_screening",
        HookType.TOOL_EXECUTE_POST,
        injection_screening,
        priority=100,  # High priority — inject warning close to the tool output
    )
    logger.debug("Registered injection_screening hook")
