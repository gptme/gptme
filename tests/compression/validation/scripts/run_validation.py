#!/usr/bin/env python3
"""
Context Compression Validation Suite Runner

Executes test tasks with compressed and original context to measure
task completion quality impact.

Usage:
    python run_validation.py --corpus ../corpus --results ../results
    python run_validation.py --category bug-fix --dry-run
    python run_validation.py --task task-001 --compression-ratio 0.15
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


class ValidationRunner:
    """Runs validation tests and collects results."""

    def __init__(
        self,
        corpus_dir: Path,
        results_dir: Path,
        gptme_path: Path = Path("gptme"),
    ):
        self.corpus_dir = corpus_dir
        self.results_dir = results_dir
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
            str(workspace),
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
        except Exception as e:
            success = False
            output = ""
            error = str(e)

        duration = time.time() - start_time

        # Check success criteria
        criteria_met = self._check_success_criteria(task, workspace, output)

        return {
            "task_id": task_id,
            "config": config_label,
            "compression_ratio": compression_ratio,
            "success": success,
            "criteria_met": criteria_met,
            "duration": duration,
            "output": output,
            "error": error,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _check_success_criteria(
        self, task: dict[str, Any], workspace: Path, output: str
    ) -> dict[str, bool]:
        """Check if task success criteria were met."""
        criteria = task.get("success_criteria", [])
        results = {}

        for criterion in criteria:
            # Simple checks - extend as needed
            met = False
            if "file" in criterion.lower() or "exists" in criterion.lower():
                # Check file existence
                met = self._check_file_criterion(criterion, workspace)
            elif "commit" in criterion.lower() or "message" in criterion.lower():
                # Check git commits
                met = self._check_commit_criterion(criterion, workspace)
            elif "test" in criterion.lower() or "pass" in criterion.lower():
                # Check test results
                met = "passed" in output.lower() or "success" in output.lower()
            else:
                # Generic check - look for keywords in output
                met = self._check_keyword_criterion(criterion, output)

            results[criterion] = met

        return results

    def _check_file_criterion(self, criterion: str, workspace: Path) -> bool:
        """Check if files mentioned in criterion exist."""
        # Extract potential file patterns from criterion
        # This is a simple heuristic - extend as needed
        patterns = ["*.md", "*.py", "*.yaml", "*.toml"]
        for pattern in patterns:
            if pattern in criterion and list(workspace.rglob(pattern)):
                return True
        return False

    def _check_commit_criterion(self, criterion: str, workspace: Path) -> bool:
        """Check if commits were made with relevant messages."""
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True,
                text=True,
                cwd=workspace,
            )
            # Check if any keywords from criterion appear in commits
            keywords = [
                word.lower()
                for word in criterion.split()
                if len(word) > 3 and word.isalpha()
            ]
            log_lower = result.stdout.lower()
            return any(keyword in log_lower for keyword in keywords)
        except Exception:
            return False

    def _check_keyword_criterion(self, criterion: str, output: str) -> bool:
        """Check if criterion keywords appear in output."""
        keywords = [
            word.lower()
            for word in criterion.split()
            if len(word) > 3 and word.isalpha()
        ]
        output_lower = output.lower()
        return any(keyword in output_lower for keyword in keywords)

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

    def generate_report(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate summary report from results."""
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


def main():
    parser = argparse.ArgumentParser(
        description="Run context compression validation suite"
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

    args = parser.parse_args()

    # Initialize runner
    runner = ValidationRunner(args.corpus, args.results)

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
        # Generate and save report
        report = runner.generate_report(results)
        report_path = args.results / f"report-{datetime.utcnow().isoformat()}.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        print("\n=== Validation Report ===")
        print(json.dumps(report, indent=2))
        print(f"\nFull report saved to: {report_path}")


if __name__ == "__main__":
    main()
