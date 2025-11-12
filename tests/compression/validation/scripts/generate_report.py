#!/usr/bin/env python3
"""Generate validation report from completed runs.

Analyzes all validation results and generates comprehensive report with:
- Success rate comparison (original vs compressed)
- Token reduction metrics
- Statistical significance testing
- Quality preservation analysis
"""

import json
import sys
from pathlib import Path
from typing import Any

from statistical_analysis import (
    calculate_mean,
    calculate_std_dev,
    welch_t_test,
)


def load_results(results_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Load all result JSON files.

    Returns:
        Dictionary mapping task names to list of results (original + compressed)
    """
    results: dict[str, list[dict[str, Any]]] = {}

    for result_file in results_dir.glob("*.json"):
        with open(result_file) as f:
            data = json.load(f)

        task_name = data["task_id"]
        if task_name not in results:
            results[task_name] = []
        results[task_name].append(data)

    return results


def calculate_success_rate(results: list[dict[str, Any]]) -> float:
    """Calculate success rate from results."""
    if not results:
        return 0.0

    successes = sum(1 for r in results if r.get("success", False))
    return successes / len(results)


def calculate_token_reduction(
    original_results: list[dict[str, Any]], compressed_results: list[dict[str, Any]]
) -> dict[str, float]:
    """Calculate token reduction metrics."""
    if not original_results or not compressed_results:
        return {"mean": 0.0, "std_dev": 0.0}

    reductions = []
    for orig, comp in zip(original_results, compressed_results):
        orig_tokens = orig.get("metrics", {}).get("input_tokens", 0)
        comp_tokens = comp.get("metrics", {}).get("input_tokens", 0)

        if orig_tokens > 0:
            reduction = ((orig_tokens - comp_tokens) / orig_tokens) * 100
            reductions.append(reduction)

    return {
        "mean": calculate_mean(reductions),
        "std_dev": calculate_std_dev(reductions),
    }


def generate_report(results_dir: Path, output_file: Path) -> None:
    """Generate comprehensive validation report."""

    # Load all results
    results_by_task = load_results(results_dir)

    if not results_by_task:
        print("Error: No results found", file=sys.stderr)
        sys.exit(1)

    # Separate original vs compressed
    original_results = []
    compressed_results = []

    for _task_name, task_results in results_by_task.items():
        for result in task_results:
            config = result.get("config", "")
            if "original" in config:
                original_results.append(result)
            elif "compressed" in config:
                compressed_results.append(result)

    # Calculate metrics
    original_success_rate = calculate_success_rate(original_results)
    compressed_success_rate = calculate_success_rate(compressed_results)

    token_reduction = calculate_token_reduction(original_results, compressed_results)

    # Statistical significance
    orig_successes = [1.0 if r.get("success") else 0.0 for r in original_results]
    comp_successes = [1.0 if r.get("success") else 0.0 for r in compressed_results]

    t_test_result = welch_t_test(orig_successes, comp_successes)

    # Format p-value safely
    p_val = t_test_result.get("p_value", 1.0)
    if isinstance(p_val, int | float):
        p_val_str = f"{p_val:.4f}"
    else:
        p_val_str = "N/A"

    # Generate markdown report
    report_lines = [
        "# Context Compression Validation Report",
        "",
        "## Executive Summary",
        "",
        f"**Original Success Rate**: {original_success_rate:.1%}",
        f"**Compressed Success Rate**: {compressed_success_rate:.1%}",
        f"**Quality Preservation**: {(compressed_success_rate/original_success_rate)*100:.1f}%",
        f"**Token Reduction**: {token_reduction['mean']:.1f}% ± {token_reduction['std_dev']:.1f}%",
        "",
        f"**Statistical Significance**: {'Yes' if t_test_result.get('significant') else 'No'} "
        f"(p={p_val_str})",
        "",
        f"**Threshold Met**: {'✅ YES' if compressed_success_rate/original_success_rate >= 0.95 else '❌ NO'} "
        f"(95% threshold)",
        "",
        "## Results by Task",
        "",
    ]

    # Per-task breakdown
    for task_name in sorted(results_by_task.keys()):
        task_results = results_by_task[task_name]

        orig = [r for r in task_results if "original" in r.get("config_name", "")]
        comp = [r for r in task_results if "compressed" in r.get("config_name", "")]

        report_lines.extend([f"### {task_name}", ""])

        if orig and comp:
            orig_success = orig[0].get("success", False)
            comp_success = comp[0].get("success", False)

            report_lines.extend(
                [
                    f"- **Original**: {'✅ Pass' if orig_success else '❌ Fail'}",
                    f"- **Compressed**: {'✅ Pass' if comp_success else '❌ Fail'}",
                    "",
                ]
            )

    # Write report
    with open(output_file, "w") as f:
        f.write("\n".join(report_lines))

    print(f"Report generated: {output_file}")
    print(
        f"\nQuality preservation: {(compressed_success_rate/original_success_rate)*100:.1f}%"
    )
    print(
        f"Token reduction: {token_reduction['mean']:.1f}% ± {token_reduction['std_dev']:.1f}%"
    )
    print(
        f"Threshold (95%) met: {'✅ YES' if compressed_success_rate/original_success_rate >= 0.95 else '❌ NO'}"
    )


def main():
    results_dir = Path("tests/compression/validation/results")
    output_file = Path("tests/compression/validation/validation_report.md")

    if not results_dir.exists():
        print(f"Error: Results directory not found: {results_dir}", file=sys.stderr)
        sys.exit(1)

    generate_report(results_dir, output_file)


if __name__ == "__main__":
    main()
