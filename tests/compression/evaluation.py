#!/usr/bin/env python3
"""
Context Compression Evaluation Infrastructure

This module provides infrastructure for validating context compression quality
by running test cases with both compressed and original contexts and comparing
task completion rates.

Architecture:
- TestCase: Represents a single test from the corpus
- TestRunner: Executes gptme with test cases
- ResultCollector: Validates outputs against verification criteria
- ComparisonReporter: Generates statistical comparisons

Usage:
    python tests/compression/evaluation.py --run-baseline
    python tests/compression/evaluation.py --run-compressed --ratio 0.15
    python tests/compression/evaluation.py --compare
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Test corpus location in Bob's workspace
CORPUS_PATH = Path(
    "/home/bob/bob/knowledge/technical/designs/compression-validation-test-corpus.md"
)
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class TestCase:
    """Represents a single test case from the corpus."""

    task_id: str
    task_type: str
    source_session: str
    duration_minutes: int
    input_text: str
    expected_output: list[str]
    verification_checklist: list[str]

    def __str__(self) -> str:
        return f"TestCase({self.task_id}, {self.task_type}, {self.duration_minutes}min)"


@dataclass
class TestResult:
    """Results from running a single test case."""

    task_id: str
    compression_ratio: float  # 1.0 = no compression, 0.15 = 85% compressed
    success: bool
    completion_rate: float  # 0.0-1.0 based on verification checklist
    output: str
    verified_items: list[str]
    failed_items: list[str]
    duration_seconds: float
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "compression_ratio": self.compression_ratio,
            "success": self.success,
            "completion_rate": self.completion_rate,
            "verified_items": self.verified_items,
            "failed_items": self.failed_items,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


class CorpusLoader:
    """Loads test cases from the validation corpus."""

    def load_corpus(self, corpus_path: Path) -> list[TestCase]:
        """Parse corpus markdown file and extract test cases."""
        if not corpus_path.exists():
            raise FileNotFoundError(f"Corpus not found: {corpus_path}")

        content = corpus_path.read_text()
        test_cases = []

        # Split into test case sections (marked by ###)
        sections = content.split("\n### ")

        for section in sections[1:]:  # Skip intro section
            test_case = self._parse_test_section(section)
            if test_case:
                test_cases.append(test_case)

        return test_cases

    def _parse_test_section(self, section: str) -> TestCase | None:
        """Parse a single test case section."""
        lines = section.split("\n")
        if not lines:
            return None

        # Initialize fields (header in lines[0] for reference)
        task_id = ""
        task_type = ""
        source_session = ""
        duration_minutes = 0
        input_text = ""
        expected_output = []
        verification_checklist = []

        # Parse fields
        current_field = None
        field_content: list[str] = []

        for line in lines[1:]:
            line = line.strip()

            # Field markers
            if line.startswith("**Task ID**:"):
                task_id = line.split(":", 1)[1].strip()
            elif line.startswith("**Task Type**:"):
                task_type = line.split(":", 1)[1].strip()
            elif line.startswith("**Source**:"):
                source_session = line.split(":", 1)[1].strip()
            elif line.startswith("**Duration**:"):
                duration_str = line.split(":", 1)[1].strip()
                # Extract minutes (e.g., "14 minutes" -> 14, "8 min" -> 8)
                try:
                    # Get first word, remove 'min' suffix if present
                    num_str = duration_str.split()[0].replace("min", "")
                    duration_minutes = int(num_str)
                except (ValueError, IndexError):
                    duration_minutes = 0

            # Content sections
            elif line.startswith("**Input**:"):
                current_field = "input"
                field_content = []
            elif line.startswith("**Expected Output**:"):
                current_field = "expected"
                field_content = []
            elif line.startswith("**Verification**:"):
                current_field = "verification"
                field_content = []
            elif line.startswith("---"):
                # Section separator
                continue
            elif current_field:
                # Accumulate content for current field
                if line.startswith("- [ ]"):
                    # Verification checklist item
                    item = line[5:].strip()
                    if item:
                        verification_checklist.append(item)
                elif line.startswith("- "):
                    # Expected output item
                    item = line[2:].strip()
                    if item:
                        expected_output.append(item)
                elif line.startswith("```"):
                    # Code block boundary
                    if current_field == "input":
                        field_content.append(line)
                elif line and current_field == "input":
                    field_content.append(line)

        # Construct input text from accumulated content
        if field_content:
            input_text = "\n".join(field_content)

        # Validate required fields
        if not task_id or not task_type:
            return None

        return TestCase(
            task_id=task_id,
            task_type=task_type,
            source_session=source_session,
            duration_minutes=duration_minutes,
            input_text=input_text,
            expected_output=expected_output,
            verification_checklist=verification_checklist,
        )


class TestRunner:
    """Executes gptme with test cases and captures results."""

    def __init__(self, gptme_path: Path):
        self.gptme_path = gptme_path
        self.results_dir = RESULTS_DIR
        self.results_dir.mkdir(exist_ok=True)

    def run_test(
        self, test_case: TestCase, compression_ratio: float = 1.0
    ) -> TestResult:
        """
        Execute a single test case with specified compression ratio.

        Args:
            test_case: Test to run
            compression_ratio: 1.0 = original, 0.15 = 85% compressed

        Returns:
            TestResult with completion status and metrics
        """
        import time

        start_time = time.time()

        try:
            # Configure compression settings
            with self._configure_compression(compression_ratio):
                # Run gptme with test input
                output = self._run_gptme(test_case.input_text)

            # Verify output against checklist
            collector = ResultCollector()
            verified, failed = collector.verify_output(
                output, test_case.verification_checklist
            )
            completion_rate = collector.calculate_completion_rate(verified, failed)

            duration = time.time() - start_time

            return TestResult(
                task_id=test_case.task_id,
                compression_ratio=compression_ratio,
                success=completion_rate >= 0.95,  # 95% completion threshold
                completion_rate=completion_rate,
                output=output,
                verified_items=verified,
                failed_items=failed,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            return TestResult(
                task_id=test_case.task_id,
                compression_ratio=compression_ratio,
                success=False,
                completion_rate=0.0,
                output="",
                verified_items=[],
                failed_items=test_case.verification_checklist,
                duration_seconds=duration,
                error=str(e),
            )

    def _configure_compression(self, ratio: float):
        """Context manager to temporarily configure compression settings."""
        from contextlib import contextmanager

        import tomli
        import tomli_w

        @contextmanager
        def config_context():
            config_path = self.gptme_path / "gptme.toml"
            backup_path = self.gptme_path / "gptme.toml.backup"

            # Backup original config
            if config_path.exists():
                import shutil

                shutil.copy2(config_path, backup_path)

            # Modify config
            try:
                if config_path.exists():
                    with open(config_path, "rb") as f:
                        config = tomli.load(f)
                else:
                    config = {}

                # Set compression settings
                if "context_compression" not in config:
                    config["context_compression"] = {}

                config["context_compression"]["enabled"] = ratio < 1.0
                config["context_compression"]["target_ratio"] = ratio

                # Write modified config
                with open(config_path, "wb") as f:
                    tomli_w.dump(config, f)

                yield

            finally:
                # Restore original config
                if backup_path.exists():
                    import shutil

                    shutil.move(backup_path, config_path)

        return config_context()

    def _run_gptme(self, input_text: str) -> str:
        """Execute gptme with given input and capture output."""
        # Create temporary file with input
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(input_text)
            input_file = f.name

        try:
            # Run gptme in non-interactive mode using installed entrypoint
            gptme_bin = self.gptme_path / ".venv" / "bin" / "gptme"
            cmd = [
                str(gptme_bin),
                "--non-interactive",
                "--no-confirm",
                input_file,
            ]

            result = subprocess.run(
                cmd,
                cwd=self.gptme_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            return result.stdout + result.stderr

        except subprocess.TimeoutExpired:
            return "ERROR: Test execution timed out"
        except Exception as e:
            return f"ERROR: {str(e)}"
        finally:
            # Cleanup temp file
            import os

            if os.path.exists(input_file):
                os.unlink(input_file)

    def run_all_tests(
        self, test_cases: list[TestCase], compression_ratio: float = 1.0
    ) -> list[TestResult]:
        """Run all test cases with specified compression ratio."""
        results = []
        for test_case in test_cases:
            print(f"Running {test_case.task_id} (ratio={compression_ratio})...")
            result = self.run_test(test_case, compression_ratio)
            results.append(result)
        return results

    def save_results(self, results: list[TestResult], filename: str):
        """Save results to JSON file."""
        output_path = self.results_dir / filename
        with open(output_path, "w") as f:
            json.dump([r.to_dict() for r in results], f, indent=2)
        print(f"Results saved to {output_path}")


class ResultCollector:
    """Validates outputs and collects metrics."""

    def verify_output(
        self, output: str, verification_checklist: list[str]
    ) -> tuple[list[str], list[str]]:
        """
        Check output against verification checklist.

        Returns:
            (verified_items, failed_items)
        """
        verified = []
        failed = []

        for item in verification_checklist:
            # Simplified verification - would need more sophisticated matching
            if self._check_item(output, item):
                verified.append(item)
            else:
                failed.append(item)

        return verified, failed

    def _check_item(self, output: str, item: str) -> bool:
        """Check if verification item is satisfied in output."""
        # TODO: Implement intelligent verification matching
        # For now, simple substring check
        return item.lower() in output.lower()

    def calculate_completion_rate(
        self, verified: list[str], failed: list[str]
    ) -> float:
        """Calculate completion rate from verification results."""
        total = len(verified) + len(failed)
        if total == 0:
            return 0.0
        return len(verified) / total


class ComparisonReporter:
    """Generates statistical comparisons between test runs."""

    def compare_results(
        self, baseline: list[TestResult], compressed: list[TestResult]
    ) -> dict[str, Any]:
        """
        Compare baseline (original) vs compressed context results.

        Returns:
            Statistical comparison report
        """
        report = {
            "baseline": self._summarize_results(baseline),
            "compressed": self._summarize_results(compressed),
            "comparison": self._calculate_differences(baseline, compressed),
        }
        return report

    def _summarize_results(self, results: list[TestResult]) -> dict[str, Any]:
        """Summarize results across all tests."""
        if not results:
            return {}

        total = len(results)
        successful = sum(1 for r in results if r.success)
        avg_completion = sum(r.completion_rate for r in results) / total
        avg_duration = sum(r.duration_seconds for r in results) / total

        return {
            "total_tests": total,
            "successful": successful,
            "success_rate": successful / total,
            "avg_completion_rate": avg_completion,
            "avg_duration_seconds": avg_duration,
        }

    def _calculate_differences(
        self, baseline: list[TestResult], compressed: list[TestResult]
    ) -> dict[str, Any]:
        """Calculate differences between baseline and compressed."""
        baseline_summary = self._summarize_results(baseline)
        compressed_summary = self._summarize_results(compressed)

        return {
            "completion_rate_delta": compressed_summary["avg_completion_rate"]
            - baseline_summary["avg_completion_rate"],
            "success_rate_delta": compressed_summary["success_rate"]
            - baseline_summary["success_rate"],
            "duration_delta": compressed_summary["avg_duration_seconds"]
            - baseline_summary["avg_duration_seconds"],
        }

    def generate_report(self, comparison: dict[str, Any], output_path: Path):
        """Generate human-readable comparison report."""
        with open(output_path, "w") as f:
            f.write("# Context Compression Evaluation Report\n\n")
            f.write("## Baseline (Original Context)\n")
            f.write(self._format_summary(comparison["baseline"]))
            f.write("\n## Compressed Context\n")
            f.write(self._format_summary(comparison["compressed"]))
            f.write("\n## Comparison\n")
            f.write(self._format_comparison(comparison["comparison"]))

        print(f"Report generated: {output_path}")

    def _format_summary(self, summary: dict[str, Any]) -> str:
        """Format summary section."""
        return f"""
