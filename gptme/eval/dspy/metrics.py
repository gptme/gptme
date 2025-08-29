"""
Metrics for evaluating gptme system prompt performance.

These metrics assess various aspects of how well system prompts work
in practice across different tasks and scenarios.
"""

import logging
from collections.abc import Callable
from typing import Any

from gptme.eval.agents import GPTMe
from gptme.eval.run import execute
from gptme.eval.types import EvalResult, EvalSpec
from gptme.message import Message

import dspy

from .signatures import PromptEvaluationSignature

logger = logging.getLogger(__name__)


def create_trajectory_feedback_metric(
    eval_specs: list[EvalSpec] | None = None,
) -> Callable[[Any, Any, Any | None], dspy.Prediction]:
    """
    Create a trajectory feedback metric for GEPA optimization.

    This metric analyzes gptme execution traces and provides rich textual
    feedback instead of just scalar scores, enabling GEPA's reflective optimization.

    Args:
        eval_specs: List of evaluation specifications

    Returns:
        A metric function that returns dspy.Prediction with score and feedback
    """

    def trajectory_feedback_metric(
        gold: Any, pred: Any, trace: Any | None = None
    ) -> dspy.Prediction:
        """
        Analyze gptme execution trajectory and provide rich feedback.
        """
        if not hasattr(pred, "eval_result") or not pred.eval_result:
            return dspy.Prediction(
                score=0.0, feedback="No evaluation result available for analysis."
            )

        result: EvalResult = pred.eval_result
        log_dir_path = getattr(pred, "log_dir_path", None)

        # Extract trajectory components using log directory path when available
        tool_usage_analysis = _analyze_tool_usage(log_dir_path, result)
        reasoning_analysis = _analyze_reasoning_quality(log_dir_path, result)
        error_analysis = _analyze_error_handling(log_dir_path, result)
        task_completion_analysis = _analyze_task_completion(result)

        # Calculate composite score
        score = _calculate_trajectory_score(
            tool_usage_analysis,
            reasoning_analysis,
            error_analysis,
            task_completion_analysis,
        )

        # Generate rich textual feedback
        feedback = _generate_trajectory_feedback(
            tool_usage_analysis,
            reasoning_analysis,
            error_analysis,
            task_completion_analysis,
        )

        return dspy.Prediction(score=score, feedback=feedback)

    return trajectory_feedback_metric


def _analyze_tool_usage(log_dir_path: str | None, result: EvalResult) -> dict[str, Any]:
    """Analyze tool usage patterns from conversation log."""
    if log_dir_path:
        try:
            from gptme.logmanager import LogManager
            from gptme.codeblock import Codeblock
            from gptme.tools import get_tool_for_langtag

            # Load the conversation log
            log_manager = LogManager.load(log_dir_path, lock=False)
            messages = log_manager.log

            tool_calls = []

            for message in messages:
                if message.role == "assistant" and message.content:
                    # Extract codeblocks using gptme's parser
                    codeblocks = Codeblock.iter_from_markdown(message.content)

                    for codeblock in codeblocks:
                        tool = get_tool_for_langtag(codeblock.lang)
                        if tool:
                            tool_calls.append(
                                {
                                    "type": tool.name,
                                    "lang_tag": codeblock.lang,
                                    "content": codeblock.content[:100] + "..."
                                    if len(codeblock.content) > 100
                                    else codeblock.content,
                                    "length": len(codeblock.content),
                                    "path": codeblock.path,
                                }
                            )

            tool_types = [call["type"] for call in tool_calls]

            return {
                "tool_calls": tool_calls,
                "num_tools_used": len(tool_calls),
                "tool_variety": len(set(tool_types)),
                "tool_types": list(set(tool_types)),
                "effectiveness": "good" if len(tool_calls) > 0 else "poor",
            }

        except Exception as e:
            logger.warning(f"Failed to analyze tool usage from log: {e}")
            # Fall back to basic analysis
            pass

    # Fallback: basic analysis from stdout/stderr
    tool_calls = []
    all_output = result.gen_stdout + result.run_stdout

    if "```shell" in all_output:
        tool_calls.append({"type": "shell", "content": "shell commands detected"})
    if "```python" in all_output or "```ipython" in all_output:
        tool_calls.append({"type": "python", "content": "python code detected"})
    if "```patch" in all_output:
        tool_calls.append({"type": "patch", "content": "patch operations detected"})
    if "```save" in all_output:
        tool_calls.append({"type": "save", "content": "file operations detected"})

    return {
        "tool_calls": tool_calls,
        "num_tools_used": len(tool_calls),
        "tool_variety": len(set(call["type"] for call in tool_calls)),
        "effectiveness": "good" if len(tool_calls) > 0 else "poor",
    }


