"""Configuration for the auto-compact tool.

Settings here control how aggressive compaction is and whether the head of the
conversation (the system prompt and original task) is protected from reduction.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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


def _get_keep_head() -> int:
    """Resolve the number of head messages to protect from compaction.

    Priority: ``GPTME_AUTOCOMPACT_KEEP_HEAD`` env var > :class:`AutoCompactConfig`
    default. Invalid/negative values fall back to the configured default.
    """
    from ...config import get_config

    cfg = AutoCompactConfig()
    raw = get_config().get_env("GPTME_AUTOCOMPACT_KEEP_HEAD")
    if raw is not None:
        try:
            val = int(raw)
            if val < 0:
                logger.warning(
                    f"Invalid GPTME_AUTOCOMPACT_KEEP_HEAD={raw!r} (negative), using default {cfg.keep_head}"
                )
                return cfg.keep_head
            return val
        except ValueError:
            logger.warning(
                f"Invalid GPTME_AUTOCOMPACT_KEEP_HEAD={raw!r}, using default {cfg.keep_head}"
            )
    return cfg.keep_head
