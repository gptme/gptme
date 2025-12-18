from unittest.mock import patch

import pytest

from gptme.tools.subagent import SubtaskDef, _subagents, subagent


def test_planner_mode_requires_subtasks():
    """Test that planner mode requires subtasks parameter."""
    with pytest.raises(ValueError, match="Planner mode requires subtasks"):
        subagent(agent_id="test-planner", prompt="Test task", mode="planner")


def test_planner_mode_spawns_executors():
    """Test that planner mode spawns executor subagents."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "First task"},
        {"id": "task2", "description": "Second task"},
    ]

    subagent(
        agent_id="test-planner",
        prompt="Overall context",
        mode="planner",
        subtasks=subtasks,
    )

    # Should have spawned 2 executor subagents
    assert len(_subagents) == initial_count + 2

    # Check executor IDs are correctly formed
    executor_ids = [s.agent_id for s in _subagents[-2:]]
    assert "test-planner-task1" in executor_ids
    assert "test-planner-task2" in executor_ids


def test_planner_mode_executor_prompts():
    """Test that executor prompts include context and subtask description."""
    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "Do something specific"}
    ]

    subagent(
        agent_id="test-planner",
        prompt="This is the overall context",
        mode="planner",
        subtasks=subtasks,
    )

    # Check the spawned executor has correct prompt
    executor = _subagents[-1]
    assert "This is the overall context" in executor.prompt
    assert "Do something specific" in executor.prompt


def test_executor_mode_still_works():
    """Test that default executor mode still works as before."""
    initial_count = len(_subagents)

    subagent(agent_id="test-executor", prompt="Simple task")

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    # Check basic properties
    executor = _subagents[-1]
    assert executor.agent_id == "test-executor"
    assert executor.prompt == "Simple task"


def test_planner_parallel_mode():
    """Test that parallel mode spawns all executors at once."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "First parallel task"},
        {"id": "task2", "description": "Second parallel task"},
        {"id": "task3", "description": "Third parallel task"},
    ]

    subagent(
        agent_id="test-parallel",
        prompt="Parallel execution test",
        mode="planner",
        subtasks=subtasks,
        execution_mode="parallel",
    )

    # All 3 executors should be spawned
    assert len(_subagents) == initial_count + 3

    # Check all have correct ID prefix
    executor_ids = [s.agent_id for s in _subagents[-3:]]
    assert all(eid.startswith("test-parallel-") for eid in executor_ids)


def test_planner_sequential_mode():
    """Test that sequential mode spawns executors one by one."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "seq1", "description": "First sequential task"},
        {"id": "seq2", "description": "Second sequential task"},
    ]

    # Mock _create_subagent_thread to avoid real chat sessions
    # Sequential mode blocks with t.join(), so we need threads to complete instantly
    with patch("gptme.tools.subagent._create_subagent_thread"):
        subagent(
            agent_id="test-sequential",
            prompt="Sequential execution test",
            mode="planner",
            subtasks=subtasks,
            execution_mode="sequential",
        )

    # Should spawn 2 executors
    assert len(_subagents) == initial_count + 2

    # Check IDs are correctly formed
    executor_ids = [s.agent_id for s in _subagents[-2:]]
    assert "test-sequential-seq1" in executor_ids
    assert "test-sequential-seq2" in executor_ids


def test_planner_default_is_parallel():
    """Test that default execution mode is parallel."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "default1", "description": "Default mode test"}
    ]

    # Don't specify execution_mode, should default to parallel
    subagent(
        agent_id="test-default",
        prompt="Default mode test",
        mode="planner",
        subtasks=subtasks,
    )

    # Should spawn 1 executor (parallel is default)
    assert len(_subagents) == initial_count + 1


def test_context_mode_default_is_full():
    """Test that default context_mode is 'full'."""
    initial_count = len(_subagents)

    subagent(agent_id="test-full", prompt="Test with full context")

    # Should spawn 1 executor with full context
    assert len(_subagents) == initial_count + 1


