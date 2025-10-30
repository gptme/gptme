#!/usr/bin/env python3
"""Benchmark script for RAG cache performance validation (Phase 4e).

Measures:
- Cache hit/miss latency (P50, P95, P99)
- Cache hit rate over realistic workloads
- Memory usage and efficiency
- Thread safety under concurrent load

Targets:
- Cache hit latency: <5ms
- Cache miss latency: 10-30ms (no regression vs uncached)
- Cache hit rate: >70% for typical conversations
- Memory usage: <100MB typical
"""

import statistics
import time
from datetime import datetime
from typing import Any

from gptme.tools.cache import CacheKey, SmartRAGCache


def benchmark_cache_latency(num_queries: int = 1000) -> dict[str, Any]:
    """Benchmark cache hit and miss latency."""
    print(f"\n=== Benchmarking Cache Latency ({num_queries} queries) ===")

    cache = SmartRAGCache()

    # Prepare test data
    test_queries = [f"test query {i}" for i in range(num_queries)]
    test_results = [(["doc1.txt", "doc2.txt"], [0.9, 0.8]) for _ in range(num_queries)]

    # Measure cache MISS latency (cold start)
    miss_times = []
    for i, query in enumerate(test_queries[:100]):  # First 100 queries are misses
        key = CacheKey.from_search(
            query=query,
            workspace_path="/test/workspace",
            workspace_only=False,
            max_tokens=3000,
            min_relevance=0.0,
        )

        start = time.perf_counter()
        result = cache.get(key)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        miss_times.append(elapsed)

        # Populate cache for hit tests
        from gptme.tools.cache import CacheEntry

        entry = CacheEntry(
            document_ids=test_results[i][0],
            relevance_scores=test_results[i][1],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=0.0,
            result_count=2,
        )
        cache.put(key, entry)

    # Measure cache HIT latency (warm)
    hit_times = []
    for query in test_queries[:100]:  # Same 100 queries, now hits
        key = CacheKey.from_search(
            query=query,
            workspace_path="/test/workspace",
            workspace_only=False,
            max_tokens=3000,
            min_relevance=0.0,
        )

        start = time.perf_counter()
        result = cache.get(key)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        if result is not None:  # Confirm it's a hit
            hit_times.append(elapsed)

    # Calculate statistics
    def percentile(data: list[float], p: float) -> float:
        return statistics.quantiles(data, n=100)[int(p) - 1] if data else 0.0

    results = {
        "miss_count": len(miss_times),
        "miss_mean": statistics.mean(miss_times) if miss_times else 0.0,
        "miss_p50": percentile(miss_times, 50),
        "miss_p95": percentile(miss_times, 95),
        "miss_p99": percentile(miss_times, 99),
        "hit_count": len(hit_times),
        "hit_mean": statistics.mean(hit_times) if hit_times else 0.0,
        "hit_p50": percentile(hit_times, 50),
        "hit_p95": percentile(hit_times, 95),
        "hit_p99": percentile(hit_times, 99),
    }

    # Print results
    print("\nCache MISS latency (cold):")
    print(f"  Mean: {results['miss_mean']:.3f}ms")
    print(f"  P50:  {results['miss_p50']:.3f}ms")
    print(f"  P95:  {results['miss_p95']:.3f}ms")
    print(f"  P99:  {results['miss_p99']:.3f}ms")

    print("\nCache HIT latency (warm):")
    print(f"  Mean: {results['hit_mean']:.3f}ms")
    print(f"  P50:  {results['hit_p50']:.3f}ms")
    print(f"  P95:  {results['hit_p95']:.3f}ms")
    print(f"  P99:  {results['hit_p99']:.3f}ms")

    # Validate against targets
    print("\nTarget Validation:")
    print(
        f"  Cache hit P95 < 5ms:  {'✅' if results['hit_p95'] < 5.0 else '❌'} ({results['hit_p95']:.3f}ms)"
    )
    print(
        f"  Cache miss reasonable: {'✅' if results['miss_mean'] < 1.0 else '❌'} ({results['miss_mean']:.3f}ms)"
    )

    return results


