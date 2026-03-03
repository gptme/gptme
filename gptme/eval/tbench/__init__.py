"""
Terminal-Bench evaluation support for gptme.

Terminal-Bench (tbench) is an external benchmark for evaluating agents on
terminal-based tasks. This module provides a gptme adapter that implements
the AbstractInstalledAgent interface.

Usage:
    # Install gptme with eval extras (includes terminal-bench in the same env):
    # pip install 'gptme[eval]'

    # Run via CLI:
    gptme-eval-tbench --task hello-world --model anthropic/claude-haiku-4-5

    # Or use terminal-bench directly:
    tb run \\
        --dataset terminal-bench-core==head \\
        --agent-import-path gptme.eval.tbench.agent:GptmeAgent \\
        --task-id hello-world

See: https://github.com/openai/terminal-bench
"""

from __future__ import annotations

# Default model for tbench evaluations — shared by agent.py and run.py
# to avoid silent drift if one file is updated without the other.
DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

# Lazy import to avoid ImportError when terminal-bench is not installed.
# The friendly error message in run.py would never be reached if we import
# GptmeAgent eagerly here (since agent.py imports terminal_bench at module level).


def __getattr__(name: str) -> object:
    if name == "GptmeAgent":
        from .agent import GptmeAgent

        return GptmeAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["GptmeAgent", "DEFAULT_MODEL"]
