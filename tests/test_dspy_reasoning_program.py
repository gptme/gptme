"""
Unit tests for the DSPy multi-stage reasoning program.

⚠️ DEPRECATED (2026-01-16): These tests cover the archived GptmeReasoningProgram.
The reasoning program was archived as part of GEPA Phase 3.2 cleanup because
it adds complexity without demonstrated benefit. See issue #790 for details.

These tests verify the reasoning program's signature classes, multi-stage flow,
error handling, and recovery logic - kept for historical reference.
"""

import warnings

import pytest

# Emit deprecation warning when running these tests
warnings.warn(
    "test_dspy_reasoning_program.py tests deprecated GptmeReasoningProgram. "
    "These tests are kept for historical reference only. See issue #790.",
    DeprecationWarning,
    stacklevel=1,
)

# Check if DSPy is available and handle import errors gracefully
try:
    from gptme.eval.dspy import _has_dspy  # fmt: skip

    if not _has_dspy():
        pytest.skip("DSPy not available", allow_module_level=True)
except (ImportError, ModuleNotFoundError):
    pytest.skip("DSPy module not available", allow_module_level=True)

# Try to import reasoning program components from archived location
try:
    import dspy

    from gptme.eval.dspy._archived.reasoning_program import (
        ExecutionSignature,
        GptmeReasoningProgram,
        MonitoringSignature,
        PlanningSignature,
        RecoverySignature,
        TaskAnalysisSignature,
        create_reasoning_program,
    )
    from gptme.eval.types import EvalSpec
except (ImportError, AttributeError) as e:
    pytest.skip(f"Reasoning program imports failed: {e}", allow_module_level=True)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-20241120"


# Fixtures
@pytest.fixture
def eval_spec():
    """Create a basic EvalSpec for testing."""
    return EvalSpec(
        name="test_task",
        prompt="Test task prompt",
        files={},
        run="echo 'test'",
        expect={},
    )


@pytest.fixture
def reasoning_program():
    """Create a reasoning program instance for testing."""
    return create_reasoning_program()


# Test Signature Classes
def test_task_analysis_signature():
    """Test TaskAnalysisSignature structure."""
    # Verify signature exists and has correct fields
    assert hasattr(TaskAnalysisSignature, "__doc__")
    assert "task_description" in TaskAnalysisSignature.input_fields
    assert "context" in TaskAnalysisSignature.input_fields
    assert "system_capabilities" in TaskAnalysisSignature.input_fields
    assert "task_type" in TaskAnalysisSignature.output_fields
    assert "key_requirements" in TaskAnalysisSignature.output_fields
    assert "constraints" in TaskAnalysisSignature.output_fields
    assert "approach_strategy" in TaskAnalysisSignature.output_fields


def test_planning_signature():
    """Test PlanningSignature structure."""
    assert hasattr(PlanningSignature, "__doc__")
    assert "task_analysis" in PlanningSignature.input_fields
    assert "available_tools" in PlanningSignature.input_fields
    assert "execution_steps" in PlanningSignature.output_fields
    assert "dependencies" in PlanningSignature.output_fields
    assert "success_criteria" in PlanningSignature.output_fields


def test_execution_signature():
    """Test ExecutionSignature structure."""
    assert hasattr(ExecutionSignature, "__doc__")
    assert "step_description" in ExecutionSignature.input_fields
    assert "current_state" in ExecutionSignature.input_fields
    assert "available_tools" in ExecutionSignature.input_fields
    assert "tool_selection" in ExecutionSignature.output_fields
    assert "tool_invocation" in ExecutionSignature.output_fields
    assert "expected_outcome" in ExecutionSignature.output_fields


