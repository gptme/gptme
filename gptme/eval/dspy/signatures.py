"""
DSPy signatures for gptme prompt optimization.

These signatures define the input-output structure for prompt optimization tasks.
"""

import dspy


class GptmeTaskSignature(dspy.Signature):
    """
    Signature for a gptme task execution.

    This represents the core interaction pattern: given a system prompt and task,
    produce a response that accomplishes the task.
    """

    system_prompt = dspy.InputField(
        desc="The system prompt that defines gptme's behavior and capabilities"
    )
    task_description = dspy.InputField(
        desc="The specific task or request from the user"
    )
    context = dspy.InputField(
        desc="Additional context like file contents, working directory, etc."
    )
    response = dspy.OutputField(
        desc="The assistant's response including tool usage and explanations"
    )


class PromptEvaluationSignature(dspy.Signature):
    """
    Signature for evaluating prompt quality.

    This is used by LLM judges to assess how well a system prompt performs.
    """

    original_prompt = dspy.InputField(desc="The original system prompt")
    task = dspy.InputField(desc="The task that was attempted")
    response = dspy.InputField(desc="The response generated with this prompt")
    expected_outcome = dspy.InputField(desc="What the ideal outcome should be")
    evaluation_criteria = dspy.InputField(
        desc="Specific criteria to evaluate (e.g., tool usage, accuracy, clarity)"
    )
    score = dspy.OutputField(
        desc="Numerical score from 1-10 indicating prompt effectiveness"
    )
    reasoning = dspy.OutputField(
        desc="Detailed explanation of the score and areas for improvement"
    )


class PromptImprovementSignature(dspy.Signature):
    """
    Signature for generating improved prompts.

    This is used to suggest specific improvements to system prompts.
    """

    current_prompt = dspy.InputField(desc="The current system prompt")
    performance_feedback = dspy.InputField(
        desc="Feedback about what went wrong or could be improved"
    )
    task_examples = dspy.InputField(
        desc="Examples of tasks where the prompt underperformed"
    )
    improvement_areas = dspy.InputField(
        desc="Specific areas to focus improvement on (e.g., tool usage, clarity, reasoning)"
    )
    improved_prompt = dspy.OutputField(desc="An improved version of the system prompt")
    changes_made = dspy.OutputField(desc="Summary of what changes were made and why")


class ToolUsageAnalysisSignature(dspy.Signature):
    """
    Signature for analyzing tool usage effectiveness.

    This helps evaluate whether the prompt leads to appropriate tool usage.
    """

    task = dspy.InputField(desc="The task that was given")
    tools_available = dspy.InputField(desc="List of tools that were available")
    tools_used = dspy.InputField(desc="List of tools that were actually used")
    execution_trace = dspy.InputField(desc="Sequence of tool calls and results")
    expected_tools = dspy.InputField(desc="Tools that should have been used ideally")

    efficiency_score = dspy.OutputField(
        desc="Score 1-10 for how efficiently tools were used"
    )
    correctness_score = dspy.OutputField(
        desc="Score 1-10 for whether the right tools were chosen"
    )
    analysis = dspy.OutputField(
        desc="Detailed analysis of tool usage patterns and suggestions"
    )
