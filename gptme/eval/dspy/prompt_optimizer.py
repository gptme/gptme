"""
Core prompt optimization functionality using DSPy.

This module provides the main PromptOptimizer class that uses DSPy's
optimization techniques to automatically improve gptme system prompts.
"""

import logging
import os
from typing import Any

from gptme.eval.agents import GPTMe
from gptme.eval.run import execute
from gptme.eval.suites.basic import tests
from gptme.eval.types import EvalSpec
from gptme.prompts import prompt_gptme

import dspy
from dspy import GEPA
from dspy.teleprompt import BootstrapFewShot, MIPROv2

from .metrics import (
    create_composite_metric,
    create_trajectory_feedback_metric,
)
from .signatures import GptmeTaskSignature, PromptImprovementSignature

logger = logging.getLogger(__name__)


class ModelNameMapper:
    """Handles mapping between gptme and DSPy model names."""

    @staticmethod
    def to_dspy_format(gptme_model: str) -> str:
        """Convert gptme model name to DSPy/litellm format."""
        if gptme_model.startswith("anthropic/"):
            return gptme_model.replace("anthropic/", "")
        elif gptme_model.startswith("openai/"):
            return gptme_model.replace("openai/", "")
        else:
            return gptme_model

    @staticmethod
    def get_reflection_model(base_model: str) -> str:
        """Get a more powerful model for reflection tasks."""
        if base_model.startswith("anthropic/"):
            if "haiku" in base_model.lower():
                return "claude-3-5-sonnet-20241022"
            else:
                return "claude-3-5-sonnet-20241022"
        elif base_model.startswith("openai/"):
            if "gpt-3.5" in base_model.lower() or "gpt-4o-mini" in base_model.lower():
                return "gpt-4o"
            else:
                return "gpt-4o"
        else:
            return ModelNameMapper.to_dspy_format(base_model)


class PromptDataset:
    """Dataset wrapper for gptme evaluation tasks."""

    def __init__(self, eval_specs: list[EvalSpec], limit: int | None = None):
        if not eval_specs:
            eval_specs = tests
        self.eval_specs = eval_specs[:limit] if limit else eval_specs

    def __len__(self) -> int:
        return len(self.eval_specs)

    def __iter__(self):
        for spec in self.eval_specs:
            yield self._spec_to_example(spec)

    def _spec_to_example(self, spec: EvalSpec) -> dspy.Example:
        """Convert an eval spec to a DSPy example, preserving the original spec."""
        return dspy.Example(
            task_description=spec.get("prompt", ""),
            context=self._build_context(spec),
            eval_spec=spec,  # Keep original spec for actual evaluation
            name=spec.get("name", "unknown"),
        ).with_inputs("task_description", "context", "eval_spec")

    def _build_context(self, spec: EvalSpec) -> str:
        """Build context string from eval spec."""
        context_parts = []

        files = spec.get("files", {})
        if files:
            context_parts.append("Files in workspace:")
            for filename, content in files.items():
                content_str = (
                    content.decode() if isinstance(content, bytes) else content
                )
                context_parts.append(f"```{filename}\n{content_str}\n```")

        if run_cmd := spec.get("run"):
            context_parts.append(f"Expected to run: {run_cmd}")

        return "\n\n".join(context_parts)


class GptmeModule(dspy.Module):
    """DSPy module for gptme task execution with GEPA optimization."""

    def __init__(
        self,
        base_system_prompt: str,
        model: str = "anthropic/claude-haiku-4-5",
    ):
        super().__init__()
        self.base_system_prompt = base_system_prompt
        self.model = model
        self.task_executor = dspy.ChainOfThought(GptmeTaskSignature)

    def forward(
        self, task_description: str, context: str, eval_spec: EvalSpec
    ) -> dspy.Prediction:
        """Execute a task using DSPy predictor + actual gptme evaluation."""
        try:
            # 1. DSPy predictor for optimization
            predictor_response = self.task_executor(
                system_prompt=self.base_system_prompt,
                task_description=task_description,
                context=context,
            )

            # 2. Run actual gptme evaluation using original EvalSpec
            agent = GPTMe(
                model=self.model,
                tool_format="markdown",
                system_prompt=self.base_system_prompt,
            )

            eval_result = execute(
                test=eval_spec,
                agent=agent,
                timeout=30,
                parallel=False,
            )

            # Extract messages from the agent's log if available
            messages = []
            if hasattr(agent, "log_dir") and agent.log_dir:
                try:
                    from gptme.logmanager import LogManager

                    log_manager = LogManager.load(agent.log_dir, lock=False)
                    messages = log_manager.log.messages
                except Exception:
                    # Fallback to empty messages if log reading fails
                    messages = []

            return dspy.Prediction(
                response=getattr(
                    predictor_response, "response", str(predictor_response)
                ),
                eval_result=eval_result,
                system_prompt=self.base_system_prompt,
                messages=messages,  # Add messages for metrics
                eval_spec=eval_spec,  # Include original spec for metrics
            )

        except Exception as e:
            logger.error(f"Error in GptmeModule forward: {e}")
            return dspy.Prediction(response=f"Error: {str(e)}", messages=[])


