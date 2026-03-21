"""
gptme agent adapter for Terminal-Bench.

Implements the AbstractInstalledAgent interface so that terminal-bench
can run gptme as an agent in its evaluation harness.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from ...llm import PROVIDER_API_KEYS
from . import DEFAULT_MODEL

try:
    from terminal_bench.agents.installed_agents.abstract_installed_agent import (
        AbstractInstalledAgent,
    )
    from terminal_bench.terminal.models import (
        TerminalCommand,
    )
except ImportError as e:
    raise ImportError(
        "terminal-bench is required for tbench evaluation. "
        "Install it with: pip install 'gptme[eval]'"
    ) from e


class GptmeAgent(AbstractInstalledAgent):
    """gptme agent adapter for Terminal-Bench evaluation.

    Wraps gptme as an AbstractInstalledAgent so it can be evaluated
    by the terminal-bench harness.

    Example usage with terminal-bench:
        tb run \\
            --dataset terminal-bench-core==head \\
            --agent-import-path gptme.eval.tbench.agent:GptmeAgent \\
            --task-id hello-world

    Or with a specific model:
        tb run \\
            --dataset terminal-bench-core==head \\
            --agent-import-path gptme.eval.tbench.agent:GptmeAgent \\
            --agent-args '{"model_name": "anthropic/claude-sonnet-4-6"}' \\
            --task-id hello-world
    """

    @staticmethod
    def name() -> str:
        return "gptme"

    _default_model = DEFAULT_MODEL

    _default_timeout_sec = (
        600.0  # 10 minutes; override via agent-args if tasks need longer
    )

    def __init__(
        self,
        model_name: str | None = None,
        max_timeout_sec: float | None = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._model_name: str = (
            model_name
            if model_name is not None
            else (os.environ.get("GPTME_MODEL") or self._default_model)
        )
        self._max_timeout_sec: float = (
            max_timeout_sec
            if max_timeout_sec is not None
            else self._default_timeout_sec
        )

    @property
    def _env(self) -> dict[str, str]:
        """Pass API keys and config from local environment to the agent."""
        env: dict[str, str] = {}

        # Pass through all provider API keys (sourced from gptme.llm.PROVIDER_API_KEYS)
        for env_var in PROVIDER_API_KEYS.values():
            if env_var in os.environ:
                env[env_var] = os.environ[env_var]

        env["GPTME_MODEL"] = self._model_name
        return env

    @property
    def _install_agent_script_path(self) -> os.PathLike:
        """Returns the path to the gptme setup script for the tbench container."""
        return Path(__file__).parent / "setup.sh"

    def _run_agent_commands(self, task_description: str) -> list[TerminalCommand]:
        """Returns terminal commands that run gptme with the given task."""
        escaped = shlex.quote(task_description)
        model_flag = shlex.quote(self._model_name)

        return [
            TerminalCommand(
                command=f"gptme -n --model {model_flag} {escaped}",
                max_timeout_sec=self._max_timeout_sec,
                block=True,
            )
        ]
