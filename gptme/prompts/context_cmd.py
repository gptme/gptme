import logging
import subprocess
import time
from pathlib import Path

from ..message import len_tokens
from ..util.context import md_codeblock

logger = logging.getLogger(__name__)

# Maximum characters for context_cmd output to prevent context explosion
# ~100k chars ~ ~25k tokens, a reasonable safeguard for most context windows
CONTEXT_CMD_MAX_CHARS = 100_000


def _truncate_context_output(
    output: str, max_chars: int = CONTEXT_CMD_MAX_CHARS
) -> str:
    """Truncate context output if it exceeds max_chars, with a clear message."""
    if len(output) <= max_chars:
        return output

    # Keep the first portion of output with truncation notice
    truncated = output[:max_chars]
    # Find a good break point (newline) to avoid cutting mid-line
    # Use max(0, ...) to handle edge case where max_chars < 1000
    last_newline = truncated.rfind("\n", max(0, max_chars - 1000), max_chars)
    if last_newline > max(0, max_chars - 1000):
        truncated = truncated[:last_newline]

    original_chars = len(output)
    kept_chars = len(truncated)
    logger.warning(
        f"Context command output truncated: {original_chars:,} chars -> {kept_chars:,} chars "
        f"(limit: {max_chars:,}). Consider optimizing your context_cmd."
    )

    truncation_notice = (
        f"\n\n... [TRUNCATED: output was {original_chars:,} chars, "
        f"showing first {kept_chars:,} chars to prevent context overflow] ..."
    )
    return truncated + truncation_notice


def get_project_context_cmd_output(cmd: str, workspace: Path) -> str | None:
    from ..util import console

    console.log(f"Using project context command: {cmd}")
    try:
        start = time.time()
        # shell=True is intentional: `cmd` is a user-configured project context
        # command. The trust model is that the user controls `cmd` and is also
        # the threat model — no untrusted input should ever reach this path.
        result = subprocess.run(
            cmd,
            check=False,
            cwd=workspace,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        duration = time.time() - start
        logger.log(
            logging.WARNING if duration > 10.0 else logging.DEBUG,
            f"Context command took {duration:.2f}s",
        )
        if result.returncode == 0:
            output = _truncate_context_output(result.stdout)
            length = len_tokens(output, "gpt-4")
            if length > 10000:
                logger.warning(
                    f"Context command '{cmd}' output is large: ~{length} tokens, consider optimizing."
                )
            return md_codeblock(cmd, output)
        logger.warning(f"Context command '{cmd}' exited with code {result.returncode}")
        # Include both stdout (partial results) and stderr (error details)
        # so LLM can see what worked and what failed for self-recovery
        # Truncate stdout to prevent context explosion, truncate stderr for safety
        output = _truncate_context_output(result.stdout)
        stderr_stripped = result.stderr.strip()
        if stderr_stripped:
            stderr_preview = stderr_stripped[:500]
            if len(stderr_stripped) > 500:
                stderr_preview += "\n... (truncated)"
            output += f"\n\n## Context Generation Error (exit {result.returncode})\n\n{stderr_preview}"
        return md_codeblock(cmd, output)
    except Exception as e:
        logger.error(f"Error running context command: {e}")
    return None
