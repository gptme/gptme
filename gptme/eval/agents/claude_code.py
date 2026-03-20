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
        """Try to extract usage info from Claude Code JSON output."""
        if not stdout.strip():
            return
        try:
            data = json.loads(stdout)
            if isinstance(data, dict) and "usage" in data:
                usage = data["usage"]
                logger.info(
                    f"Claude Code usage: "
                    f"input={usage.get('input_tokens', '?')}, "
                    f"output={usage.get('output_tokens', '?')}"
                )
        except (json.JSONDecodeError, TypeError):
            # Output may contain multiple JSON lines or non-JSON content
            pass
