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
import sys
import time
from pathlib import Path

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


# gptme tool names -> Claude Code built-in tool names. ``read`` includes the
# read-only discovery tools (Glob, Grep) Claude Code uses in place of shelling
# out to ls/grep; ``append`` needs Edit for existing files and Write otherwise.
# Claude Code refuses to Edit or overwrite a file it has not Read, so the
# writing tools carry Read.
GPTME_TO_CLAUDE_CODE_TOOLS: dict[str, tuple[str, ...]] = {
    "shell": ("Bash",),
    "read": ("Read", "Glob", "Grep"),
    "save": ("Read", "Write"),
    "append": ("Read", "Edit", "Write"),
    "patch": ("Read", "Edit"),
    "morph": ("Read", "Edit"),
    "browser": ("WebFetch", "WebSearch"),
}


def map_tools_to_claude_code(tools: list[str]) -> list[str]:
    """Translate gptme tool names to Claude Code tool names.

    Names that already look like Claude Code tools (capitalized, e.g. ``Bash``)
    pass through unchanged. Unknown gptme names are dropped with a warning.
    Order is preserved and duplicates removed.
    """
    mapped: list[str] = []
    unknown: list[str] = []
    for tool in tools:
        if tool in GPTME_TO_CLAUDE_CODE_TOOLS:
            names: tuple[str, ...] = GPTME_TO_CLAUDE_CODE_TOOLS[tool]
        elif tool[:1].isupper():
            names = (tool,)
        else:
            unknown.append(tool)
            continue
        mapped.extend(n for n in names if n not in mapped)
    if unknown:
        logger.warning(
            "ClaudeCodeAgent: no Claude Code equivalent for gptme tool(s) %r; "
            "they are not available to the agent.",
            unknown,
        )
    return mapped


# Pre-approved when the eval sets no tool list and isolates host settings. A
# ``-p`` run cannot answer permission prompts, and with ``--setting-sources ""``
# no host allowlist applies, so without this an unrestricted run could not write
# or run anything. With user context the host's own permission settings decide.
UNRESTRICTED_ALLOWED_TOOLS: tuple[str, ...] = tuple(
    dict.fromkeys(n for names in GPTME_TO_CLAUDE_CODE_TOOLS.values() for n in names)
)


# Admin-managed MCP config location for the current OS. When one exists Claude
# Code takes exclusive MCP control and rejects --strict-mcp-config at startup.
# Only the current platform's path is checked: the Windows path is relative on
# POSIX, so an unrelated file under the CWD with that name would otherwise
# disable MCP isolation.
_MANAGED_MCP_CONFIG_PATH = (
    Path("C:/Program Files/ClaudeCode/managed-mcp.json")
    if sys.platform == "win32"
    else Path("/Library/Application Support/ClaudeCode/managed-mcp.json")
    if sys.platform == "darwin"
    else Path("/etc/claude-code/managed-mcp.json")
)

MANAGED_MCP_CONFIG_PATHS: tuple[Path, ...] = (_MANAGED_MCP_CONFIG_PATH,)


def _managed_mcp_config_present() -> bool:
    return any(path.is_file() for path in MANAGED_MCP_CONFIG_PATHS)


def claude_code_tool_args(
    tools: list[str] | None,
    include_user_context: bool,
    managed_mcp: bool | None = None,
) -> list[str]:
    """Isolation and tool flags shared by the local and Docker runs.

    ``tools`` are Claude Code tool names (None: unrestricted, []: none).
    ``managed_mcp``: whether an admin-managed MCP config applies to the run
    (None: check this host; the Docker run passes False for its container).
    """
    if managed_mcp is None:
        managed_mcp = _managed_mcp_config_present()
    isolation_requested = not include_user_context or tools is not None
    if isolation_requested and managed_mcp:
        # A managed config takes exclusive MCP control and rejects
        # --strict-mcp-config, so the eval cannot keep its tool contract: the
        # admin's MCP servers would load even when tools=[] requests none.
        # Fail loudly instead of silently measuring a different tool set.
        raise RuntimeError(
            "Cannot isolate MCP servers for this Claude Code eval run: an "
            "admin-managed MCP config is present, which takes exclusive MCP "
            "control and rejects --strict-mcp-config. Remove the managed config, "
            "or run the eval in Docker (its environment ignores the host's "
            "managed config)."
        )
    args: list[str] = []
    if not include_user_context:
        # Keep the host's hooks and permission allowlists out of the eval.
        args.extend(["--setting-sources", ""])
    if isolation_requested:
        # --tools limits only built-in tools; MCP servers add their own, so a
        # tool-limited eval loads none even when it keeps user settings.
        args.append("--strict-mcp-config")
    if tools is None:
        if include_user_context:
            return args
        args.extend(["--allowedTools", ",".join(UNRESTRICTED_ALLOWED_TOOLS)])
    else:
        # --tools restricts the built-in set; --allowedTools pre-approves it.
        args.extend(["--tools", ",".join(tools)])
        if tools:
            args.extend(["--allowedTools", ",".join(tools)])
    return args


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

        cmd.extend(
            claude_code_tool_args(self._claude_code_tools(), self.include_user_context)
        )

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

        cc_tools = self._claude_code_tools()

        try:
            # Start the container first so startup failures don't create a
            # dangling cost-tracking session before generation even begins.
            docker_env.start_container()
            CostTracker.start_session(f"claude-code-eval:{self.cc_model}")
            stdout, stderr, exit_code = docker_env.run_claude_code(
                prompt=eval_prompt,
                model=self.cc_model,
                tools=cc_tools,
                max_turns=self.max_turns,
                include_user_context=self.include_user_context,
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

    def _claude_code_tools(self) -> list[str] | None:
        """Claude Code tool names for ``self.tools``; None means no restriction.

        An explicit empty list stays empty, so a run that asked for no tools
        gets ``--tools ""`` rather than Claude Code's default set.
        """
        if self.tools is None:
            return None
        return map_tools_to_claude_code(self.tools)

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
