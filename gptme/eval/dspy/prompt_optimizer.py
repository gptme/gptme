"""
Core prompt optimization functionality using DSPy.

This module provides the main PromptOptimizer class that uses DSPy's
optimization techniques to automatically improve gptme system prompts.
"""

import logging
from collections.abc import Callable
from typing import Any

from gptme.eval.suites import tests as gptme_eval_tests
from gptme.eval.types import EvalSpec
from gptme.prompts import prompt_gptme

import dspy
from dspy.teleprompt import BootstrapFewShot, MIPROv2

from .metrics import create_composite_metric
from .signatures import GptmeTaskSignature, PromptImprovementSignature

logger = logging.getLogger(__name__)


class PromptDataset:
    """
    Dataset wrapper for gptme evaluation tasks.

    Converts gptme eval specs into DSPy-compatible format.
    """

    def __init__(self, eval_specs: list[EvalSpec], limit: int | None = None):
        self.eval_specs = eval_specs[:limit] if limit else eval_specs

    def __len__(self) -> int:
        return len(self.eval_specs)

    def __iter__(self):
        for spec in self.eval_specs:
            yield self._spec_to_example(spec)

    def _spec_to_example(self, spec: EvalSpec) -> dspy.Example:
        """Convert an eval spec to a DSPy example."""
        return dspy.Example(
            task_description=spec.get("prompt", ""),
            context=self._build_context(spec),
            expected_outcome=spec.get("run", ""),
            tools=spec.get("tools", []),
            name=spec.get("name", "unknown"),
        ).with_inputs("task_description", "context")

    def _build_context(self, spec: EvalSpec) -> str:
        """Build context string from eval spec."""
        context_parts = []

        # Add file contents
        files = spec.get("files", {})
        if files:
            context_parts.append("Files in workspace:")
            for filename, content in files.items():
                content_str = (
                    content.decode() if isinstance(content, bytes) else content
                )
                context_parts.append(f"```{filename}\n{content_str}\n```")

        # Add expected run command
        if run_cmd := spec.get("run"):
            context_parts.append(f"Expected to run: {run_cmd}")

        return "\n\n".join(context_parts)


class GptmeModule(dspy.Module):
    """
    DSPy module that wraps gptme's core functionality.

    This allows DSPy to optimize the system prompt by testing different
    variations against evaluation tasks.
    """

    def __init__(self, system_prompt_template: str):
        super().__init__()
        self.system_prompt_template = system_prompt_template
        self.signature = GptmeTaskSignature

    def forward(self, task_description: str, context: str) -> dspy.Prediction:
        """
        Execute a task with the current system prompt.

        This would integrate with gptme's actual execution engine.
        For now, it's a placeholder that simulates the process.
        """
        # TODO: Actually run gptme with this system prompt on the task
        # This would involve:
        # 1. Creating a conversation with the system prompt
        # 2. Adding the task as a user message
        # 3. Running gptme's execution loop
        # 4. Collecting the results

        # Placeholder response
        response = f"Executed task: {task_description}"

        return dspy.Prediction(
            response=response, system_prompt=self.system_prompt_template
        )


