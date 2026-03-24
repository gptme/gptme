#!/usr/bin/env python3
"""Aggregate eval results into a model comparison leaderboard.

Reads eval_results/*/eval_results.csv files and generates a comparison table
showing pass rates per model across test suites.

Usage (standalone):
    python -m gptme.eval.leaderboard [--format rst|csv|markdown|json|html] [--min-tests N]

Usage (via eval CLI):
    gptme eval --leaderboard [--leaderboard-format rst|csv|markdown|json|html]
"""

import argparse
import csv
import html
import io
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Test suite membership — tests not in either set count toward Overall only
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
    # practical (original)
    "build-api",
    "parse-log",
    "add-error-handling",
    # practical2
    "sort-and-filter",
    "template-fill",
    "validate-csv",
    # practical3
    "write-tests-calculator",
    "sqlite-store",
    # practical4
    "group-by",
    "schedule-overlaps",
    "topo-sort",
    # practical5
    "rename-function",
    "data-pipeline",
    "regex-scrub",
    # practical6
    "csv-analysis",
    "word-frequency",
    "merge-configs",
    # practical7
    "ini-to-json",
    "json-diff",
    "changelog-gen",
    # practical8
    "url-stats",
    "markdown-toc",
    "json-flatten",
    # practical9
    "env-parser",
    "yaml-merge",
    "git-log-stats",
    # practical10
    "semver-sort",
    "date-histogram",
    "tsv-to-csv",
    # practical11
    "roman-numerals",
    "run-length-encoding",
    "anagram-groups",
    # practical12
    "frequent-words",
    "collatz-sequence",
    "log-level-stats",
    # practical13
    "summary-stats",
    "pascal-triangle",
    "caesar-cipher",
    # practical14
    "matrix-transpose",
    "ipv4-classify",
    "bracket-balance",
}


def normalize_model(model: str) -> str:
    """Normalize model names for cleaner display."""
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
        # OpenRouter-proxied versions of direct-API models
        "openrouter/openai/gpt-4o-mini": "GPT-4o Mini (OR)",
        # Model IDs without date suffix
        "anthropic/claude-sonnet-4-5": "Claude Sonnet 4.5",
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
    """Load all eval results from CSV files in timestamped subdirectories."""
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
            # Sort by run_dir to ensure chronological order
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
    # Tie-break: prefer format with more tests.
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

    max_model_len = max(
        (len(normalize_model(s["model"])) for s in ranked),
        default=5,
    )
    model_col_width = max(5, max_model_len)

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


def format_html_page(ranked: list[dict]) -> str:
    """Format results as a self-contained HTML page for publishing."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_models = len(ranked)
    max_tests = max((s["total_tests"] for s in ranked), default=0)

    rows_html = []
    for i, stats in enumerate(ranked):
        display_name = normalize_model(stats["model"])
        fmt = stats["format"] or "default"
        pct = stats["pass_rate"] * 100

        if pct >= 80:
            badge_class = "badge-green"
        elif pct >= 50:
            badge_class = "badge-yellow"
        else:
            badge_class = "badge-red"

        basic = (
            f"{stats['basic_passed']}/{stats['basic_total']}"
            if stats["basic_total"] > 0
            else "-"
        )
        practical = (
            f"{stats['practical_passed']}/{stats['practical_total']}"
            if stats["practical_total"] > 0
            else "-"
        )

        rows_html.append(
            f"<tr>"
            f"<td class='rank'>{i + 1}</td>"
            f"<td class='model'>{_html_escape(display_name)}</td>"
            f"<td>{_html_escape(fmt)}</td>"
            f"<td><span class='badge {badge_class}'>{pct:.0f}%</span></td>"
            f"<td>{stats['total_passed']}/{stats['total_tests']}</td>"
            f"<td>{basic}</td>"
            f"<td>{practical}</td>"
            f"<td><div class='bar' style='width:{pct:.0f}%'></div></td>"
            f"</tr>"
        )

    rows_str = "\n        ".join(rows_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gptme Eval Leaderboard</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --accent: #58a6ff;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); padding: 2rem 1rem;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  h1 a {{ color: var(--accent); text-decoration: none; }}
  h1 a:hover {{ text-decoration: underline; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  table {{
    width: 100%; border-collapse: collapse;
    background: var(--surface); border-radius: 6px; overflow: hidden;
  }}
  th, td {{ padding: 0.6rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: var(--bg); font-size: 0.8rem; text-transform: uppercase;
       letter-spacing: 0.05em; color: var(--muted); }}
  tr:last-child td {{ border-bottom: none; }}
  .rank {{ color: var(--muted); width: 2rem; text-align: center; }}
  .model {{ font-weight: 600; }}
  .badge {{
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 12px;
    font-size: 0.8rem; font-weight: 600;
  }}
  .badge-green {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .badge-yellow {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .badge-red {{ background: rgba(248,81,73,0.15); color: var(--red); }}
  td:last-child {{ width: 120px; }}
  .bar {{
    height: 8px; border-radius: 4px; background: var(--accent);
    min-width: 2px; transition: width 0.3s;
  }}
  footer {{ margin-top: 1.5rem; color: var(--muted); font-size: 0.8rem; text-align: center; }}
  footer a {{ color: var(--accent); text-decoration: none; }}
  @media (max-width: 640px) {{
    th, td {{ padding: 0.4rem; font-size: 0.85rem; }}
    td:last-child {{ display: none; }}
    th:last-child {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1><a href="https://gptme.org">gptme</a> Eval Leaderboard</h1>
  <p class="meta">{total_models} models &middot; up to {max_tests} tests &middot; updated {now}</p>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Model</th><th>Format</th><th>Rate</th>
        <th>Passed</th><th>Basic</th><th>Practical</th><th></th>
      </tr>
    </thead>
    <tbody>
        {rows_str}
    </tbody>
  </table>
  <footer>
    Generated by <a href="https://github.com/gptme/gptme">gptme</a> eval suite
    &middot; <code>gptme eval --leaderboard --leaderboard-format html</code>
  </footer>
</div>
</body>
</html>"""


