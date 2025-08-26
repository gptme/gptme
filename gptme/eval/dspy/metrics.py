"""
Metrics for evaluating gptme system prompt performance.

These metrics assess various aspects of how well system prompts work
in practice across different tasks and scenarios.
"""

import logging
from collections.abc import Callable
from typing import Any

from gptme.eval.types import EvalResult, EvalSpec
from gptme.message import Message

import dspy

from .signatures import PromptEvaluationSignature

logger = logging.getLogger(__name__)


def create_task_success_metric(
    eval_specs: list[EvalSpec],
) -> Callable[[Any, Any, Any | None], float]:
    """
    Create a metric that measures task completion success rate.

    Args:
        eval_specs: List of evaluation specifications from gptme's eval framework

    Returns:
        A metric function that can be used with DSPy optimizers
    """

    def task_success_metric(gold: Any, pred: Any, trace: Any | None = None) -> float:
        """
        Evaluate task success based on expected outcomes.

        Returns a score between 0 and 1 indicating success rate.
        """
        if not hasattr(pred, "eval_result") or not pred.eval_result:
            return 0.0

        result: EvalResult = pred.eval_result

        # Calculate success rate based on passed expectations
        total_expectations = len(result.results)
        if total_expectations == 0:
            return 0.0

        passed_expectations = sum(1 for r in result.results if r.passed)
        success_rate = passed_expectations / total_expectations

        logger.debug(
            f"Task success rate: {success_rate} ({passed_expectations}/{total_expectations})"
        )
        return success_rate

    return task_success_metric


def create_tool_usage_metric() -> Callable[[Any, Any, Any | None], float]:
    """
    Create a metric that evaluates tool usage effectiveness.

    Returns:
        A metric function that evaluates tool usage patterns
    """

    def tool_usage_metric(gold: Any, pred: Any, trace: Any | None = None) -> float:
        """
        Evaluate how effectively tools were used.

        Considers:
        - Whether appropriate tools were selected
        - Whether tools were used efficiently
        - Whether tool usage followed best practices
        """
        if not hasattr(pred, "messages") or not pred.messages:
            return 0.0

        messages: list[Message] = pred.messages
        tool_calls = [
            msg
            for msg in messages
            if msg.role == "assistant"
            and any(block.tool for block in getattr(msg, "blocks", []))
        ]

        if not tool_calls:
            # No tools used - could be good or bad depending on task
            expected_tools = getattr(gold, "tools", [])
            return 1.0 if not expected_tools else 0.0

        # Analyze tool usage patterns
        score = 0.0
        total_weight = 0.0

        # Check if required tools were used
        if hasattr(gold, "tools") and gold.tools:
            required_tools = set(gold.tools)
            used_tools = set()
            for msg in tool_calls:
                for block in getattr(msg, "blocks", []):
                    if block.tool:
                        used_tools.add(block.tool)

            tool_coverage = len(used_tools.intersection(required_tools)) / len(
                required_tools
            )
            score += tool_coverage * 0.4
            total_weight += 0.4

        # Check for efficient tool usage (not too many redundant calls)
        tool_call_count = len(tool_calls)
        efficiency_score = (
            max(0.0, 1.0 - (tool_call_count - 3) * 0.1) if tool_call_count > 3 else 1.0
        )
        score += efficiency_score * 0.3
        total_weight += 0.3

        # Check for appropriate tool sequencing (if we have trace data)
        if trace:
            # TODO: Analyze tool call sequence for logical progression
            pass

        # General tool usage score
        score += 0.5  # Base score for using tools at all
        total_weight += 0.3

        final_score = score / total_weight if total_weight > 0 else 0.0
        logger.debug(f"Tool usage score: {final_score}")
        return final_score

    return tool_usage_metric


def create_llm_judge_metric(
    judge_criteria: str = "overall effectiveness",
) -> Callable[[Any, Any, Any | None], float]:
    """
    Create an LLM-based judge metric for evaluating prompt quality.

    Args:
        judge_criteria: What specific aspect to evaluate

    Returns:
        A metric function that uses an LLM to judge response quality
    """

    judge = dspy.ChainOfThought(PromptEvaluationSignature)

    def llm_judge_metric(gold: Any, pred: Any, trace: Any | None = None) -> float:
        """
        Use an LLM to judge the quality of the response.
        """
        try:
            # Extract relevant information
            original_prompt = getattr(gold, "system_prompt", "")
            task = getattr(gold, "task_description", "")
            response = str(pred) if pred else ""
            expected = getattr(gold, "expected_outcome", "")

            # Get LLM judgment
            judgment = judge(
                original_prompt=original_prompt,
                task=task,
                response=response,
                expected_outcome=expected,
                evaluation_criteria=judge_criteria,
            )

            # Extract numeric score (1-10) and normalize to 0-1
            score_str = judgment.score.strip()
            try:
                score = float(score_str)
                normalized_score = (score - 1) / 9  # Convert 1-10 to 0-1
                return max(0.0, min(1.0, normalized_score))
            except ValueError:
                logger.warning(f"Could not parse LLM judge score: {score_str}")
                return 0.0

        except Exception as e:
            logger.error(f"Error in LLM judge metric: {e}")
            return 0.0

    return llm_judge_metric


def create_composite_metric(
    task_weight: float = 0.4,
    tool_weight: float = 0.3,
    judge_weight: float = 0.3,
    eval_specs: list[EvalSpec] | None = None,
) -> Callable[[Any, Any, Any | None], float]:
    """
    Create a composite metric that combines multiple evaluation aspects.

    Args:
        task_weight: Weight for task success metric
        tool_weight: Weight for tool usage metric
        judge_weight: Weight for LLM judge metric
        eval_specs: Evaluation specifications for task success metric

    Returns:
        A composite metric function
    """

    task_metric = create_task_success_metric(eval_specs or [])
    tool_metric = create_tool_usage_metric()
    judge_metric = create_llm_judge_metric()

    def composite_metric(gold: Any, pred: Any, trace: Any | None = None) -> float:
        """
        Combine multiple metrics with specified weights.
        """
        task_score = task_metric(gold, pred, trace)
        tool_score = tool_metric(gold, pred, trace)
        judge_score = judge_metric(gold, pred, trace)

        composite_score = (
            task_score * task_weight
            + tool_score * tool_weight
            + judge_score * judge_weight
        )

        logger.info(
            f"Composite score: {composite_score:.3f} "
            f"(task: {task_score:.3f}, tool: {tool_score:.3f}, judge: {judge_score:.3f})"
        )

        return composite_score

    return composite_metric


def evaluate_prompt_on_task(
    system_prompt: str,
    task_spec: EvalSpec,
    model: str,
) -> dict[str, Any]:
    """
    Evaluate a single system prompt on a specific task.

    Args:
        system_prompt: The system prompt to test
        task_spec: Task specification from gptme eval framework
        model: Model to use for evaluation

    Returns:
        Dictionary containing evaluation results and metrics
    """
    # TODO: Integrate with gptme's actual evaluation runner
    # This would need to:
    # 1. Set up a conversation with the system prompt
    # 2. Run the task
    # 3. Collect results and apply metrics
    # 4. Return structured evaluation data

    return {
        "task_name": task_spec.get("name", "unknown"),
        "system_prompt": system_prompt,
        "model": model,
        "success_rate": 0.0,  # Placeholder
        "tool_usage_score": 0.0,  # Placeholder
        "judge_score": 0.0,  # Placeholder
        "composite_score": 0.0,  # Placeholder
        "details": {},
    }
