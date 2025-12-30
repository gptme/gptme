"""
Metrics for evaluating gptme system prompt performance.

These metrics assess various aspects of how well system prompts work
in practice across different tasks and scenarios.
"""

import logging
import traceback
from collections.abc import Callable
from typing import Any

import dspy

from gptme.codeblock import Codeblock
from gptme.eval.agents import GPTMe
from gptme.eval.run import execute
from gptme.eval.types import EvalResult, EvalSpec
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import get_tool_for_langtag, init_tools
from gptme.tools.base import ToolUse

from .signatures import PromptEvaluationSignature

logger = logging.getLogger(__name__)


class TrajectoryAnalyzer:
    """Centralized analyzer for gptme execution trajectories."""

    def __init__(self, result: EvalResult, log_dir_path: str | None = None):
        self.result = result
        self.log_dir_path = log_dir_path
        self._messages = self._load_messages() if log_dir_path else []

    def _load_messages(self) -> list[Message]:
        """Load conversation messages from log directory."""
        if not self.log_dir_path:
            return []

        try:
            log_manager = LogManager.load(self.log_dir_path, lock=False)
            return log_manager.log.messages  # Fix: access .messages attribute
        except Exception as e:
            logger.warning(f"Failed to load messages: {e}")
            return []

    def analyze_all(self) -> dict[str, Any]:
        """Run all trajectory analyses and return combined results."""
        return {
            "tool_usage": self._analyze_tool_usage(),
            "reasoning": self._analyze_reasoning(),
            "error_handling": self._analyze_error_handling(),
            "task_completion": self._analyze_task_completion(),
        }

    def _analyze_tool_usage(self) -> dict[str, Any]:
        """Simplified tool usage analysis."""
        if self._messages:
            tool_calls = []
            for message in self._messages:
                if message.role == "assistant" and message.content:
                    codeblocks = Codeblock.iter_from_markdown(message.content)
                    for cb in codeblocks:
                        if tool := get_tool_for_langtag(cb.lang):
                            tool_calls.append(tool.name)

            return {
                "tool_calls": len(tool_calls),
                "tool_variety": len(set(tool_calls)),
                "effectiveness": "good" if tool_calls else "poor",
            }

        # Fallback analysis from output
        output = self.result.gen_stdout + self.result.run_stdout
        tools_used = sum(
            1
            for pattern in ["```shell", "```python", "```patch", "```save"]
            if pattern in output
        )

        return {
            "tool_calls": tools_used,
            "tool_variety": tools_used,
            "effectiveness": "good" if tools_used > 0 else "poor",
        }

    def _analyze_reasoning(self) -> dict[str, Any]:
        """Simplified reasoning analysis."""
        if self._messages:
            assistant_msgs = [m for m in self._messages if m.role == "assistant"]
            avg_length = sum(len(str(m.content)) for m in assistant_msgs) / max(
                len(assistant_msgs), 1
            )
        else:
            avg_length = len(self.result.gen_stdout + self.result.run_stdout)

        return {
            "avg_length": avg_length,
            "quality": "good" if avg_length > 100 else "needs_improvement",
        }

    def _analyze_error_handling(self) -> dict[str, Any]:
        """Simplified error handling analysis."""
        if self._messages:
            errors = sum(
                1
                for m in self._messages
                if m.role == "assistant"
                and any(word in str(m.content).lower() for word in ["error", "failed"])
            )
            recoveries = sum(
                1
                for m in self._messages
                if m.role == "assistant"
                and any(word in str(m.content).lower() for word in ["fix", "retry"])
            )
        else:
            errors = len(
                [
                    line
                    for line in (self.result.gen_stderr + self.result.run_stderr).split(
                        "\n"
                    )
                    if any(word in line.lower() for word in ["error", "failed"])
                ]
            )
            recoveries = 0

        return {
            "errors": errors,
            "recoveries": recoveries,
            "effectiveness": "good" if recoveries >= errors else "needs_improvement",
        }

    def _analyze_task_completion(self) -> dict[str, Any]:
        """Analyze task completion success."""
        passed = sum(1 for r in self.result.results if r.passed)
        total = len(self.result.results)
        success_rate = passed / max(total, 1)

        return {
            "success_rate": success_rate,
            "quality": "excellent"
            if success_rate >= 0.9
            else "good"
            if success_rate >= 0.7
            else "needs_improvement",
        }