def _html_escape(s: str) -> str:
    """HTML escaping for untrusted model names."""
    return html.escape(s)


def format_json(ranked: list[dict]) -> str:
    """Format results as JSON for programmatic use."""
    models = [
        {
            "model": stats["model"],
            "display_name": normalize_model(stats["model"]),
            "format": stats["format"] or "default",
            "pass_rate": round(stats["pass_rate"], 4),
            "total_passed": stats["total_passed"],
            "total_tests": stats["total_tests"],
            "basic": {
                "passed": stats["basic_passed"],
                "total": stats["basic_total"],
            },
            "practical": {
                "passed": stats["practical_passed"],
                "total": stats["practical_total"],
            },
        }
        for stats in ranked
    ]
    return json.dumps({"models": models}, indent=2)


def generate_leaderboard(
    results_dir: Path,
    output_format: str = "markdown",
    min_tests: int = 4,
) -> str:
    """Generate a leaderboard from eval results.

    Args:
        results_dir: Path to eval_results directory with timestamped subdirs.
        output_format: One of "rst", "csv", "markdown", "json", "html".
        min_tests: Minimum number of tests for a model to appear.

    Returns:
        Formatted leaderboard string.

    Raises:
        FileNotFoundError: If results_dir does not exist or contains no results.
        ValueError: If no models meet the min_tests threshold.
    """
    results = load_results(results_dir)
    if not results:
        raise FileNotFoundError(f"No eval results found in {results_dir}")

    ranked = aggregate_results(results, min_tests=min_tests)
    if not ranked:
        raise ValueError(f"No models with >= {min_tests} tests found.")

    formatters = {
        "rst": format_rst_table,
        "csv": format_csv_table,
        "markdown": format_markdown_table,
        "json": format_json,
        "html": format_html_page,
    }
    formatter = formatters.get(output_format)
    if formatter is None:
        raise ValueError(
            f"Unknown format: {output_format!r}. Valid formats: {list(formatters)}"
        )
    return formatter(ranked)


def main():
    parser = argparse.ArgumentParser(description="Generate eval leaderboard")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("eval_results"),
        help="Directory containing eval results",
    )
    parser.add_argument(
        "--format",
        choices=["rst", "csv", "markdown", "json", "html"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--min-tests",
        type=int,
        default=4,
        help="Minimum number of tests for a model to be included (default: 4)",
    )
    args = parser.parse_args()

    try:
        output = generate_leaderboard(
            results_dir=args.results_dir,
            output_format=args.format,
            min_tests=args.min_tests,
        )
    except (FileNotFoundError, ValueError) as e:
        # Print a placeholder instead of failing — allows docs builds to succeed
        # even when eval results are not available
        print(f"*No leaderboard data available ({e})*", file=sys.stderr)
        if args.format == "rst":
            print("*No eval results available. Run evals to populate the leaderboard.*")
        elif args.format == "html":
            print("<p><em>No eval results available.</em></p>")
        elif args.format == "json":
            print('{"models": [], "error": "' + str(e).replace('"', '\\"') + '"}')
        else:
            print("*No eval results available.*")
        return
    if args.format == "csv":
        print(output, end="")
    else:
        print(output)


if __name__ == "__main__":
    main()
