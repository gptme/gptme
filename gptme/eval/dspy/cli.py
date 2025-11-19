"""
Command-line interface for gptme prompt optimization using DSPy.

This module provides CLI commands for running prompt optimization experiments.
"""

import logging
import sys
from pathlib import Path
from typing import cast

import click

from gptme.eval.suites import tests as gptme_eval_tests
from gptme.eval.types import EvalSpec

from .experiments import quick_prompt_test, run_prompt_optimization_experiment
from .prompt_optimizer import get_current_gptme_prompt
from .tasks import (
    analyze_task_coverage,
    get_prompt_optimization_tasks,
    get_task_metadata,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-haiku-4-5"


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """gptme prompt optimization using DSPy.

    Examples:

    \b
      # Run full optimization experiment
      gptme-dspy optimize --name "my_experiment" --model anthropic/claude-haiku-4-5

    \b
      # Quick test of prompt variations
      gptme-dspy quick-test --prompt-files prompt1.txt prompt2.txt --num-examples 5

    \b
      # Show current system prompt
      gptme-dspy show-prompt

    \b
      # List available tasks
      gptme-dspy list-tasks --optimization-tasks
    """
    # Configure logging
    log_format = (
        "%(levelname)s: %(message)s"
        if not verbose
        else "%(name)s - %(levelname)s: %(message)s"
    )

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format=log_format)
    else:
        logging.basicConfig(level=logging.WARNING, format=log_format)
        # Specifically quiet down our DSPy modules
        logging.getLogger("gptme.eval.dspy").setLevel(logging.ERROR)


