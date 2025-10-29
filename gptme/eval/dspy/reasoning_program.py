"""
Multi-stage reasoning program for GEPA optimization.

This module implements a structured reasoning program that breaks down
task execution into distinct stages: analyze, plan, execute, monitor, and recover.
"""

import logging
from typing import Any

import dspy

from gptme.eval.types import EvalSpec

logger = logging.getLogger(__name__)


# Stage 1: Task Analysis
class TaskAnalysisSignature(dspy.Signature):
    """
    Analyze a task to understand requirements, constraints, and approach.

    This is the first stage that breaks down what needs to be done.
    """

    task_description = dspy.InputField(desc="The task to be accomplished")
    context = dspy.InputField(desc="Available context: files, environment, constraints")
    system_capabilities = dspy.InputField(
        desc="Available tools and capabilities for task execution"
    )

    task_type = dspy.OutputField(
        desc="Classification of task type (debugging, implementation, research, etc.)"
    )
    key_requirements = dspy.OutputField(
        desc="Essential requirements that must be satisfied"
    )
    constraints = dspy.OutputField(desc="Constraints and limitations to consider")
    approach_strategy = dspy.OutputField(
        desc="High-level strategy for approaching this task"
    )


# Stage 2: Planning
class PlanningSignature(dspy.Signature):
    """
    Create a step-by-step execution plan based on task analysis.

    This stage translates analysis into actionable steps.
    """

    task_analysis = dspy.InputField(
        desc="Results from task analysis stage including requirements and strategy"
    )
    available_tools = dspy.InputField(
        desc="List of available tools and their capabilities"
    )

    execution_steps = dspy.OutputField(
        desc="Ordered list of steps to execute, with tool requirements for each"
    )
    dependencies = dspy.OutputField(
        desc="Dependencies between steps and required ordering"
    )
    success_criteria = dspy.OutputField(
        desc="How to verify each step and overall success"
    )


# Stage 3: Execution
class ExecutionSignature(dspy.Signature):
    """
    Execute a single step from the plan using appropriate tools.

    This stage translates a step into specific tool actions.
    """

    step_description = dspy.InputField(
        desc="The specific step to execute from the plan"
    )
    current_state = dspy.InputField(
        desc="Current state of execution including previous step results"
    )
    available_tools = dspy.InputField(desc="Tools available for this step")

    tool_selection = dspy.OutputField(desc="Which tool(s) to use and why")
    tool_invocation = dspy.OutputField(desc="Specific tool commands or code to execute")
    expected_outcome = dspy.OutputField(
        desc="What result to expect from this execution"
    )


# Stage 4: Monitoring
class MonitoringSignature(dspy.Signature):
    """
    Monitor execution results and assess progress toward goals.

    This stage evaluates whether steps are succeeding.
    """

    step_description = dspy.InputField(desc="The step that was executed")
    execution_result = dspy.InputField(desc="The actual result from tool execution")
    expected_outcome = dspy.InputField(desc="What was expected from this step")
    success_criteria = dspy.InputField(desc="Criteria for determining success")

    status = dspy.OutputField(
        desc="Status assessment: success, partial_success, failure, or needs_recovery"
    )
    progress_assessment = dspy.OutputField(
        desc="How much progress was made toward the overall goal"
    )
    issues_detected = dspy.OutputField(desc="Any issues or problems that were detected")
    next_action = dspy.OutputField(
        desc="Recommended next action: continue, retry, recover, or abort"
    )


# Stage 5: Recovery
class RecoverySignature(dspy.Signature):
    """
    Develop recovery strategies when errors or failures occur.

    This stage handles error cases and develops recovery plans.
    """

    error_description = dspy.InputField(desc="Description of what went wrong")
    execution_context = dspy.InputField(desc="Context of execution when error occurred")
    previous_attempts = dspy.InputField(
        desc="Any previous recovery attempts and their outcomes"
    )

    error_analysis = dspy.OutputField(desc="Analysis of root cause and error type")
    recovery_strategy = dspy.OutputField(desc="Strategy for recovering from this error")
    alternative_approach = dspy.OutputField(
        desc="Alternative approach if recovery strategy fails"
    )
    preventive_measures = dspy.OutputField(
        desc="How to prevent similar errors in future"
    )


