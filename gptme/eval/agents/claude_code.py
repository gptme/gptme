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
from ..execenv import DockerClaudeCodeEnv
from ..filestore import FileStore
from ..types import Files
from . import Agent

logger = logging.getLogger(__name__)

CLAUDE_CODE_MODEL_PREFIX = "claude-code/"

WORKSPACE_INSTRUCTION = (
    "IMPORTANT: You are running inside an isolated eval workspace "
    "(cwd is the workspace). All files you create MUST use RELATIVE "
    "paths (e.g. 'server.py') — NEVER use absolute paths. "
    "Files saved with absolute paths will not be found by the eval checker.\n\n"
)


def is_claude_code_model(model: str) -> bool:
    """Check if a model string requests the Claude Code agent."""
    return model.startswith(CLAUDE_CODE_MODEL_PREFIX)


def parse_claude_code_model(model: str) -> str:
    """Extract the underlying model name from a claude-code/ prefixed string."""
    suffix = model[len(CLAUDE_CODE_MODEL_PREFIX) :]
    if not suffix:
        raise ValueError(
            f"Missing model name after '{CLAUDE_CODE_MODEL_PREFIX}' prefix"
        )
    return suffix


class ClaudeCodeAgent(Agent):
    """Eval agent that delegates to Claude Code CLI.

    Wraps ``claude -p <prompt> --output-format json`` in a subprocess,
    using the same workspace/file conventions as the GPTMe agent so
    results are directly comparable.
    """

    def __init__(
        self,
        model: str,
        timeout: int = 600,
        max_turns: int = 30,
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
        self.timeout = timeout
        self.max_turns = max_turns

    def act(self, files: Files | None, prompt: str) -> Files:
        store = FileStore(working_dir=self.workspace_dir)
        if files:
            store.upload(files)

        if self.use_docker:
            return self._act_docker(store, prompt)
        return self._act_local(store, prompt)

    def _act_local(self, store: FileStore, prompt: str) -> Files:
        """Execute Claude Code directly on the host."""
        if not self.include_user_context:
            logger.warning(
                "include_user_context=False has no effect for ClaudeCodeAgent — "
                "the Claude Code CLI reads its own user config (e.g. ~/.claude/) unconditionally."
            )
        claude_bin = shutil.which("claude")
        if claude_bin is None:
            raise FileNotFoundError(
                "Claude Code CLI ('claude') not found on PATH. "
                "Install it from https://docs.anthropic.com/en/docs/claude-code"
            )

        # Prepend workspace instruction to prompt so Claude Code also
        # uses relative paths for file creation inside the eval workspace.
        eval_prompt = WORKSPACE_INSTRUCTION + prompt

        CostTracker.start_session(f"claude-code-eval:{self.cc_model}")

        cmd = [
            claude_bin,
            "-p",
            eval_prompt,
            "--output-format",
            "json",
            "--model",
            self.cc_model,
            "--max-turns",
            str(self.max_turns),
        ]

        if self.tools:
            logger.warning(
                "ClaudeCodeAgent: tools=%r uses gptme tool names which may not "
                "match Claude Code's --allowedTools identifiers. "
                "Tool restrictions may not be fully enforced.",
                self.tools,
            )
            cmd.extend(["--allowedTools", ",".join(self.tools)])

        env = os.environ.copy()
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
                timeout=self.timeout,
            )

            if result.returncode != 0:
                logger.warning(f"Claude Code exited with code {result.returncode}")
                if result.stderr:
                    logger.warning(f"stderr: {result.stderr[:500]}")

            self._parse_usage(result.stdout)

        except subprocess.TimeoutExpired:
            logger.error(
                f"Claude Code timed out after {self.timeout}s — process killed"
            )
            raise
        except Exception as e:
            logger.error(f"Claude Code execution failed: {e}")
            raise

        print("--- Finished generation (Claude Code) ---\n")
        return store.download()

    def _act_docker(self, store: FileStore, prompt: str) -> Files:
        """Execute Claude Code inside a Docker container for isolation."""
        print("\n--- Start of generation (Claude Code, Docker-isolated) ---")
        logger.debug(f"Working in {store.working_dir} (Docker mode)")

        # Prepend workspace instruction for Docker mode too
        eval_prompt = WORKSPACE_INSTRUCTION + prompt

        docker_env = DockerClaudeCodeEnv(
            host_dir=self.workspace_dir,
            timeout=self.timeout,
        )

        if self.tools:
            logger.warning(
                "ClaudeCodeAgent: tools=%r uses gptme tool names which may not "
                "match Claude Code's --allowedTools identifiers. "
                "Tool restrictions may not be fully enforced.",
                self.tools,
            )

        try:
            # Start the container first so startup failures don't create a
            # dangling cost-tracking session before generation even begins.
            docker_env.start_container()
            CostTracker.start_session(f"claude-code-eval:{self.cc_model}")
            stdout, stderr, exit_code = docker_env.run_claude_code(
                prompt=eval_prompt,
                model=self.cc_model,
                tools=self.tools,
                max_turns=self.max_turns,
            )

            if exit_code != 0:
                logger.warning(f"Docker Claude Code exited with code {exit_code}")
                if stderr:
                    logger.warning(f"stderr: {stderr[:500]}")

            self._parse_usage(stdout)

        except Exception as e:
            logger.error(f"Docker Claude Code execution failed: {e}")
            raise
        finally:
            docker_env.cleanup()

        print("--- Finished generation (Claude Code, Docker-isolated) ---\n")
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
                # Only record from the final "result" line which has total_cost_usd.
                # Per-turn assistant events also carry "usage" but recording those
                # would double-count costs proportional to the number of turns.
                if not isinstance(data, dict) or "total_cost_usd" not in data:
                    continue
                usage = data.get("usage", {})
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
