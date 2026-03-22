#!/usr/bin/env python3
"""Aggregate eval results into a model comparison leaderboard.

Reads eval_results/*/eval_results.csv files and generates a comparison table
showing pass rates per model across test suites.

Usage:
    python scripts/eval_leaderboard.py [--format rst|csv|markdown] [--min-tests N]
"""

import csv
import io
import sys
from collections import defaultdict
from pathlib import Path

# Test suite membership
BASIC_TESTS = {
    "hello",
    "hello-patch",
    "hello-ask",
    "prime100",
    "read-modify",
    "write-tests",
    "json-transform",
    "count-words",
    "multi-file-refactor",
    "fix-bug",
    "generate-cli",
    "implement-class",
    "json-filter",
    "find-and-fix",
    "debug-type-error",
    "fix-import-error",
    "extract-function",
    "optimize-performance",
}

PRACTICAL_TESTS = {
    "build-api",
    "parse-log",
    "add-error-handling",
    "sort-and-filter",
    "template-fill",
    "validate-csv",
    "write-tests-calculator",
    "sqlite-store",
    "group-by",
    "schedule-overlaps",
    "topo-sort",
    "rename-function",
    "data-pipeline",
    "regex-scrub",
    "csv-analysis",
    "word-frequency",
    "merge-configs",
    "ini-to-json",
    "json-diff",
    "changelog-gen",
}


def normalize_model(model: str) -> str:
    """Normalize model names for cleaner display."""
    # Remove provider prefixes for cleaner names
    replacements = {
        "anthropic/claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet (Oct 2024)",
        "anthropic/claude-3-5-sonnet-20240620": "Claude 3.5 Sonnet (Jun 2024)",
        "anthropic/claude-3-5-haiku-20241022": "Claude 3.5 Haiku",
        "anthropic/claude-3-haiku-20240307": "Claude 3 Haiku",
        "anthropic/claude-haiku-4-5": "Claude Haiku 4.5",
        "anthropic/claude-sonnet-4-20250514": "Claude Sonnet 4",
        "anthropic/claude-sonnet-4-6": "Claude Sonnet 4.6",
        "anthropic/claude-opus-4-1-20250805": "Claude Opus 4.1",
        "anthropic/claude-opus-4-6": "Claude Opus 4.6",
        "openai/gpt-4o": "GPT-4o",
        "openai/gpt-4o-mini": "GPT-4o Mini",
        "openai/gpt-4-turbo": "GPT-4 Turbo",
        "openai/gpt-5": "GPT-5",
        "openai/gpt-5-mini": "GPT-5 Mini",
        "openai/o1-mini": "o1-mini",
        "openai/o1-preview": "o1-preview",
        "openai-subscription/gpt-5.4": "GPT-5.4",
        "deepseek/deepseek-chat": "DeepSeek V3",
        "deepseek/deepseek-reasoner": "DeepSeek R1",
        "gemini/gemini-1.5-flash-latest": "Gemini 1.5 Flash",
        "gemini/gemini-2.5-flash": "Gemini 2.5 Flash",
        "groq/moonshotai/kimi-k2-instruct": "Kimi K2",
        "groq/qwen/qwen3-32b": "Qwen3 32B",
        "openrouter/google/gemini-flash-1.5": "Gemini 1.5 Flash (OR)",
        "openrouter/google/gemini-pro-1.5": "Gemini 1.5 Pro (OR)",
        "openrouter/google/gemma-2-9b-it": "Gemma 2 9B",
        "openrouter/google/gemma-2-27b-it": "Gemma 2 27B",
        "openrouter/meta-llama/llama-3.1-8b-instruct": "Llama 3.1 8B",
        "openrouter/meta-llama/llama-3.1-70b-instruct": "Llama 3.1 70B",
        "openrouter/meta-llama/llama-3.1-405b-instruct": "Llama 3.1 405B",
        "openrouter/meta-llama/llama-3.2-11b-vision-instruct": "Llama 3.2 11B",
        "openrouter/meta-llama/llama-3.2-90b-vision-instruct": "Llama 3.2 90B",
        "openrouter/nousresearch/hermes-2-pro-llama-3-8b": "Hermes 2 Pro 8B",
        "openrouter/nousresearch/hermes-3-llama-3.1-405b": "Hermes 3 405B",
        "openrouter/nousresearch/hermes-3-llama-3.1-70b": "Hermes 3 70B",
        "openrouter/nousresearch/hermes-4-70b": "Hermes 4 70B",
        "openrouter/mistralai/magistral-medium-2506": "Magistral Medium",
        "openrouter/moonshotai/kimi-k2-0905": "Kimi K2 (OR)",
        "openrouter/qwen/qwen3-max": "Qwen3 Max",
        "openrouter/x-ai/grok-4-fast:free": "Grok 4 Fast",
        "openrouter/x-ai/grok-code-fast-1": "Grok Code Fast",
        "openrouter/z-ai/glm-5": "GLM-5",
    }
    return replacements.get(model, model)


