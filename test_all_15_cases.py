#!/usr/bin/env python3
"""Comprehensive test of adaptive compression across all 15 test cases."""

import sys
from pathlib import Path
from typing import cast

# Add context_compression to path
sys.path.insert(0, str(Path(__file__).parent / "gptme"))

from context_compression.analyzer.task_analyzer import TaskAnalyzer

# All 15 test case descriptions
TESTS = {
    "test-01": {
        "name": "Bug Fix - PR Critical Issues",
        "description": """
Context: gptme-agent-template PR #29 has 3 critical issues identified by greptile (1/5 confidence score):
1. scripts/context.sh line 32: Unconditional tasks.py call will fail when not installed
2. .pre-commit-config.yaml line 51: tasks.py validation hook fails (file doesn't exist)
3. fork.sh lines 130-134: Missing submodule init, unconditional tasks.py call
Task: Fix all 3 issues to make tasks.py truly optional as documented.
Files: scripts/context.sh, .pre-commit-config.yaml, fork.sh
        """,
        "expected_category": "focused",
        "expected_ratio": (0.10, 0.20),
    },
    "test-02": {
        "name": "Investigation - Twitter Monitoring",
        "description": """
Context: Erik asks: "Your workflow that monitors the timeline should maybe draft some tweets?"
Issue: Unclear if Twitter monitoring system is working correctly.
Task: Investigate whether workflow.py properly drafts tweets and identify why no drafts are visible.
Resources: gptme-contrib/scripts/twitter/workflow.py, tweets/ directory, systemd logs
        """,
        "expected_category": "focused",
        "expected_ratio": (0.10, 0.20),
    },
    "test-03": {
        "name": "Analysis - GEPA Results",
        "description": """
Context: GEPA Phase 3.3 26-task validation completed. Results available in experiments/ directory.
Task: Analyze results, compare to Test 2 baseline (78.8% GEPA score), and create comprehensive analysis document.
Data: experiments/gepa-26-task-validation_* directories, Test 2 baseline: 8 tasks, 78.8% GEPA score
        """,
        "expected_category": "mixed",
        "expected_ratio": (0.20, 0.30),
    },
    "test-04": {
        "name": "PR Creation - Lessons System",
        "description": """
Context: sync-agent-template task, Phase 3.1, PR 1.2
Task: Create PR #28 to upstream lessons system structure to gptme-agent-template.
Requirements: Complete directory structure (lessons/patterns/, lessons/workflow/), lesson template, enhanced README
        """,
        "expected_category": "mixed",
        "expected_ratio": (0.20, 0.30),
    },
    "test-05": {
        "name": "CI Fix - Agent Name Check",
        "description": """
Context: PR #27 (gptme-agent-template) has CI failure
Issue: Pre-commit hook "Check for agent-instance names" failing
Task: Investigate CI failure, find root cause, verify fix, and trigger CI.
        """,
        "expected_category": "focused",
        "expected_ratio": (0.10, 0.20),
    },
    "test-06": {
        "name": "Implementation - GEPA Config Fix",
        "description": """
Context: GEPA Phase 3.3 - Task source using wrong default (8 tests instead of 26)
Task: Fix OptimizationExperiment to use get_prompt_optimization_tasks() (26 tasks) instead of gptme_eval_tests (8 tests).
Files: gptme/eval/dspy/experiments.py
Changes: Add import, update 3 method defaults
        """,
        "expected_category": "focused",
        "expected_ratio": (0.10, 0.20),
    },
    "test-07": {
        "name": "Design - ActivityWatch AI Integration",
        "description": """
Context: Opportunity identification task, focusing on ActivityWatch feature-driven development.
Background: Research identified AI-powered productivity coaching as top priority
Task: Design complete AI integration architecture for ActivityWatch "Quantified Self AI Coach" feature.
Requirements: Feature request research, analysis document, AI integration architecture (~400 lines), system architecture (5 layers), Phase 1 MVP design
        """,
        "expected_category": "architecture",
        "expected_ratio": (0.30, 0.50),
    },
    "test-08": {
        "name": "Research Comparison - HGM vs GEPA",
        "description": """
Context: Erik asked: "This is starting to look a lot like GEPA, how is it different?"
Background: HGM research completed, GEPA 68% complete
Task: Compare HGM and GEPA approaches, identify key differences and relationships.
Requirements: Comparative analysis, relationship mapping, integration opportunities
        """,
        "expected_category": "mixed",
        "expected_ratio": (0.20, 0.30),
    },
    "test-09": {
        "name": "Research - Funding Investigation",
        "description": """
Context: Issue #148 - Investigate Pace Capital Uncapped funding opportunity
Task: Research and analyze the Pace Capital Uncapped offering to help Erik decide whether to take the initial call.
Requirements: Comprehensive analysis (600+ lines), structure explanation, pros/cons, questions for call, comparison with alternatives
        """,
        "expected_category": "mixed",
        "expected_ratio": (0.20, 0.30),
    },
    "test-10": {
        "name": "Simple Bug Fix - Counter Increment",
        "description": """
Context: gptme-contrib Issue #19 - drafts_generated counter not incremented for main draft
Problem: Counter only increments for thread drafts (line 999), not main drafts (line 970)
Task: Add counter increment for main draft to match thread draft behavior.
        """,
        "expected_category": "focused",
        "expected_ratio": (0.10, 0.20),
    },
    "test-11": {
        "name": "Maintenance - Task Cleanup",
        "description": """
Context: Task system has accumulated stale entries and inconsistencies
Task: Review all tasks, update states, clean up duplicates, ensure metadata consistency.
Requirements: Task review, state updates, metadata validation, documentation
        """,
        "expected_category": "mixed",
        "expected_ratio": (0.20, 0.30),
    },
    "test-12": {
        "name": "PR Creation - Task Scheduler System",
        "description": """
Context: sync-agent-template task, Phase 3.1
Task: Create PR for task scheduler system implementation.
Requirements: Complete package structure, scheduler.py (~200 lines), priority queue, interval management, tests, documentation
        """,
        "expected_category": "architecture",
        "expected_ratio": (0.30, 0.50),
    },
    "test-13": {
        "name": "Bug Diagnosis - Greptile Review",
        "description": """
Context: Greptile review identified potential issues in codebase
Task: Investigate reported issues, verify if they're real bugs or false positives, document findings.
Requirements: Issue verification, code analysis, report generation
        """,
        "expected_category": "mixed",
        "expected_ratio": (0.20, 0.30),
    },
    "test-14": {
        "name": "Phase Completion - Implementation Planning",
        "description": """
Context: Major project phase nearing completion
Task: Create comprehensive implementation plan for next phase, including architecture decisions, milestones, resource requirements.
Requirements: Phase analysis, architecture planning, milestone definition, resource allocation, risk assessment
        """,
        "expected_category": "architecture",
        "expected_ratio": (0.30, 0.50),
    },
    "test-15": {
        "name": "Implementation - Service Package",
        "description": """
Context: Issue #167 Phase 3 Set 1 Day 2 - Implement Input Orchestrator Service
Requirements:
1. Core package: orchestrator.py (~180 lines), input_sources.py (~80 lines)
2. Source implementations: github_source, email_source, webhook_source, scheduler_source
3. Example config, Tests, README
Task: Implement complete service package.
        """,
        "expected_category": "architecture",
        "expected_ratio": (0.30, 0.50),
    },
}