- Total tests: {summary['total_tests']}
- Successful: {summary['successful']}
- Success rate: {summary['success_rate']:.1%}
- Avg completion rate: {summary['avg_completion_rate']:.1%}
- Avg duration: {summary['avg_duration_seconds']:.1f}s
"""

    def _format_comparison(self, comparison: dict[str, Any]) -> str:
        """Format comparison section."""
        return f"""
- Completion rate delta: {comparison['completion_rate_delta']:+.1%}
- Success rate delta: {comparison['success_rate_delta']:+.1%}
- Duration delta: {comparison['duration_delta']:+.1f}s
"""


def main():
    """CLI interface for running evaluations."""
    import argparse

    parser = argparse.ArgumentParser(description="Context compression evaluation")
    parser.add_argument(
        "--run-baseline",
        action="store_true",
        help="Run baseline tests (original context)",
    )
    parser.add_argument(
        "--run-compressed",
        action="store_true",
        help="Run tests with compressed context",
    )
    parser.add_argument(
        "--ratio", type=float, default=0.15, help="Compression ratio (default: 0.15)"
    )
    parser.add_argument(
        "--compare", action="store_true", help="Generate comparison report"
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS_PATH,
        help="Path to test corpus",
    )
    parser.add_argument(
        "--list-tests",
        action="store_true",
        help="List all test cases from corpus",
    )

    args = parser.parse_args()

    # Load corpus
    loader = CorpusLoader()
    print(f"Loading corpus from {args.corpus}")
    test_cases = loader.load_corpus(args.corpus)
    print(f"Loaded {len(test_cases)} test cases")

    if args.list_tests:
        print("\n=== Test Cases ===")
        for tc in test_cases:
            print(f"\n{tc.task_id}:")
            print(f"  Type: {tc.task_type}")
            print(f"  Source: {tc.source_session}")
            print(f"  Duration: {tc.duration_minutes}min")
            print(f"  Verification items: {len(tc.verification_checklist)}")
            print(f"  Expected outputs: {len(tc.expected_output)}")
        return

    # Initialize runner
    gptme_path = Path(__file__).parent.parent.parent  # gptme repo root
    runner = TestRunner(gptme_path)

    if args.run_baseline:
        print("\n=== Running Baseline Tests (Original Context) ===")
        baseline_results = runner.run_all_tests(test_cases, compression_ratio=1.0)
        runner.save_results(baseline_results, "baseline_results.json")

    if args.run_compressed:
        print(f"\n=== Running Compressed Tests (ratio={args.ratio}) ===")
        compressed_results = runner.run_all_tests(
            test_cases, compression_ratio=args.ratio
        )
        runner.save_results(compressed_results, f"compressed_results_{args.ratio}.json")

    if args.compare:
        print("\n=== Generating Comparison Report ===")
        # Load results
        baseline_path = RESULTS_DIR / "baseline_results.json"
        compressed_path = RESULTS_DIR / f"compressed_results_{args.ratio}.json"

        if not baseline_path.exists() or not compressed_path.exists():
            print(
                "Error: Missing results files. Run baseline and compressed tests first."
            )
            sys.exit(1)

        with open(baseline_path) as f:
            baseline_data = json.load(f)
        with open(compressed_path) as f:
            compressed_data = json.load(f)

        # Convert back to TestResult objects
        baseline_results = [TestResult(**data) for data in baseline_data]
        compressed_results = [TestResult(**data) for data in compressed_data]

        # Generate comparison
        reporter = ComparisonReporter()
        comparison = reporter.compare_results(baseline_results, compressed_results)
        report_path = RESULTS_DIR / f"comparison_report_{args.ratio}.md"
        reporter.generate_report(comparison, report_path)


if __name__ == "__main__":
    main()
