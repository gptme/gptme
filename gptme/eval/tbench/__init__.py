"""
Terminal-Bench evaluation support for gptme.

Terminal-Bench (tbench) is an external benchmark for evaluating agents on
terminal-based tasks. This module provides a gptme adapter that implements
the AbstractInstalledAgent interface.

Usage:
    # Install terminal-bench: uv tool install terminal-bench

    # Run via CLI:
    gptme-eval-tbench --task hello-world --model anthropic/claude-haiku-4-5

    # Or use terminal-bench directly:
    tb run \\
        --dataset terminal-bench-core==head \\
        --agent-import-path gptme.eval.tbench.agent:GptmeAgent \\
        --task-id hello-world

See: https://github.com/openai/terminal-bench
"""

from .agent import GptmeAgent

__all__ = ["GptmeAgent"]