def test_context_mode_instructions_only():
    """Test that instructions-only mode works with minimal context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-instructions-only",
        prompt="Simple computation task",
        context_mode="instructions-only",
    )

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    executor = _subagents[-1]
    assert executor.agent_id == "test-instructions-only"
    assert executor.prompt == "Simple computation task"


def test_context_mode_selective_requires_context_include():
    """Test that selective mode requires context_include parameter."""
    with pytest.raises(ValueError, match="context_include parameter required"):
        subagent(
            agent_id="test-selective-error",
            prompt="Test task",
            context_mode="selective",
        )


def test_context_mode_selective_with_tools():
    """Test selective mode with tools context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-tools",
        prompt="Use tools to complete task",
        context_mode="selective",
        context_include=["tools"],
    )

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    executor = _subagents[-1]
    assert executor.agent_id == "test-selective-tools"


def test_context_mode_selective_with_agent():
    """Test selective mode with agent context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-agent",
        prompt="Task requiring agent identity",
        context_mode="selective",
        context_include=["agent"],
    )

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1


def test_context_mode_selective_with_workspace():
    """Test selective mode with workspace context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-workspace",
        prompt="Task requiring workspace files",
        context_mode="selective",
        context_include=["workspace"],
    )

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1


def test_context_mode_selective_multiple_components():
    """Test selective mode with multiple context components."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-multiple",
        prompt="Complex task needing multiple contexts",
        context_mode="selective",
        context_include=["agent", "tools", "workspace"],
    )

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1


def test_planner_mode_with_context_modes():
    """Test that planner mode works with context modes."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "Simple computation"},
        {"id": "task2", "description": "Complex analysis"},
    ]

    # Planner with instructions-only context
    subagent(
        agent_id="test-planner-context",
        prompt="Overall task context",
        mode="planner",
        subtasks=subtasks,
        context_mode="instructions-only",
    )

    # Should spawn 2 executors
    assert len(_subagents) == initial_count + 2


# Phase 1 Tests: Subprocess mode, callbacks, batch execution


def test_subagent_with_use_subprocess():
    """Test that use_subprocess parameter is accepted."""
    import inspect

    sig = inspect.signature(subagent)

    # Verify subprocess parameter exists (callbacks removed in favor of hooks)
    assert "use_subprocess" in sig.parameters

    # Verify default value
    assert sig.parameters["use_subprocess"].default is False

    # Callbacks have been removed - completion is now delivered via LOOP_CONTINUE hook
    assert "on_complete" not in sig.parameters
    assert "on_progress" not in sig.parameters


def test_subagent_batch_creates_batch_job():
    """Test that subagent_batch returns a BatchJob with correct structure."""
    from gptme.tools.subagent import BatchJob, _subagents, subagent_batch

    # Clear any previous subagents
    _subagents.clear()

    # Mock to prevent actual subagent execution
    with patch("gptme.tools.subagent.subagent") as mock_subagent:
        job = subagent_batch(
            [
                ("agent1", "prompt1"),
                ("agent2", "prompt2"),
            ]
        )

        # Verify BatchJob structure
        assert isinstance(job, BatchJob)
        assert job.agent_ids == ["agent1", "agent2"]
        assert len(job.results) == 0  # No results yet

        # Verify subagent was called for each task
        assert mock_subagent.call_count == 2


def test_batch_job_is_complete():
    """Test BatchJob.is_complete() method."""
    from gptme.tools.subagent import BatchJob, ReturnType

    job = BatchJob(agent_ids=["a1", "a2"])
    assert not job.is_complete()

    job.results["a1"] = ReturnType("success", "done")
    assert not job.is_complete()

    job.results["a2"] = ReturnType("success", "done")
    assert job.is_complete()


def test_batch_job_get_completed():
    """Test BatchJob.get_completed() method."""
    from gptme.tools.subagent import BatchJob, ReturnType

    job = BatchJob(agent_ids=["a1", "a2"])

    # Add one result
    job.results["a1"] = ReturnType("success", "result1")

    completed = job.get_completed()
    assert len(completed) == 1
    assert "a1" in completed
    assert completed["a1"]["status"] == "success"


def test_subagent_execution_mode_field():
    """Test that Subagent has execution_mode field."""
    import threading
    from pathlib import Path

    from gptme.tools.subagent import Subagent

    t = threading.Thread(target=lambda: None)
    sa = Subagent(
        agent_id="test",
        prompt="test prompt",
        thread=t,
        logdir=Path("/tmp"),
        model=None,
        execution_mode="thread",
    )
    assert sa.execution_mode == "thread"

    sa2 = Subagent(
        agent_id="test2",
        prompt="test prompt",
        thread=None,
        logdir=Path("/tmp"),
        model=None,
        execution_mode="subprocess",
        process=None,
    )
    assert sa2.execution_mode == "subprocess"


