"""Performance tests for TaskAnalyzer.

Target: <100ms analysis overhead for typical tasks.
"""

import time
from pathlib import Path

import pytest

from gptme.context_compression.analyzer import TaskAnalyzer


def measure_analyze_time(
    analyzer: TaskAnalyzer, task_desc: str, n_runs: int = 10
) -> tuple[float, float]:
    """Measure average analysis time over n runs.

    Returns:
        Tuple of (average_ms, max_ms)
    """
    times = []
    for _ in range(n_runs):
        start = time.perf_counter()
        analyzer.analyze(task_description=task_desc)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        times.append(elapsed)

    return sum(times) / len(times), max(times)


def test_analyzer_performance_simple():
    """Test performance on simple focused task."""
    analyzer = TaskAnalyzer()
    task = "Fix bug in file.py: counter not incrementing"

    avg_ms, max_ms = measure_analyze_time(analyzer, task, n_runs=20)

    print("\nSimple task analysis:")
    print(f"  Average: {avg_ms:.2f}ms")
    print(f"  Maximum: {max_ms:.2f}ms")

    # Target: <100ms
    assert avg_ms < 100, f"Average time {avg_ms:.2f}ms exceeds 100ms target"
    assert max_ms < 150, f"Maximum time {max_ms:.2f}ms exceeds 150ms threshold"


def test_analyzer_performance_complex():
    """Test performance on complex architecture task."""
    analyzer = TaskAnalyzer()
    task = """Implement new service package with:
    - API client (3 files)
    - Data models (5 files)
    - Integration tests (2 files)
    - Documentation
    """

    avg_ms, max_ms = measure_analyze_time(analyzer, task, n_runs=20)

    print("\nComplex task analysis:")
    print(f"  Average: {avg_ms:.2f}ms")
    print(f"  Maximum: {max_ms:.2f}ms")

    # Target: <100ms even for complex tasks
    assert avg_ms < 100, f"Average time {avg_ms:.2f}ms exceeds 100ms target"
    assert max_ms < 150, f"Maximum time {max_ms:.2f}ms exceeds 150ms threshold"


def test_analyzer_performance_with_workspace():
    """Test performance when workspace context is provided."""
    analyzer = TaskAnalyzer()
    task = "Add tests for new API endpoints"
    workspace = Path.cwd()  # Use current directory

    times = []
    for _ in range(10):
        start = time.perf_counter()
        analyzer.analyze(task_description=task, workspace_path=workspace)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    avg_ms = sum(times) / len(times)
    max_ms = max(times)

    print("\nWorkspace context analysis:")
    print(f"  Average: {avg_ms:.2f}ms")
    print(f"  Maximum: {max_ms:.2f}ms")

    # Workspace analysis may be slightly slower, allow 150ms
    assert avg_ms < 150, f"Average time {avg_ms:.2f}ms exceeds 150ms threshold"


def test_analyzer_performance_batch():
    """Test performance over batch of diverse tasks."""
    analyzer = TaskAnalyzer()

    tasks = [
        "Fix typo in README.md",
        "Add validation to user input",
        "Implement authentication system",
        "Refactor config loading",
        "Debug CI failure in tests",
        "Add API endpoint for user management",
        "Write documentation for new feature",
        "Optimize database queries",
    ]

    all_times = []
    for task in tasks:
        start = time.perf_counter()
        analyzer.analyze(task_description=task)
        elapsed = (time.perf_counter() - start) * 1000
        all_times.append(elapsed)

    avg_ms = sum(all_times) / len(all_times)
    max_ms = max(all_times)

    print(f"\nBatch analysis ({len(tasks)} tasks):")
    print(f"  Average: {avg_ms:.2f}ms")
    print(f"  Maximum: {max_ms:.2f}ms")
    print(f"  Total: {sum(all_times):.2f}ms")

    assert avg_ms < 100, f"Average time {avg_ms:.2f}ms exceeds 100ms target"


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s"])
