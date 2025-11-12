#!/usr/bin/env python3
"""
Context Compression Validation Suite Runner (Enhanced)

Executes test tasks with compressed and original context to measure
task completion quality impact.

Days 3-4 Enhancements:
- Custom validators for sophisticated criteria checking
- Context size and token measurement
- Statistical comparison tools
- Visualization generation (charts, HTML reports)

Usage:
    python run_validation.py --corpus ../corpus --results ../results
    python run_validation.py --category bug-fix --dry-run
    python run_validation.py --task task-001 --compression-ratio 0.15
    python run_validation.py --generate-reports
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# Import enhancement modules
from context_metrics import (
    aggregate_metrics,
    measure_context_size,
)
from enhanced_validators import validate_all_criteria
from statistical_analysis import (
    assess_quality_degradation,
    generate_comparison_report,
)
from visualization import (
    generate_html_report,
    generate_markdown_summary,
)


class ValidationRunner:
    """Runs validation tests and collects results."""

    def __init__(
        self,
        corpus_dir: Path,
        results_dir: Path,
        gptme_path: Path | None = None,
    ):
        self.corpus_dir = corpus_dir
        self.results_dir = results_dir
        # Default to repository root (4 levels up from this script)
        if gptme_path is None:
            gptme_path = Path(__file__).resolve().parent.parent.parent.parent
        self.gptme_path = gptme_path
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def load_tasks(self, category: str | None = None) -> list[dict[str, Any]]:
        """Load task definitions from corpus."""
        tasks = []
        for task_file in sorted(self.corpus_dir.glob("task-*.yaml")):
            with open(task_file) as f:
                task = yaml.safe_load(f)
                if category is None or task.get("category") == category:
                    tasks.append(task)
        return tasks

    def run_task(
        self,
        task: dict[str, Any],
        compression_ratio: float | None,
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Run a single task with or without compression."""
        task_id = task["id"]
        config_label = (
            f"compressed-{compression_ratio}" if compression_ratio else "original"
        )

        print(f"Running {task_id} ({config_label})...")

        # Create temporary workspace
        workspace = self.results_dir / f"{task_id}-{config_label}"
        workspace.mkdir(parents=True, exist_ok=True)

        # Write gptme.toml if compression enabled
        if compression_ratio is not None:
            config_path = workspace / "gptme.toml"
            config_content = f"""
[compression]
enabled = true
target_ratio = {compression_ratio}
min_section_length = 50
"""
            config_path.write_text(config_content)

        # Run gptme with task prompt
        cmd = [
            "gptme",
            "-n",  # Non-interactive
            "--workspace",
            str(workspace.resolve()),  # Use absolute path
            task["prompt"],
        ]

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.gptme_path,
            )
            success = result.returncode == 0
            output = result.stdout
            error = result.stderr
        except subprocess.TimeoutExpired:
            success = False
            output = ""
            error = f"Timeout after {timeout}s"

        duration = time.time() - start_time

        # Check success criteria using enhanced validators
        criteria_met = validate_all_criteria(
            task.get("success_criteria", []), workspace, output
        )

        # Measure context size
        context_metrics = measure_context_size(workspace)

        return {
            "task_id": task_id,
            "config": config_label,
            "compression_ratio": compression_ratio,
            "success": success,
            "criteria_met": criteria_met,
            "duration": duration,
            "output": output,
            "error": error,
            "context_metrics": context_metrics,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def run_validation(
        self,
        tasks: list[dict[str, Any]],
        compression_ratios: list[float | None],
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Run validation suite across tasks and compression ratios."""
        results = []

        for task in tasks:
            for ratio in compression_ratios:
                if dry_run:
                    print(
                        f"Would run: {task['id']} "
                        f"(ratio={ratio if ratio else 'original'})"
                    )
                else:
                    result = self.run_task(task, ratio)
                    results.append(result)
                    self._save_result(result)

        return results

    def _save_result(self, result: dict[str, Any]) -> None:
        """Save individual result to file."""
        task_id = result["task_id"]
        config = result["config"]
        filename = f"{task_id}-{config}-{result['timestamp']}.json"
        filepath = self.results_dir / filename

        with open(filepath, "w") as f:
            json.dump(result, f, indent=2)

    def generate_basic_report(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate basic summary report from results."""
        if not results:
            return {"error": "No results to report"}

        # Group results by config
        by_config: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            config = result["config"]
            if config not in by_config:
                by_config[config] = []
            by_config[config].append(result)

        # Calculate metrics per config
        report: dict[str, Any] = {
            "total_tasks": len(set(r["task_id"] for r in results)),
            "configs": {},
            "timestamp": datetime.utcnow().isoformat(),
        }

        for config, config_results in by_config.items():
            success_count = sum(1 for r in config_results if r["success"])
            total = len(config_results)
            avg_duration = sum(r["duration"] for r in config_results) / total

            # Calculate criteria completion rate
            criteria_completion = []
            for result in config_results:
                if result["criteria_met"]:
                    met_count = sum(1 for met in result["criteria_met"].values() if met)
                    total_criteria = len(result["criteria_met"])
                    if total_criteria > 0:
                        criteria_completion.append(met_count / total_criteria)

            avg_criteria = (
                sum(criteria_completion) / len(criteria_completion)
                if criteria_completion
                else 0
            )

            report["configs"][config] = {
                "success_rate": success_count / total,
                "success_count": success_count,
                "total": total,
                "avg_duration": avg_duration,
                "avg_criteria_completion": avg_criteria,
            }

        return report

    def generate_enhanced_reports(
        self, results: list[dict[str, Any]], output_dir: Path
    ) -> None:
        """Generate enhanced reports with statistics and visualizations.

        Creates:
        - Statistical comparison report (JSON)
        - Quality assessment report (JSON)
        - HTML report with charts
        - Markdown summary
        - Context metrics analysis
        """
        print("\n=== Generating Enhanced Reports ===\n")

        # Group results by config
        by_config: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            config = result["config"]
            if config not in by_config:
                by_config[config] = []
            by_config[config].append(result)

        original_results = by_config.get("original", [])
        compressed_results = []

        # Get all compressed results (any ratio)
        for config, config_results in by_config.items():
            if "compressed" in config:
                compressed_results.extend(config_results)

        if not original_results or not compressed_results:
            print("⚠️  Need both original and compressed results for comparison")
            return

        # 1. Statistical comparison
        print("1. Generating statistical comparison...")
        comparison = generate_comparison_report(original_results, compressed_results)

        comparison_path = output_dir / "statistical_comparison.json"
        with open(comparison_path, "w") as f:
            json.dump(comparison, f, indent=2)
        print(f"   ✓ Saved to {comparison_path}")

        # 2. Quality assessment
        print("2. Assessing quality degradation...")
        assessment = assess_quality_degradation(comparison, threshold=0.05)

        assessment_path = output_dir / "quality_assessment.json"
        with open(assessment_path, "w") as f:
            json.dump(assessment, f, indent=2)
        print(f"   ✓ Saved to {assessment_path}")

        # Print assessment summary
        if assessment["acceptable"]:
            print("   ✅ Quality: ACCEPTABLE (< 5% degradation)")
        else:
            print("   ❌ Quality: DEGRADED")
            for issue in assessment["issues"]:
                print(f"      - {issue}")

        # 3. Context metrics aggregation
        print("3. Analyzing context metrics...")
        metrics = aggregate_metrics(results)

        metrics_path = output_dir / "context_metrics.json"
        with open(metrics_path, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"   ✓ Saved to {metrics_path}")

        # 4. HTML report
        print("4. Generating HTML report...")
        html_path = output_dir / "validation_report.html"
        generate_html_report(results, comparison, html_path)
        print(f"   ✓ Saved to {html_path}")

        # 5. Markdown summary
        print("5. Generating markdown summary...")
        markdown = generate_markdown_summary(results, comparison)

        md_path = output_dir / "VALIDATION_SUMMARY.md"
        md_path.write_text(markdown)
        print(f"   ✓ Saved to {md_path}")

        print("\n=== Reports Generated Successfully ===\n")


def main():
    parser = argparse.ArgumentParser(
        description="Run context compression validation suite (Enhanced)"
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).parent.parent / "corpus",
        help="Path to task corpus directory",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(__file__).parent.parent / "results",
        help="Path to results directory",
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Filter tasks by category",
    )
    parser.add_argument(
        "--task",
        type=str,
        help="Run specific task ID only",
    )
    parser.add_argument(
        "--compression-ratio",
        type=float,
        nargs="*",
        default=[None, 0.15],
        help="Compression ratios to test (None = original)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be run without executing",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout per task in seconds (default: 600)",
    )
    parser.add_argument(
        "--generate-reports",
        action="store_true",
        help="Generate enhanced reports from existing results",
    )

    args = parser.parse_args()

    # Initialize runner
    runner = ValidationRunner(args.corpus, args.results)

    # If only generating reports, load existing results
    if args.generate_reports:
        print("Loading existing results...")
        results = []
        for result_file in sorted(args.results.glob("task-*.json")):
            with open(result_file) as f:
                results.append(json.load(f))

        if not results:
            print("No results found in", args.results)
            sys.exit(1)

        print(f"Loaded {len(results)} results")
        runner.generate_enhanced_reports(results, args.results)
        sys.exit(0)

    # Load tasks
    tasks = runner.load_tasks(args.category)

    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]

    if not tasks:
        print("No tasks found matching criteria")
        sys.exit(1)

    print(f"Loaded {len(tasks)} tasks")
    print(f"Testing compression ratios: {args.compression_ratio}")

    # Run validation
    results = runner.run_validation(tasks, args.compression_ratio, args.dry_run)

    if not args.dry_run:
        # Generate basic report
        basic_report = runner.generate_basic_report(results)
        basic_report_path = (
            args.results / f"report-{datetime.utcnow().isoformat()}.json"
        )
        with open(basic_report_path, "w") as f:
            json.dump(basic_report, f, indent=2)

        print("\n=== Basic Validation Report ===")
        print(json.dumps(basic_report, indent=2))

        # Generate enhanced reports
        runner.generate_enhanced_reports(results, args.results)

        print(f"\nAll reports saved to: {args.results}")


if __name__ == "__main__":
    main()
