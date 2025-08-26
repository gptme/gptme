"""
Integration tests for DSPy prompt optimization.

These tests verify integration with gptme's existing evaluation framework.
"""

import argparse
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from gptme.eval.dspy.experiments import OptimizationExperiment, quick_prompt_test
from gptme.eval.dspy.metrics import create_composite_metric
from gptme.eval.dspy.prompt_optimizer import (
    GptmeModule,
    PromptDataset,
    get_current_gptme_prompt,
)
from gptme.eval.dspy.tasks import get_prompt_optimization_tasks, get_tasks_by_focus_area
from gptme.eval.types import EvalSpec


def test_dataset_creation():
    """Test creating DSPy datasets from gptme eval specs."""

    # Sample eval specs similar to gptme's format
    eval_specs = cast(
        list[EvalSpec],
        [
            {
                "name": "test-task",
                "files": {"hello.py": 'print("Hello")'},
                "run": "python hello.py",
                "prompt": "Create a hello world script",
                "tools": ["save"],
                "expect": {"correct_output": lambda ctx: "Hello" in ctx.stdout},
            }
        ],
    )

    dataset = PromptDataset(eval_specs)
    assert len(dataset) == 1

    examples = list(dataset)
    assert len(examples) == 1

    example = examples[0]
    assert hasattr(example, "task_description")
    assert hasattr(example, "context")
    assert example.task_description == "Create a hello world script"
    assert "hello.py" in example.context


def test_dataset_limiting():
    """Test dataset size limiting."""

    eval_specs = cast(
        list[EvalSpec],
        [{"name": f"task-{i}", "prompt": f"Task {i}"} for i in range(10)],
    )

    # Test without limit
    dataset_full = PromptDataset(eval_specs)
    assert len(dataset_full) == 10

    # Test with limit
    dataset_limited = PromptDataset(eval_specs, limit=5)
    assert len(dataset_limited) == 5


@patch("gptme.eval.dspy.prompt_optimizer.dspy")
def test_gptme_module_creation(mock_dspy):
    """Test creating DSPy module wrapper for gptme."""

    prompt = "You are gptme, a helpful assistant."
    module = GptmeModule(prompt)

    assert module.system_prompt_template == prompt
    assert hasattr(module, "forward")


def test_experiment_initialization():
    """Test experiment setup and initialization."""

    with tempfile.TemporaryDirectory() as tmp_dir:
        experiment = OptimizationExperiment(
            name="test_experiment", output_dir=Path(tmp_dir), model="test-model"
        )

        assert experiment.name == "test_experiment"
        assert experiment.model == "test-model"
        assert experiment.output_dir == Path(tmp_dir)
        assert "experiment_name" in experiment.results
        assert experiment.results["experiment_name"] == "test_experiment"


def test_prompt_comparison_structure():
    """Test structure of prompt comparison functionality."""

    prompts = {
        "prompt1": "You are a helpful assistant.",
        "prompt2": "You are an advanced AI assistant.",
    }

    # Mock the actual optimization to avoid expensive calls
    with patch("gptme.eval.dspy.experiments.PromptOptimizer") as mock_optimizer:
        mock_instance = MagicMock()
        mock_instance.compare_prompts.return_value = {
            "prompt1": {"average_score": 0.7},
            "prompt2": {"average_score": 0.8},
        }
        mock_optimizer.return_value = mock_instance

        with patch("gptme.eval.dspy.experiments.get_all_tests") as mock_tests:
            mock_tests.return_value = [{"name": "test", "prompt": "test prompt"}]

            result = quick_prompt_test(prompts, num_examples=2, model="test-model")

            assert isinstance(result, dict)
            assert "prompt1" in result
            assert "prompt2" in result


def test_task_focus_areas():
    """Test that tasks properly define focus areas."""

    all_tasks = get_prompt_optimization_tasks()

    # Collect all focus areas
    focus_areas = set()
    for task in all_tasks:
        focus_areas.update(task.get("focus_areas", []))

    assert len(focus_areas) > 0

    # Test that we can filter by each focus area
    for area in focus_areas:
        filtered_tasks = get_tasks_by_focus_area(area)
        assert len(filtered_tasks) > 0

        # Verify all returned tasks actually have this focus area
        for task in filtered_tasks:
            assert area in task.get("focus_areas", [])


def test_cli_commands_structure():
    """Test that CLI commands are properly structured."""

    # Test that main parser can be created
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    # Test that we can add a command (without actually running it)
    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--model", default="test-model")

    # Parse some test arguments
    args = parser.parse_args(["test", "--model", "anthropic/claude-sonnet-4-20250514"])
    assert args.model == "anthropic/claude-sonnet-4-20250514"


def test_metric_composition():
    """Test that metrics can be properly composed."""

    # Test creating composite metric with different weights
    metric1 = create_composite_metric(
        task_weight=0.5, tool_weight=0.3, judge_weight=0.2
    )
    metric2 = create_composite_metric(
        task_weight=0.7, tool_weight=0.2, judge_weight=0.1
    )

    assert callable(metric1)
    assert callable(metric2)

    # Test that weights are properly handled (would need actual execution to fully verify)


def test_prompt_template_extraction():
    """Test extracting prompts from gptme's prompt system."""

    # Test both interactive and non-interactive modes
    interactive_prompt = get_current_gptme_prompt(interactive=True)
    non_interactive_prompt = get_current_gptme_prompt(interactive=False)

    assert isinstance(interactive_prompt, str)
    assert isinstance(non_interactive_prompt, str)
    assert len(interactive_prompt) > 0
    assert len(non_interactive_prompt) > 0

    # Interactive should mention user interaction
    assert "interactive" in interactive_prompt.lower()
    # Non-interactive should mention automatic execution
    assert "non-interactive" in non_interactive_prompt.lower()


@pytest.mark.integration
def test_end_to_end_structure():
    """Test the complete structure of an optimization workflow (without execution)."""

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Initialize experiment
        experiment = OptimizationExperiment(
            name="integration_test", output_dir=Path(tmp_dir), model="test-model"
        )

        # Verify experiment structure
        assert experiment.name == "integration_test"
        assert experiment.output_dir.exists()
        assert "experiment_name" in experiment.results

        # Test result saving structure
        results_file = experiment.save_results()
        assert results_file.exists()
        assert results_file.suffix == ".json"

        # Test report generation structure
        report = experiment.generate_report()
        assert isinstance(report, str)
        assert "integration_test" in report
        assert "Prompt Optimization Report" in report