def test_subagent_status_returns_dict():
    """Test that subagent_status returns a dictionary."""
    from gptme.tools.subagent import subagent, subagent_status

    # First create a subagent
    subagent(agent_id="test-status-agent", prompt="Simple test")

    # Get status
    status = subagent_status("test-status-agent")
    assert isinstance(status, dict)
    assert "status" in status


def test_subagent_status_unknown_agent():
    """Test that subagent_status raises ValueError for unknown agents."""
    import pytest

    from gptme.tools.subagent import subagent_status

    with pytest.raises(ValueError, match="not found"):
        subagent_status("nonexistent-agent-xyz")


def test_subagent_wait_basic():
    """Test that subagent_wait can wait for completion."""
    from gptme.tools.subagent import subagent, subagent_wait

    # Create a simple subagent
    subagent(agent_id="test-wait-agent", prompt="Simple quick task")

    # Wait with a short timeout (subagent should complete quickly for simple prompt)
    # Note: This test may take up to timeout seconds
    result = subagent_wait("test-wait-agent", timeout=30)
    assert isinstance(result, dict)
    # Status should be either success, failure, or running (if it takes too long)
    assert result.get("status") in ["success", "failure", "running", "timeout"]


def test_subagent_read_log_returns_string():
    """Test that subagent_read_log returns a string with log content."""
    from gptme.tools.subagent import subagent, subagent_read_log

    # Create a subagent first
    subagent(agent_id="test-log-agent", prompt="Log test task")

    # Read the log
    result = subagent_read_log("test-log-agent")
    assert isinstance(result, str)
    # The result should contain some log content
    assert len(result) > 0


# Subprocess mode execution tests (per Erik's review comment)


def test_subprocess_mode_creates_process():
    """Test that subprocess mode actually creates a subprocess.Popen object."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.subagent import _subagents, subagent

    # Clear previous subagents
    _subagents.clear()

    # Mock subprocess.Popen to avoid actually running gptme
    mock_process = MagicMock()
    mock_process.poll.return_value = None  # Process still running

    with patch("gptme.tools.subagent.subprocess.Popen", return_value=mock_process):
        subagent(
            agent_id="test-subprocess",
            prompt="Simple test task",
            use_subprocess=True,
        )

        # Verify subprocess was created
        assert len(_subagents) >= 1
        sa = next((s for s in _subagents if s.agent_id == "test-subprocess"), None)
        assert sa is not None
        assert sa.execution_mode == "subprocess"
        assert sa.process is mock_process


def test_subprocess_mode_command_construction():
    """Test that subprocess mode constructs the correct gptme command."""
    from unittest.mock import MagicMock, patch

    captured_cmd: list[str] = []

    def capture_popen(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.poll.return_value = None
        return mock

    from gptme.tools.subagent import _subagents, subagent

    _subagents.clear()

    with patch("gptme.tools.subagent.subprocess.Popen", side_effect=capture_popen):
        subagent(
            agent_id="test-cmd",
            prompt="Test prompt for command",
            use_subprocess=True,
        )

    # Verify command structure
    assert "-m" in captured_cmd
    assert "gptme" in captured_cmd
    assert "-n" in captured_cmd  # Non-interactive
    assert "--no-confirm" in captured_cmd
    assert "Test prompt for command" in captured_cmd


def test_subprocess_mode_completion_stored():
    """Test that subprocess completion results are stored in cache."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagents,
        subagent,
        subagent_status,
    )

    _subagents.clear()
    _subagent_results.clear()

    # Mock process that completes successfully
    mock_process = MagicMock()
    mock_process.poll.return_value = 0  # Completed
    mock_process.communicate.return_value = ("Success output", "")

    with patch("gptme.tools.subagent.subprocess.Popen", return_value=mock_process):
        subagent(
            agent_id="test-complete",
            prompt="Task to complete",
            use_subprocess=True,
        )

    # Give monitor thread time to process (it runs in daemon thread)
    import time

    time.sleep(0.5)

    # Verify status can be retrieved
    status = subagent_status("test-complete")
    assert isinstance(status, dict)
    assert "status" in status
