"""
DSPy integration for gptme prompt optimization.

This module provides tools for automatically optimizing gptme's system prompts
using DSPy's prompt optimization techniques like MIPROv2 and BootstrapFewShot.
"""

from functools import lru_cache


@lru_cache
def _has_dspy() -> bool:
    """Check if DSPy is available."""
    try:
        import dspy  # noqa

        return True
    except ImportError:
        return False
