"""
DSPy integration for gptme prompt optimization.

This module provides tools for automatically optimizing gptme's system prompts
using DSPy's prompt optimization techniques like MIPROv2 and BootstrapFewShot.
"""

import importlib.util
import logging
from functools import lru_cache

__all__ = ["main"]

logger = logging.getLogger(__name__)


@lru_cache
def _has_dspy() -> bool:
    """Check if DSPy is available."""
    return importlib.util.find_spec("dspy") is not None


def main() -> None:
    """Entry point for ``gptme-dspy``.

    ``.cli`` imports dspy at module scope, so it is imported here rather than
    at package scope: importing it eagerly makes ``_has_dspy()`` unreachable
    and leaves ``gptme-dspy`` dying with ``ModuleNotFoundError: No module
    named 'dspy'`` during the console-script import, before any gptme code
    runs (gptme/gptme#290 is the same failure for ``gptme-server``).
    """
    if not _has_dspy():
        logger.error(
            "gptme installed without needed extras for dspy. "
            "Install them with `pip install gptme[dspy]`"
        )
        raise SystemExit(1)

    from .cli import main as _main

    _main()