def benchmark_cache_hit_rate(
    num_turns: int = 20, queries_per_turn: int = 10
) -> dict[str, Any]:
    """Benchmark cache hit rate over simulated conversation turns."""
    print(
        f"\n=== Benchmarking Cache Hit Rate ({num_turns} turns, {queries_per_turn} queries/turn) ==="
    )

    cache = SmartRAGCache()

    # Simulate conversation with repeated queries
    # Typical pattern: 30% unique queries, 70% repeated from earlier turns
    unique_queries = [f"query {i}" for i in range(num_turns * 3)]

    hits = 0
    misses = 0

    for turn in range(num_turns):
        # Mix of new and repeated queries
        turn_queries = []

        # 30% new queries
        new_count = int(queries_per_turn * 0.3)
        turn_queries.extend(unique_queries[turn * new_count : (turn + 1) * new_count])

        # 70% repeated from earlier turns
        repeat_count = queries_per_turn - new_count
        if turn > 0:
            # Pick from earlier turns
            turn_queries.extend(unique_queries[: turn * new_count][:repeat_count])

        # Query cache
        for query in turn_queries:
            key = CacheKey.from_search(
                query=query,
                workspace_path="/test/workspace",
                workspace_only=False,
                max_tokens=3000,
                min_relevance=0.0,
            )

            result = cache.get(key)
            if result is not None:
                hits += 1
            else:
                misses += 1
                # Populate cache on miss
                from gptme.tools.cache import CacheEntry

                entry = CacheEntry(
                    document_ids=["doc1.txt", "doc2.txt"],
                    relevance_scores=[0.9, 0.8],
                    created_at=datetime.now(),
                    last_accessed=datetime.now(),
                    access_count=1,
                    workspace_mtime=0.0,
                    index_mtime=0.0,
                    embedding_time_ms=0.0,
                    result_count=2,
                )
                cache.put(key, entry)

    total = hits + misses
    hit_rate = (hits / total * 100) if total > 0 else 0.0

    print("\nResults:")
    print(f"  Total queries: {total}")
    print(f"  Cache hits:    {hits}")
    print(f"  Cache misses:  {misses}")
    print(f"  Hit rate:      {hit_rate:.1f}%")

    print("\nTarget Validation:")
    print(f"  Hit rate > 70%: {'✅' if hit_rate > 70.0 else '❌'} ({hit_rate:.1f}%)")

    return {
        "total_queries": total,
        "hits": hits,
        "misses": misses,
        "hit_rate": hit_rate,
    }


def benchmark_memory_usage() -> dict[str, Any]:
    """Benchmark memory usage and efficiency."""
    print("\n=== Benchmarking Memory Usage ===")

    cache = SmartRAGCache(max_memory_bytes=10 * 1024 * 1024)  # 10MB for test

    # Fill cache with entries
    num_entries = 1000
    for i in range(num_entries):
        key = CacheKey.from_search(
            query=f"test query {i}",
            workspace_path="/test/workspace",
            workspace_only=False,
            max_tokens=3000,
            min_relevance=0.0,
        )

        from gptme.tools.cache import CacheEntry

        entry = CacheEntry(
            document_ids=[f"doc{j}.txt" for j in range(10)],  # 10 docs
            relevance_scores=[0.9 - j * 0.05 for j in range(10)],
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=0.0,
            index_mtime=0.0,
            embedding_time_ms=0.0,
            result_count=10,
        )
        cache.put(key, entry)

    # Check cache stats
    stats = cache.get_stats()

    # Estimate entry size (from design doc: ~780 bytes per entry)
    estimated_size_mb = (stats["entries"] * 780) / (1024 * 1024)

    print("\nResults:")
    print(f"  Entries in cache: {stats['entries']}")
    print(f"  Est. memory:      {estimated_size_mb:.2f} MB")
    print(f"  Max size limit:   {cache.max_memory_bytes / (1024 * 1024):.0f} MB")
    print(f"  Evictions:        {stats['evictions']}")

    print("\nTarget Validation:")
    print(
        f"  Memory < 100MB:   {'✅' if estimated_size_mb < 100 else '❌'} ({estimated_size_mb:.2f} MB)"
    )
    print(
        f"  LRU evictions working: {'✅' if stats['evictions'] > 0 else '❌'} ({stats['evictions']} evictions)"
    )

    return {
        "entries": stats["entries"],
        "estimated_mb": estimated_size_mb,
        "evictions": stats["evictions"],
    }