class PromptOptimizer:
    """
    Main class for optimizing gptme system prompts using DSPy.
    """

    def __init__(
        self,
        model: str,
        optimizer_type: str = "miprov2",
        max_demos: int = 3,
        num_trials: int = 10,
    ):
        self.model = model
        self.optimizer_type = optimizer_type
        self.max_demos = max_demos
        self.num_trials = num_trials

        # Initialize DSPy with the specified model
        self._setup_dspy()

    def _setup_dspy(self):
        """Initialize DSPy with the specified model."""
        # Map gptme model names to DSPy/litellm format
        if self.model.startswith("anthropic/"):
            # Extract the actual model name and map to litellm format
            base_model = self.model.replace("anthropic/", "")
            if "claude-sonnet-4" in base_model:
                dspy_model = "claude-3-5-sonnet-20241022"  # Latest Sonnet
            elif "claude-sonnet-3.5" in base_model:
                dspy_model = "claude-3-5-sonnet-20241022"
            elif "claude-haiku" in base_model:
                dspy_model = "claude-3-5-haiku-20241022"
            elif "claude-opus" in base_model:
                dspy_model = "claude-3-opus-20240229"
            else:
                dspy_model = "claude-3-5-sonnet-20241022"  # Default to latest Sonnet
        elif self.model.startswith("openai/"):
            # Remove openai/ prefix for OpenAI models
            dspy_model = self.model.replace("openai/", "")
        else:
            # Use the model as-is for other providers
            dspy_model = self.model

        # Configure DSPy
        lm = dspy.LM(dspy_model)
        dspy.configure(lm=lm)
        logger.info(f"Configured DSPy with model: {dspy_model}")

    def optimize_prompt(
        self,
        base_prompt: str,
        eval_specs: list[EvalSpec] | None = None,
        train_size: int = 10,
        val_size: int = 5,
    ) -> tuple[str, dict[str, Any]]:
        """
        Optimize a system prompt using DSPy techniques.

        Args:
            base_prompt: The starting system prompt to optimize
            eval_specs: Evaluation specifications to test against
            train_size: Number of training examples to use
            val_size: Number of validation examples to use

        Returns:
            Tuple of (optimized_prompt, optimization_results)
        """
        logger.info("Starting prompt optimization...")

        # Get evaluation specs if not provided
        if eval_specs is None:
            eval_specs = gptme_eval_tests

        # Create datasets
        train_data = PromptDataset(eval_specs[:train_size])
        val_data = PromptDataset(eval_specs[train_size : train_size + val_size])

        # Create metric
        metric = create_composite_metric(eval_specs=eval_specs)

        # Create the module to optimize
        module = GptmeModule(base_prompt)

        # Choose optimizer
        if self.optimizer_type.lower() == "miprov2":
            optimizer = MIPROv2(
                metric=metric,
                auto="medium",
                max_bootstrapped_demos=self.max_demos,
                max_labeled_demos=train_size,
            )
        elif self.optimizer_type.lower() == "bootstrap":
            optimizer = BootstrapFewShot(
                metric=metric, max_bootstrapped_demos=self.max_demos
            )
        else:
            raise ValueError(f"Unknown optimizer type: {self.optimizer_type}")

        # Run optimization
        logger.info(f"Running {self.optimizer_type} optimization...")
        try:
            optimized_module = optimizer.compile(
                module,
                trainset=list(train_data),
                # valset=list(val_data) if val_data else None,
            )

            # Extract the optimized prompt
            optimized_prompt = getattr(
                optimized_module, "system_prompt_template", base_prompt
            )

            # Evaluate the optimized prompt
            results = self._evaluate_prompt(optimized_prompt, val_data, metric)

            logger.info("Prompt optimization completed successfully")
            return optimized_prompt, results

        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            return base_prompt, {"error": str(e)}

    def _evaluate_prompt(
        self,
        prompt: str,
        val_data: PromptDataset,
        metric: Callable[[Any, Any, Any | None], float],
    ) -> dict[str, Any]:
        """Evaluate a prompt against validation data."""
        scores = []

        for example in val_data:
            # Create a prediction object (placeholder)
            pred = dspy.Prediction(
                response=f"Response to: {example.task_description}",
                system_prompt=prompt,
            )

            score = metric(example, pred, None)
            scores.append(score)

        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            "average_score": avg_score,
            "individual_scores": scores,
            "num_examples": len(scores),
            "optimized_prompt": prompt,
        }

    def compare_prompts(
        self,
        prompts: dict[str, str],
        eval_specs: list[EvalSpec] | None = None,
        num_examples: int = 10,
    ) -> dict[str, dict[str, Any]]:
        """
        Compare multiple prompts against evaluation tasks.

        Args:
            prompts: Dictionary mapping prompt names to prompt text
            eval_specs: Evaluation specifications to test against
            num_examples: Number of examples to test with

        Returns:
            Dictionary mapping prompt names to their evaluation results
        """
        if eval_specs is None:
            eval_specs = gptme_eval_tests

        val_data = PromptDataset(eval_specs[:num_examples])
        metric = create_composite_metric(eval_specs=eval_specs)

        results = {}
        for name, prompt in prompts.items():
            logger.info(f"Evaluating prompt: {name}")
            results[name] = self._evaluate_prompt(prompt, val_data, metric)

        return results

    def suggest_improvements(
        self, current_prompt: str, performance_feedback: str, task_examples: list[str]
    ) -> tuple[str, str]:
        """
        Use DSPy to suggest specific improvements to a prompt.

        Args:
            current_prompt: The current system prompt
            performance_feedback: Feedback about issues with the prompt
            task_examples: Examples where the prompt underperformed

        Returns:
            Tuple of (improved_prompt, explanation_of_changes)
        """
        improver = dspy.ChainOfThought(PromptImprovementSignature)

        result = improver(
            current_prompt=current_prompt,
            performance_feedback=performance_feedback,
            task_examples="\n".join(task_examples),
            improvement_areas="tool usage, clarity, task completion",
        )

        return result.improved_prompt, result.changes_made


def get_current_gptme_prompt(
    interactive: bool = True, model: str = "anthropic/claude-sonnet-4-20250514"
) -> str:
    """Get the current gptme system prompt."""
    # Generate the current system prompt
    messages = list(prompt_gptme(interactive, model))

    # Combine all system messages
    prompt_parts = []
    for msg in messages:
        if msg.role == "system":
            prompt_parts.append(msg.content)

    return "\n\n".join(prompt_parts)
