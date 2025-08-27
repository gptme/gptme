"""
High-level experiments for gptme prompt optimization.

This module provides pre-configured experiments and analysis tools
for systematically optimizing gptme's system prompts.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from gptme.eval.suites import tests as gptme_eval_tests
from gptme.eval.types import EvalSpec

from .prompt_optimizer import PromptOptimizer, get_current_gptme_prompt

logger = logging.getLogger(__name__)


class OptimizationExperiment:
    """
    A systematic prompt optimization experiment.

    Manages the full lifecycle of prompt optimization including:
    - Baseline evaluation
    - Optimization runs
    - Results comparison
    - Report generation
    """

    def __init__(
        self,
        name: str,
        output_dir: Path,
        model: str,
    ):
        self.name = name
        self.model = model
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)

        self.results: dict[str, Any] = {
            "experiment_name": name,
            "model": model,
            "timestamp": datetime.now().isoformat(),
            "baseline": {},
            "optimizations": {},
            "comparisons": {},
        }

    def run_baseline_evaluation(
        self, eval_specs: list[EvalSpec] | None = None, num_examples: int = 20
    ) -> dict[str, Any]:
        """Run baseline evaluation with current prompt."""
        logger.info("Running baseline evaluation...")

        if eval_specs is None:
            eval_specs = gptme_eval_tests

        current_prompt = get_current_gptme_prompt(interactive=False, model=self.model)

        # TODO: Actually run evaluation with gptme's eval framework
        # This would integrate with gptme.eval.run to execute tasks

        # Placeholder baseline results
        baseline_results = {
            "prompt": current_prompt,
            "average_score": 0.65,  # Placeholder
            "task_success_rate": 0.70,  # Placeholder
            "tool_usage_score": 0.60,  # Placeholder
            "num_examples": num_examples,
            "timestamp": datetime.now().isoformat(),
        }

        self.results["baseline"] = baseline_results

        logger.info(f"Baseline average score: {baseline_results['average_score']:.3f}")
        return baseline_results

    def run_optimization(
        self,
        optimizer_name: str,
        optimizer_config: dict[str, Any],
        eval_specs: list[EvalSpec] | None = None,
        train_size: int = 15,
        val_size: int = 10,
    ) -> dict[str, Any]:
        """Run a single optimization experiment."""
        logger.info(f"Running optimization: {optimizer_name}")

        if eval_specs is None:
            eval_specs = gptme_eval_tests

        # Get baseline prompt
        base_prompt = get_current_gptme_prompt(interactive=False, model=self.model)

        # Initialize optimizer
        optimizer = PromptOptimizer(model=self.model, **optimizer_config)

        # Run optimization
        optimized_prompt, optimization_results = optimizer.optimize_prompt(
            base_prompt=base_prompt,
            eval_specs=eval_specs,
            train_size=train_size,
            val_size=val_size,
        )

        # Store results
        optimization_data = {
            "optimizer_name": optimizer_name,
            "optimizer_config": optimizer_config,
            "base_prompt": base_prompt,
            "optimized_prompt": optimized_prompt,
            "results": optimization_results,
            "timestamp": datetime.now().isoformat(),
        }

        self.results["optimizations"][optimizer_name] = optimization_data

        # Save optimized prompt
        prompt_file = self.output_dir / f"{optimizer_name}_prompt.txt"
        with open(prompt_file, "w") as f:
            f.write(optimized_prompt)

        logger.info(
            f"Optimization {optimizer_name} completed. "
            f"Score: {optimization_results.get('average_score', 0):.3f}"
        )

        return optimization_data

    def compare_all_optimizations(
        self, eval_specs: list[EvalSpec] | None = None, num_examples: int = 15
    ) -> dict[str, Any]:
        """Compare all optimized prompts against baseline and each other."""
        logger.info("Running comprehensive comparison...")

        if eval_specs is None:
            eval_specs = gptme_eval_tests

        # Collect all prompts to compare
        prompts = {}

        # Add baseline
        if "baseline" in self.results:
            prompts["baseline"] = self.results["baseline"]["prompt"]

        # Add optimized prompts
        for opt_name, opt_data in self.results["optimizations"].items():
            prompts[opt_name] = opt_data["optimized_prompt"]

        # Compare prompts
        optimizer = PromptOptimizer(model=self.model)
        comparison_results = optimizer.compare_prompts(
            prompts=prompts, eval_specs=eval_specs, num_examples=num_examples
        )

        self.results["comparisons"] = {
            "results": comparison_results,
            "timestamp": datetime.now().isoformat(),
            "num_examples": num_examples,
        }

        return comparison_results

    def generate_report(self) -> str:
        """Generate a comprehensive optimization report."""
        report = []
        report.append(f"# Prompt Optimization Report: {self.name}")
        report.append(f"**Model:** {self.model}")
        report.append(f"**Timestamp:** {self.results['timestamp']}")
        report.append("")

        # Baseline results
        if "baseline" in self.results:
            baseline = self.results["baseline"]
            report.append("## Baseline Performance")
            avg_score = baseline.get("average_score", "N/A")
            if isinstance(avg_score, int | float):
                report.append(f"- Average Score: {avg_score:.3f}")
            else:
                report.append(f"- Average Score: {avg_score}")
            success_rate = baseline.get("task_success_rate", "N/A")
            if isinstance(success_rate, int | float):
                report.append(f"- Task Success Rate: {success_rate:.3f}")
            else:
                report.append(f"- Task Success Rate: {success_rate}")
            tool_score = baseline.get("tool_usage_score", "N/A")
            if isinstance(tool_score, int | float):
                report.append(f"- Tool Usage Score: {tool_score:.3f}")
            else:
                report.append(f"- Tool Usage Score: {tool_score}")
            report.append("")

        # Optimization results
        if "optimizations" in self.results:
            report.append("## Optimization Results")
            for opt_name, opt_data in self.results["optimizations"].items():
                results = opt_data.get("results", {})
                report.append(f"### {opt_name}")
                report.append(f"- Average Score: {results.get('average_score', 'N/A')}")
                report.append(
                    f"- Optimizer Config: {opt_data.get('optimizer_config', {})}"
                )
                report.append("")

        # Comparison results
        if "comparisons" in self.results:
            report.append("## Final Comparison")
            comparisons_data = self.results["comparisons"]
            comparison = comparisons_data.get("results", {})

            # Sort by average score
            sorted_results = sorted(
                comparison.items(),
                key=lambda x: x[1].get("average_score", 0),
                reverse=True,
            )

            report.append("| Prompt | Average Score | Examples |")
            report.append("|--------|---------------|----------|")
            for name, result in sorted_results:
                score = result.get("average_score", 0)
                num_ex = result.get("num_examples", 0)
                report.append(f"| {name} | {score:.3f} | {num_ex} |")
            report.append("")

        # Best performing prompt
        if "comparisons" in self.results:
            comparisons_data = self.results["comparisons"]
            comparison = comparisons_data.get("results", {})
            if comparison:  # Only proceed if we have comparison results
                best_name = max(
                    comparison.keys(),
                    key=lambda k: comparison[k].get("average_score", 0),
                )
                best_score = comparison[best_name].get("average_score", 0)

                report.append("## Recommendations")
                report.append(
                    f"**Best performing prompt:** {best_name} (score: {best_score:.3f})"
                )

                if best_name != "baseline":
                    improvement = best_score - comparison.get("baseline", {}).get(
                        "average_score", 0
                    )
                    report.append(f"**Improvement over baseline:** +{improvement:.3f}")
                report.append("")

        report_text = "\n".join(report)

        # Ensure output directory exists and save report
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_file = self.output_dir / f"{self.name}_report.md"
        with open(report_file, "w") as f:
            f.write(report_text)

        return report_text

    def save_results(self) -> Path:
        """Save complete experiment results to JSON."""
        results_file = self.output_dir / f"{self.name}_results.json"
        with open(results_file, "w") as f:
            json.dump(self.results, f, indent=2)
        return results_file


default_optimizers = {
    "miprov2_light": {
        "optimizer_type": "miprov2",
        "max_demos": 2,
        "num_trials": 5,
    },
    "miprov2_medium": {
        "optimizer_type": "miprov2",
        "max_demos": 3,
        "num_trials": 10,
    },
    "bootstrap": {
        "optimizer_type": "bootstrap",
        "max_demos": 3,
        "num_trials": 8,
    },
}


def run_prompt_optimization_experiment(
    experiment_name: str,
    model: str,
    optimizers: dict[str, dict[str, Any]],
    output_dir: Path,
) -> OptimizationExperiment:
    """
    Run a complete prompt optimization experiment.

    Args:
        experiment_name: Name for the experiment
        model: Model to use for optimization
        optimizers: Dictionary of optimizer configurations
        output_dir: Directory to save results

    Returns:
        OptimizationExperiment instance with results
    """
    experiment = OptimizationExperiment(
        name=experiment_name, output_dir=output_dir, model=model
    )
    optimizers = optimizers or default_optimizers

    # Run baseline evaluation
    experiment.run_baseline_evaluation()

    # Run each optimization
    for opt_name, opt_config in optimizers.items():
        try:
            experiment.run_optimization(opt_name, opt_config)
        except Exception as e:
            logger.error(f"Failed to run optimization {opt_name}: {e}")

    # Compare all results
    experiment.compare_all_optimizations()

    # Generate and save report
    report = experiment.generate_report()
    results_file = experiment.save_results()

    logger.info(f"Experiment completed. Results saved to {results_file}")
    logger.info(f"Report:\n{report}")

    return experiment


def quick_prompt_test(
    prompt_variations: dict[str, str],
    num_examples: int,
    model: str,
) -> dict[str, Any]:
    """
    Quick test of prompt variations without full optimization.

    Args:
        prompt_variations: Dictionary of prompt names to prompt text
        num_examples: Number of examples to test with
        model: Model to use for testing

    Returns:
        Comparison results
    """
    logger.info(f"Running quick prompt test with {len(prompt_variations)} variations")

    optimizer = PromptOptimizer(model=model)

    # Get limited evaluation specs
    eval_specs = gptme_eval_tests[:num_examples]

    results = optimizer.compare_prompts(
        prompts=prompt_variations, eval_specs=eval_specs, num_examples=num_examples
    )

    # Print quick summary
    print("\n=== Quick Prompt Test Results ===")
    for name, result in sorted(
        results.items(), key=lambda x: x[1].get("average_score", 0), reverse=True
    ):
        score = result.get("average_score", 0)
        print(f"{name:20} | Score: {score:.3f}")

    return results


if __name__ == "__main__":
    # Example usage
    experiment = run_prompt_optimization_experiment(
        experiment_name="gptme_prompt_optimization_v1",
        model="anthropic/claude-sonnet-4-20250514",
        optimizers={},
        output_dir=Path("./experiments/gptme_prompt_optimization_v1"),
    )