def create_trajectory_feedback_metric(
    eval_specs: list[EvalSpec] | None = None,
) -> Callable[[Any, Any, Any | None, str | None, Any | None], dspy.Prediction]:
    """Simplified trajectory feedback metric using TrajectoryAnalyzer."""

    def trajectory_feedback_metric(
        gold: Any,
        pred: Any,
        trace: Any | None = None,
        pred_name: str | None = None,
        pred_trace: Any | None = None,
    ) -> dspy.Prediction:
        if not hasattr(pred, "eval_result") or not pred.eval_result:
            return dspy.Prediction(
                score=0.0, feedback="No evaluation result available."
            )

        # Use simplified analyzer
        analyzer = TrajectoryAnalyzer(
            pred.eval_result, getattr(pred, "log_dir_path", None)
        )
        analysis = analyzer.analyze_all()

        # Calculate simple composite score
        scores = [
            0.8 if analysis["tool_usage"]["effectiveness"] == "good" else 0.4,
            0.8 if analysis["reasoning"]["quality"] == "good" else 0.4,
            0.8 if analysis["error_handling"]["effectiveness"] == "good" else 0.4,
            analysis["task_completion"]["success_rate"],
        ]
        score = sum(scores) / len(scores)

        # Generate concise feedback
        feedback = f"""=== TRAJECTORY ANALYSIS ===
Tool Usage: {analysis['tool_usage']['effectiveness']} ({analysis['tool_usage']['tool_calls']} calls)
Reasoning: {analysis['reasoning']['quality']} (avg {analysis['reasoning']['avg_length']:.0f} chars)
Error Handling: {analysis['error_handling']['effectiveness']} ({analysis['error_handling']['recoveries']}/{analysis['error_handling']['errors']} recovered)
Task Completion: {analysis['task_completion']['success_rate']:.1%} success rate

=== RECOMMENDATIONS ==="""

        if analysis["tool_usage"]["effectiveness"] != "good":
            feedback += "\n- Use more diverse tools for complex tasks"
        if analysis["reasoning"]["quality"] != "good":
            feedback += "\n- Provide more detailed reasoning steps"
        if analysis["error_handling"]["effectiveness"] != "good":
            feedback += "\n- Improve error detection and recovery"
        if analysis["task_completion"]["success_rate"] < 0.8:
            feedback += "\n- Focus on meeting all task expectations"

        if all(
            a["effectiveness"] == "good"
            if "effectiveness" in a
            else a["quality"] == "good"
            if "quality" in a
            else a["success_rate"] >= 0.8
            for a in analysis.values()
        ):
            feedback += "\n- Performance is good, continue current approach"

        return dspy.Prediction(score=score, feedback=feedback)

    return trajectory_feedback_metric


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
    # Initialize tools once when metric is created, not on every evaluation
    init_tools(["save", "shell", "patch", "read", "ipython"])

    def tool_usage_metric(gold: Any, pred: Any, trace: Any | None = None) -> float:
        """
        Evaluate how effectively tools were used.

        Considers:
        - Whether appropriate tools were used
        - Whether tools were used efficiently
        - Whether tool usage followed best practices
        """
        # Get messages from pred (added by GptmeModule) - must be present
        messages: list[Message] = pred.messages  # type: ignore
        if not messages:
            raise ValueError(
                "No messages available for tool usage analysis - evaluation may have failed"
            )

        # Count tool calls
        tool_calls = []
        used_tools = set()

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

            traceback.print_exc()
            return 0.0

    return llm_judge_metric


def compose_metric_scores(
    task_score: float,
    tool_score: float,
    judge_score: float,
    task_weight: float = 0.4,
    tool_weight: float = 0.3,
    judge_weight: float = 0.3,
) -> float:
    """Compose individual metric scores into composite score using specified weights."""
    return (
        task_score * task_weight + tool_score * tool_weight + judge_score * judge_weight
    )


def create_composite_metric(
    eval_specs: list[EvalSpec] | None = None,
) -> Callable[[Any, Any, Any | None], float]:
    """
    Create a composite metric that combines multiple evaluation aspects.

    Args:
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

        composite_score = compose_metric_scores(task_score, tool_score, judge_score)

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