def parse_model_format(model: str, fmt: str) -> tuple[str, str]:
    """Parse model name and format, handling @format suffix in model name.

    Some older CSV rows encode format in the model name as 'model@format'.
    Newer rows use the Tool Format column separately.
    """
    if "@" in model:
        base, embedded_fmt = model.rsplit("@", 1)
        return base, embedded_fmt
    return model, fmt


def load_results(results_dir: Path) -> list[dict]:
    """Load all eval results from CSV files."""
    if not results_dir.exists():
        return []
    all_results = []
    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        csv_path = d / "eval_results.csv"
        if not csv_path.exists():
            continue
        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw_model = row.get("Model", "").strip()
                    raw_fmt = row.get("Tool Format", "").strip()
                    test = row.get("Test", "").strip()
                    passed = row.get("Passed", "").strip().lower()
                    if raw_model and test and passed in ("true", "false"):
                        model, fmt = parse_model_format(raw_model, raw_fmt)
                        all_results.append(
                            {
                                "model": model,
                                "format": fmt,
                                "test": test,
                                "passed": passed == "true",
                                "run_dir": d.name,
                            }
                        )
        except csv.Error as e:
            print(f"Warning: skipping malformed CSV {csv_path}: {e}", file=sys.stderr)
    return all_results


def aggregate_results(results: list[dict], min_tests: int = 4) -> list[dict]:
    """Aggregate results into per-model stats.

    For each model, picks the best tool format and computes pass rates
    on basic and practical test suites.
    """
    # Group by (model, format) -> test -> latest result
    model_fmt: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in results:
        model_fmt[(r["model"], r["format"])][r["test"]].append(r)

    # For each (model, format), compute stats
    fmt_stats = {}
    for (model, fmt), tests_dict in model_fmt.items():
        basic_passed = 0
        basic_total = 0
        practical_passed = 0
        practical_total = 0
        total_passed = 0
        total_tests = 0

        for test_name, runs in tests_dict.items():
            # Sort by run_dir to ensure chronological order regardless of insertion order
            latest = sorted(runs, key=lambda r: r["run_dir"])[-1]
            total_tests += 1
            if latest["passed"]:
                total_passed += 1

            if test_name in BASIC_TESTS:
                basic_total += 1
                if latest["passed"]:
                    basic_passed += 1
            elif test_name in PRACTICAL_TESTS:
                practical_total += 1
                if latest["passed"]:
                    practical_passed += 1
            # Tests not in either suite count toward Overall but show as '-' in
            # breakdown columns — this is intentional (e.g. browser, init_projects).

        if total_tests >= min_tests:
            fmt_stats[(model, fmt)] = {
                "model": model,
                "format": fmt,
                "basic_passed": basic_passed,
                "basic_total": basic_total,
                "practical_passed": practical_passed,
                "practical_total": practical_total,
                "total_passed": total_passed,
                "total_tests": total_tests,
                "pass_rate": total_passed / total_tests if total_tests > 0 else 0,
            }

    # For each base model, pick the best format (highest pass rate).
    # Tie-breaking: on equal pass rates the first format encountered in insertion order
    # wins (strict >, not >=). Python 3.7+ preserves dict insertion order so this is
    # deterministic, but arbitrary. Prefer the format with more tests as explicit
    # secondary sort to make the intent clear.
    best_by_model: dict[str, dict] = {}
    for (model, _fmt), stats in fmt_stats.items():
        current = best_by_model.get(model)
        if (
            current is None
            or stats["pass_rate"] > current["pass_rate"]
            or (
                stats["pass_rate"] == current["pass_rate"]
                and stats["total_tests"] > current["total_tests"]
            )
        ):
            best_by_model[model] = stats

    # Sort by pass rate descending, then total tests descending
    ranked = sorted(
        best_by_model.values(),
        key=lambda x: (-x["pass_rate"], -x["total_tests"]),
    )
    return ranked


