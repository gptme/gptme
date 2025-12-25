"""Keyword detector hook for triggering actions based on message content patterns.

This hook scans messages for configurable keyword patterns and triggers
associated actions when matches are found. Useful for:
- Critical error escalation (e.g., "CRITICAL" triggers special handling)
- Pattern-based automation (e.g., specific phrases trigger behaviors)
- Content-aware responses (e.g., detect frustration patterns)

Inspired by oh-my-opencode's keyword detector pattern.
See Issue #205 for context.
"""

import logging
import re
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Types of actions that can be triggered by keyword matches."""

    INJECT_MESSAGE = "inject_message"  # Inject a system message
    LOG_WARNING = "log_warning"  # Log a warning
    LOG_ERROR = "log_error"  # Log an error
    EMIT_METRIC = "emit_metric"  # Emit a metric (for telemetry)


@dataclass
class KeywordPattern:
    """A pattern to match and its associated action."""

    name: str
    pattern: str  # Regex pattern
    action: ActionType
    message: str | None = None  # Message to inject or log
    case_sensitive: bool = False
    enabled: bool = True
    cooldown_messages: int = 5  # Don't trigger again for N messages
    _last_triggered: int = field(default=-100, repr=False)

    def matches(self, text: str) -> bool:
        """Check if the pattern matches the given text."""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return bool(re.search(self.pattern, text, flags))


# Default patterns for common scenarios
DEFAULT_PATTERNS: list[KeywordPattern] = [
    KeywordPattern(
        name="critical_error",
        pattern=r"\b(CRITICAL|FATAL|CATASTROPHIC)\b",
        action=ActionType.INJECT_MESSAGE,
        message="⚠️ Critical issue detected. Consider pausing to assess the situation before continuing.",
        cooldown_messages=10,
    ),
    KeywordPattern(
        name="stuck_pattern",
        pattern=r"(stuck|frustrated|not working|keeps failing|tried everything)",
        action=ActionType.INJECT_MESSAGE,
        message="💡 Detected potential stumbling. Consider: 1) Taking a different approach, 2) Breaking down the problem, 3) Researching the specific error.",
        cooldown_messages=8,
    ),
    KeywordPattern(
        name="security_mention",
        pattern=r"\b(password|secret|api.?key|token|credential)s?\s*(=|:)\s*['\"]?[a-zA-Z0-9]",
        action=ActionType.LOG_WARNING,
        message="Potential secret detected in message content",
        cooldown_messages=3,
    ),
]


class KeywordDetector:
    """Manages keyword patterns and their detection."""

    def __init__(self, patterns: list[KeywordPattern] | None = None):
        self.patterns = patterns if patterns is not None else list(DEFAULT_PATTERNS)
        self.message_count = 0
        self._custom_handlers: dict[
            str, Callable[[str, KeywordPattern], Message | None]
        ] = {}

    def add_pattern(self, pattern: KeywordPattern) -> None:
        """Add a new pattern to detect."""
        self.patterns.append(pattern)

    def remove_pattern(self, name: str) -> bool:
        """Remove a pattern by name. Returns True if found and removed."""
        for i, p in enumerate(self.patterns):
            if p.name == name:
                del self.patterns[i]
                return True
        return False

    def register_handler(
        self,
        pattern_name: str,
        handler: Callable[[str, KeywordPattern], Message | None],
    ) -> None:
        """Register a custom handler for a specific pattern."""
        self._custom_handlers[pattern_name] = handler

    def check_message(self, message: Message) -> Generator[Message, None, None]:
        """Check a message against all patterns and yield any triggered actions."""
        if message.role != "assistant":
            # Only check assistant messages to avoid feedback loops
            return

        self.message_count += 1
        content = (
            message.content
            if isinstance(message.content, str)
            else str(message.content)
        )

        for pattern in self.patterns:
            if not pattern.enabled:
                continue

            # Check cooldown
            if self.message_count - pattern._last_triggered < pattern.cooldown_messages:
                continue

            if pattern.matches(content):
                pattern._last_triggered = self.message_count
                logger.debug(f"Keyword pattern '{pattern.name}' matched")

                # Check for custom handler first
                if pattern.name in self._custom_handlers:
                    result = self._custom_handlers[pattern.name](content, pattern)
                    if result:
                        yield result
                    continue

                # Default action handling
                if pattern.action == ActionType.INJECT_MESSAGE and pattern.message:
                    yield Message("system", pattern.message)
                elif pattern.action == ActionType.LOG_WARNING and pattern.message:
                    logger.warning(f"[{pattern.name}] {pattern.message}")
                elif pattern.action == ActionType.LOG_ERROR and pattern.message:
                    logger.error(f"[{pattern.name}] {pattern.message}")
                elif pattern.action == ActionType.EMIT_METRIC:
                    # Future: integrate with telemetry
                    logger.info(f"Metric: keyword_match.{pattern.name}")


# Global detector instance
_detector: KeywordDetector | None = None


def get_detector() -> KeywordDetector:
    """Get or create the global keyword detector."""
    global _detector
    if _detector is None:
        _detector = KeywordDetector()
    return _detector


def keyword_detector_hook(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook that scans messages for keyword patterns and triggers actions.

    Args:
        manager: The log manager with conversation state

    Yields:
        System messages triggered by keyword matches
    """
    detector = get_detector()

    # Check the most recent assistant message
    log = manager.log
    if not log.messages:
        return

    # Get the last message
    last_msg = log.messages[-1]

    # Check for matches and yield any triggered messages
    yield from detector.check_message(last_msg)


def register_keyword_detector_hooks() -> None:
    """Register the keyword detector hook."""
    register_hook(
        name="keyword_detector",
        hook_type=HookType.MESSAGE_POST_PROCESS,
        func=keyword_detector_hook,
        priority=50,  # Run after most other hooks
        enabled=True,
    )


# Auto-register on module import if this is enabled
# For now, keep it opt-in via explicit registration
