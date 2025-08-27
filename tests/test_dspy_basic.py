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
except ImportError:
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
    )
    from gptme.eval.dspy.signatures import (  # fmt: skip
        GptmeTaskSignature,
        PromptEvaluationSignature,
    )
    from gptme.eval.dspy.tasks import (  # fmt: skip
        analyze_task_coverage,
        create_error_handling_tasks,
        create_instruction_following_tasks,
        create_reasoning_tasks,
        create_tool_usage_tasks,
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

    tool_tasks = create_tool_usage_tasks()
    assert isinstance(tool_tasks, list)
    assert len(tool_tasks) > 0
    assert all("name" in task for task in tool_tasks)
    assert all("prompt" in task for task in tool_tasks)

    reasoning_tasks = create_reasoning_tasks()
    assert isinstance(reasoning_tasks, list)
    assert len(reasoning_tasks) > 0

    instruction_tasks = create_instruction_following_tasks()
    assert isinstance(instruction_tasks, list)
    assert len(instruction_tasks) > 0

    error_tasks = create_error_handling_tasks()
    assert isinstance(error_tasks, list)
    assert len(error_tasks) > 0

    all_tasks = get_prompt_optimization_tasks()
    assert len(all_tasks) == len(tool_tasks) + len(reasoning_tasks) + len(
        instruction_tasks
    ) + len(error_tasks)


def test_task_structure():
    """Test that tasks have required structure."""

    tasks = get_prompt_optimization_tasks()

    for task in tasks:
        assert "name" in task
        assert "prompt" in task
        assert "focus_areas" in task
        assert isinstance(task["focus_areas"], list)
        assert len(task["focus_areas"]) > 0


def test_metrics_creation():
    """Test creation of evaluation metrics."""

    # Test metric creation (without actually calling them)
    task_metric = create_task_success_metric([])
    assert callable(task_metric)

    tool_metric = create_tool_usage_metric()
    assert callable(tool_metric)

    composite_metric = create_composite_metric()
    assert callable(composite_metric)


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


def test_cli_argument_parsing():
    """Test CLI argument parsing without actually running commands."""

    # Test help command
    with patch.object(sys, "argv", ["cli.py", "--help"]):
        with pytest.raises(SystemExit):
            main()

    # Test show-prompt command parsing - verify it runs successfully
    with patch.object(sys, "argv", ["cli.py", "show-prompt", "--model", "test-model"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        # CLI commands normally exit with code 0 on success
        assert exc_info.value.code == 0
