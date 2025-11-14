"""
High-level experiments for gptme prompt optimization.

Refactored to use composition and single responsibility principle.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from gptme.eval.suites import tests as gptme_eval_tests
from gptme.eval.types import EvalSpec

from .prompt_optimizer import PromptOptimizer, get_current_gptme_prompt
from .tasks import get_prompt_optimization_tasks

logger = logging.getLogger(__name__)


class ExperimentReporter:
    """Handles report generation for optimization experiments."""

    def __init__(self, experiment_data: dict[str, Any]):
        self.data = experiment_data

    def generate_report(self) -> str:
        """Generate a comprehensive optimization report."""
        sections = [
            self._header_section(),
            self._baseline_section(),
            self._optimizations_section(),
            self._comparison_section(),
            self._recommendations_section(),
        ]
        return "\n\n".join(filter(None, sections))

    def _header_section(self) -> str:
        return f"""# Prompt Optimization Report: {self.data['experiment_name']}
**Model:** {self.data['model']}
**Timestamp:** {self.data['timestamp']}"""

    def _baseline_section(self) -> str:
        if "baseline" not in self.data:
            return ""

        baseline = self.data["baseline"]
        return f"""## Baseline Performance
- Average Score: {baseline.get('average_score', 'N/A')}
- Task Success Rate: {baseline.get('task_success_rate', 'N/A')}
- Tool Usage Score: {baseline.get('tool_usage_score', 'N/A')}"""

    def _optimizations_section(self) -> str:
        if "optimizations" not in self.data:
            return ""

        lines = ["## Optimization Results"]
        for name, opt_data in self.data["optimizations"].items():
            results = opt_data.get("results", {})
            lines.extend(
                [
                    f"### {name}",
                    f"- Average Score: {results.get('average_score', 'N/A')}",
                    f"- Config: {opt_data.get('optimizer_config', {})}",
                    "",
                ]
            )
        return "\n".join(lines)

    def _comparison_section(self) -> str:
        if "comparisons" not in self.data:
            return ""

        comparison = self.data["comparisons"].get("results", {})
        if not comparison:
            return ""

        sorted_results = sorted(
            comparison.items(),
            key=lambda x: x[1].get("average_score", 0),
            reverse=True,
        )

        lines = [
            "## Final Comparison",
            "| Prompt | Average Score | Examples |",
            "|--------|---------------|----------|",
        ]

        for name, result in sorted_results:
            score = result.get("average_score", 0)
            num_ex = result.get("num_examples", 0)
            lines.append(f"| {name} | {score:.3f} | {num_ex} |")

        return "\n".join(lines)

    def _recommendations_section(self) -> str:
        if "comparisons" not in self.data:
            return ""

        comparison = self.data["comparisons"].get("results", {})
        if not comparison:
            return ""

        best_name = max(
            comparison.keys(), key=lambda k: comparison[k].get("average_score", 0)
        )
        best_score = comparison[best_name].get("average_score", 0)

        lines = [
            "## Recommendations",
            f"**Best performing prompt:** {best_name} (score: {best_score:.3f})",
        ]

        if best_name != "baseline":
            baseline_score = comparison.get("baseline", {}).get("average_score", 0)
            improvement = best_score - baseline_score
            lines.append(f"**Improvement over baseline:** +{improvement:.3f}")

        return "\n".join(lines)


class ExperimentRunner:
    """Handles the execution of optimization experiments."""

    def __init__(self, model: str, dry_run: bool = False):
        self.model = model
        self.dry_run = dry_run

    def _generate_fake_results(self, num_examples: int = 15) -> dict[str, Any]:
        """Generate fake results for dry run mode."""
        import random

        random.seed(42)  # Consistent fake results

        return {
            "average_score": round(random.uniform(0.65, 0.85), 3),
            "task_success_rate": round(random.uniform(0.6, 0.9), 3),
            "tool_usage_score": round(random.uniform(0.7, 0.95), 3),
            "judge_score": round(random.uniform(0.6, 0.8), 3),
            "num_examples": num_examples,
        }

    def _generate_fake_comparison(
        self, prompts: dict[str, str], num_examples: int
    ) -> dict[str, Any]:
        """Generate fake comparison results for dry run mode."""
        import random

        random.seed(42)  # Consistent fake results

        results = {}
        for name in prompts.keys():
            # Slightly vary scores to simulate realistic differences
            base_score = 0.75 + hash(name) % 100 / 1000  # Deterministic but varied
            results[name] = {
                "average_score": round(base_score, 3),
                "task_success_rate": round(base_score - 0.05, 3),
                "tool_usage_score": round(base_score + 0.05, 3),
                "judge_score": round(base_score - 0.02, 3),
                "num_examples": num_examples,
            }
        return results

    def run_baseline(
        self, eval_specs: list[EvalSpec], num_examples: int = 20
    ) -> dict[str, Any]:
        """Run baseline evaluation with current prompt."""
        logger.info("Running baseline evaluation...")
        current_prompt = get_current_gptme_prompt(interactive=False, model=self.model)

        if self.dry_run:
            logger.info("🧪 DRY RUN: Using fake baseline results")
            baseline_results = self._generate_fake_results(num_examples)
        else:
            # Use existing evaluation infrastructure with rich results
            from gptme.eval.dspy.prompt_optimizer import PromptDataset, PromptOptimizer

            # Reuse the existing prompt evaluation system
            optimizer = PromptOptimizer(model=self.model)
            limited_specs = eval_specs[:num_examples] if eval_specs else []

            if not limited_specs:
                from gptme.eval.suites.basic import tests

                limited_specs = tests[:num_examples]

            val_data = PromptDataset(limited_specs)

            # _evaluate_prompt now returns rich breakdown results
            baseline_results = optimizer._evaluate_prompt(current_prompt, val_data)

        return {
            "prompt": current_prompt,
            "average_score": baseline_results["average_score"],
            "task_success_rate": baseline_results["task_success_rate"],
            "tool_usage_score": baseline_results["tool_usage_score"],
            "judge_score": baseline_results["judge_score"],
            "num_examples": baseline_results["num_examples"],
            "timestamp": datetime.now().isoformat(),
            "detailed_results": baseline_results,
        }

    def run_optimization(
        self,
        optimizer_config: dict[str, Any],
        base_prompt: str,
        eval_specs: list[EvalSpec],
        train_size: int = 15,
        val_size: int = 10,
    ) -> tuple[str, dict[str, Any]]:
        """Run a single optimization."""
        if self.dry_run:
            logger.info("🧪 DRY RUN: Using fake optimization results")
            optimizer_type = optimizer_config.get("optimizer_type", "unknown")
            fake_optimized_prompt = (
                f"{base_prompt}\n\n# [DRY RUN] Fake optimization by {optimizer_type}"
            )
            fake_results = self._generate_fake_results()
            # Slightly boost scores to simulate optimization improvement
            for key in [
                "average_score",
                "task_success_rate",
                "tool_usage_score",
                "judge_score",
            ]:
                if key in fake_results:
                    fake_results[key] = min(0.95, fake_results[key] + 0.05)
            return fake_optimized_prompt, fake_results
        else:
            optimizer = PromptOptimizer(model=self.model, **optimizer_config)
            return optimizer.optimize_prompt(
                base_prompt=base_prompt,
                eval_specs=eval_specs,
                train_size=train_size,
                val_size=val_size,
            )

    def compare_prompts(
        self,
        prompts: dict[str, str],
        eval_specs: list[EvalSpec],
        num_examples: int = 15,
    ) -> dict[str, Any]:
        """Compare multiple prompts."""
        if self.dry_run:
            logger.info("🧪 DRY RUN: Using fake comparison results")
            return self._generate_fake_comparison(prompts, num_examples)
        else:
            optimizer = PromptOptimizer(model=self.model)
            return optimizer.compare_prompts(
                prompts=prompts, eval_specs=eval_specs, num_examples=num_examples
            )


class OptimizationExperiment:
    """Simplified experiment orchestrator using composition."""

    def __init__(self, name: str, output_dir: Path, model: str, dry_run: bool = False):
        self.name = name
        self.model = model
        self.output_dir = output_dir.resolve()  # Convert to absolute path
        self.dry_run = dry_run
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.runner = ExperimentRunner(model, dry_run=dry_run)
        self.results: dict[str, Any] = {
            "experiment_name": name,
            "model": model,
            "timestamp": datetime.now().isoformat(),
        }

    def run_baseline_evaluation(
        self, eval_specs: list[EvalSpec] | None = None, num_examples: int = 20
    ) -> dict[str, Any]:
        """Run baseline evaluation."""
        if eval_specs is None:
            eval_specs = get_prompt_optimization_tasks()

        baseline = self.runner.run_baseline(eval_specs, num_examples)
        self.results["baseline"] = baseline
        logger.info(f"Baseline average score: {baseline['average_score']:.3f}")
        return baseline

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
            eval_specs = get_prompt_optimization_tasks()

        base_prompt = get_current_gptme_prompt(interactive=False, model=self.model)

        optimized_prompt, optimization_results = self.runner.run_optimization(
            optimizer_config, base_prompt, eval_specs, train_size, val_size
        )

        optimization_data = {
            "optimizer_name": optimizer_name,
            "optimizer_config": optimizer_config,
            "base_prompt": base_prompt,
            "optimized_prompt": optimized_prompt,
            "results": optimization_results,
            "timestamp": datetime.now().isoformat(),
        }

        if "optimizations" not in self.results:
            self.results["optimizations"] = {}
        optimizations = self.results["optimizations"]
        assert isinstance(optimizations, dict)
        optimizations[optimizer_name] = optimization_data

        # Save optimized prompt
        prompt_file = self.output_dir / f"{optimizer_name}_prompt.txt"
        prompt_file.write_text(optimized_prompt)

        logger.info(
            f"Optimization {optimizer_name} completed. Score: {optimization_results.get('average_score', 0):.3f}"
        )
        return optimization_data

    def compare_all_optimizations(
        self, eval_specs: list[EvalSpec] | None = None, num_examples: int = 15
    ) -> dict[str, Any]:
        """Compare all optimized prompts."""
        logger.info("Running comprehensive comparison...")

        if eval_specs is None:
            eval_specs = get_prompt_optimization_tasks()

        # Collect prompts
        prompts = {}
        if "baseline" in self.results:
            baseline = self.results["baseline"]
            assert isinstance(baseline, dict)
            prompts["baseline"] = baseline["prompt"]

        for opt_name, opt_data in self.results.get("optimizations", {}).items():
            assert isinstance(opt_data, dict)
            prompts[opt_name] = opt_data["optimized_prompt"]

        comparison_results = self.runner.compare_prompts(
            prompts, eval_specs, num_examples
        )

        self.results["comparisons"] = {
            "results": comparison_results,
            "timestamp": datetime.now().isoformat(),
            "num_examples": num_examples,
        }

        return comparison_results

    def generate_report(self) -> str:
        """Generate experiment report."""
        reporter = ExperimentReporter(self.results)
        report = reporter.generate_report()

        report_file = self.output_dir / f"{self.name}_report.md"
        report_file.write_text(report)

        return report

    def save_results(self) -> Path:
        """Save experiment results."""
        results_file = self.output_dir / f"{self.name}_results.json"
        results_file.write_text(json.dumps(self.results, indent=2))
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
    train_size: int = 15,
    val_size: int = 10,
    baseline_examples: int = 20,
    comparison_examples: int = 15,
    dry_run: bool = False,
) -> OptimizationExperiment:
    """
    Run a complete prompt optimization experiment.

    Args:
        experiment_name: Name for the experiment
        model: Model to use for optimization
        optimizers: Dictionary of optimizer configurations
        output_dir: Directory to save results
        train_size: Number of training examples for optimization
        val_size: Number of validation examples for optimization
        baseline_examples: Number of examples for baseline evaluation
        comparison_examples: Number of examples for final comparison

    Returns:
        OptimizationExperiment instance with results
    """
    experiment = OptimizationExperiment(
        name=experiment_name, output_dir=output_dir, model=model, dry_run=dry_run
    )
    optimizers = optimizers or default_optimizers

    # Run baseline evaluation
    experiment.run_baseline_evaluation(num_examples=baseline_examples)

    # Run each optimization
    for opt_name, opt_config in optimizers.items():
        try:
            experiment.run_optimization(
                opt_name, opt_config, train_size=train_size, val_size=val_size
            )
        except Exception as e:
            logger.error(f"Failed to run optimization {opt_name}: {e}")

    # Compare all results
    experiment.compare_all_optimizations(num_examples=comparison_examples)

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

    # Print detailed breakdown
    print("\n=== Quick Prompt Test Results ===")
    for name, result in sorted(
        results.items(), key=lambda x: x[1].get("average_score", 0), reverse=True
    ):
        composite = result.get("average_score", 0)
        task = result.get("task_success_rate", 0)
        tool = result.get("tool_usage_score", 0)
        judge = result.get("judge_score", 0)

        print(
            f"{name:15} | Composite: {composite:.3f} | Task: {task:.3f} | Tool: {tool:.3f} | Judge: {judge:.3f}"
        )

    return results


if __name__ == "__main__":
    # Example usage
    experiment = run_prompt_optimization_experiment(
        experiment_name="gptme_prompt_optimization_v1",
        model="anthropic/claude-sonnet-4-20250514",
        optimizers={},
        output_dir=Path("./experiments/gptme_prompt_optimization_v1"),
    )