def _analyze_reasoning_quality(
    log_dir_path: str | None, result: EvalResult
) -> dict[str, Any]:
    """Analyze reasoning quality from conversation log."""
    if log_dir_path:
        try:
            from gptme.logmanager import LogManager

            # Load the conversation log
            log_manager = LogManager.load(log_dir_path, lock=False)
            messages = log_manager.log

            reasoning_steps = []

            for message in messages:
                if message.role == "assistant" and message.content:
                    # Analyze reasoning content
                    reasoning_steps.append(
                        {
                            "length": len(str(message.content)),
                            "content": str(message.content)[:200] + "..."
                            if len(str(message.content)) > 200
                            else str(message.content),
                        }
                    )

            avg_reasoning_length: float = (
                sum(step["length"] for step in reasoning_steps) / len(reasoning_steps)  # type: ignore
                if reasoning_steps
                else 0.0
            )

            return {
                "reasoning_steps": reasoning_steps,
                "num_steps": len(reasoning_steps),
                "avg_step_length": avg_reasoning_length,
                "quality": "good"
                if avg_reasoning_length > 100
                else "needs_improvement",
            }

        except Exception as e:
            logger.warning(f"Failed to analyze reasoning from log: {e}")
            # Fall back to basic analysis
            pass

    # Fallback: basic analysis from stdout
    all_output = result.gen_stdout + result.run_stdout
    reasoning_length = len(all_output)

    return {
        "reasoning_steps": [
            {"length": reasoning_length, "content": all_output[:200] + "..."}
        ],
        "num_steps": 1 if reasoning_length > 0 else 0,
        "avg_step_length": reasoning_length,
        "quality": "good" if reasoning_length > 100 else "needs_improvement",
    }


def _analyze_error_handling(
    log_dir_path: str | None, result: EvalResult
) -> dict[str, Any]:
    """Analyze error handling and recovery from conversation log."""
    if log_dir_path:
        try:
            from gptme.logmanager import LogManager

            # Load the conversation log
            log_manager = LogManager.load(log_dir_path, lock=False)
            messages = log_manager.log

            errors_found = []
            recovery_attempts = []

            for message in messages:
                if message.role == "assistant" and message.content:
                    content = str(message.content).lower()
                    if any(
                        error_word in content
                        for error_word in ["error", "failed", "exception"]
                    ):
                        errors_found.append(message.content)
                    if any(
                        recovery_word in content
                        for recovery_word in ["try again", "fix", "correct", "retry"]
                    ):
                        recovery_attempts.append(message.content)

            return {
                "errors_encountered": len(errors_found),
                "recovery_attempts": len(recovery_attempts),
                "recovery_ratio": len(recovery_attempts) / len(errors_found)
                if errors_found
                else 1.0,
                "effectiveness": "good"
                if len(recovery_attempts) >= len(errors_found)
                else "needs_improvement",
            }

        except Exception as e:
            logger.warning(f"Failed to analyze error handling from log: {e}")
            # Fall back to basic analysis
            pass

    # Fallback: basic analysis from stderr
    all_stderr = result.gen_stderr + result.run_stderr
    error_keywords = ["error", "failed", "exception"]
    errors_in_stderr = len(
        [
            line
            for line in all_stderr.split("\n")
            if any(error_word in line.lower() for error_word in error_keywords)
        ]
    )

    return {
        "errors_encountered": errors_in_stderr,
        "recovery_attempts": 0,  # Can't detect recovery attempts from stderr alone
        "recovery_ratio": 0.0 if errors_in_stderr > 0 else 1.0,
        "effectiveness": "needs_improvement" if errors_in_stderr > 0 else "good",
    }


def _analyze_task_completion(result: EvalResult) -> dict[str, Any]:
    """Analyze overall task completion quality."""
    total_expectations = len(result.results)
    passed_expectations = sum(1 for r in result.results if r.passed)
    success_rate = (
        passed_expectations / total_expectations if total_expectations > 0 else 0.0
    )

    return {
        "success_rate": success_rate,
        "total_expectations": total_expectations,
        "passed_expectations": passed_expectations,
        "quality": "excellent"
        if success_rate >= 0.9
        else "good"
        if success_rate >= 0.7
        else "needs_improvement",
    }


