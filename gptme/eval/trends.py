#!/usr/bin/env python3
"""Track eval result trends over time — detect regressions and improvements.

Reads eval_results/*/eval_results.csv and shows how test pass rates change
across runs. Highlights tests that regressed (used to pass, now fail) or
improved (used to fail, now pass).

Usage:
    python scripts/eval_trends.py [--model MODEL] [--last N] [--format table|json]
    python scripts/eval_trends.py --regressions          # show only regressions
    python scripts/eval_trends.py --improvements         # show only improvements
    python scripts/eval_trends.py --model claude-sonnet   # filter by model substring
    python scripts/eval_trends.py --diff                  # compare latest vs previous run
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def parse_run_timestamp(dirname: str) -> datetime:
    """Parse YYYYMMDD_HHMMSSZ directory name into datetime."""
    try:
        return datetime.strptime(dirname, "%Y%m%d_%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def load_all_results(
    results_dir: Path,
) -> list[dict]:
    """Load all eval results with timestamps, sorted chronologically."""
    all_results = []
    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        csv_path = d / "eval_results.csv"
        if not csv_path.exists():
            continue
        run_ts = parse_run_timestamp(d.name)
        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    model = row.get("Model", "").strip()
                    fmt = row.get("Tool Format", "").strip()
                    test = row.get("Test", "").strip()
                    passed_str = row.get("Passed", "").strip().lower()
                    if model and test and passed_str in ("true", "false"):
                        # Handle @format suffix in model name
                        if "@" in model:
                            model, fmt = model.rsplit("@", 1)
                        all_results.append(
                            {
                                "model": model,
                                "format": fmt,
                                "test": test,
                                "passed": passed_str == "true",
                                "run_dir": d.name,
                                "timestamp": run_ts,
                            }
                        )
        except csv.Error:
            continue
    return all_results


def compute_trends(
    results: list[dict],
    model_filter: str | None = None,
    last_n_runs: int | None = None,
) -> dict:
    """Compute per-model, per-test trends over time.

    Returns a structure with regressions, improvements, and per-test history.
    """
    # Group by (model, format) -> test -> chronological list of (run_dir, passed)
    by_model_test: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for r in results:
        model_key = f"{r['model']}@{r['format']}" if r["format"] else r["model"]
        if model_filter and model_filter.lower() not in model_key.lower():
            continue
        by_model_test[model_key][r["test"]].append(
            {
                "run": r["run_dir"],
                "passed": r["passed"],
                "timestamp": r["timestamp"].isoformat(),
            }
        )

    # Detect changes between last two runs per (model, test)
    regressions = []
    improvements = []
    stable_pass = []
    stable_fail = []
    flaky = []

    for model_key, tests in sorted(by_model_test.items()):
        for test_name, runs in sorted(tests.items()):
            # Sort by run dir (chronological)
            runs_sorted = sorted(runs, key=lambda r: r["run"])

            if last_n_runs:
                runs_sorted = runs_sorted[-last_n_runs:]

            if len(runs_sorted) < 2:
                continue

            latest = runs_sorted[-1]
            previous = runs_sorted[-2]

            # Count pass rate over all available runs
            total = len(runs_sorted)
            passed = sum(1 for r in runs_sorted if r["passed"])
            pass_rate = passed / total

            entry = {
                "model": model_key,
                "test": test_name,
                "latest_passed": latest["passed"],
                "previous_passed": previous["passed"],
                "latest_run": latest["run"],
                "previous_run": previous["run"],
                "pass_rate": round(pass_rate, 3),
                "total_runs": total,
            }

            # Check pass_rate first to catch intermittent tests before binary direction.
            # Require at least 4 runs to avoid misclassifying single-change tests as flaky.
            if total >= 4 and 0.1 < pass_rate < 0.9:
                flaky.append(entry)
            elif previous["passed"] and not latest["passed"]:
                regressions.append(entry)
            elif not previous["passed"] and latest["passed"]:
                improvements.append(entry)
            elif latest["passed"]:
                stable_pass.append(entry)
            else:
                stable_fail.append(entry)

    return {
        "regressions": regressions,
        "improvements": improvements,
        "stable_pass": stable_pass,
        "stable_fail": stable_fail,
        "flaky": flaky,
    }


def compute_diff(results: list[dict], model_filter: str | None = None) -> dict:
    """Compare the latest run against the previous run for each model.

    Returns per-model diff showing which tests changed status.
    """
    # Find unique runs per model
    model_runs: dict[str, set[str]] = defaultdict(set)
    for r in results:
        model_key = f"{r['model']}@{r['format']}" if r["format"] else r["model"]
        if model_filter and model_filter.lower() not in model_key.lower():
            continue
        model_runs[model_key].add(r["run_dir"])

    diffs = {}
    for model_key in sorted(model_runs):
        runs = sorted(model_runs[model_key])
        if len(runs) < 2:
            continue

        latest_run = runs[-1]
        prev_run = runs[-2]

        # Build test -> passed maps for each run
        latest_results = {}
        prev_results = {}
        for r in results:
            mk = f"{r['model']}@{r['format']}" if r["format"] else r["model"]
            if mk != model_key:
                continue
            if r["run_dir"] == latest_run:
                latest_results[r["test"]] = r["passed"]
            elif r["run_dir"] == prev_run:
                prev_results[r["test"]] = r["passed"]

        gained = []
        lost = []
        unchanged_pass = []
        unchanged_fail = []
        new_tests = []
        removed_tests = []

        all_tests = sorted(set(latest_results) | set(prev_results))
        for test in all_tests:
            in_latest = test in latest_results
            in_prev = test in prev_results

            if in_latest and in_prev:
                if latest_results[test] and not prev_results[test]:
                    gained.append(test)
                elif not latest_results[test] and prev_results[test]:
                    lost.append(test)
                elif latest_results[test]:
                    unchanged_pass.append(test)
                else:
                    unchanged_fail.append(test)
            elif in_latest and not in_prev:
                new_tests.append({"test": test, "passed": latest_results[test]})
            elif in_prev and not in_latest:
                removed_tests.append({"test": test, "was_passing": prev_results[test]})

        diffs[model_key] = {
            "latest_run": latest_run,
            "prev_run": prev_run,
            "gained": gained,
            "lost": lost,
            "unchanged_pass": len(unchanged_pass),
            "unchanged_fail": len(unchanged_fail),
            "new_tests": new_tests,
            "removed_tests": removed_tests,
        }

    return diffs


def format_table(trends: dict) -> str:
    """Format trends as a readable table."""
    lines = []

    if trends["regressions"]:
        lines.append("## Regressions (was passing, now failing)")
        lines.append("")
        lines.append(f"{'Model':<45} {'Test':<30} {'Pass Rate':>10} {'Runs':>5}")
        lines.append("-" * 95)
        lines.extend(
            f"{r['model']:<45} {r['test']:<30} {r['pass_rate']:>9.0%} {r['total_runs']:>5}"
            for r in sorted(trends["regressions"], key=lambda x: x["pass_rate"])
        )
        lines.append("")

    if trends["improvements"]:
        lines.append("## Improvements (was failing, now passing)")
        lines.append("")
        lines.append(f"{'Model':<45} {'Test':<30} {'Pass Rate':>10} {'Runs':>5}")
        lines.append("-" * 95)
        lines.extend(
            f"{r['model']:<45} {r['test']:<30} {r['pass_rate']:>9.0%} {r['total_runs']:>5}"
            for r in sorted(
                trends["improvements"], key=lambda x: x["pass_rate"], reverse=True
            )
        )
        lines.append("")

    if trends["flaky"]:
        lines.append("## Flaky Tests (intermittent pass/fail)")
        lines.append("")
        lines.append(f"{'Model':<45} {'Test':<30} {'Pass Rate':>10} {'Runs':>5}")
        lines.append("-" * 95)
        lines.extend(
            f"{r['model']:<45} {r['test']:<30} {r['pass_rate']:>9.0%} {r['total_runs']:>5}"
            for r in sorted(trends["flaky"], key=lambda x: x["pass_rate"])
        )
        lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append(f"  Regressions:  {len(trends['regressions'])}")
    lines.append(f"  Improvements: {len(trends['improvements'])}")
    lines.append(f"  Flaky:        {len(trends['flaky'])}")
    lines.append(f"  Stable pass:  {len(trends['stable_pass'])}")
    lines.append(f"  Stable fail:  {len(trends['stable_fail'])}")

    return "\n".join(lines)


def format_diff(diffs: dict) -> str:
    """Format run-to-run diff as a readable summary."""
    lines = []
    for model, diff in diffs.items():
        lines.append(f"## {model}")
        lines.append(f"  Comparing: {diff['prev_run']} → {diff['latest_run']}")

        if diff["gained"]:
            lines.append(f"  + Gained ({len(diff['gained'])}):")
            lines.extend(f"    + {t}" for t in diff["gained"])

        if diff["lost"]:
            lines.append(f"  - Lost ({len(diff['lost'])}):")
            lines.extend(f"    - {t}" for t in diff["lost"])

        if diff["new_tests"]:
            lines.append(f"  * New tests ({len(diff['new_tests'])}):")
            lines.extend(
                f"    * {t['test']} ({'PASS' if t['passed'] else 'FAIL'})"
                for t in diff["new_tests"]
            )

        if diff.get("removed_tests"):
            lines.append(f"  ~ Removed tests ({len(diff['removed_tests'])}):")
            lines.extend(
                f"    ~ {t['test']} (was {'PASS' if t['was_passing'] else 'FAIL'})"
                for t in diff["removed_tests"]
            )

        lines.append(
            f"  Unchanged: {diff['unchanged_pass']} pass, {diff['unchanged_fail']} fail"
        )
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Track eval result trends over time")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("eval_results"),
        help="Directory containing eval results",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Filter by model name substring (e.g. 'claude-sonnet')",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help="Only consider last N runs per model/test",
    )
    filter_group = parser.add_mutually_exclusive_group()
    filter_group.add_argument(
        "--regressions",
        action="store_true",
        help="Show only regressions",
    )
    filter_group.add_argument(
        "--improvements",
        action="store_true",
        help="Show only improvements",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Compare latest vs previous run (per model)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    args = parser.parse_args()

    if not args.results_dir.is_dir():
        print(f"Results directory not found: {args.results_dir}", file=sys.stderr)
        sys.exit(1)

    if args.diff and args.last is not None:
        print("Warning: --last is ignored in --diff mode.", file=sys.stderr)

    if args.diff and (args.regressions or args.improvements):
        print(
            "Warning: --regressions/--improvements is ignored in --diff mode.",
            file=sys.stderr,
        )

    results = load_all_results(args.results_dir)
    if not results:
        print("No eval results found.", file=sys.stderr)
        sys.exit(1)

    if args.diff:
        diffs = compute_diff(results, model_filter=args.model)
        if args.format == "json":
            print(json.dumps(diffs, indent=2))
        else:
            output = format_diff(diffs)
            if output.strip():
                print(output)
            else:
                print("No comparable runs found.")
        return

    trends = compute_trends(results, model_filter=args.model, last_n_runs=args.last)

    if args.regressions:
        trends = {**trends, "improvements": [], "flaky": []}
    elif args.improvements:
        trends = {**trends, "regressions": [], "flaky": []}

    if args.format == "json":
        print(json.dumps(trends, indent=2, default=str))
        return

    print(format_table(trends))


if __name__ == "__main__":
    main()
