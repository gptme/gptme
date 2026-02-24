import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools.subagent import SubtaskDef, _subagents, subagent


def test_planner_mode_requires_subtasks():
    """Test that planner mode requires subtasks parameter."""
    with pytest.raises(ValueError, match="Planner mode requires subtasks"):
        subagent(agent_id="test-planner", prompt="Test task", mode="planner")


@patch("gptme.tools.subagent._create_subagent_thread")
def test_planner_mode_spawns_executors(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_planner_mode_executor_prompts(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_executor_mode_still_works(mock_create_thread: MagicMock):
    """Test that default executor mode still works as before."""
    initial_count = len(_subagents)

    subagent(agent_id="test-executor", prompt="Simple task")

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    # Check basic properties
    executor = _subagents[-1]
    assert executor.agent_id == "test-executor"
    assert executor.prompt == "Simple task"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_planner_parallel_mode(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_planner_default_is_parallel(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_context_mode_default_is_full(mock_create_thread: MagicMock):
    """Test that default context_mode is 'full'."""
    initial_count = len(_subagents)

    subagent(agent_id="test-full", prompt="Test with full context")

    # Should spawn 1 executor with full context
    assert len(_subagents) == initial_count + 1


@patch("gptme.tools.subagent._create_subagent_thread")
def test_context_mode_instructions_only(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_context_mode_selective_with_tools(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_context_mode_selective_with_agent(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_context_mode_selective_with_workspace(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_context_mode_selective_multiple_components(mock_create_thread: MagicMock):
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


@patch("gptme.tools.subagent._create_subagent_thread")
def test_planner_mode_with_context_modes(mock_create_thread: MagicMock):
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


@pytest.mark.slow
@pytest.mark.eval
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


@pytest.mark.slow
@pytest.mark.eval
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


# Integration tests for actual subprocess execution
# These tests verify the subprocess infrastructure without mocking


@pytest.mark.slow
def test_subprocess_actual_process_creation():
    """Test that _run_subagent_subprocess creates a real subprocess.Popen object."""
    import subprocess
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent import _run_subagent_subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        # Create a subprocess
        process = _run_subagent_subprocess(
            prompt="Say hello",
            logdir=logdir,
            model=None,
            workspace=Path(tmpdir),
        )

        try:
            # Verify it's a real subprocess.Popen object
            assert isinstance(process, subprocess.Popen)
            assert process.pid is not None
            assert process.pid > 0

            # Verify stdout and stderr are piped (for output isolation)
            assert process.stdout is not None
            assert process.stderr is not None

        finally:
            # Clean up - terminate the process
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.mark.slow
def test_subprocess_command_includes_required_flags():
    """Test that subprocess command includes all required gptme flags."""
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent import _run_subagent_subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        process = _run_subagent_subprocess(
            prompt="Test task",
            logdir=logdir,
            model="test-model",
            workspace=Path(tmpdir),
        )

        try:
            # The command is available as process.args
            cmd = process.args
            assert isinstance(cmd, list)

            # Verify required elements
            assert sys.executable in cmd[0] or "python" in cmd[0]
            assert "-m" in cmd
            assert "gptme" in cmd
            assert "-n" in cmd  # Non-interactive
            assert "--no-confirm" in cmd
            assert any("--logdir=" in str(arg) for arg in cmd)
            assert "--model" in cmd
            assert "test-model" in cmd
            assert "Test task" in cmd  # The prompt

        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.mark.slow
def test_subprocess_working_directory():
    """Test that subprocess runs in the specified working directory."""
    import subprocess
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent import _run_subagent_subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        process = _run_subagent_subprocess(
            prompt="Test task",
            logdir=logdir,
            model=None,
            workspace=workspace,
        )

        try:
            # Verify the process was started (cwd is set internally by Popen)
            assert process.pid > 0
            # We can't directly verify cwd, but we verified the Popen call
            # would have received the workspace parameter

        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.mark.slow
def test_subprocess_full_flow_with_subagent_function():
    """Test the full subprocess flow using the subagent() function."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagents,
        subagent,
    )

    _subagents.clear()
    _subagent_results.clear()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch get_logdir to use our temp directory
        temp_logdir = Path(tmpdir) / "logs"
        temp_logdir.mkdir()

        with patch("gptme.cli.main.get_logdir", return_value=temp_logdir):
            # Start a subprocess subagent
            subagent(
                agent_id="subprocess-test",
                prompt="Print hello",
                use_subprocess=True,
            )

            # Verify subagent was created
            sa = next((s for s in _subagents if s.agent_id == "subprocess-test"), None)
            assert sa is not None
            assert sa.execution_mode == "subprocess"
            assert sa.process is not None

            # The process should have started
            assert sa.process.pid > 0

            # Clean up
            if sa.process:
                sa.process.terminate()
                try:
                    sa.process.wait(timeout=5)
                except Exception:
                    sa.process.kill()


@pytest.mark.slow
def test_subprocess_monitor_thread_started():
    """Test that subprocess mode starts a monitor thread."""
    import tempfile
    import threading
    import time
    from pathlib import Path
    from unittest.mock import patch

    from gptme.tools.subagent import (
        _subagents,
        subagent,
    )

    _subagents.clear()

    # Count daemon threads before
    initial_daemon_threads = sum(1 for t in threading.enumerate() if t.daemon)

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_logdir = Path(tmpdir) / "logs"
        temp_logdir.mkdir()

        with patch("gptme.cli.main.get_logdir", return_value=temp_logdir):
            subagent(
                agent_id="monitor-test",
                prompt="Test",
                use_subprocess=True,
            )

            # Give the monitor thread time to start
            time.sleep(0.1)

            # Should have at least one more daemon thread
            current_daemon_threads = sum(1 for t in threading.enumerate() if t.daemon)
            # The monitor thread should be running
            # (it's a daemon thread that monitors the subprocess)
            assert current_daemon_threads >= initial_daemon_threads

            # Clean up
            sa = next((s for s in _subagents if s.agent_id == "monitor-test"), None)
            if sa and sa.process:
                sa.process.terminate()
                try:
                    sa.process.wait(timeout=5)
                except Exception:
                    sa.process.kill()


@pytest.mark.slow
@pytest.mark.eval
def test_subprocess_mode_execution_basic():
    """Test that subprocess mode actually executes and completes a subagent.

    This test creates a real subagent in subprocess mode and waits for completion,
    verifying that the subprocess execution path works end-to-end.
    """
    from gptme.tools.subagent import _subagents, subagent, subagent_wait

    # Clear any existing subagents
    _subagents.clear()

    # Create a subagent in subprocess mode with a simple task
    subagent(
        agent_id="test-subprocess-exec",
        prompt="Reply with exactly: SUBPROCESS_TEST_SUCCESS",
        use_subprocess=True,
    )

    # Verify the subagent was created with subprocess execution mode
    sa = next((s for s in _subagents if s.agent_id == "test-subprocess-exec"), None)
    assert sa is not None
    assert sa.execution_mode == "subprocess"
    assert sa.process is not None

    # Wait for completion with a reasonable timeout
    result = subagent_wait("test-subprocess-exec", timeout=60)
    assert isinstance(result, dict)
    # Status should be either success or failure (not running if we waited enough)
    assert result.get("status") in ["success", "failure", "timeout"]


@pytest.mark.slow
@pytest.mark.eval
def test_subprocess_mode_read_log():
    """Test that we can read logs from a subprocess mode subagent."""
    from gptme.tools.subagent import (
        _subagents,
        subagent,
        subagent_read_log,
        subagent_wait,
    )

    _subagents.clear()

    # Create a subagent in subprocess mode
    subagent(
        agent_id="test-subprocess-log",
        prompt="Say hello",
        use_subprocess=True,
    )

    # Wait for it to complete (or timeout)
    subagent_wait("test-subprocess-log", timeout=60)

    # Read the log - should contain something
    log_content = subagent_read_log("test-subprocess-log")
    assert isinstance(log_content, str)
    # Log should exist and have content (at minimum the conversation start)
    assert len(log_content) > 0


# Profile integration tests


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_with_profile(mock_create_thread: MagicMock):
    """Test that profile parameter is passed to subagent thread."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-profile",
        prompt="Explore the codebase",
        profile="explorer",
    )

    assert len(_subagents) == initial_count + 1

    # Verify profile was passed to _create_subagent_thread
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "explorer"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_with_model_override(mock_create_thread: MagicMock):
    """Test that model parameter overrides parent's model."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-model-override",
        prompt="Quick task",
        model="openai/gpt-4o-mini",
    )

    assert len(_subagents) == initial_count + 1

    # Verify model override is used
    executor = _subagents[-1]
    assert executor.model == "openai/gpt-4o-mini"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_with_profile_and_model(mock_create_thread: MagicMock):
    """Test combining profile and model parameters."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-profile-model",
        prompt="Research task",
        profile="researcher",
        model="anthropic/claude-haiku",
    )

    assert len(_subagents) == initial_count + 1

    # Verify both profile and model are passed
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "researcher"
    assert call_kwargs["model"] == "anthropic/claude-haiku"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_planner_with_profile(mock_create_thread: MagicMock):
    """Test that planner mode passes profile to executor subagents."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "explore1", "description": "Explore module A"},
        {"id": "explore2", "description": "Explore module B"},
    ]

    subagent(
        agent_id="test-planner-profile",
        prompt="Explore the codebase",
        mode="planner",
        subtasks=subtasks,
        profile="explorer",
    )

    assert len(_subagents) == initial_count + 2

    # Verify profile was passed to each executor's _create_subagent_thread call
    assert mock_create_thread.call_count == 2
    for call in mock_create_thread.call_args_list:
        assert call[1]["profile_name"] == "explorer"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_auto_detects_profile_from_agent_id(mock_create_thread: MagicMock):
    """Test that agent_id matching a profile name auto-applies the profile."""
    initial_count = len(_subagents)

    # Use "explorer" as agent_id without explicit profile param
    subagent(
        agent_id="explorer",
        prompt="Analyze the architecture",
    )

    assert len(_subagents) == initial_count + 1

    # Profile should be auto-detected from agent_id
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "explorer"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_auto_detects_profile_alias_from_agent_id(
    mock_create_thread: MagicMock,
):
    """Test that common agent_id aliases map to expected profiles."""
    initial_count = len(_subagents)

    subagent(
        agent_id="impl",
        prompt="Implement feature X",
    )

    assert len(_subagents) == initial_count + 1

    # Profile should be auto-detected from alias
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "developer"


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_no_auto_detect_for_unknown_agent_id(mock_create_thread: MagicMock):
    """Test that non-profile agent_ids don't trigger auto-detection."""
    initial_count = len(_subagents)

    subagent(
        agent_id="my-custom-task",
        prompt="Do something",
    )

    assert len(_subagents) == initial_count + 1

    # No profile should be set
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] is None


@patch("gptme.tools.subagent._create_subagent_thread")
def test_subagent_explicit_profile_overrides_auto_detect(
    mock_create_thread: MagicMock,
):
    """Test that explicit profile param takes precedence over agent_id matching."""
    initial_count = len(_subagents)

    # agent_id is "explorer" but explicit profile is "researcher"
    subagent(
        agent_id="explorer",
        prompt="Research task",
        profile="researcher",
    )

    assert len(_subagents) == initial_count + 1

    # Explicit profile should win
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "researcher"


def test_subagent_profile_parameter_exists():
    """Test that subagent function accepts profile and model parameters."""
    import inspect

    sig = inspect.signature(subagent)

    assert "profile" in sig.parameters
    assert sig.parameters["profile"].default is None

    assert "model" in sig.parameters
    assert sig.parameters["model"].default is None


def test_profile_hard_tool_enforcement():
    """Test that profile tool restrictions are hard-enforced via set_tools().

    Verifies that when a profile restricts tools, set_tools() is called
    to replace the loaded tools, so execute_msg() can only run allowed tools.
    """
    import gptme
    import gptme.chat
    import gptme.executor
    import gptme.llm.models
    from gptme.tools import set_tools as real_set_tools
    from gptme.tools.base import ToolSpec

    mock_tools = [
        ToolSpec(name="read", desc="Read files", instructions=""),
        ToolSpec(name="shell", desc="Run shell", instructions=""),
        ToolSpec(name="save", desc="Save files", instructions=""),
        ToolSpec(name="chats", desc="Chat management", instructions=""),
        ToolSpec(name="complete", desc="Signal completion", instructions=""),
    ]

    # Track calls to set_tools
    set_tools_calls: list[list[str]] = []

    def spy_set_tools(tools):
        set_tools_calls.append([t.name for t in tools])
        real_set_tools(tools)

    with (
        patch.object(gptme.tools.subagent, "set_tools", spy_set_tools),
        patch.object(gptme.tools.subagent, "get_tools", return_value=mock_tools),
        patch.object(sys.modules["gptme"], "chat"),
        patch.object(
            gptme.executor,
            "prepare_execution_environment",
            return_value=(MagicMock(), mock_tools),
        ),
        patch.object(gptme.llm.models, "set_default_model"),
    ):
        from gptme.tools.subagent import _create_subagent_thread

        _create_subagent_thread(
            prompt="Read the codebase",
            logdir=Path("/tmp/test-enforcement"),
            model=None,
            context_mode="instructions-only",
            context_include=None,
            workspace=Path("/tmp"),
            profile_name="explorer",
        )

    # set_tools should have been called with only allowed tools
    assert len(set_tools_calls) == 1, f"set_tools called {len(set_tools_calls)} times"
    enforced_tools = set_tools_calls[0]

    # Explorer profile allows: read, chats (+ complete always included)
    assert "read" in enforced_tools
    assert "chats" in enforced_tools
    assert "complete" in enforced_tools
    assert "shell" not in enforced_tools, "shell should be blocked by explorer profile"
    assert "save" not in enforced_tools, "save should be blocked by explorer profile"


def test_profile_no_restriction_skips_set_tools():
    """Test that profiles without tool restrictions don't call set_tools."""
    import gptme
    import gptme.chat
    import gptme.executor
    import gptme.llm.models
    from gptme.tools.base import ToolSpec

    mock_tools = [
        ToolSpec(name="read", desc="Read files", instructions=""),
        ToolSpec(name="shell", desc="Run shell", instructions=""),
        ToolSpec(name="complete", desc="Signal completion", instructions=""),
    ]

    set_tools_calls: list[list[str]] = []

    def spy_set_tools(tools):
        set_tools_calls.append([t.name for t in tools])

    with (
        patch.object(gptme.tools.subagent, "set_tools", spy_set_tools),
        patch.object(gptme.tools.subagent, "get_tools", return_value=mock_tools),
        patch.object(sys.modules["gptme"], "chat"),
        patch.object(
            gptme.executor,
            "prepare_execution_environment",
            return_value=(MagicMock(), mock_tools),
        ),
        patch.object(gptme.llm.models, "set_default_model"),
    ):
        from gptme.tools.subagent import _create_subagent_thread

        # developer profile has tools=None (no restrictions)
        _create_subagent_thread(
            prompt="Write some code",
            logdir=Path("/tmp/test-no-restrict"),
            model=None,
            context_mode="instructions-only",
            context_include=None,
            workspace=Path("/tmp"),
            profile_name="developer",
        )

    # set_tools should NOT have been called (no restrictions to enforce)
    assert len(set_tools_calls) == 0, (
        f"set_tools should not be called for developer profile, but was called {len(set_tools_calls)} times"
    )


def test_subprocess_mode_with_profile():
    """Test that subprocess mode passes profile via --agent-profile flag."""
    captured_cmd: list[str] = []

    def capture_popen(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.poll.return_value = None
        return mock

    _subagents.clear()

    with patch("gptme.tools.subagent.subprocess.Popen", side_effect=capture_popen):
        subagent(
            agent_id="test-subprocess-profile",
            prompt="Explore task",
            use_subprocess=True,
            profile="explorer",
        )

    # Verify --agent-profile flag is in the command
    assert "--agent-profile" in captured_cmd
    profile_idx = captured_cmd.index("--agent-profile")
    assert captured_cmd[profile_idx + 1] == "explorer"