def format_rst_table(ranked: list[dict]) -> str:
    """Format results as an RST table."""
    lines = []

    # Compute dynamic Model column width so long unknown model names don't get
    # silently truncated and corrupt the RST simple table.
    max_model_len = max(
        (len(normalize_model(s["model"])) for s in ranked),
        default=5,
    )
    model_col_width = max(5, max_model_len)  # at least 5 chars

    # Format (10): covers all gptme tool-format strings (markdown/xml/tool/native/v2 ≤ 8).
    # Overall (15): covers the widest possible value "58/58 (100%)" = 12 chars.
    # Both are safe to hardcode; only Model varies by unknown external names.
    cols = [
        ("Model", model_col_width),
        ("Format", 10),
        ("Overall", 15),
        ("Basic", 10),
        ("Practical", 10),
    ]
    header_sep = "  ".join("=" * w for _, w in cols)
    header = "  ".join(f"{name:<{w}}" for name, w in cols)

    lines.append(header_sep)
    lines.append(header)
    lines.append(header_sep)

    for stats in ranked:
        display_name = normalize_model(stats["model"])
        fmt = stats["format"] or "default"
        overall = (
            f"{stats['total_passed']}/{stats['total_tests']} ({stats['pass_rate']:.0%})"
        )
        if stats["basic_total"] > 0:
            basic = f"{stats['basic_passed']}/{stats['basic_total']}"
        else:
            basic = "-"
        if stats["practical_total"] > 0:
            practical = f"{stats['practical_passed']}/{stats['practical_total']}"
        else:
            practical = "-"

        row = (
            f"{display_name:<{cols[0][1]}}  "
            f"{fmt:<{cols[1][1]}}  "
            f"{overall:<{cols[2][1]}}  "
            f"{basic:<{cols[3][1]}}  "
            f"{practical:<{cols[4][1]}}"
        )
        lines.append(row)

    lines.append(header_sep)
    return "\n".join(lines)


def format_csv_table(ranked: list[dict]) -> str:
    """Format results as a CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Model", "Format", "Passed", "Total", "Pass Rate", "Basic", "Practical"]
    )
    for stats in ranked:
        writer.writerow(
            [
                normalize_model(stats["model"]),
                stats["format"] or "default",
                stats["total_passed"],
                stats["total_tests"],
                f"{stats['pass_rate']:.1%}",
                f"{stats['basic_passed']}/{stats['basic_total']}"
                if stats["basic_total"]
                else "-",
                f"{stats['practical_passed']}/{stats['practical_total']}"
                if stats["practical_total"]
                else "-",
            ]
        )
    return output.getvalue()


def format_markdown_table(ranked: list[dict]) -> str:
    """Format results as a Markdown table."""
    lines = []
    lines.append("| Model | Format | Overall | Basic | Practical |")
    lines.append("|-------|--------|---------|-------|-----------|")

    for stats in ranked:
        display_name = normalize_model(stats["model"])
        fmt = stats["format"] or "default"
        overall = (
            f"{stats['total_passed']}/{stats['total_tests']} ({stats['pass_rate']:.0%})"
        )
        if stats["basic_total"] > 0:
            basic = f"{stats['basic_passed']}/{stats['basic_total']}"
        else:
            basic = "-"
        if stats["practical_total"] > 0:
            practical = f"{stats['practical_passed']}/{stats['practical_total']}"
        else:
            practical = "-"
        lines.append(f"| {display_name} | {fmt} | {overall} | {basic} | {practical} |")

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate eval leaderboard")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("eval_results"),
        help="Directory containing eval results",
    )
    parser.add_argument(
        "--format",
        choices=["rst", "csv", "markdown"],
        default="rst",
        help="Output format",
    )
    parser.add_argument(
        "--min-tests",
        type=int,
        default=4,
        help="Minimum number of tests for a model to be included",
    )
    args = parser.parse_args()

    results = load_results(args.results_dir)
    if not results:
        print("No eval results found.", file=sys.stderr)
        sys.exit(1)

    ranked = aggregate_results(results, min_tests=args.min_tests)
    if not ranked:
        print(
            f"No models with >= {args.min_tests} tests found.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.format == "rst":
        print(format_rst_table(ranked))
    elif args.format == "markdown":
        print(format_markdown_table(ranked))
    elif args.format == "csv":
        print(format_csv_table(ranked), end="")


if __name__ == "__main__":
    main()