class PromptOptimizer:
    """Main class for optimizing gptme system prompts using DSPy."""

    def __init__(
        self,
        model: str,
        optimizer_type: str = "miprov2",
        max_demos: int = 3,
        num_trials: int = 10,
        # GEPA-specific parameters
        auto: str | None = None,
        max_full_evals: int | None = None,
        max_metric_calls: int | None = None,
        reflection_minibatch_size: int = 3,
        num_threads: int = 4,
    ):
        self.model = model
        self.optimizer_type = optimizer_type
        self.max_demos = max_demos
        self.num_trials = num_trials
        # GEPA-specific
        self.auto = auto
        self.max_full_evals = max_full_evals
        self.max_metric_calls = max_metric_calls
        self.reflection_minibatch_size = reflection_minibatch_size
        self.num_threads = num_threads
        self._setup_dspy()

    def _setup_dspy(self):
        """Initialize DSPy with the specified model."""
        dspy_model = ModelNameMapper.to_dspy_format(self.model)

        # Reduce DSPy logging noise
        os.environ["DSPY_LOGGING_LEVEL"] = "ERROR"

        lm = dspy.LM(dspy_model)
        dspy.configure(lm=lm)
        logger.debug(f"Configured DSPy with model: {dspy_model}")

    def optimize_prompt(
        self,
        base_prompt: str,
        eval_specs: list[EvalSpec] | None = None,
        train_size: int = 10,
        val_size: int = 5,
    ) -> tuple[str, dict[str, Any]]:
        """Optimize a system prompt using DSPy techniques."""
        logger.info("Starting prompt optimization...")

        if eval_specs is None:
            eval_specs = tests[: train_size + val_size]

        # Create datasets
        train_data = PromptDataset(eval_specs[:train_size])
        val_data = PromptDataset(eval_specs[train_size : train_size + val_size])

        # Create module and optimizer
        module = GptmeModule(base_prompt, self.model)
        optimizer = self._create_optimizer(eval_specs)

        try:
            logger.info(f"Running {self.optimizer_type} optimization...")
            optimized_module = optimizer.compile(module, trainset=list(train_data))

            # Extract optimized prompt
            optimized_prompt = getattr(
                optimized_module, "base_system_prompt", base_prompt
            )

            # Evaluate results (now uses individual metrics internally)
            results = self._evaluate_prompt(optimized_prompt, val_data)

            logger.info("Prompt optimization completed successfully")
            return optimized_prompt, results

        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            return base_prompt, {"error": str(e)}

    def _create_optimizer(self, eval_specs: list[EvalSpec]):
        """Create the appropriate DSPy optimizer."""
        if self.optimizer_type.lower() == "miprov2":
            metric = create_composite_metric(eval_specs=eval_specs)
            return MIPROv2(
                metric=metric,
                auto="medium",
                max_bootstrapped_demos=self.max_demos,
                max_labeled_demos=self.max_demos * 2,
                num_candidates=self.num_trials,
            )
        elif self.optimizer_type.lower() == "bootstrap":
            metric = create_composite_metric(eval_specs=eval_specs)
            return BootstrapFewShot(
                metric=metric,
                max_bootstrapped_demos=self.max_demos,
                max_rounds=self.num_trials,
            )
        elif self.optimizer_type.lower() == "gepa":
            trajectory_metric = create_trajectory_feedback_metric(eval_specs=eval_specs)
            reflection_model = ModelNameMapper.get_reflection_model(self.model)
            reflection_lm = dspy.LM(reflection_model)

            # Build GEPA config with proper budget handling
            gepa_kwargs = {
                "metric": trajectory_metric,
                "num_threads": self.num_threads,
                "track_stats": True,
                "reflection_minibatch_size": self.reflection_minibatch_size,
                "reflection_lm": reflection_lm,
            }

            # Add exactly one budget parameter
            if self.auto is not None:
                gepa_kwargs["auto"] = self.auto
            elif self.max_full_evals is not None:
                gepa_kwargs["max_full_evals"] = self.max_full_evals
            elif self.max_metric_calls is not None:
                gepa_kwargs["max_metric_calls"] = self.max_metric_calls
            else:
                # Default fallback
                gepa_kwargs["auto"] = "light"

            return GEPA(**gepa_kwargs)
        else:
            raise ValueError(f"Unknown optimizer type: {self.optimizer_type}")

    def _evaluate_prompt(self, prompt: str, val_data: PromptDataset) -> dict[str, Any]:
        """Evaluate a prompt against validation data with individual metric breakdowns."""
        from .metrics import (
            create_task_success_metric,
            create_tool_usage_metric,
            create_llm_judge_metric,
            compose_metric_scores,
        )

        # Create individual metrics - no duplication, single source of truth
        eval_specs = [example.eval_spec for example in val_data]
        task_metric = create_task_success_metric(eval_specs)
        tool_metric = create_tool_usage_metric()
        judge_metric = create_llm_judge_metric()

        # Run evaluation once per example
        task_scores = []
        tool_scores = []
        judge_scores = []
        module = GptmeModule(prompt, self.model)

        for example in val_data:
            pred = module(
                task_description=example.task_description,
                context=example.context,
                eval_spec=example.eval_spec,
            )

            # Run individual metrics once - no duplication
            task_scores.append(task_metric(example, pred, None))
            tool_scores.append(tool_metric(example, pred, None))
            judge_scores.append(judge_metric(example, pred, None))

        # Calculate averages
        avg_task = sum(task_scores) / len(task_scores) if task_scores else 0.0
        avg_tool = sum(tool_scores) / len(tool_scores) if tool_scores else 0.0
        avg_judge = sum(judge_scores) / len(judge_scores) if judge_scores else 0.0

        # Calculate composite using shared composition function
        avg_composite = compose_metric_scores(avg_task, avg_tool, avg_judge)
        composite_scores = [
            compose_metric_scores(t, tool, j)
            for t, tool, j in zip(task_scores, tool_scores, judge_scores)
        ]

        return {
            "average_score": avg_composite,
            "task_success_rate": avg_task,
            "tool_usage_score": avg_tool,
            "judge_score": avg_judge,
            "individual_scores": composite_scores,
            "individual_task_scores": task_scores,
            "individual_tool_scores": tool_scores,
            "individual_judge_scores": judge_scores,
            "num_examples": len(task_scores),
            "optimized_prompt": prompt,
        }

    def compare_prompts(
        self,
        prompts: dict[str, str],
        eval_specs: list[EvalSpec] | None = None,
        num_examples: int = 10,
    ) -> dict[str, dict[str, Any]]:
        """Compare multiple prompts against evaluation tasks."""
        if eval_specs is None:
            eval_specs = tests[:num_examples]

        val_data = PromptDataset(eval_specs)

        results = {}
        for name, prompt in prompts.items():
            logger.info(f"Evaluating prompt: {name}")
            results[name] = self._evaluate_prompt(prompt, val_data)

        return results

    def suggest_improvements(
        self, current_prompt: str, performance_feedback: str, task_examples: list[str]
    ) -> tuple[str, str]:
        """Use DSPy to suggest specific improvements to a prompt."""
        improver = dspy.ChainOfThought(PromptImprovementSignature)

        result = improver(
            current_prompt=current_prompt,
            performance_feedback=performance_feedback,
            task_examples="\n".join(task_examples),
            improvement_areas="tool usage, clarity, task completion",
        )

        return result.improved_prompt, result.changes_made


def get_current_gptme_prompt(interactive: bool, model: str) -> str:
    """Get the current gptme system prompt."""
    messages = list(prompt_gptme(interactive, model))
    prompt_parts = []
    for msg in messages:
        if msg.role == "system":
            prompt_parts.append(msg.content)
    return "\n\n".join(prompt_parts)