def test_monitoring_signature():
    """Test MonitoringSignature structure."""
    assert hasattr(MonitoringSignature, "__doc__")
    assert "step_description" in MonitoringSignature.input_fields
    assert "execution_result" in MonitoringSignature.input_fields
    assert "expected_outcome" in MonitoringSignature.input_fields
    assert "success_criteria" in MonitoringSignature.input_fields
    assert "status" in MonitoringSignature.output_fields
    assert "progress_assessment" in MonitoringSignature.output_fields
    assert "issues_detected" in MonitoringSignature.output_fields
    assert "next_action" in MonitoringSignature.output_fields


def test_recovery_signature():
    """Test RecoverySignature structure."""
    assert hasattr(RecoverySignature, "__doc__")
    assert "error_description" in RecoverySignature.input_fields
    assert "execution_context" in RecoverySignature.input_fields
    assert "previous_attempts" in RecoverySignature.input_fields
    assert "error_analysis" in RecoverySignature.output_fields
    assert "recovery_strategy" in RecoverySignature.output_fields
    assert "alternative_approach" in RecoverySignature.output_fields
    assert "preventive_measures" in RecoverySignature.output_fields


# Test GptmeReasoningProgram Class
def test_reasoning_program_initialization():
    """Test reasoning program initialization."""
    program = GptmeReasoningProgram()
    assert program.base_prompt == "You are a helpful AI assistant."
    assert hasattr(program, "analyze")
    assert hasattr(program, "plan")
    assert hasattr(program, "execute")
    assert hasattr(program, "monitor")
    assert hasattr(program, "recover")

    # Test custom base prompt
    custom_program = GptmeReasoningProgram(base_prompt="Custom prompt")
    assert custom_program.base_prompt == "Custom prompt"


def test_reasoning_program_modules():
    """Test that reasoning program has all required modules."""
    program = create_reasoning_program()
    assert isinstance(program.analyze, dspy.ChainOfThought)
    assert isinstance(program.plan, dspy.ChainOfThought)
    assert isinstance(program.execute, dspy.ChainOfThought)
    assert isinstance(program.monitor, dspy.ChainOfThought)
    assert isinstance(program.recover, dspy.ChainOfThought)


@pytest.mark.skip(
    reason="Requires LLM API access - use for integration testing with --eval flag"
)
def test_reasoning_program_forward_success(reasoning_program, eval_spec):
    """Test successful execution through all stages."""
    result = reasoning_program.forward(
        task_description="Write a simple hello world script",
        context="Empty directory",
        eval_spec=eval_spec,
        available_tools="shell, python, save",
    )

    # Verify prediction structure
    assert hasattr(result, "response")
    assert hasattr(result, "analysis")
    assert hasattr(result, "plan")
    assert hasattr(result, "execution")
    assert hasattr(result, "monitoring")
    assert result.eval_spec == eval_spec

    # Verify response contains expected sections
    response = result.response
    assert "# Task Analysis" in response
    assert "# Execution Plan" in response
    assert "# Execution" in response
    assert "# Monitoring" in response


def test_reasoning_program_forward_error_handling(reasoning_program, eval_spec):
    """Test error handling in forward method."""
    # Test with invalid inputs that should trigger error handling
    result = reasoning_program.forward(
        task_description=None,  # Invalid input
        context="",
        eval_spec=eval_spec,
    )

    # Should return error prediction
    assert hasattr(result, "response")
    assert "Error in reasoning program:" in result.response or hasattr(result, "error")


@pytest.mark.skip(
    reason="Requires LLM API access - use for integration testing with --eval flag"
)
def test_reasoning_program_execute_with_recovery_success(reasoning_program):
    """Test execute_with_recovery with successful execution."""
    execution, success = reasoning_program.execute_with_recovery(
        step_description="Print hello world",
        current_state="Initial state",
        available_tools="python",
        max_retries=3,
    )

    assert success is True
    assert execution is not None