class GptmeReasoningProgram(dspy.Module):
    """
    Multi-stage reasoning program for task execution.

    This program structures task execution into five distinct stages:
    1. Analyze: Understand the task requirements and constraints
    2. Plan: Create a step-by-step execution plan
    3. Execute: Execute individual steps using tools
    4. Monitor: Assess execution progress and detect issues
    5. Recover: Develop recovery strategies when needed

    The program is designed to be optimized by GEPA, which can learn
    from trajectories across all these stages.

    Note: This currently only runs DSPy reasoning chains without actual
    gptme evaluation. Future phases will integrate with GptmeModule-style
    execution for complete end-to-end optimization.
    """

    def __init__(self, base_prompt: str = "You are a helpful AI assistant."):
        super().__init__()
        self.base_prompt = base_prompt
        self.analyze = dspy.ChainOfThought(TaskAnalysisSignature)
        self.plan = dspy.ChainOfThought(PlanningSignature)
        self.execute = dspy.ChainOfThought(ExecutionSignature)
        self.monitor = dspy.ChainOfThought(MonitoringSignature)
        self.recover = dspy.ChainOfThought(RecoverySignature)

    def forward(
        self,
        task_description: str,
        context: str,
        eval_spec: EvalSpec,
        available_tools: str = "shell, python, read, save, patch, browser",
    ) -> dspy.Prediction:
        """
        Execute a task through the multi-stage reasoning process.

        Args:
            task_description: The task to accomplish
            context: Context including files, environment, etc.
            eval_spec: Original evaluation specification
            available_tools: Tools available for execution

        Returns:
            Prediction containing full execution trajectory
        """
        try:
            # Stage 1: Analyze the task
            analysis = self.analyze(
                task_description=task_description,
                context=context,
                system_capabilities=available_tools,
            )

            # Stage 2: Create execution plan
            plan = self.plan(
                task_analysis=str(analysis),
                available_tools=available_tools,
            )

            # Stage 3-5: Execute, monitor, and recover as needed
            # For now, we'll execute a simplified version
            # Full implementation would iterate through plan steps
            execution_steps = (
                getattr(plan, "execution_steps", "") or "No execution plan generated"
            )
            execution = self.execute(
                step_description=execution_steps,
                current_state="Initial state",
                available_tools=available_tools,
            )

            monitoring = self.monitor(
                step_description=execution_steps,
                execution_result=str(execution),
                expected_outcome=getattr(execution, "expected_outcome", ""),
                success_criteria=getattr(plan, "success_criteria", ""),
            )

            # Build comprehensive response
            response_parts = [
                "# Task Analysis",
                f"Task Type: {getattr(analysis, 'task_type', 'N/A')}",
                f"Strategy: {getattr(analysis, 'approach_strategy', 'N/A')}",
                "",
                "# Execution Plan",
                str(getattr(plan, "execution_steps", "N/A")),
                "",
                "# Execution",
                f"Tool Selection: {getattr(execution, 'tool_selection', 'N/A')}",
                f"Actions: {getattr(execution, 'tool_invocation', 'N/A')}",
                "",
                "# Monitoring",
                f"Status: {getattr(monitoring, 'status', 'N/A')}",
                f"Progress: {getattr(monitoring, 'progress_assessment', 'N/A')}",
            ]

            return dspy.Prediction(
                response="\n".join(response_parts),
                analysis=analysis,
                plan=plan,
                execution=execution,
                monitoring=monitoring,
                eval_spec=eval_spec,
            )

        except Exception as e:
            logger.exception(f"Error in GptmeReasoningProgram: {e}")
            return dspy.Prediction(
                response=f"Error in reasoning program: {str(e)}",
                error=str(e),
                eval_spec=eval_spec,
            )

    def execute_with_recovery(
        self,
        step_description: str,
        current_state: str,
        available_tools: str,
        max_retries: int = 3,
    ) -> tuple[dspy.Prediction, bool]:
        """
        Execute a step with automatic recovery on failure.

        Returns:
            Tuple of (execution_result, success_flag)
        """
        previous_attempts: list[dict[str, Any]] = []

        for attempt in range(max_retries):
            # Execute step
            execution = self.execute(
                step_description=step_description,
                current_state=current_state,
                available_tools=available_tools,
            )

            # Monitor execution
            monitoring = self.monitor(
                step_description=step_description,
                execution_result=str(execution),
                expected_outcome=getattr(execution, "expected_outcome", ""),
                success_criteria="Step completes without errors",
            )

            status = getattr(monitoring, "status", "failure")

            if status in ["success", "partial_success"]:
                return execution, True

            # Attempt recovery
            if attempt < max_retries - 1:
                recovery = self.recover(
                    error_description=getattr(monitoring, "issues_detected", ""),
                    execution_context=current_state,
                    previous_attempts=str(previous_attempts),
                )
                previous_attempts.append(
                    {
                        "attempt": attempt + 1,
                        "error": getattr(monitoring, "issues_detected", ""),
                        "recovery": str(recovery),
                    }
                )

                # Update approach based on recovery strategy
                step_description = f"{step_description}\n\nRecovery Strategy: {getattr(recovery, 'recovery_strategy', '')}"

        return execution, False


def create_reasoning_program() -> GptmeReasoningProgram:
    """
    Factory function to create a reasoning program instance.

    This can be extended to support different configurations or variants.
    """
    return GptmeReasoningProgram()
