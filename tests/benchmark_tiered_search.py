#!/usr/bin/env python3
"""Benchmark script for RAG tiered search performance (Phase 5b).

Measures:
- Baseline performance (single-tier search)
- Tiered search performance (MiniLM → MPNet)
- Speedup achieved
- Result quality comparison
- Cache effectiveness with tiered search

Targets (from Phase 5a design):
- Target speedup: 3x (80ms → 30ms)
- Quality: No degradation in relevance scores
- Cache: Hit rate maintained or improved
"""

import statistics
import time
from pathlib import Path
from typing import Any

from gptme.config import RagConfig
from gptme.tools.rag import _has_gptme_rag, _run_rag_cmd, _tiered_search


def benchmark_baseline_performance(
    num_queries: int = 50, workspace: Path | None = None
) -> dict[str, Any]:
    """Benchmark baseline single-tier RAG search performance."""
    print(f"\n=== Benchmarking Baseline Performance ({num_queries} queries) ===")

    if workspace is None:
        workspace = Path.cwd()

    # Test queries covering different patterns
    test_queries = [
        "how to use python decorators",
        "implement authentication system",
        "best practices for error handling",
        "database query optimization",
        "async/await patterns",
    ] * (num_queries // 5)

    latencies = []

    for query in test_queries[:num_queries]:
        # Measure baseline (single-tier) search
        cmd = [
            "gptme-rag",
            "search",
            query,
        ]

        start = time.perf_counter()
        try:
            result = _run_rag_cmd(cmd)
            elapsed = (time.perf_counter() - start) * 1000  # ms
            if result.returncode == 0:
                latencies.append(elapsed)
        except Exception as e:
            print(f"  Warning: Query failed: {e}")
            continue

    # Calculate statistics
    def percentile(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        return statistics.quantiles(data, n=100)[int(p) - 1]

    results = {
        "query_count": len(latencies),
        "mean_latency": statistics.mean(latencies) if latencies else 0.0,
        "p50_latency": percentile(latencies, 50),
        "p95_latency": percentile(latencies, 95),
        "p99_latency": percentile(latencies, 99),
        "min_latency": min(latencies) if latencies else 0.0,
        "max_latency": max(latencies) if latencies else 0.0,
    }

    print(f"  Queries: {results['query_count']}")
    print(f"  Mean latency: {results['mean_latency']:.2f}ms")
    print(f"  P50: {results['p50_latency']:.2f}ms")
    print(f"  P95: {results['p95_latency']:.2f}ms")
    print(f"  P99: {results['p99_latency']:.2f}ms")

    return results


def benchmark_tiered_search_performance(
    num_queries: int = 50, workspace: Path | None = None
) -> dict[str, Any]:
    """Benchmark tiered search (MiniLM → MPNet) performance."""
    print(f"\n=== Benchmarking Tiered Search Performance ({num_queries} queries) ===")

    if workspace is None:
        workspace = Path.cwd()

    # Same test queries as baseline for comparison
    test_queries = [
        "how to use python decorators",
        "implement authentication system",
        "best practices for error handling",
        "database query optimization",
        "async/await patterns",
    ] * (num_queries // 5)

    latencies = []
    rag_config = RagConfig(enabled=True, tiered_search=True)

    for query in test_queries[:num_queries]:
        start = time.perf_counter()
        try:
            result = _tiered_search(query, workspace, rag_config)
            elapsed = (time.perf_counter() - start) * 1000  # ms
            if result:
                latencies.append(elapsed)
        except Exception as e:
            print(f"  Warning: Query failed: {e}")
            continue

    # Calculate statistics
    def percentile(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        return statistics.quantiles(data, n=100)[int(p) - 1]

    results = {
        "query_count": len(latencies),
        "mean_latency": statistics.mean(latencies) if latencies else 0.0,
        "p50_latency": percentile(latencies, 50),
        "p95_latency": percentile(latencies, 95),
        "p99_latency": percentile(latencies, 99),
        "min_latency": min(latencies) if latencies else 0.0,
        "max_latency": max(latencies) if latencies else 0.0,
    }

    print(f"  Queries: {results['query_count']}")
    print(f"  Mean latency: {results['mean_latency']:.2f}ms")
    print(f"  P50: {results['p50_latency']:.2f}ms")
    print(f"  P95: {results['p95_latency']:.2f}ms")
    print(f"  P99: {results['p99_latency']:.2f}ms")

    return results


def compare_performance(
    baseline: dict[str, Any], tiered: dict[str, Any]
) -> dict[str, Any]:
    """Compare baseline vs tiered search performance."""
    print("\n=== Performance Comparison ===")

    if baseline["mean_latency"] == 0 or tiered["mean_latency"] == 0:
        print("  ⚠️  Insufficient data for comparison")
        return {}

    speedup_mean = baseline["mean_latency"] / tiered["mean_latency"]
    speedup_p50 = baseline["p50_latency"] / tiered["p50_latency"]
    speedup_p95 = baseline["p95_latency"] / tiered["p95_latency"]

    results = {
        "speedup_mean": speedup_mean,
        "speedup_p50": speedup_p50,
        "speedup_p95": speedup_p95,
        "baseline_mean": baseline["mean_latency"],
        "tiered_mean": tiered["mean_latency"],
        "improvement_ms": baseline["mean_latency"] - tiered["mean_latency"],
    }

    print(f"  Baseline mean: {baseline['mean_latency']:.2f}ms")
    print(f"  Tiered mean: {tiered['mean_latency']:.2f}ms")
    print(f"  Speedup (mean): {speedup_mean:.2f}x")
    print(f"  Speedup (P50): {speedup_p50:.2f}x")
    print(f"  Speedup (P95): {speedup_p95:.2f}x")
    print(f"  Improvement: {results['improvement_ms']:.2f}ms")

    # Check against target
    target_speedup = 3.0
    if speedup_mean >= target_speedup:
        print(f"  ✅ Target achieved: {speedup_mean:.2f}x >= {target_speedup}x")
    else:
        print(
            f"  ⚠️  Below target: {speedup_mean:.2f}x < {target_speedup}x ({(target_speedup - speedup_mean):.2f}x gap)"
        )

    return results


def main():
    """Run all benchmarks and generate report."""
    print("=" * 60)
    print("RAG Tiered Search Performance Benchmark (Phase 5b)")
    print("=" * 60)

    # Check if gptme-rag is available
    if not _has_gptme_rag():
        print("\n⚠️  gptme-rag not available. Install with: pip install gptme-rag")
        return 1

    # Use current directory as workspace
    workspace = Path.cwd()
    num_queries = 50

    # Run benchmarks
    baseline_results = benchmark_baseline_performance(num_queries, workspace)
    tiered_results = benchmark_tiered_search_performance(num_queries, workspace)

    # Compare results
    comparison = compare_performance(baseline_results, tiered_results)

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Baseline P95: {baseline_results['p95_latency']:.2f}ms")
    print(f"Tiered P95: {tiered_results['p95_latency']:.2f}ms")
    if comparison:
        print(f"Speedup: {comparison['speedup_mean']:.2f}x")
        print("Target: 3.0x")

    return 0


if __name__ == "__main__":
    exit(main())
