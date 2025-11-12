"""Context size and token measurement for validation suite.

Measures input context size, compression ratios, and token usage
to analyze compression effectiveness.
"""

import json
import subprocess
from pathlib import Path
from typing import Any


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count tokens in text using gptme-util.

    Args:
        text: Text to count tokens in
        model: Model to use for tokenization (default: gpt-4)

    Returns:
        Token count, or estimate if gptme-util unavailable
    """
    try:
        # Try using gptme-util
        result = subprocess.run(
            ["gptme-util", "tokens", "count"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse output (format: "Tokens: 1234")
        output = result.stdout.strip()
        if "Tokens:" in output:
            return int(output.split("Tokens:")[1].strip())

        # Fallback to simple estimate
        return estimate_tokens(text)

    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        # Fallback to estimation
        return estimate_tokens(text)


def estimate_tokens(text: str) -> int:
    """Estimate token count using simple heuristic.

    Uses ~4 characters per token as rough approximation.
    """
    return len(text) // 4


def measure_context_size(workspace: Path) -> dict[str, Any]:
    """Measure context size for a task run.

    Reads gptme conversation log and analyzes context usage.

    Args:
        workspace: Task workspace directory

    Returns:
        Dictionary with context metrics:
        - total_tokens: Total tokens in context
        - total_chars: Total characters in context
        - system_tokens: Tokens in system prompt
        - user_tokens: Tokens in user messages
        - assistant_tokens: Tokens in assistant messages
    """
    # Find conversation log
    log_file = workspace / ".gptme" / "conversation.jsonl"

    if not log_file.exists():
        return {
            "total_tokens": 0,
            "total_chars": 0,
            "system_tokens": 0,
            "user_tokens": 0,
            "assistant_tokens": 0,
            "error": "No conversation log found",
        }

    # Parse conversation
    messages = []
    with open(log_file) as f:
        for line in f:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Aggregate by role
    metrics = {
        "total_tokens": 0,
        "total_chars": 0,
        "system_tokens": 0,
        "user_tokens": 0,
        "assistant_tokens": 0,
    }

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        tokens = count_tokens(content)
        chars = len(content)

        metrics["total_tokens"] += tokens
        metrics["total_chars"] += chars

        if role == "system":
            metrics["system_tokens"] += tokens
        elif role == "user":
            metrics["user_tokens"] += tokens
        elif role == "assistant":
            metrics["assistant_tokens"] += tokens

    return metrics


def calculate_compression_ratio(
    original_metrics: dict[str, Any],
    compressed_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Calculate compression ratio and savings.

    Args:
        original_metrics: Context metrics from original run
        compressed_metrics: Context metrics from compressed run

    Returns:
        Dictionary with compression statistics:
        - token_reduction_pct: Percentage of tokens reduced
        - char_reduction_pct: Percentage of characters reduced
        - tokens_saved: Number of tokens saved
        - chars_saved: Number of characters saved
    """
    orig_tokens = original_metrics.get("total_tokens", 0)
    comp_tokens = compressed_metrics.get("total_tokens", 0)

    orig_chars = original_metrics.get("total_chars", 0)
    comp_chars = compressed_metrics.get("total_chars", 0)

    if orig_tokens == 0:
        return {
            "token_reduction_pct": 0.0,
            "char_reduction_pct": 0.0,
            "tokens_saved": 0,
            "chars_saved": 0,
            "error": "Original metrics missing",
        }

    tokens_saved = orig_tokens - comp_tokens
    chars_saved = orig_chars - comp_chars

    return {
        "token_reduction_pct": (tokens_saved / orig_tokens) * 100,
        "char_reduction_pct": (chars_saved / orig_chars) * 100,
        "tokens_saved": tokens_saved,
        "chars_saved": chars_saved,
    }


def measure_compression_effectiveness(
    task_id: str,
    original_workspace: Path,
    compressed_workspace: Path,
) -> dict[str, Any]:
    """Measure compression effectiveness for a task.

    Compares original and compressed runs to calculate metrics.

    Args:
        task_id: Task identifier
        original_workspace: Workspace from original run
        compressed_workspace: Workspace from compressed run

    Returns:
        Complete effectiveness metrics
    """
    original_metrics = measure_context_size(original_workspace)
    compressed_metrics = measure_context_size(compressed_workspace)

    compression_stats = calculate_compression_ratio(
        original_metrics, compressed_metrics
    )

    return {
        "task_id": task_id,
        "original": original_metrics,
        "compressed": compressed_metrics,
        "compression": compression_stats,
    }


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate metrics across all validation results.

    Args:
        results: List of task results with context metrics

    Returns:
        Aggregated metrics summary
    """
    # Group by config
    by_config: dict[str, list[dict[str, Any]]] = {}

    for result in results:
        config = result.get("config", "unknown")
        if config not in by_config:
            by_config[config] = []
        by_config[config].append(result)

    # Calculate aggregate stats per config
    aggregated = {}

    for config, config_results in by_config.items():
        total_tokens = sum(
            r.get("context_metrics", {}).get("total_tokens", 0) for r in config_results
        )

        avg_tokens = total_tokens / len(config_results) if config_results else 0

        aggregated[config] = {
            "total_tasks": len(config_results),
            "total_tokens": total_tokens,
            "avg_tokens": avg_tokens,
        }

    # Calculate compression savings if both original and compressed exist
    if "original" in aggregated and any("compressed" in k for k in aggregated):
        original_total = aggregated["original"]["total_tokens"]

        for config, stats in aggregated.items():
            if "compressed" in config and original_total > 0:
                compressed_total = stats["total_tokens"]
                stats["token_reduction_pct"] = (
                    (original_total - compressed_total) / original_total
                ) * 100

    return aggregated