def _calculate_trajectory_score(
    tool_analysis: dict,
    reasoning_analysis: dict,
    error_analysis: dict,
    completion_analysis: dict,
) -> float:
    """Calculate composite trajectory score."""
    # Weight the different aspects
    tool_score = 0.8 if tool_analysis["effectiveness"] == "good" else 0.4
    reasoning_score = 0.8 if reasoning_analysis["quality"] == "good" else 0.4
    error_score = 0.9 if error_analysis["effectiveness"] == "good" else 0.5
    completion_score = completion_analysis["success_rate"]

    # Composite score with weights
    return (
        tool_score * 0.25
        + reasoning_score * 0.25
        + error_score * 0.25
        + completion_score * 0.25
    )


def _generate_trajectory_feedback(
    tool_analysis: dict,
    reasoning_analysis: dict,
    error_analysis: dict,
    completion_analysis: dict,
) -> str:
    """Generate rich textual feedback for GEPA optimization."""
    feedback_parts = []

    # Tool usage feedback
    feedback_parts.append("=== TOOL USAGE ANALYSIS ===")
    feedback_parts.append(f"Tools used: {tool_analysis['num_tools_used']}")
    feedback_parts.append(
        f"Tool variety: {tool_analysis['tool_variety']} different tool types"
    )
    feedback_parts.append(f"Tool effectiveness: {tool_analysis['effectiveness']}")

    if tool_analysis["effectiveness"] != "good":
        feedback_parts.append(
            "IMPROVEMENT: Consider using more diverse tools for better task completion"
        )

    # Reasoning feedback
    feedback_parts.append("\n=== REASONING ANALYSIS ===")
    feedback_parts.append(f"Reasoning steps: {reasoning_analysis['num_steps']}")
    feedback_parts.append(
        f"Average step length: {reasoning_analysis['avg_step_length']:.0f} characters"
    )
    feedback_parts.append(f"Reasoning quality: {reasoning_analysis['quality']}")

    if reasoning_analysis["quality"] != "good":
        feedback_parts.append(
            "IMPROVEMENT: Provide more detailed step-by-step reasoning and explanations"
        )

    # Error handling feedback
    feedback_parts.append("\n=== ERROR HANDLING ANALYSIS ===")
    feedback_parts.append(f"Errors encountered: {error_analysis['errors_encountered']}")
    feedback_parts.append(f"Recovery attempts: {error_analysis['recovery_attempts']}")
    feedback_parts.append(f"Recovery ratio: {error_analysis['recovery_ratio']:.2f}")
    feedback_parts.append(f"Error handling: {error_analysis['effectiveness']}")

    if error_analysis["effectiveness"] != "good":
        feedback_parts.append(
            "IMPROVEMENT: Better error detection and recovery strategies needed"
        )

    # Task completion feedback
    feedback_parts.append("\n=== TASK COMPLETION ANALYSIS ===")
    feedback_parts.append(f"Success rate: {completion_analysis['success_rate']:.1%}")
    feedback_parts.append(
        f"Expectations met: {completion_analysis['passed_expectations']}/{completion_analysis['total_expectations']}"
    )
    feedback_parts.append(f"Overall quality: {completion_analysis['quality']}")

    # Generate specific recommendations
    feedback_parts.append("\n=== OPTIMIZATION RECOMMENDATIONS ===")
    recommendations = []

    if tool_analysis["num_tools_used"] < 2:
        recommendations.append(
            "- Use more tools to solve complex problems systematically"
        )

    if reasoning_analysis["avg_step_length"] < 50:
        recommendations.append(
            "- Provide more detailed explanations of reasoning steps"
        )

    if error_analysis["recovery_ratio"] < 0.8:
        recommendations.append(
            "- Improve error detection and implement recovery strategies"
        )

    if completion_analysis["success_rate"] < 0.8:
        recommendations.append(
            "- Focus on meeting all task expectations, not just partial completion"
        )

    if not recommendations:
        recommendations.append("- Continue current approach, performance is good")

    feedback_parts.extend(recommendations)

    return "\n".join(feedback_parts)


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
        result: EvalResult = pred.eval_result  # type: ignore

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
        - Whether appropriate tools were used
        - Whether tools were used efficiently
        - Whether tool usage followed best practices
        """
        # Get messages from pred (added by GptmeModule)
        messages: list[Message] = pred.messages  # type: ignore
        if not messages:
            return 0.0

        # Count tool calls
        tool_calls = []
        used_tools = set()

        from gptme.tools import init_tools
        from gptme.tools.base import ToolUse

        # Initialize tools first (they're not loaded in this context)
        init_tools(["save", "shell", "patch", "read", "ipython"])

        for msg in messages:
            if msg.role == "assistant":
                # Parse tool uses from assistant messages
                tool_uses = list(
                    ToolUse.iter_from_content(
                        msg.content, tool_format_override="markdown"
                    )
                )

                for tool_use in tool_uses:
                    tool_calls.append(msg)
                    used_tools.add(tool_use.tool)

        if not tool_calls:
            expected_tools = getattr(gold, "tools", [])
            return 1.0 if not expected_tools else 0.0

        # Get expected tools
        expected_tools = getattr(gold, "tools", [])

        # Analyze tool usage patterns
        score = 0.0
        total_weight = 0.0

        # Check if required tools were used
        if expected_tools:
            required_tools = set(expected_tools)
            tool_coverage = len(used_tools.intersection(required_tools)) / len(
                required_tools
            )
            coverage_score = tool_coverage * 0.4
            score += coverage_score
            total_weight += 0.4

        # Check for efficient tool usage (not too many redundant calls)
        tool_call_count = len(tool_calls)
        efficiency_score = (
            max(0.0, 1.0 - (tool_call_count - 3) * 0.1) if tool_call_count > 3 else 1.0
        )
        efficiency_contribution = efficiency_score * 0.3
        score += efficiency_contribution
        total_weight += 0.3

        # General tool usage score
        base_contribution = 0.5 * 0.3
        score += base_contribution
        total_weight += 0.3

        final_score = score / total_weight if total_weight > 0 else 0.0
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
                # Handle "9/10" format
                if "/" in score_str:
                    score = float(score_str.split("/")[0])
                else:
                    score = float(score_str)

                normalized_score = (score - 1) / 9  # Convert 1-10 to 0-1
                final_score = max(0.0, min(1.0, normalized_score))
                return final_score
            except ValueError:
                logger.warning(f"Could not parse LLM judge score: {score_str}")
                return 0.0

        except Exception as e:
            logger.error(f"Error in LLM judge metric: {e}")
            import traceback

            traceback.print_exc()
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
    try:
        # Create a GPTMe agent
        agent = GPTMe(model=model, tool_format="markdown", system_prompt=system_prompt)

        # Run actual gptme evaluation
        result = execute(test=task_spec, agent=agent, timeout=60, parallel=False)

        # Calculate metrics from actual results
        task_success = 0.0
        if not hasattr(result, "results"):
            raise ValueError("EvalResult missing results attribute")

        passed = sum(1 for r in result.results if r.passed)
        task_success = passed / len(result.results)

        # Tool usage analysis
        tool_score = 0.0
        # EvalResult doesn't have messages attribute, so we can't analyze tool usage here
        tool_calls: list = []
        # Simple tool usage score
        expected_tools = task_spec.get("tools", [])
        if expected_tools:
            used_tools = set()
            for msg in tool_calls:
                for block in getattr(msg, "blocks", []):
                    if block.tool:
                        used_tools.add(block.tool)
            tool_score = len(used_tools.intersection(set(expected_tools))) / len(
                expected_tools
            )
        else:
            tool_score = (
                1.0 if not tool_calls else 0.8
            )  # Reward not using tools when not needed

        # LLM judge score (simplified)
        judge_score = min(1.0, task_success + 0.3)  # Basic heuristic

        # Composite score
        composite_score = task_success * 0.4 + tool_score * 0.3 + judge_score * 0.3

        return {
            "task_name": task_spec.get("name", "unknown"),
            "system_prompt": system_prompt,
            "model": model,
            "success_rate": task_success,
            "tool_usage_score": tool_score,
            "judge_score": judge_score,
            "composite_score": composite_score,
            "details": {
                "result": result,
                "num_tool_calls": len(tool_calls) if "tool_calls" in locals() else 0,
                "expected_tools": expected_tools,
                "used_tools": list(used_tools) if "used_tools" in locals() else [],
            },
        }

    except Exception as e:
        logger.error(f"Failed to run actual evaluation: {e}")
        # Fallback to basic evaluation
        return {
            "task_name": task_spec.get("name", "unknown"),
            "system_prompt": system_prompt,
            "model": model,
            "success_rate": 0.0,
            "tool_usage_score": 0.0,
            "judge_score": 0.0,
            "composite_score": 0.0,
            "details": {"error": str(e)},
        }
