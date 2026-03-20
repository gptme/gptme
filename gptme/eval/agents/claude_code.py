"""Claude Code agent for gptme eval system.

Runs eval tasks through Claude Code CLI (`claude -p`) instead of gptme,
enabling direct harness comparison on the same eval suite.

Usage:
    gptme-eval basic --model claude-code/claude-sonnet-4-6

The ``claude-code/`` prefix selects this agent; the remainder is passed
as ``--model`` to the Claude Code CLI.
"""

import json
import logging
import os
import shutil
import subprocess
import time

from ...util.cost_tracker import CostEntry, CostTracker
from ..filestore import FileStore
from ..types import Files
from . import Agent

logger = logging.getLogger(__name__)

CLAUDE_CODE_MODEL_PREFIX = "claude-code/"


def is_claude_code_model(model: str) -> bool:
    """Check if a model string requests the Claude Code agent."""
    return model.startswith(CLAUDE_CODE_MODEL_PREFIX)


def parse_claude_code_model(model: str) -> str:
    """Extract the underlying model name from a claude-code/ prefixed string."""
    return model[len(CLAUDE_CODE_MODEL_PREFIX) :]


class ClaudeCodeAgent(Agent):
    """Eval agent that delegates to Claude Code CLI.

    Wraps ``claude -p <prompt> --output-format json`` in a subprocess,
    using the same workspace/file conventions as the GPTMe agent so
    results are directly comparable.
    """

    def __init__(
        self,
        model: str,
        **kwargs,
    ):
        # Strip the prefix for the underlying model name
        cc_model = (
            parse_claude_code_model(model) if is_claude_code_model(model) else model
        )
        # Claude Code doesn't use tool_format, default to markdown
        kwargs.setdefault("tool_format", "markdown")
        super().__init__(model=model, **kwargs)
        self.cc_model = cc_model

    def act(self, files: Files | None, prompt: str) -> Files:
        # Start cost tracking for this eval run
        CostTracker.start_session(f"claude-code-eval:{self.cc_model}")
        store = FileStore(working_dir=self.workspace_dir)
        if files:
            store.upload(files)

        claude_bin = shutil.which("claude")
        if claude_bin is None:
            raise FileNotFoundError(
                "Claude Code CLI ('claude') not found on PATH. "
                "Install it from https://docs.anthropic.com/en/docs/claude-code"
            )

        cmd = [
            claude_bin,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            self.cc_model,
            "--max-turns",
            "30",
        ]

        if self.tools:
            # CC CLI accepts comma-separated tool names in a single --allowedTools arg
            cmd.extend(["--allowedTools", ",".join(self.tools)])

        env = os.environ.copy()
        # Prevent nested session detection if already inside Claude Code
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)

        print("\n--- Start of generation (Claude Code) ---")
        logger.debug(f"Working in {self.workspace_dir}")
        logger.debug(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                env=env,
                stdin=subprocess.DEVNULL,
                check=False,
            )

            if result.returncode != 0:
                logger.warning(f"Claude Code exited with code {result.returncode}")
                if result.stderr:
                    logger.warning(f"stderr: {result.stderr[:500]}")

            # Parse JSON output for cost/usage info if available
            self._parse_usage(result.stdout)

        except Exception as e:
            logger.error(f"Claude Code execution failed: {e}")
            raise

        print("--- Finished generation (Claude Code) ---\n")
        return store.download()

    def _parse_usage(self, stdout: str) -> None:
        """Extract usage info from Claude Code NDJSON output and record into CostTracker.

        Claude Code with ``--output-format json`` emits one JSON object per
        line (NDJSON).  The result line contains ``total_cost_usd`` and ``usage``
        with token counts.  We record a :class:`CostEntry` so that eval results
        include accurate cost data for ClaudeCodeAgent runs.
        """
        if not stdout.strip():
            return
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict) or "usage" not in data:
                    continue
                usage = data["usage"]
                input_tokens = int(usage.get("input_tokens", 0))
                output_tokens = int(usage.get("output_tokens", 0))
                cache_read = int(usage.get("cache_read_input_tokens", 0))
                cache_create = int(usage.get("cache_creation_input_tokens", 0))
                cost_usd = float(data.get("total_cost_usd", 0.0))
                logger.info(
                    f"Claude Code usage: "
                    f"input={input_tokens}, output={output_tokens}, "
                    f"cost=${cost_usd:.4f}"
                )
                CostTracker.record(
                    CostEntry(
                        timestamp=time.time(),
                        model=self.cc_model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read,
                        cache_creation_tokens=cache_create,
                        cost=cost_usd,
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
