"""Configuration for the auto-compact tool.

Settings here control how aggressive compaction is and whether the head of the
conversation (the system prompt and original task) is protected from reduction.
"""

from dataclasses import dataclass


@dataclass
class AutoCompactConfig:
    """Configuration for auto-compaction behavior.

    ``keep_head`` protects the first ``N`` messages (by position) of the log from
    any compaction — reasoning stripping, tool-result truncation, and assistant
    compression all skip them. The default of 2 protects the system prompt and
    the first user message, which typically carry the original task. Set to 0 to
    disable head retention (pre-existing behavior).
    """

    keep_head: int = 2
    """Number of messages at the start of the log to protect from compaction."""