@cli.command()
@click.option("--name", default="prompt_optimization", help="Experiment name")
@click.option("--model", default=DEFAULT_MODEL, help="Model to use")
@click.option("--output-dir", help="Output directory for results")
@click.option(
    "--max-demos", type=int, default=3, help="Maximum number of demo examples"
)
@click.option(
    "--num-trials", type=int, default=10, help="Number of optimization trials"
)
@click.option("--train-size", type=int, default=15, help="Number of training examples")
@click.option("--val-size", type=int, default=10, help="Number of validation examples")
@click.option(
    "--baseline-examples",
    type=int,
    default=20,
    help="Number of examples for baseline evaluation",
)
@click.option(
    "--comparison-examples",
    type=int,
    default=15,
    help="Number of examples for final comparison",
)
@click.option(
    "--optimizers",
    multiple=True,
    type=click.Choice(["miprov2", "bootstrap"]),
    help="Optimizers to use (default: miprov2, bootstrap)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Skip actual optimization calls, use fake results for testing",
)
def optimize(
    name: str,
    model: str,
    output_dir: str | None,
    max_demos: int,
    num_trials: int,
    train_size: int,
    val_size: int,
    baseline_examples: int,
    comparison_examples: int,
    optimizers: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Run a full prompt optimization experiment.

    For quick testing, use smaller values:
    --train-size 3 --val-size 2 --baseline-examples 5 --comparison-examples 3
    """
    print(f"Starting prompt optimization experiment: {name}")
    print(f"Using model: {model}")
    print(
        f"Dataset sizes: train={train_size}, val={val_size}, baseline={baseline_examples}, comparison={comparison_examples}"
    )
    print(f"Output directory: {output_dir}")

    # Configure optimizers based on args
    optimizer_configs = {}

    if not optimizers or "miprov2" in optimizers:
        optimizer_configs["miprov2"] = {
            "optimizer_type": "miprov2",
            "max_demos": max_demos,
            "num_trials": num_trials,
        }

    if not optimizers or "bootstrap" in optimizers:
        optimizer_configs["bootstrap"] = {
            "optimizer_type": "bootstrap",
            "max_demos": max_demos,
            "num_trials": max(num_trials // 2, 3),
        }

    # Run experiment
    try:
        experiment = run_prompt_optimization_experiment(
            experiment_name=name,
            model=model,
            optimizers=optimizer_configs,
            output_dir=Path(output_dir) if output_dir else Path("experiments"),
            train_size=train_size,
            val_size=val_size,
            baseline_examples=baseline_examples,
            comparison_examples=comparison_examples,
            dry_run=dry_run,
        )

        print("\n✅ Experiment completed successfully!")
        print(f"📊 Results saved to: {experiment.output_dir}")
        print(f"📝 Report: {experiment.output_dir / f'{name}_report.md'}")

        # Print quick summary
        if "comparisons" in experiment.results:
            comparisons_data = experiment.results["comparisons"]
            assert isinstance(comparisons_data, dict)  # Type narrowing
            comparison = comparisons_data["results"]
            assert isinstance(comparison, dict)  # Type narrowing

            best_name = max(
                comparison.keys(), key=lambda k: comparison[k].get("average_score", 0)
            )
            best_score = comparison[best_name].get("average_score", 0)
            baseline_data = comparison.get("baseline", {})
            assert isinstance(baseline_data, dict)  # Type narrowing
            baseline_score = baseline_data.get("average_score", 0)

            print(f"\n📈 Best performing prompt: {best_name} (score: {best_score:.3f})")
            if baseline_score > 0:
                improvement = best_score - baseline_score
                print(f"📊 Improvement over baseline: {improvement:+.3f}")

    except Exception as e:
        print(f"❌ Experiment failed: {e}")
        logger.exception("Optimization experiment failed")
        sys.exit(1)


@cli.command("quick-test")
@click.option("--prompt-files", multiple=True, help="Prompt files to compare")
@click.option("--num-examples", type=int, default=5, help="Number of examples to test")
@click.option("--model", default=DEFAULT_MODEL, help="Model to use")
def quick_test(prompt_files: tuple[str, ...], num_examples: int, model: str) -> None:
    """Run a quick test of prompt variations."""
    print(f"Running quick prompt test with {num_examples} examples")

    # Load prompt variations
    prompts = {}

    # Add current prompt as baseline
    current_prompt = get_current_gptme_prompt(interactive=True, model=model)
    prompts["baseline"] = current_prompt

    # Add prompt files if specified
    if prompt_files:
        for file_path in prompt_files:
            path = Path(file_path)
            if path.exists():
                prompts[path.stem] = path.read_text()
            else:
                print(f"⚠️  Prompt file not found: {file_path}")

    if len(prompts) == 1:
        print(
            "⚠️  Only one prompt available. Add --prompt-files to compare multiple prompts."
        )

    # Run comparison
    try:
        quick_prompt_test(
            prompt_variations=prompts, num_examples=num_examples, model=model
        )

        print("\n✅ Quick test completed!")

    except Exception as e:
        print(f"❌ Quick test failed: {e}")
        logger.exception("Quick test failed")
        sys.exit(1)


@cli.command("show-prompt")
@click.option("--model", default=DEFAULT_MODEL, help="Model to use")
@click.option("--non-interactive", is_flag=True, help="Show non-interactive prompt")
def show_prompt(model: str, non_interactive: bool) -> None:
    """Show the current gptme system prompt."""
    current_prompt = get_current_gptme_prompt(
        interactive=not non_interactive, model=model
    )

    print("=== Current gptme System Prompt ===")
    print(current_prompt)
    print(f"\nPrompt length: {len(current_prompt)} characters")
    print(f"Lines: {current_prompt.count(chr(10)) + 1}")


@cli.command("list-tasks")
@click.option(
    "--optimization-tasks",
    is_flag=True,
    help="Show prompt optimization tasks instead of standard eval tasks",
)
def list_tasks(optimization_tasks: bool) -> None:
    """List available evaluation tasks."""
    if optimization_tasks:
        tasks = get_prompt_optimization_tasks()
        print("=== Prompt Optimization Tasks ===")
        print(f"Total tasks: {len(tasks)}\n")

        for task in tasks:
            name = task.get("name", "unknown")
            metadata = get_task_metadata(name)
            focus_areas = metadata.get("focus_areas", [])
            prompt = task.get("prompt", "")[:100]

            print(f"• {name}")
            print(f"  Focus: {', '.join(focus_areas)}")
            print(
                f"  Task: {prompt}{'...' if len(task.get('prompt', '')) > 100 else ''}"
            )
            print()
    else:
        tasks = cast(list[EvalSpec], gptme_eval_tests)
        print("=== Standard Evaluation Tasks ===")
        print(f"Total tasks: {len(tasks)}\n")

        for task in tasks:
            name = task.get("name", "unknown")
            prompt = task.get("prompt", "")[:100]
            tools = task.get("tools", [])

            print(f"• {name}")
            print(f"  Tools: {', '.join(tools) if tools else 'none'}")
            print(
                f"  Task: {prompt}{'...' if len(task.get('prompt', '')) > 100 else ''}"
            )
            print()


@cli.command("optimize-gepa")
@click.option("--name", default="gepa_optimization", help="Experiment name")
@click.option("--model", default=DEFAULT_MODEL, help="Model to use")
@click.option("--output-dir", help="Output directory for results")
@click.option("--train-size", type=int, default=15, help="Number of training examples")
@click.option("--val-size", type=int, default=10, help="Number of validation examples")
@click.option(
    "--baseline-examples",
    type=int,
    default=20,
    help="Number of examples for baseline evaluation",
)
@click.option(
    "--comparison-examples",
    type=int,
    default=15,
    help="Number of examples for final comparison",
)
# GEPA budget configuration (mutually exclusive)
@click.option(
    "--auto",
    type=click.Choice(["light", "medium", "heavy"]),
    help="Preset budget configuration",
)
@click.option("--max-full-evals", type=int, help="Maximum number of full evaluations")
@click.option("--max-metric-calls", type=int, help="Maximum number of metric calls")
# GEPA-specific options
@click.option(
    "--reflection-minibatch-size",
    type=int,
    default=3,
    help="Size of reflection minibatches",
)
@click.option("--num-threads", type=int, default=4, help="Number of parallel threads")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Skip actual optimization calls, use fake results for testing",
)
def optimize_gepa(
    name: str,
    model: str,
    output_dir: str | None,
    train_size: int,
    val_size: int,
    baseline_examples: int,
    comparison_examples: int,
    auto: str | None,
    max_full_evals: int | None,
    max_metric_calls: int | None,
    reflection_minibatch_size: int,
    num_threads: int,
    dry_run: bool,
) -> None:
    """Run GEPA prompt optimization with proper budget configuration.

    Budget Configuration: Exactly one of --auto, --max-full-evals, or --max-metric-calls must be provided.

    Examples:

    \b
      # Quick experimentation
      gptme-dspy optimize-gepa --auto light

    \b
      # Custom budget with explicit eval limit
      gptme-dspy optimize-gepa --max-full-evals 10

    \b
      # Super small test run
      gptme-dspy optimize-gepa --auto light --train-size 3 --val-size 2
    """
    # Validate budget configuration
    budget_options = [auto, max_full_evals, max_metric_calls]
    provided_options = [opt for opt in budget_options if opt is not None]

    if len(provided_options) != 1:
        print(
            "❌ Error: Exactly one of --auto, --max-full-evals, or --max-metric-calls must be provided"
        )
        print("Examples:")
        print("  gptme-dspy optimize-gepa --auto light")
        print("  gptme-dspy optimize-gepa --max-full-evals 10")
        sys.exit(1)

    print(f"Starting GEPA optimization experiment: {name}")
    print(f"Using model: {model}")
    print(
        f"Dataset sizes: train={train_size}, val={val_size}, baseline={baseline_examples}, comparison={comparison_examples}"
    )
    print(
        f"Budget: {auto or f'max_full_evals={max_full_evals}' or f'max_metric_calls={max_metric_calls}'}"
    )
    print(f"Output directory: {output_dir}")

    # Configure GEPA optimizer
    gepa_config = {
        "optimizer_type": "gepa",
        "reflection_minibatch_size": reflection_minibatch_size,
        "num_threads": num_threads,
    }

    # Add budget configuration
    if auto:
        gepa_config["auto"] = auto
    elif max_full_evals:
        gepa_config["max_full_evals"] = max_full_evals
    elif max_metric_calls:
        gepa_config["max_metric_calls"] = max_metric_calls

    optimizer_configs = {"gepa": gepa_config}

    # Run experiment
    try:
        experiment = run_prompt_optimization_experiment(
            experiment_name=name,
            model=model,
            optimizers=optimizer_configs,
            output_dir=Path(output_dir) if output_dir else Path("experiments"),
            train_size=train_size,
            val_size=val_size,
            baseline_examples=baseline_examples,
            comparison_examples=comparison_examples,
            dry_run=dry_run,
        )

        print("\n✅ GEPA experiment completed successfully!")
        print(f"📊 Results saved to: {experiment.output_dir}")
        print(f"📝 Report: {experiment.output_dir / f'{name}_report.md'}")

        # Print quick summary
        if "comparisons" in experiment.results:
            comparisons_data = experiment.results["comparisons"]
            assert isinstance(comparisons_data, dict)
            comparison = comparisons_data["results"]
            assert isinstance(comparison, dict)

            best_name = max(
                comparison.keys(), key=lambda k: comparison[k].get("average_score", 0)
            )
            best_score = comparison[best_name].get("average_score", 0)
            baseline_data = comparison.get("baseline", {})
            assert isinstance(baseline_data, dict)
            baseline_score = baseline_data.get("average_score", 0)

            print(f"\n📈 Best performing prompt: {best_name} (score: {best_score:.3f})")
            if baseline_score > 0:
                improvement = best_score - baseline_score
                print(f"📊 Improvement over baseline: {improvement:+.3f}")

    except Exception as e:
        print(f"❌ GEPA experiment failed: {e}")
        logger.exception("GEPA optimization experiment failed")
        sys.exit(1)


@cli.command("analyze-coverage")
def analyze_coverage() -> None:
    """Analyze task coverage by focus areas."""
    coverage = analyze_task_coverage()

    print("=== Task Coverage Analysis ===")
    print(f"Total focus areas: {len(coverage)}")
    print()

    for area, tasks in sorted(coverage.items()):
        print(f"📋 {area} ({len(tasks)} tasks)")
        for task in tasks:
            print(f"   • {task}")
        print()


def main():
    """Main CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
