"""
Core prompt optimization functionality using DSPy.

This module provides the main PromptOptimizer class that uses DSPy's
optimization techniques to automatically improve gptme system prompts.
"""

import logging
import os
import re
from collections.abc import Callable
from typing import Any

from gptme.eval.agents import GPTMe
from gptme.eval.run import execute
from gptme.eval.suites import tests as gptme_eval_tests
from gptme.eval.suites.basic import tests
from gptme.eval.types import EvalSpec
from gptme.logmanager import Log
from gptme.prompts import prompt_gptme

import dspy
from dspy.teleprompt import BootstrapFewShot, MIPROv2

from .metrics import create_composite_metric
from .signatures import PromptImprovementSignature

logger = logging.getLogger(__name__)


task_weight = 0.4
tool_weight = 0.3
judge_weight = 0.3


class PromptDataset:
    """
    Dataset wrapper for gptme evaluation tasks.

    Converts gptme eval specs into DSPy-compatible format.
    """

    def __init__(self, eval_specs: list[EvalSpec], limit: int | None = None):
        # Use actual gptme eval specs instead of custom DSPy ones
        if not eval_specs:
            eval_specs = tests
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
    DSPy module that optimizes gptme system prompts.

    Uses PromptImprovementSignature to generate improved system prompt variations.
    """

    def __init__(
        self, base_system_prompt: str, eval_specs: list[EvalSpec] | None = None
    ):
        super().__init__()
        # Use the existing PromptImprovementSignature to optimize system prompts
        self.prompt_optimizer = dspy.ChainOfThought(PromptImprovementSignature)
        self.base_system_prompt = base_system_prompt
        # Store original eval specs for lookup by task description
        self.eval_specs_lookup = {}
        if eval_specs:
            for spec in eval_specs:
                self.eval_specs_lookup[spec.get("prompt", "")] = spec

    def forward(self, task_description: str, context: str) -> dspy.Prediction:
        """
        Generate an improved system prompt and evaluate it with gptme.
        DSPy will optimize the prompt improvement process.
        """
        try:
            # Use the original EvalSpec instead of creating a fake one
            original_spec = self.eval_specs_lookup.get(task_description)
            if not original_spec:
                raise ValueError(
                    f"Could not find original EvalSpec for task: {task_description[:50]}..."
                )

            # Use DSPy to generate an improved system prompt for this specific task
            prompt_improvement = self.prompt_optimizer(
                current_prompt=self.base_system_prompt,
                performance_feedback="Optimize for task completion and effective tool usage",
                task_examples=f"Task: {task_description}\nContext: {context}",
                improvement_areas="tool usage, task completion, clarity, error handling",
            )
            improved_system_prompt = prompt_improvement.improved_prompt

            # Use the original EvalSpec directly
            eval_spec = original_spec.copy()

            # Parse context to extract files if any
            if "```" in context:
                file_blocks = re.findall(
                    r"```(\w+\.?\w*)\n(.*?)\n```", context, re.DOTALL
                )
                files = {}
                for filename, content in file_blocks:
                    files[filename] = content
                eval_spec["files"] = files

            # Get the model name from DSPy settings and convert back to gptme format
            dspy_model = (
                dspy.settings.lm.model
                if hasattr(dspy.settings, "lm")
                else "claude-3-5-haiku-20241022"
            )

            # Convert DSPy model name back to gptme format
            if dspy_model.startswith("claude-"):
                model = f"anthropic/{dspy_model}"
            elif dspy_model.startswith("gpt-"):
                model = f"openai/{dspy_model}"
            else:
                model = dspy_model

            # Create a GPTMe agent with the DSPy-improved system prompt
            agent = GPTMe(
                model=model,
                tool_format="markdown",
                system_prompt=improved_system_prompt,  # DSPy-optimized prompt!
            )

            # Run the actual evaluation
            result = execute(
                test=eval_spec,
                agent=agent,
                timeout=30,
                parallel=False,
            )

            # Read back the actual conversation messages from the agent's logdir
            # (now properly set by execute() after subprocess completion)
            messages = []
            response = "No response generated"

            if agent.log_dir and (agent.log_dir / "conversation.jsonl").exists():
                log = Log.read_jsonl(agent.log_dir / "conversation.jsonl")
                messages = log.messages

                # Extract response from the last assistant message
                try:
                    response = str(messages[-1].content)
                except (AttributeError, IndexError):
                    response = "No response generated"

            return dspy.Prediction(
                response=response,
                improved_system_prompt=improved_system_prompt,
                changes_made=getattr(prompt_improvement, "changes_made", ""),
                eval_result=result,  # Include actual results for metrics
                messages=messages,  # Include the actual conversation messages
            )

        except Exception as e:
            logger.warning(f"Failed to run actual gptme evaluation: {e}")
            # Return failure case
            return dspy.Prediction(
                response=f"Failed to execute task: {e}",
                improved_system_prompt=self.base_system_prompt,
                changes_made="",
                eval_result=None,
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
            dspy_model = self.model.replace("anthropic/", "")
        elif self.model.startswith("openai/"):
            # Remove openai/ prefix for OpenAI models
            dspy_model = self.model.replace("openai/", "")
        else:
            # Use the model as-is for other providers
            dspy_model = self.model

        # Configure DSPy with reduced logging
        os.environ["DSPY_LOGGING_LEVEL"] = "ERROR"

        # Configure DSPy
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
            eval_specs = tests[: train_size + val_size]  # Use real gptme tests

        # Create datasets
        train_data = PromptDataset(eval_specs[:train_size])
        val_data = PromptDataset(eval_specs[train_size : train_size + val_size])

        # Create metric
        metric = create_composite_metric(eval_specs=eval_specs)

        # Create the module to optimize
        module = GptmeModule(base_prompt, eval_specs)

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
        """Evaluate a prompt against validation data with detailed score breakdown."""
        scores = []
        detailed_results = []

        # Create GptmeModule with the prompt to test as the base system prompt
        module = GptmeModule(prompt, val_data.eval_specs)

        for example in val_data:
            print(f"DEBUG: Evaluating example: {example.task_description[:50]}...")

            # Actually run the evaluation (use DSPy's preferred calling pattern)
            pred = module(
                task_description=example.task_description, context=example.context
            )
            # Get detailed score breakdown
            detailed_score = self._get_detailed_score_breakdown(example, pred)
            scores.append(detailed_score["composite_score"])
            detailed_results.append(detailed_score)

        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Calculate average breakdowns
        avg_task_score = (
            sum(r["task_score"] for r in detailed_results) / len(detailed_results)
            if detailed_results
            else 0.0
        )
        avg_tool_score = (
            sum(r["tool_score"] for r in detailed_results) / len(detailed_results)
            if detailed_results
            else 0.0
        )
        avg_judge_score = (
            sum(r["judge_score"] for r in detailed_results) / len(detailed_results)
            if detailed_results
            else 0.0
        )

        print("\n=== OVERALL SCORE BREAKDOWN ===")
        print(f"Task Success:  {avg_task_score:.3f} (weight: {task_weight})")
        print(f"Tool Usage:    {avg_tool_score:.3f} (weight: {tool_weight})")
        print(f"LLM Judge:     {avg_judge_score:.3f} (weight: {judge_weight})")
        print(f"Composite:     {avg_score:.3f}")
        print("================================")

        return {
            "average_score": avg_score,
            "individual_scores": scores,
            "detailed_results": detailed_results,
            "breakdown": {
                "avg_task_score": avg_task_score,
                "avg_tool_score": avg_tool_score,
                "avg_judge_score": avg_judge_score,
            },
            "num_examples": len(scores),
            "optimized_prompt": prompt,
        }

    def _get_detailed_score_breakdown(self, gold: Any, pred: Any) -> dict[str, float]:
        """Get detailed score breakdown for a single example."""
        from .metrics import (
            create_llm_judge_metric,
            create_task_success_metric,
            create_tool_usage_metric,
        )

        # Create individual metrics
        task_metric = create_task_success_metric([])
        tool_metric = create_tool_usage_metric()
        judge_metric = create_llm_judge_metric()

        # Calculate individual scores
        task_score = task_metric(gold, pred, None)
        tool_score = tool_metric(gold, pred, None)
        judge_score = judge_metric(gold, pred, None)

        # Calculate composite with weights
        composite_score = (
            task_score * task_weight
            + tool_score * tool_weight
            + judge_score * judge_weight
        )

        return {
            "task_score": task_score,
            "tool_score": tool_score,
            "judge_score": judge_score,
            "composite_score": composite_score,
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


def get_current_gptme_prompt(interactive: bool, model: str) -> str:
    """Get the current gptme system prompt."""
    # Generate the current system prompt
    messages = list(prompt_gptme(interactive, model))

    # Combine all system messages
    prompt_parts = []
    for msg in messages:
        if msg.role == "system":
            prompt_parts.append(msg.content)

    return "\n\n".join(prompt_parts)
