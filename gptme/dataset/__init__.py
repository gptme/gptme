"""Dataset construction utilities for gptme.

This subpackage provides tools for constructing fine-tuning datasets from
gptme session trajectories. The main entry point is ``trajectory_to_env``,
which scans session logs and associated git commits to produce
``TaskEnvironment`` JSONL records suitable for fine-tuning pipelines.

See ``gptme.dataset.trajectory_to_env`` for details.
"""

from .trajectory_to_env import TaskEnvironment, extract_environments, scan_sessions

__all__ = ["TaskEnvironment", "extract_environments", "scan_sessions"]
