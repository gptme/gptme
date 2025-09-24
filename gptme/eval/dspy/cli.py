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


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-20241022"


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """gptme prompt optimization using DSPy.

    Examples:

    \b
      # Run full optimization experiment
      gptme-eval dspy optimize --name "my_experiment" --model anthropic/claude-3-5-haiku-20241022

    \b
      # Quick test of prompt variations
      gptme-eval dspy quick-test --prompt-files prompt1.txt prompt2.txt --num-examples 5

    \b
      # Show current system prompt
      gptme-eval dspy show-prompt

    \b
      # List available tasks
      gptme-eval dspy list-tasks --optimization-tasks
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
@click.option(
    "--optimizers",
    multiple=True,
    type=click.Choice(["miprov2", "bootstrap", "gepa"]),
    help="Optimizers to use (default: miprov2, bootstrap)",
)
def optimize(
    name: str,
    model: str,
    output_dir: str | None,
    max_demos: int,
    num_trials: int,
    optimizers: tuple[str, ...],
) -> None:
    """Run a full prompt optimization experiment."""
    print(f"Starting prompt optimization experiment: {name}")
    print(f"Using model: {model}")
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

    if "gepa" in optimizers:
        optimizer_configs["gepa"] = {
            "optimizer_type": "gepa",
            "max_demos": max_demos,
            "num_trials": num_trials,  # GEPA is sample efficient, can use full trials
        }

    # Run experiment
    try:
        experiment = run_prompt_optimization_experiment(
            experiment_name=name,
            model=model,
            optimizers=optimizer_configs,
            output_dir=Path(output_dir) if output_dir else Path("experiments"),
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


from .tasks import (
    analyze_task_coverage,
    get_prompt_optimization_tasks,
    get_task_metadata,
)


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