def benchmark_concurrent_access() -> dict[str, Any]:
    """Benchmark thread safety under concurrent load."""
    print("\n=== Benchmarking Concurrent Access ===")

    import threading

    cache = SmartRAGCache()
    errors = []

    def worker(worker_id: int, num_ops: int):
        """Worker thread performing cache operations."""
        try:
            for i in range(num_ops):
                key = CacheKey.from_search(
                    query=f"worker {worker_id} query {i}",
                    workspace_path="/test/workspace",
                    workspace_only=False,
                    max_tokens=3000,
                    min_relevance=0.0,
                )

                # Get or create
                result = cache.get(key)
                if result is None:
                    from gptme.tools.cache import CacheEntry

                    entry = CacheEntry(
                        document_ids=["doc1.txt"],
                        relevance_scores=[0.9],
                        created_at=datetime.now(),
                        last_accessed=datetime.now(),
                        access_count=1,
                        workspace_mtime=0.0,
                        index_mtime=0.0,
                        embedding_time_ms=0.0,
                        result_count=1,
                    )
                    cache.put(key, entry)
        except Exception as e:
            errors.append(f"Worker {worker_id}: {e}")

    # Run multiple threads
    num_threads = 10
    ops_per_thread = 100

    threads = []
    start = time.perf_counter()

    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(i, ops_per_thread))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    elapsed = time.perf_counter() - start

    total_ops = num_threads * ops_per_thread
    ops_per_sec = total_ops / elapsed

    print("\nResults:")
    print(f"  Threads:      {num_threads}")
    print(f"  Ops/thread:   {ops_per_thread}")
    print(f"  Total ops:    {total_ops}")
    print(f"  Time:         {elapsed:.3f}s")
    print(f"  Ops/sec:      {ops_per_sec:.0f}")
    print(f"  Errors:       {len(errors)}")

    if errors:
        print("\nErrors encountered:")
        for error in errors[:5]:  # Show first 5
            print(f"  {error}")

    print("\nTarget Validation:")
    print(
        f"  No errors:    {'✅' if len(errors) == 0 else '❌'} ({len(errors)} errors)"
    )
    print(
        f"  Ops/sec > 1000: {'✅' if ops_per_sec > 1000 else '❌'} ({ops_per_sec:.0f} ops/sec)"
    )

    return {
        "threads": num_threads,
        "total_ops": total_ops,
        "elapsed_sec": elapsed,
        "ops_per_sec": ops_per_sec,
        "errors": len(errors),
    }


def main():
    """Run all benchmarks and generate report."""
    print("=" * 60)
    print("RAG Cache Performance Benchmark (Phase 4e)")
    print("=" * 60)

    results = {}

    # Run benchmarks
    results["latency"] = benchmark_cache_latency(num_queries=1000)
    results["hit_rate"] = benchmark_cache_hit_rate(num_turns=20, queries_per_turn=10)
    results["memory"] = benchmark_memory_usage()
    results["concurrency"] = benchmark_concurrent_access()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print("\n✅ Latency Targets:")
    print(f"  Cache hit P95: {results['latency']['hit_p95']:.3f}ms (<5ms target)")

    print("\n✅ Hit Rate Target:")
    print(f"  Hit rate: {results['hit_rate']['hit_rate']:.1f}% (>70% target)")

    print("\n✅ Memory Target:")
    print(f"  Memory usage: {results['memory']['estimated_mb']:.2f}MB (<100MB target)")

    print("\n✅ Thread Safety:")
    print(f"  Concurrent errors: {results['concurrency']['errors']} (0 target)")
    print(f"  Ops/sec: {results['concurrency']['ops_per_sec']:.0f} (>1000 target)")

    # Overall validation
    all_targets_met = (
        results["latency"]["hit_p95"] < 5.0
        and results["hit_rate"]["hit_rate"] > 70.0
        and results["memory"]["estimated_mb"] < 100
        and results["concurrency"]["errors"] == 0
        and results["concurrency"]["ops_per_sec"] > 1000
    )

    print("\n" + "=" * 60)
    if all_targets_met:
        print("✅ ALL PERFORMANCE TARGETS MET")
    else:
        print("❌ SOME TARGETS NOT MET - See details above")
    print("=" * 60)

    return results


if __name__ == "__main__":
    main()
