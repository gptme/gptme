#!/usr/bin/env python3
"""Simple test of task analyzer without full gptme imports."""

import sys
from pathlib import Path

# Add context_compression to path
sys.path.insert(0, str(Path(__file__).parent / "gptme"))

# Import directly from analyzer submodule
from context_compression.analyzer.task_analyzer import TaskAnalyzer

# Test descriptions
TEST_05 = """
Context: PR #27 (gptme-agent-template) has CI failure
Issue: Pre-commit hook "Check for agent-instance names" failing
Task: Investigate CI failure, find root cause, verify fix, and trigger CI.
"""

TEST_10 = """
Context: gptme-contrib Issue #19 - drafts_generated counter not incremented for main draft
Problem: Counter only increments for thread drafts (line 999), not main drafts (line 970)
Task: Add counter increment for main draft to match thread draft behavior.
"""

TEST_15 = """
Context: Issue #167 Phase 3 Set 1 Day 2 - Implement Input Orchestrator Service
Requirements:
1. Core package (packages/lib/):
   - orchestrator.py: Multi-source coordination (~180 lines)
   - input_sources.py: Base classes (~80 lines)
2. Source implementations: github_source, email_source, webhook_source, scheduler_source
3. Example config, Tests, README
Task: Implement complete service package.
"""


def test_task(
    name: str,
    description: str,
    expected_category: str,
    expected_ratio_range: tuple[float, float],
):
    """Test task analysis."""
    print(f"\n{'='*60}")
    print(f"Test: {name}")
    print(f"{'='*60}")
    print(f"Description: {description[:100]}...")

    analyzer = TaskAnalyzer()
    result = analyzer.analyze(task_description=description)

    print("\nAnalysis:")
    print(
        f"  Complexity: {result.complexity_category} (score: {result.complexity_score:.3f})"
    )
    print(f"  Compression: {result.compression_ratio:.3f} ({result.ratio_category})")
    print(f"  Estimated Reduction: {result.estimated_reduction*100:.1f}%")

    category_match = result.complexity_category == expected_category
    ratio_match = (
        expected_ratio_range[0] <= result.compression_ratio <= expected_ratio_range[1]
    )

    print("\nExpected:")
    print(f"  Category: {expected_category}")
    print(f"  Ratio Range: {expected_ratio_range}")

    print("\nValidation:")
    print(
        f"  {'✓' if category_match else '✗'} Category: {result.complexity_category} {'==' if category_match else '!='} {expected_category}"
    )
    print(
        f"  {'✓' if ratio_match else '✗'} Ratio: {result.compression_ratio:.3f} in {expected_ratio_range}"
    )

    passed = category_match and ratio_match
    print(f"\n{'✓ PASS' if passed else '✗ FAIL'}")

    return passed


def main():
    """Run all tests."""
    print("Phase 3.1 Week 5 - Adaptive Compression Validation")

    results = []

    # Test 05: CI Fix (focused)
    results.append(test_task("test-05-ci-fix", TEST_05, "focused", (0.10, 0.20)))

    # Test 10: Bug Fix (focused)
    results.append(test_task("test-10-bug-fix", TEST_10, "focused", (0.10, 0.20)))

    # Test 15: Implementation (architecture)
    results.append(
        test_task("test-15-orchestrator", TEST_15, "architecture", (0.30, 0.50))
    )

    # Summary
    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"Target: {total}/{total} (100%)")

    if passed == total:
        print("\n✓ All critical tests passed!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
