"""
Command-line interface for gptme prompt optimization using DSPy.

This module provides CLI commands for running prompt optimization experiments.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, cast

from gptme.eval.suites import tests as gptme_eval_tests

from .experiments import quick_prompt_test, run_prompt_optimization_experiment
from .prompt_optimizer import get_current_gptme_prompt
from .tasks import analyze_task_coverage, get_prompt_optimization_tasks

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"


def cmd_optimize(args) -> None:
    """Run a full prompt optimization experiment."""
    print(f"Starting prompt optimization experiment: {args.name}")
    print(f"Using model: {args.model}")
    print(f"Output directory: {args.output_dir}")

    # Configure optimizers based on args
    optimizers = {}

    if args.optimizers is None or "miprov2" in args.optimizers:
        optimizers["miprov2"] = {
            "optimizer_type": "miprov2",
            "max_demos": args.max_demos,
            "num_trials": args.num_trials,
        }

    if args.optimizers is None or "bootstrap" in args.optimizers:
        optimizers["bootstrap"] = {
            "optimizer_type": "bootstrap",
            "max_demos": args.max_demos,
            "num_trials": max(args.num_trials // 2, 3),
        }

    # Run experiment
    try:
        experiment = run_prompt_optimization_experiment(
            experiment_name=args.name,
            model=args.model,
            optimizers=optimizers,
            output_dir=Path(args.output_dir)
            if args.output_dir
            else Path("experiments"),
        )

        print("\n✅ Experiment completed successfully!")
        print(f"📊 Results saved to: {experiment.output_dir}")
        print(f"📝 Report: {experiment.output_dir / f'{args.name}_report.md'}")

        # Print quick summary
        if "comparisons" in experiment.results:
            comparison = experiment.results["comparisons"]["results"]
            best_name = max(
                comparison.keys(), key=lambda k: comparison[k].get("average_score", 0)
            )
            best_score = comparison[best_name].get("average_score", 0)
            baseline_score = comparison.get("baseline", {}).get("average_score", 0)

            print(f"\n📈 Best performing prompt: {best_name} (score: {best_score:.3f})")
            if baseline_score > 0:
                improvement = best_score - baseline_score
                print(f"📊 Improvement over baseline: {improvement:+.3f}")

    except Exception as e:
        print(f"❌ Experiment failed: {e}")
        logger.exception("Optimization experiment failed")
        sys.exit(1)


def cmd_quick_test(args) -> None:
    """Run a quick test of prompt variations."""
    print(f"Running quick prompt test with {args.num_examples} examples")

    # Load prompt variations
    prompts = {}

    # Add current prompt as baseline
    current_prompt = get_current_gptme_prompt(model=args.model)
    prompts["current"] = current_prompt

    # Add prompt files if specified
    if args.prompt_files:
        for file_path in args.prompt_files:
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
            prompt_variations=prompts, num_examples=args.num_examples, model=args.model
        )

        print("\n✅ Quick test completed!")

    except Exception as e:
        print(f"❌ Quick test failed: {e}")
        logger.exception("Quick test failed")
        sys.exit(1)


def cmd_show_current_prompt(args) -> None:
    """Show the current gptme system prompt."""
    current_prompt = get_current_gptme_prompt(
        interactive=not args.non_interactive, model=args.model
    )

    print("=== Current gptme System Prompt ===")
    print(current_prompt)
    print(f"\nPrompt length: {len(current_prompt)} characters")
    print(f"Lines: {current_prompt.count(chr(10)) + 1}")


def cmd_list_tasks(args) -> None:
    """List available evaluation tasks."""
    if args.optimization_tasks:
        tasks = get_prompt_optimization_tasks()
        print("=== Prompt Optimization Tasks ===")
        print(f"Total tasks: {len(tasks)}\n")

        for task in tasks:
            name = task.get("name", "unknown")
            focus_areas = task.get("focus_areas", [])
            prompt = task.get("prompt", "")[:100]

            print(f"• {name}")
            print(f"  Focus: {', '.join(focus_areas)}")
            print(
                f"  Task: {prompt}{'...' if len(task.get('prompt', '')) > 100 else ''}"
            )
            print()
    else:
        tasks = cast(list[dict[str, Any]], gptme_eval_tests)
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


def cmd_analyze_coverage(args) -> None:
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
    parser = argparse.ArgumentParser(
        description="gptme prompt optimization using DSPy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  # Run full optimization experiment
  python -m gptme.eval.dspy.cli optimize --name "my_experiment" --model {DEFAULT_MODEL}

  # Quick test of prompt variations
  python -m gptme.eval.dspy.cli quick-test --prompt-files prompt1.txt prompt2.txt --num-examples 5

  # Show current system prompt
  python -m gptme.eval.dspy.cli show-prompt

  # List available tasks
  python -m gptme.eval.dspy.cli list-tasks --optimization-tasks
        """,
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Optimize command
    optimize_parser = subparsers.add_parser(
        "optimize", help="Run prompt optimization experiment"
    )
    optimize_parser.add_argument(
        "--name", default="prompt_optimization", help="Experiment name"
    )
    optimize_parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to use")
    optimize_parser.add_argument("--output-dir", help="Output directory for results")
    optimize_parser.add_argument(
        "--max-demos", type=int, default=3, help="Maximum number of demo examples"
    )
    optimize_parser.add_argument(
        "--num-trials", type=int, default=10, help="Number of optimization trials"
    )
    optimize_parser.add_argument(
        "--optimizers",
        nargs="+",
        choices=["miprov2", "bootstrap"],
        help="Optimizers to use (default: both)",
    )
    optimize_parser.set_defaults(func=cmd_optimize)

    # Quick test command
    quick_parser = subparsers.add_parser(
        "quick-test", help="Quick test of prompt variations"
    )
    quick_parser.add_argument(
        "--prompt-files", nargs="+", help="Prompt files to compare"
    )
    quick_parser.add_argument(
        "--num-examples", type=int, default=5, help="Number of examples to test"
    )
    quick_parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to use")
    quick_parser.set_defaults(func=cmd_quick_test)

    # Show current prompt
    prompt_parser = subparsers.add_parser(
        "show-prompt", help="Show current system prompt"
    )
    prompt_parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to use")
    prompt_parser.add_argument(
        "--non-interactive", action="store_true", help="Show non-interactive prompt"
    )
    prompt_parser.set_defaults(func=cmd_show_current_prompt)

    # List tasks
    tasks_parser = subparsers.add_parser(
        "list-tasks", help="List available evaluation tasks"
    )
    tasks_parser.add_argument(
        "--optimization-tasks",
        action="store_true",
        help="Show prompt optimization tasks instead of standard eval tasks",
    )
    tasks_parser.set_defaults(func=cmd_list_tasks)

    # Analyze coverage
    coverage_parser = subparsers.add_parser(
        "analyze-coverage", help="Analyze task coverage by focus areas"
    )
    coverage_parser.set_defaults(func=cmd_analyze_coverage)

    # Parse arguments
    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Run command
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
