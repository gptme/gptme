"""
Basic tests for the DSPy prompt optimization module.

These tests verify basic functionality without running expensive optimizations.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Check if DSPy is available and handle import errors gracefully
try:
    from gptme.eval.dspy import _has_dspy  # fmt: skip

    if not _has_dspy():
        pytest.skip("DSPy not available", allow_module_level=True)
except (ImportError, ModuleNotFoundError):
    pytest.skip("DSPy module not available", allow_module_level=True)

# Try to import all DSPy components directly - skip gracefully if any fail
try:
    from gptme.eval.dspy.cli import main  # fmt: skip
    from gptme.eval.dspy.experiments import (  # fmt: skip
        OptimizationExperiment,
        run_prompt_optimization_experiment,
    )
    from gptme.eval.dspy.metrics import (  # fmt: skip
        create_composite_metric,
        create_task_success_metric,
        create_tool_usage_metric,
    )
    from gptme.eval.dspy.prompt_optimizer import (  # fmt: skip
        PromptOptimizer,
        get_current_gptme_prompt,
        is_quota_error,
    )
    from gptme.eval.dspy.signatures import (  # fmt: skip
        GptmeTaskSignature,
        PromptEvaluationSignature,
    )
    from gptme.eval.dspy.tasks import (  # fmt: skip
        analyze_task_coverage,
        create_advanced_tasks,
        create_essential_tasks,
        get_prompt_optimization_tasks,
        get_tasks_by_focus_area,
    )
except (ImportError, AttributeError) as e:
    pytest.skip(f"DSPy imports failed: {e}", allow_module_level=True)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-20241120"


# Test basic imports
def test_imports():
    """Test that all main modules can be imported."""
    # Test that the imports are actually available
    assert PromptOptimizer is not None
    assert create_task_success_metric is not None
    assert create_tool_usage_metric is not None
    assert GptmeTaskSignature is not None
    assert PromptEvaluationSignature is not None
    assert run_prompt_optimization_experiment is not None


def test_task_creation():
    """Test creation of evaluation tasks."""

    # Test new essential tasks API
    essential_tasks = create_essential_tasks()
    assert isinstance(essential_tasks, list)
    assert len(essential_tasks) > 0
    assert all("name" in task for task in essential_tasks)
    assert all("prompt" in task for task in essential_tasks)

    # Test new advanced tasks API
    advanced_tasks = create_advanced_tasks()
    assert isinstance(advanced_tasks, list)
    assert len(advanced_tasks) > 0
    assert all("name" in task for task in advanced_tasks)
    assert all("prompt" in task for task in advanced_tasks)

    # Test focus area filtering
    from gptme.eval.dspy.tasks import get_task_metadata

    tool_tasks = get_tasks_by_focus_area("tool_selection")
    assert isinstance(tool_tasks, list)
    # May be empty if no tasks have this focus area
    if tool_tasks:
        # Verify all returned tasks have metadata with this focus area
        for task in tool_tasks:
            metadata = get_task_metadata(task["name"])
            assert "focus_areas" in metadata
            assert "tool_selection" in metadata["focus_areas"]

    debugging_tasks = get_tasks_by_focus_area("debugging")
    assert isinstance(debugging_tasks, list)

    # Test getting all tasks
    all_tasks = get_prompt_optimization_tasks()
    assert isinstance(all_tasks, list)
    assert len(all_tasks) > 0


def test_task_structure():
    """Test that tasks have required structure."""
    from gptme.eval.dspy.tasks import get_task_metadata

    tasks = get_prompt_optimization_tasks()

    for task in tasks:
        assert "name" in task
        assert "prompt" in task

        # Check that metadata contains focus_areas
        metadata = get_task_metadata(task["name"])
        assert "focus_areas" in metadata
        assert isinstance(metadata["focus_areas"], list)
        assert len(metadata["focus_areas"]) > 0


def test_metrics_creation():
    """Test creation of evaluation metrics."""

    # Test metric creation (without actually calling them)
    task_metric = create_task_success_metric([])
    assert callable(task_metric)

    tool_metric = create_tool_usage_metric()
    assert callable(tool_metric)

    composite_metric = create_composite_metric()
    assert callable(composite_metric)


def test_tool_metric_empty_messages():
    """Test that tool_usage_metric returns 0.0 on empty messages instead of raising."""
    tool_metric = create_tool_usage_metric()

    # Simulate a prediction with empty messages (e.g. from API quota exhaustion)
    pred = MagicMock()
    pred.messages = []
    gold = MagicMock()
    gold.tools = ["save"]

    # Should return 0.0 gracefully, not raise ValueError
    score = tool_metric(gold, pred, None)
    assert score == 0.0


@patch("gptme.eval.dspy.prompt_optimizer.dspy")
def test_prompt_optimizer_init(mock_dspy):
    """Test PromptOptimizer initialization."""

    # Mock DSPy to avoid actual model setup
    mock_dspy.LM.return_value = MagicMock()
    mock_dspy.configure = MagicMock()

    optimizer = PromptOptimizer(model=DEFAULT_MODEL, optimizer_type="miprov2")

    assert optimizer.model == DEFAULT_MODEL
    assert optimizer.optimizer_type == "miprov2"
    mock_dspy.configure.assert_called_once()


def test_get_current_prompt():
    """Test getting current gptme prompt."""

    prompt = get_current_gptme_prompt(interactive=True, model=DEFAULT_MODEL)

    assert isinstance(prompt, str)
    assert len(prompt) > 100  # Should be a substantial prompt
    assert "gptme" in prompt.lower()


def test_task_coverage_analysis():
    """Test task coverage analysis functionality."""

    coverage = analyze_task_coverage()
    assert isinstance(coverage, dict)
    assert len(coverage) > 0

    # Test that each focus area has at least one task
    for area, tasks in coverage.items():
        assert isinstance(tasks, list)
        assert len(tasks) > 0

        # Test getting tasks by focus area
        focus_tasks = get_tasks_by_focus_area(area)
        assert len(focus_tasks) >= len(
            tasks
        )  # Should find at least the tasks in coverage


@pytest.mark.skipif(
    True,  # Skip by default as this requires DSPy and can be slow
    reason="Expensive test performing actual optimization",
)
def test_optimization_experiment():
    """Test running a small optimization experiment."""

    experiment = OptimizationExperiment(
        name="test_experiment",
        model=DEFAULT_MODEL,
        output_dir=Path("/tmp/gptme_test_experiment"),
    )

    assert experiment.name == "test_experiment"
    assert experiment.model == DEFAULT_MODEL
    assert experiment.output_dir.exists()


def test_is_quota_error():
    """Test is_quota_error correctly identifies API quota/rate-limit errors."""
    # Anthropic usage limit message (the real-world trigger)
    assert is_quota_error(
        Exception("You have reached your specified API usage limits.")
    )

    # Rate limit phrase variants
    assert is_quota_error(Exception("rate limit exceeded"))
    assert is_quota_error(Exception("rate_limit error"))

    # API quota phrase (specific, not bare "quota")
    assert is_quota_error(Exception("api quota exceeded"))

    # Should NOT match unrelated quota messages (false-positive guard)
    assert not is_quota_error(Exception("disk quota exceeded"))
    assert not is_quota_error(Exception("quota: storage full"))

    # Generic errors that are NOT quota-related
    assert not is_quota_error(ValueError("No messages available"))
    assert not is_quota_error(RuntimeError("Connection timeout"))
    assert not is_quota_error(Exception("Bad request: invalid model"))


def test_is_quota_error_type_based():
    """Test is_quota_error with anthropic exception types (isinstance-based paths)."""
    try:
        from anthropic import BadRequestError, RateLimitError
        from httpx import Request, Response

        request = Request("POST", "https://api.anthropic.com/v1/messages")

        # RateLimitError → always a quota error
        rate_limit_err = RateLimitError(
            "rate limit exceeded",
            response=Response(429, request=request),
            body={},
        )
        assert is_quota_error(rate_limit_err)

        # BadRequestError with "usage limits" → quota error
        quota_bad_request = BadRequestError(
            "You have reached your specified API usage limits.",
            response=Response(400, request=request),
            body={},
        )
        assert is_quota_error(quota_bad_request)

        # BadRequestError without quota message → not a quota error
        other_bad_request = BadRequestError(
            "invalid model specified",
            response=Response(400, request=request),
            body={},
        )
        assert not is_quota_error(other_bad_request)

    except ImportError:
        pytest.skip("anthropic package not available for isinstance tests")


def test_cli_argument_parsing():
    """Test CLI argument parsing without actually running commands."""

    # Test help command
    with patch.object(sys, "argv", ["cli.py", "--help"]), pytest.raises(SystemExit):
        main()

    # Test show-prompt command parsing - verify it runs successfully
    with patch.object(sys, "argv", ["cli.py", "show-prompt", "--model", "test-model"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        # CLI commands normally exit with code 0 on success
        assert exc_info.value.code == 0
