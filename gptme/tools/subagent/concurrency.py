"""Concurrency limiter for parallel subagents.

Provides a module-level semaphore that caps the number of concurrently running
subagents. The limit is resolved at first use from (highest to lowest priority):

1. ``GPTME_SUBAGENT_MAX_CONCURRENT`` environment variable
2. ``[subagent] max_concurrent`` in ``gptme.toml``
3. ``min(8, os.cpu_count() or 2)`` safe default (mirrors Claude Code's ``min(16, cores-2)``)
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

_slot_sem: threading.BoundedSemaphore | None = None
_slot_sem_lock = threading.Lock()


def _max_concurrent() -> int:
    """Resolve max concurrent subagents from env > config > cpu-based default."""
    env_val = os.environ.get("GPTME_SUBAGENT_MAX_CONCURRENT")
    if env_val:
        try:
            val = int(env_val)
            if val < 1:
                raise ValueError(f"must be >= 1, got {val}")
            return val
        except ValueError as e:
            logger.warning(
                f"Ignoring invalid GPTME_SUBAGENT_MAX_CONCURRENT={env_val!r}: {e}"
            )
    config_mc: int | None = None
    try:
        from gptme.config import get_config

        project = get_config().project
        if project is not None:
            config_mc = project.subagent.max_concurrent
    except Exception:
        pass
    if config_mc is not None:
        if config_mc < 1:
            raise ValueError(
                f"[subagent] max_concurrent in gptme.toml must be >= 1, got {config_mc}"
            )
        return config_mc
    return min(8, os.cpu_count() or 2)


def get_slot_sem() -> threading.BoundedSemaphore:
    """Return the global concurrency semaphore, initializing lazily on first call."""
    global _slot_sem
    with _slot_sem_lock:
        if _slot_sem is None:
            _slot_sem = threading.BoundedSemaphore(_max_concurrent())
        return _slot_sem


def _reset_slot_sem(max_concurrent: int | None = None) -> None:
    """Reset the semaphore (for testing only)."""
    global _slot_sem
    with _slot_sem_lock:
        _slot_sem = (
            threading.BoundedSemaphore(max_concurrent)
            if max_concurrent is not None
            else None
        )
