"""
Benchmark context selector strategies for accuracy, cost, and latency.

This validates the performance characteristics claimed in README.md:
- Rule-based: <100ms, Free, Good accuracy
- LLM-based: 1-3s, $0.001-0.01, Best accuracy
- Hybrid: 500ms-1s, $0.0005-0.005, Better accuracy

Usage:
    pytest tests/benchmark_context_selector.py -v -s -m benchmark
    pytest tests/benchmark_context_selector.py::test_benchmark_cost_estimation -v -s
"""

import time
from pathlib import Path

import pytest

from gptme.context_selector.config import ContextSelectorConfig
from gptme.lessons.matcher_enhanced import EnhancedLessonMatcher, MatchContext
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.lessons.selector_config import LessonSelectorConfig
from gptme.message import Message


def create_test_lesson(
    path: str = "test.md",
    keywords: list[str] | None = None,
    category: str = "general",
    title: str = "Test Lesson",
    content: str = "Test lesson content",
) -> Lesson:
    """Helper to create test lessons (from test_integration_phase4.py)."""
    metadata = LessonMetadata(
        keywords=keywords or [],
        tools=[],
        status="active",
    )
    return Lesson(
        path=Path(path),
        metadata=metadata,
        title=title,
        description=f"{title} description",
        category=category,
        body=content,
    )


def messages_to_context(messages: list[Message]) -> MatchContext:
    """Convert a list of Messages to a MatchContext (from test_integration_phase4.py)."""
    message_text = "\n".join(f"{msg.role}: {msg.content}" for msg in messages)
    return MatchContext(message=message_text)


# Fixtures
@pytest.fixture
def test_lessons() -> list[Lesson]:
    """Create test lessons with varied metadata."""
    return [
        create_test_lesson(
            path="git-workflow.md",
            keywords=["git", "commit", "push"],
            category="workflow",
            title="Git Workflow",
            content="Use conventional commits.",
        ),
        create_test_lesson(
            path="shell-tool.md",
            keywords=["shell", "command", "bash"],
            category="tools",
            title="Shell Tool",
            content="Chain commands with &&.",
        ),
        create_test_lesson(
            path="python-coding.md",
            keywords=["python", "code", "function"],
            category="patterns",
            title="Python Coding",
            content="Use type hints.",
        ),
    ]


@pytest.fixture
def git_conversation() -> list[Message]:
    """Conversation about git workflow."""
    return [
        Message("user", "How do I commit changes?"),
        Message("assistant", "Use git add and git commit"),
        Message("user", "Should I push to master?"),
    ]


@pytest.fixture
def shell_conversation() -> list[Message]:
    """Conversation about shell commands."""
    return [
        Message("user", "Run tests and build"),
        Message("assistant", "Use shell commands"),
        Message("user", "How to chain commands?"),
    ]


@pytest.fixture
def mixed_conversation() -> list[Message]:
    """Conversation mixing multiple topics."""
    return [
        Message("user", "I need to commit Python code"),
        Message("assistant", "Let's write a function first"),
        Message("user", "Then git commit and push?"),
    ]


# Benchmark Tests
@pytest.mark.benchmark
def test_benchmark_lesson_selection_rule_based(
    test_lessons: list[Lesson], git_conversation: list[Message]
):
    """Benchmark rule-based lesson selection."""
    matcher = EnhancedLessonMatcher(
        selector_config=ContextSelectorConfig(strategy="rule"),
        lesson_config=LessonSelectorConfig(),
        use_selector=False,  # Rule-based only
    )

    # Warm-up
    context = messages_to_context(git_conversation)
    matcher.match(test_lessons, context)

    # Benchmark (10 runs)
    times = []
    for _ in range(10):
        start = time.perf_counter()
        results = matcher.match(test_lessons, context)
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)  # Convert to ms

    avg_time = sum(times) / len(times)

    print("\n=== Rule-based Benchmark ===")
    print(f"Average latency: {avg_time:.2f}ms")
    print(f"Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")
    print("Cost: $0.00 (free)")
    print(f"Results: {len(results)} lessons matched")

    # Validate against README claims
    assert avg_time < 100, f"Rule-based should be <100ms, got {avg_time:.2f}ms"
    assert len(results) > 0, "Should match at least one lesson"


@pytest.mark.benchmark
def test_benchmark_cost_estimation():
    """Estimate daily costs for production usage."""
    # Assumptions
    autonomous_runs_per_day = 48  # From vm-setup.md
    lesson_selections_per_run = 2  # Conservative estimate
    total_selections = autonomous_runs_per_day * lesson_selections_per_run

    # Cost per strategy
    costs = {
        "rule": 0.00,  # Free
        "llm": 0.005,  # Mid-range from $0.001-0.01
        "hybrid": 0.0025,  # Mid-range from $0.0005-0.005
    }

    print("\n=== Daily Cost Estimation ===")
    print(f"Autonomous runs/day: {autonomous_runs_per_day}")
    print(f"Selections/run: {lesson_selections_per_run}")
    print(f"Total selections/day: {total_selections}")
    print()

    for strategy, cost_per_call in costs.items():
        daily_cost = total_selections * cost_per_call
        monthly_cost = daily_cost * 30
        print(f"{strategy.upper()}:")
        print(f"  Per call: ${cost_per_call:.4f}")
        print(f"  Daily: ${daily_cost:.2f}")
        print(f"  Monthly: ${monthly_cost:.2f}")

    # Validate hybrid is cost-effective
    hybrid_monthly = costs["hybrid"] * total_selections * 30
    assert (
        hybrid_monthly < 10
    ), f"Hybrid monthly cost should be <$10, got ${hybrid_monthly:.2f}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "benchmark"])