@pytest.mark.skip(
    reason="Requires LLM API access - use for integration testing with --eval flag"
)
def test_reasoning_program_execute_with_recovery_failure(reasoning_program):
    """Test execute_with_recovery with failure and recovery attempts."""
    # Simulate a failing step
    execution, success = reasoning_program.execute_with_recovery(
        step_description="Do something impossible",
        current_state="Current state",
        available_tools="none",
        max_retries=2,
    )

    # Should attempt recovery but eventually fail
    # Note: Actual behavior depends on LLM responses
    assert execution is not None


def test_create_reasoning_program_factory():
    """Test the factory function creates a valid program."""
    program = create_reasoning_program()
    assert isinstance(program, GptmeReasoningProgram)
    assert hasattr(program, "forward")
    assert hasattr(program, "execute_with_recovery")


# Integration Tests
@pytest.mark.skip(
    reason="Requires LLM API access - use for integration testing with --eval flag"
)
def test_multi_stage_flow_integration(reasoning_program, eval_spec):
    """Test complete multi-stage flow from analysis to monitoring."""
    # Configure LLM
    dspy.configure(lm=dspy.LM(model=DEFAULT_MODEL))

    result = reasoning_program.forward(
        task_description="Create a Python script that calculates factorial",
        context="Empty directory with Python available",
        eval_spec=eval_spec,
        available_tools="python, save, shell",
    )

    # Verify all stages produced outputs
    assert hasattr(result, "analysis")
    assert hasattr(result, "plan")
    assert hasattr(result, "execution")
    assert hasattr(result, "monitoring")

    # Verify analysis contains expected fields
    analysis = result.analysis
    assert hasattr(analysis, "task_type") or "task_type" in str(analysis)
    assert hasattr(analysis, "approach_strategy") or "approach_strategy" in str(
        analysis
    )

    # Verify plan contains steps
    plan = result.plan
    assert hasattr(plan, "execution_steps") or "execution_steps" in str(plan)

    # Verify execution contains tool selection
    execution = result.execution
    assert hasattr(execution, "tool_selection") or "tool_selection" in str(execution)

    # Verify monitoring contains status
    monitoring = result.monitoring
    assert hasattr(monitoring, "status") or "status" in str(monitoring)


@pytest.mark.skip(
    reason="Requires LLM API access - use for integration testing with --eval flag"
)
def test_recovery_flow_integration(reasoning_program):
    """Test recovery flow when execution fails."""
    dspy.configure(lm=dspy.LM(model=DEFAULT_MODEL))

    # Execute with recovery on a deliberately difficult task
    execution, success = reasoning_program.execute_with_recovery(
        step_description="Access non-existent file",
        current_state="Working directory",
        available_tools="shell",
        max_retries=2,
    )

    # Should attempt recovery
    assert execution is not None
    # Success may vary based on LLM's ability to recover


# Edge Cases and Error Handling
def test_reasoning_program_empty_inputs(reasoning_program, eval_spec):
    """Test handling of empty inputs."""
    result = reasoning_program.forward(
        task_description="",
        context="",
        eval_spec=eval_spec,
        available_tools="",
    )

    # Should handle empty inputs gracefully
    assert result is not None
    assert hasattr(result, "response")


def test_reasoning_program_very_long_inputs(reasoning_program, eval_spec):
    """Test handling of very long inputs."""
    long_description = "Do something " * 1000  # Very long description
    result = reasoning_program.forward(
        task_description=long_description,
        context="context",
        eval_spec=eval_spec,
    )

    # Should handle long inputs without crashing
    assert result is not None


@pytest.mark.skip(
    reason="integration test: requires DSPy LM configuration, run with --eval"
)
def test_execute_with_recovery_max_retries(reasoning_program):
    """Test that execute_with_recovery respects max_retries."""
    # Test with 0 retries - should only try once
    execution, success = reasoning_program.execute_with_recovery(
        step_description="test step",
        current_state="state",
        available_tools="tools",
        max_retries=1,
    )

    # Should complete without error even with limited retries
    assert execution is not None
