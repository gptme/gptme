"""Batch runner for gptme utility CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO


def _read_prompts(stdin: TextIO) -> list[str]:
    return [prompt for line in stdin if (prompt := line.strip())]


def _usage_tokens(metadata: dict[str, Any]) -> int:
    usage = metadata.get("usage")
    if not isinstance(usage, dict):
        return 0

    token_keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    )
    total = 0
    for key in token_keys:
        value = usage.get(key, 0)
        if isinstance(value, int):
            total += value
    return total


def _iter_json_events(stdout: str) -> Iterable[dict[str, Any]]:
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _count_tool_calls(content: str) -> int:
    from ..tools import ToolUse  # fmt: skip

    return sum(1 for _tooluse in ToolUse.iter_from_content(content))


def _summarize_child_output(
    *,
    index: int,
    prompt: str,
    duration_s: float,
    returncode: int,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    tokens = 0
    tool_calls = 0
    exit_reason = "done" if returncode == 0 else "error"

    for event in _iter_json_events(stdout):
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            tokens += _usage_tokens(metadata)

        if event.get("type") != "message":
            continue

        content = event.get("content")
        if isinstance(content, str):
            tool_calls += _count_tool_calls(content)
            if content.startswith("Stopped: reached max steps limit"):
                exit_reason = "max_turns"

    record: dict[str, Any] = {
        "index": index,
        "prompt": prompt,
        "exit_reason": exit_reason,
        "tokens": tokens,
        "duration_s": round(duration_s, 3),
        "tool_calls": tool_calls,
    }
    if returncode != 0:
        record["returncode"] = returncode
        if stderr.strip():
            record["error"] = stderr.strip().splitlines()[-1]
    return record


def _run_one_prompt(
    *,
    index: int,
    prompt: str,
    model: str | None,
    max_turns: int,
    timeout: float,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["GPTME_MAX_STEPS"] = str(max_turns)

    cmd = [
        sys.executable,
        "-m",
        "gptme",
        "--non-interactive",
        "--output-format",
        "json",
        "--no-stream",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    start = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        duration_s = time.monotonic() - start
        return {
            "index": index,
            "prompt": prompt,
            "exit_reason": "timeout",
            "tokens": 0,
            "duration_s": round(duration_s, 3),
            "tool_calls": 0,
            "error": f"timed out after {timeout:g}s",
            "returncode": None,
        }

    duration_s = time.monotonic() - start
    return _summarize_child_output(
        index=index,
        prompt=prompt,
        duration_s=duration_s,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@click.command("batch")
@click.option("--model", default=None, help="Model override for every prompt.")
@click.option(
    "--max-turns",
    default=20,
    show_default=True,
    type=click.IntRange(min=1),
    help="Maximum gptme response/tool steps per prompt.",
)
@click.option(
    "--timeout",
    default=120.0,
    show_default=True,
    type=click.FloatRange(min=0.1),
    help="Per-prompt timeout in seconds.",
)
@click.option(
    "--jsonl-only",
    is_flag=True,
    help="Suppress progress output on stderr.",
)
def batch_cmd(
    model: str | None,
    max_turns: int,
    timeout: float,
    jsonl_only: bool,
) -> None:
    """Run stdin prompts as fresh non-interactive gptme sessions."""
    prompts = _read_prompts(sys.stdin)

    for index, prompt in enumerate(prompts):
        record = _run_one_prompt(
            index=index,
            prompt=prompt,
            model=model,
            max_turns=max_turns,
            timeout=timeout,
        )
        click.echo(json.dumps(record, sort_keys=True))
        if not jsonl_only:
            click.echo(
                f"[{index + 1}/{len(prompts)}] "
                f"{record['exit_reason']} ({record['duration_s']:.1f}s)",
                err=True,
            )