def test_task(test_id: str, test_info: dict):
    """Test single task analysis."""
    print(f"\n{'='*70}")
    print(f"{test_id}: {test_info['name']}")
    print(f"{'='*70}")

    analyzer = TaskAnalyzer()
    result = analyzer.analyze(task_description=test_info["description"])

    expected_category = test_info["expected_category"]
    expected_ratio = test_info["expected_ratio"]

    print(
        f"  Complexity: {result.complexity_category} (score: {result.complexity_score:.3f})"
    )
    print(f"  Compression: {result.compression_ratio:.3f} ({result.ratio_category})")

    category_match = result.complexity_category == expected_category
    ratio_match = expected_ratio[0] <= result.compression_ratio <= expected_ratio[1]

    print(f"  Expected: {expected_category}, {expected_ratio}")
    print(f"  {'✓' if category_match else '✗'} Category match")
    print(f"  {'✓' if ratio_match else '✗'} Ratio match")

    passed = category_match and ratio_match
    print(f"  {'✓ PASS' if passed else '✗ FAIL'}")

    return passed


def main():
    """Run all 15 tests."""
    print("=" * 70)
    print("Context Compression Phase 3.1 Week 5 - Complete Validation")
    print("Testing adaptive compression across all 15 test cases")
    print("=" * 70)

    results = {}
    for test_id in sorted(TESTS.keys()):
        results[test_id] = test_task(test_id, TESTS[test_id])

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    passed = sum(results.values())
    total = len(results)

    print(f"\nOverall: {passed}/{total} ({passed/total*100:.1f}%)")
    print("Target: 95%+ (14+/15)")

    # By category
    categories: dict[str, list[tuple[str, bool]]] = {
        "focused": [],
        "mixed": [],
        "architecture": [],
    }
    for test_id, passed in results.items():
        cat = cast(str, TESTS[test_id]["expected_category"])
        categories[cat].append((test_id, passed))

    for cat, tests in categories.items():
        passed_cat = sum(1 for _, p in tests if p)
        total_cat = len(tests)
        print(
            f"\n{cat.capitalize()}: {passed_cat}/{total_cat} ({passed_cat/total_cat*100:.0f}%)"
        )
        for test_id, passed in tests:
            status = "✓" if passed else "✗"
            print(f"  {status} {test_id}")

    if passed >= 14:
        print(f"\n✓ SUCCESS: {passed}/15 tests passed (target: 95%+)")
        return 0
    else:
        print(f"\n✗ FAILED: {passed}/15 tests passed (target: 14+)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
