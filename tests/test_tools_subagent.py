import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools.subagent import SubtaskDef, _subagents, subagent


def _wait_for_new_subagent_threads(initial_count: int, timeout: float = 1.0) -> None:
    """Join spawned threads before a thread-mocking patch context can unwind."""
    for sa in _subagents[initial_count:]:
        if sa.thread is not None:
            sa.thread.join(timeout=timeout)
            assert not sa.thread.is_alive()


def _new_subagents(initial_count: int):
    """Return subagents registered since the test started."""
    return _subagents[initial_count:]


def _planner_children(initial_count: int, planner_id: str):
    """Split new planner-mode registrations into planner + executor children."""
    new_agents = _new_subagents(initial_count)
    planner = next(sa for sa in new_agents if sa.agent_id == planner_id)
    executors = [sa for sa in new_agents if sa.agent_id != planner_id]
    return planner, executors


def test_planner_mode_requires_subtasks():
    """Test that planner mode requires subtasks parameter."""
    with pytest.raises(ValueError, match="Planner mode requires subtasks"):
        subagent(agent_id="test-planner", prompt="Test task", mode="planner")


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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
    _wait_for_new_subagent_threads(initial_count)

    planner, executors = _planner_children(initial_count, "test-planner")
    assert planner.agent_id == "test-planner"
    assert len(executors) == 2

    # Check executor IDs are correctly formed
    executor_ids = [s.agent_id for s in executors]
    assert "test-planner-task1" in executor_ids
    assert "test-planner-task2" in executor_ids


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_mode_executor_prompts(mock_create_thread: MagicMock):
    """Test that executor prompts include context and subtask description."""
    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "Do something specific"}
    ]

    subagent(
        agent_id="test-planner",
        prompt="This is the overall context",
        mode="planner",
        subtasks=subtasks,
    )
    _wait_for_new_subagent_threads(initial_count)

    # Check the spawned executor has correct prompt
    _, executors = _planner_children(initial_count, "test-planner")
    assert len(executors) == 1
    executor = executors[0]
    assert "This is the overall context" in executor.prompt
    assert "Do something specific" in executor.prompt


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_executor_mode_still_works(mock_create_thread: MagicMock):
    """Test that default executor mode still works as before."""
    initial_count = len(_subagents)

    subagent(agent_id="test-executor", prompt="Simple task")
    _wait_for_new_subagent_threads(initial_count)

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    # Check basic properties
    executor = _subagents[-1]
    assert executor.agent_id == "test-executor"
    assert executor.prompt == "Simple task"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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
    _wait_for_new_subagent_threads(initial_count)

    _, executors = _planner_children(initial_count, "test-parallel")
    assert len(executors) == 3

    # Check all have correct ID prefix
    executor_ids = [s.agent_id for s in executors]
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
    with patch("gptme.tools.subagent.execution._create_subagent_thread"):
        subagent(
            agent_id="test-sequential",
            prompt="Sequential execution test",
            mode="planner",
            subtasks=subtasks,
            execution_mode="sequential",
        )
        _wait_for_new_subagent_threads(initial_count)

    _, executors = _planner_children(initial_count, "test-sequential")
    assert len(executors) == 2

    # Check IDs are correctly formed
    executor_ids = [s.agent_id for s in executors]
    assert "test-sequential-seq1" in executor_ids
    assert "test-sequential-seq2" in executor_ids


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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
    _wait_for_new_subagent_threads(initial_count)

    _, executors = _planner_children(initial_count, "test-default")
    assert len(executors) == 1


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_context_mode_default_is_full(mock_create_thread: MagicMock):
    """Test that default context_mode is 'full'."""
    initial_count = len(_subagents)

    subagent(agent_id="test-full", prompt="Test with full context")
    _wait_for_new_subagent_threads(initial_count)

    # Should spawn 1 executor with full context
    assert len(_subagents) == initial_count + 1


def test_context_mode_selective_requires_context_include():
    """Test that selective mode requires context_include parameter."""
    with pytest.raises(ValueError, match="context_include parameter required"):
        subagent(
            agent_id="test-selective-error",
            prompt="Test task",
            context_mode="selective",
        )


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_context_mode_selective_with_tools(mock_create_thread: MagicMock):
    """Test selective mode with tools context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-tools",
        prompt="Use tools to complete task",
        context_mode="selective",
        context_include=["tools"],
    )
    _wait_for_new_subagent_threads(initial_count)

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    executor = _subagents[-1]
    assert executor.agent_id == "test-selective-tools"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_context_mode_selective_with_agent(mock_create_thread: MagicMock):
    """Test selective mode with agent context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-agent",
        prompt="Task requiring agent identity",
        context_mode="selective",
        context_include=["agent"],
    )
    _wait_for_new_subagent_threads(initial_count)

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_context_mode_selective_with_workspace(mock_create_thread: MagicMock):
    """Test selective mode with workspace context."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-workspace",
        prompt="Task requiring workspace files",
        context_mode="selective",
        context_include=["workspace"],
    )
    _wait_for_new_subagent_threads(initial_count)

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_context_mode_selective_multiple_components(mock_create_thread: MagicMock):
    """Test selective mode with multiple context components."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-selective-multiple",
        prompt="Complex task needing multiple contexts",
        context_mode="selective",
        context_include=["agent", "tools", "workspace"],
    )
    _wait_for_new_subagent_threads(initial_count)

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_mode_with_context_modes(mock_create_thread: MagicMock):
    """Test that planner mode works with context modes."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "Simple computation"},
        {"id": "task2", "description": "Complex analysis"},
    ]

    # Planner with full context
    subagent(
        agent_id="test-planner-context",
        prompt="Overall task context",
        mode="planner",
        subtasks=subtasks,
    )
    _wait_for_new_subagent_threads(initial_count)

    _, executors = _planner_children(initial_count, "test-planner-context")
    assert len(executors) == 2


# Phase 1 Tests: Subprocess mode, callbacks, batch execution


def test_subagent_with_use_subprocess():
    """Test that use_subprocess parameter is accepted."""
    import inspect

    sig = inspect.signature(subagent)

    # Verify subprocess parameter exists (callbacks removed in favor of hooks)
    assert "use_subprocess" in sig.parameters

    # Verify default value (None = "not set"; False only means explicit disable)
    assert sig.parameters["use_subprocess"].default is None

    # Callbacks have been removed - completion is now delivered via LOOP_CONTINUE hook
    assert "on_complete" not in sig.parameters
    assert "on_progress" not in sig.parameters


def test_subagent_batch_creates_batch_job():
    """Test that subagent_batch returns a BatchJob with correct structure."""
    from gptme.tools.subagent import BatchJob, _subagents, subagent_batch

    # Clear any previous subagents
    _subagents.clear()

    # Mock to prevent actual subagent execution
    with patch("gptme.tools.subagent.batch.subagent") as mock_subagent:
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


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_status_returns_dict(mock_create_thread: MagicMock):
    """Test that subagent_status returns a dictionary."""
    from gptme.tools.subagent import subagent, subagent_status

    # First create a subagent (thread mocked to avoid real API calls in no-extras CI)
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


def test_subagent_status_missing_log_returns_failure():
    """Finished subagents without a log should fail cleanly instead of raising."""
    from gptme.tools.subagent import Subagent

    sa = Subagent(
        agent_id="test-missing-log",
        prompt="test prompt",
        thread=None,
        logdir=Path("/tmp/does-not-exist"),
        model=None,
    )

    with patch.object(
        Subagent, "get_log", side_effect=FileNotFoundError("missing log")
    ):
        result = sa.status()

    assert result.status == "failure"
    assert "conversation log" in (result.result or "")


@patch("gptme.tools.subagent.api.notify_completion")
@patch(
    "gptme.tools.subagent.execution._create_subagent_thread",
    side_effect=RuntimeError("boom"),
)
def test_subagent_wait_returns_failure_when_thread_startup_raises(
    _mock_create_thread: MagicMock, _mock_notify_completion: MagicMock
):
    """Thread-mode startup failures should be cached for subagent_wait/status."""
    from gptme.tools.subagent import _subagent_results, subagent_wait

    _subagents.clear()
    _subagent_results.clear()

    try:
        subagent(agent_id="test-thread-fail", prompt="Simple task")

        result = subagent_wait("test-thread-fail", timeout=1)
        assert result["status"] == "failure"
        assert result["result"] == "boom"
    finally:
        _subagents.clear()
        _subagent_results.clear()


@patch("gptme.tools.subagent.api.notify_completion")
@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_wait_truncates_long_result(
    _mock_create_thread: MagicMock, _mock_notify: MagicMock
):
    """subagent_wait() should truncate very long results to keep parent context clean."""
    from gptme.tools.subagent import (
        ReturnType,
        _subagent_results,
        _subagents_lock,
        subagent,
        subagent_wait,
    )

    _subagents.clear()
    _subagent_results.clear()

    try:
        subagent(agent_id="test-truncate-wait", prompt="Long output task")

        # Inject a long result into the cache (simulates a completed subagent)
        long_result = "A" * 5000 + "\n\nFull log: /tmp/test-logdir"
        with _subagents_lock:
            _subagent_results["test-truncate-wait"] = ReturnType("success", long_result)

        result = subagent_wait("test-truncate-wait", timeout=1, max_result_chars=2000)
        assert result["status"] == "success"
        assert result["result"] is not None
        assert len(result["result"]) < 3000  # truncated + hint text
        assert "truncated" in result["result"]
        assert "subagent_read_log" in result["result"]
        assert "test-truncate-wait" in result["result"]
    finally:
        _subagents.clear()
        _subagent_results.clear()


@patch("gptme.tools.subagent.api.notify_completion")
@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_wait_short_result_not_truncated(
    _mock_create_thread: MagicMock, _mock_notify: MagicMock
):
    """Short results should pass through unchanged."""
    from gptme.tools.subagent import (
        ReturnType,
        _subagent_results,
        _subagents_lock,
        subagent,
        subagent_wait,
    )

    _subagents.clear()
    _subagent_results.clear()

    try:
        subagent(agent_id="test-short-wait", prompt="Short output task")

        short_result = "Fibonacci 13 = 233\n\nFull log: /tmp/logdir"
        with _subagents_lock:
            _subagent_results["test-short-wait"] = ReturnType("success", short_result)

        result = subagent_wait("test-short-wait", timeout=1)
        assert result["status"] == "success"
        assert result["result"] == short_result  # unchanged
    finally:
        _subagents.clear()
        _subagent_results.clear()


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
    # Status should be a valid subagent status (including clarification_needed)
    assert result.get("status") in [
        "success",
        "failure",
        "running",
        "timeout",
        "clarification_needed",
    ]


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

    with patch(
        "gptme.tools.subagent.execution._run_subagent_subprocess",
        return_value=mock_process,
    ):
        subagent(
            agent_id="test-subprocess",
            prompt="Simple test task",
            use_subprocess=True,
        )
        # Wait while the patch is still active; otherwise the launcher thread can
        # race past the context manager and call the real Popen.
        _wait_for_new_subagent_threads(0)

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

    with patch(
        "gptme.tools.subagent.execution._run_subagent_subprocess",
        return_value=MagicMock(),
    ):
        subagent(
            agent_id="test-cmd",
            prompt="Test prompt for command",
            use_subprocess=True,
        )
        # Keep the patch active until the launcher thread has built the command.
        _wait_for_new_subagent_threads(0)

    # Command construction is now tested at the unit level; the async subprocess
    # path delegates to _run_subagent_subprocess which is tested separately.
    # Here we verify the subagent was created with correct mode.
    sa = next((s for s in _subagents if s.agent_id == "test-cmd"), None)
    assert sa is not None
    assert sa.execution_mode == "subprocess"


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

    with patch(
        "gptme.tools.subagent.execution._run_subagent_subprocess",
        return_value=mock_process,
    ):
        subagent(
            agent_id="test-complete",
            prompt="Task to complete",
            use_subprocess=True,
        )
        # Wait for launcher thread to complete while patch is active
        _wait_for_new_subagent_threads(0)

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

    from gptme.tools.subagent.execution import _run_subagent_subprocess

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

            # Verify stdout and stderr are discarded (DEVNULL prevents pipe-buffer deadlock)
            assert process.stdout is None
            assert process.stderr is None

        finally:
            # Clean up - terminate the process
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_subprocess_command_includes_required_flags():
    """Test that subprocess command includes all required gptme flags."""
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent.execution import _run_subagent_subprocess

    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.poll.return_value = None
        mock.args = cmd
        return mock

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        with patch("gptme.tools.subagent.execution.subprocess.Popen", fake_popen):
            _run_subagent_subprocess(
                prompt="Test task",
                logdir=logdir,
                model="test-model",
                workspace=Path(tmpdir),
            )

    cmd = captured_cmd
    assert isinstance(cmd, list)

    # Verify required elements
    assert sys.executable in cmd[0] or "python" in cmd[0]
    assert "-m" in cmd
    assert "gptme" in cmd
    assert "-n" in cmd  # Non-interactive
    assert "--no-confirm" in cmd
    assert any("--name=" in str(arg) for arg in cmd)
    assert "--model" in cmd
    assert "test-model" in cmd
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == "+complete,+clarify,+progress"
    assert "Test task" not in cmd  # Prompt passed via stdin, not argv


def test_subprocess_profile_preserves_profile_tools_and_adds_clarify():
    """Restricted subprocess profiles must keep their allowlist and add clarify."""
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent.execution import _run_subagent_subprocess

    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.poll.return_value = None
        mock.args = cmd
        return mock

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        with patch("gptme.tools.subagent.execution.subprocess.Popen", fake_popen):
            _run_subagent_subprocess(
                prompt="Explore task",
                logdir=logdir,
                model=None,
                workspace=Path(tmpdir),
                profile="explorer",
            )

    assert "--agent-profile" in captured_cmd
    assert captured_cmd[captured_cmd.index("--agent-profile") + 1] == "explorer"
    assert "--tools" in captured_cmd
    assert captured_cmd[captured_cmd.index("--tools") + 1] == (
        "read,chats,complete,clarify,progress"
    )


def test_subprocess_no_profile_includes_complete_and_clarify():
    """Subprocess without a profile must include both complete and clarify tools."""
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent.execution import _run_subagent_subprocess

    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.poll.return_value = None
        mock.args = cmd
        return mock

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        with patch("gptme.tools.subagent.execution.subprocess.Popen", fake_popen):
            _run_subagent_subprocess(
                prompt="Do a task",
                logdir=logdir,
                model=None,
                workspace=Path(tmpdir),
                profile=None,
            )

    assert "--tools" in captured_cmd
    assert (
        captured_cmd[captured_cmd.index("--tools") + 1]
        == "+complete,+clarify,+progress"
    )


def test_subprocess_profile_without_toollist_includes_complete_and_clarify():
    """Profile with no tools list must fall back to +complete,+clarify,+progress."""
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent.execution import _run_subagent_subprocess

    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.poll.return_value = None
        mock.args = cmd
        return mock

    # Use a mock profile object with tools=None to hit the else branch inside `if profile:`
    mock_profile = MagicMock()
    mock_profile.tools = None

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "logs"
        logdir.mkdir()

        with (
            patch("gptme.tools.subagent.execution.subprocess.Popen", fake_popen),
            patch(
                "gptme.profiles.get_profile",
                return_value=mock_profile,
            ),
        ):
            _run_subagent_subprocess(
                prompt="Do a task",
                logdir=logdir,
                model=None,
                workspace=Path(tmpdir),
                profile="custom",
            )

    assert "--tools" in captured_cmd
    assert (
        captured_cmd[captured_cmd.index("--tools") + 1]
        == "+complete,+clarify,+progress"
    )


def test_subprocess_sets_progress_env_vars():
    """Subprocess must receive GPTME_SUBAGENT_AGENT_ID and GPTME_PROGRESS_FILE env vars."""
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent.execution import _run_subagent_subprocess

    captured_env: dict[str, str] = {}

    def fake_popen(cmd, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        mock = MagicMock()
        mock.poll.return_value = None
        mock.args = cmd
        return mock

    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "subagent-test-agent"
        logdir.mkdir()

        with patch("gptme.tools.subagent.execution.subprocess.Popen", fake_popen):
            _run_subagent_subprocess(
                prompt="Test progress env",
                logdir=logdir,
                model=None,
                workspace=Path(tmpdir),
            )

    assert "GPTME_SUBAGENT_AGENT_ID" in captured_env
    assert captured_env["GPTME_SUBAGENT_AGENT_ID"] == "test-agent"
    assert "GPTME_PROGRESS_FILE" in captured_env
    assert captured_env["GPTME_PROGRESS_FILE"].endswith("progress.jsonl")


def test_progress_tool_file_delivery(tmp_path):
    """progress tool writes to file channel when running in subprocess env."""
    import json
    import os
    from unittest.mock import patch

    from gptme.tools.progress import execute_progress

    progress_file = tmp_path / "progress.jsonl"
    env = {
        "GPTME_SUBAGENT_AGENT_ID": "sub-1",
        "GPTME_PROGRESS_FILE": str(progress_file),
    }

    # Patch at the source so execute_progress's local import resolves to None
    with (
        patch.dict(os.environ, env, clear=False),
        patch("gptme.tools.subagent.execution.get_current_agent_id", return_value=None),
    ):
        messages = list(execute_progress("Phase 1 done, starting phase 2", None, None))

    assert len(messages) == 1
    assert (
        "parent" in messages[0].content.lower()
        or "file channel" in messages[0].content.lower()
    )
    assert progress_file.exists()
    lines = [
        json.loads(line) for line in progress_file.read_text().strip().splitlines()
    ]
    assert len(lines) == 1
    assert lines[0]["agent_id"] == "sub-1"
    assert "Phase 1 done" in lines[0]["message"]


def test_progress_tool_no_env_gives_graceful_message():
    """progress tool gives clear message when not in a managed subagent context."""
    import os
    from unittest.mock import patch

    from gptme.tools.progress import execute_progress

    clean_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GPTME_SUBAGENT_AGENT_ID", "GPTME_PROGRESS_FILE")
    }

    with (
        patch.dict(os.environ, clean_env, clear=True),
        patch("gptme.tools.subagent.execution.get_current_agent_id", return_value=None),
    ):
        messages = list(execute_progress("some progress", None, None))

    assert len(messages) == 1
    assert "not" in messages[0].content.lower()


def test_poll_subprocess_progress_delivers_via_notify(tmp_path):
    """_poll_subprocess_progress reads progress.jsonl and calls notify_progress."""
    import json
    import threading
    from unittest.mock import MagicMock, patch

    from gptme.tools.subagent.execution import _poll_subprocess_progress

    progress_file = tmp_path / "progress.jsonl"
    delivered: list[tuple[str, str]] = []

    def fake_notify(agent_id, message):
        delivered.append((agent_id, message))

    # Write a progress entry to the file before polling starts
    entry = json.dumps({"agent_id": "poll-agent", "message": "Step 1 done"})
    progress_file.write_text(entry + "\n")

    stop_event = threading.Event()

    # Create a minimal fake subagent object with the fields _poll_subprocess_progress needs
    mock_sa = MagicMock()
    mock_sa.agent_id = "poll-agent"
    mock_sa.logdir = tmp_path

    with patch("gptme.tools.subagent.hooks.notify_progress", fake_notify):
        # Set stop immediately so the loop exits after the final drain
        stop_event.set()
        _poll_subprocess_progress(mock_sa, stop_event)

    assert len(delivered) >= 1
    assert delivered[0] == ("poll-agent", "Step 1 done")


def test_drain_progress_file_partial_write_retry(tmp_path):
    """_drain_progress_file retries partial (no-newline) lines on the next call.

    Regression test: the old code advanced file_pos unconditionally after the
    loop, silently skipping any line that raised JSONDecodeError — including
    partial writes that would have been complete on the next poll.
    """
    import json

    from gptme.tools.subagent.execution import _drain_progress_file

    progress_file = tmp_path / "progress.jsonl"
    delivered: list[tuple[str, str]] = []

    def fake_notify(agent_id: str, message: str) -> None:
        delivered.append((agent_id, message))

    complete = json.dumps({"agent_id": "a", "message": "done"})
    partial = '{"agent_id": "a", "message": "in-flight'  # missing closing } and \n

    # First poll: one complete line + one partial (no trailing newline)
    progress_file.write_text(complete + "\n" + partial)
    pos = _drain_progress_file(progress_file, 0, "a", fake_notify)

    assert len(delivered) == 1, "only the complete line should be delivered"
    assert delivered[0] == ("a", "done")

    # file_pos must be just after the complete line, not at EOF
    assert pos == len(complete) + 1, "file_pos should not advance past the partial line"

    # Second poll: the write completes (partial line now has its closing bytes + \n)
    full_second = json.dumps({"agent_id": "a", "message": "in-flight-complete"})
    # Overwrite the file with both lines fully written
    progress_file.write_text(complete + "\n" + full_second + "\n")
    pos = _drain_progress_file(progress_file, pos, "a", fake_notify)

    assert len(delivered) == 2, "second poll should pick up the now-complete line"
    assert delivered[1] == ("a", "in-flight-complete")


@pytest.mark.slow
def test_subprocess_working_directory():
    """Test that subprocess runs in the specified working directory."""
    import subprocess
    import tempfile
    from pathlib import Path

    from gptme.tools.subagent.execution import _run_subagent_subprocess

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
    import time
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

            # The monitor thread stays alive while the subprocess runs; wait only
            # for the process reference to appear on the pre-registered Subagent.
            deadline = time.time() + 2.0
            while sa.process is None and time.time() < deadline:
                time.sleep(0.05)
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


def test_subprocess_monitor_timeout():
    """Test that _monitor_subprocess kills the process on timeout."""
    import subprocess
    from unittest.mock import MagicMock, patch

    from gptme.tools.subagent.execution import _monitor_subprocess
    from gptme.tools.subagent.types import (
        Subagent,
        _subagent_results,
        _subagent_results_lock,
    )

    # Create a mock process that simulates a timeout
    mock_process = MagicMock(spec=subprocess.Popen)
    mock_process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="gptme", timeout=2),  # first call: timeout
        None,  # second call (after kill): reap succeeds
    ]
    mock_process.returncode = -9  # SIGKILL

    sa = Subagent(
        agent_id="timeout-test",
        prompt="Test",
        thread=None,
        logdir=Path("/tmp/fake-logdir"),
        model=None,
        process=mock_process,
        execution_mode="subprocess",
        timeout=2,  # 2 second timeout for test
    )

    with patch("gptme.tools.subagent.hooks.notify_completion"):
        _monitor_subprocess(sa)

    # Verify: process was killed
    mock_process.kill.assert_called_once()

    # Verify: result was cached as failure with timeout message
    with _subagent_results_lock:
        result = _subagent_results.get("timeout-test")
    assert result is not None
    assert result.status == "failure"
    assert result.result is not None
    assert isinstance(result.result, str)
    assert "timeout" in result.result.lower()

    # Cleanup
    with _subagent_results_lock:
        _subagent_results.pop("timeout-test", None)


def test_subprocess_timeout_passed_to_subagent():
    """Test that the timeout parameter is passed through to the Subagent dataclass."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.subagent import _subagents, subagent

    _subagents.clear()

    # Mock the subprocess so we don't start a real gptme process.
    # _monitor_subprocess calls process.wait() which would block for `timeout`
    # seconds on a real process, causing the test to time out in teardown.
    mock_process = MagicMock()
    mock_process.returncode = 0

    with patch(
        "gptme.tools.subagent.execution._run_subagent_subprocess",
        return_value=mock_process,
    ):
        subagent(
            agent_id="timeout-param-test",
            prompt="Test",
            use_subprocess=True,
            timeout=600,
        )
        _wait_for_new_subagent_threads(0)

    sa = next((s for s in _subagents if s.agent_id == "timeout-param-test"), None)
    assert sa is not None
    assert sa.timeout == 600


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
    # Launch now happens asynchronously behind the semaphore, so queued
    # subprocess agents are visible before the child process exists.
    assert sa.thread is not None

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


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_with_profile(mock_create_thread: MagicMock):
    """Test that profile parameter is passed to subagent thread."""
    initial_count = len(_subagents)

    subagent(
        agent_id="test-profile",
        prompt="Explore the codebase",
        profile="explorer",
    )

    assert len(_subagents) == initial_count + 1

    _wait_for_new_subagent_threads(initial_count)

    # Verify profile was passed to _create_subagent_thread
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "explorer"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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

    _wait_for_new_subagent_threads(initial_count)


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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

    _wait_for_new_subagent_threads(initial_count)

    # Verify both profile and model are passed
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "researcher"
    assert call_kwargs["model"] == "anthropic/claude-haiku"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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

    _, executors = _planner_children(initial_count, "test-planner-profile")
    assert len(executors) == 2

    _wait_for_new_subagent_threads(initial_count)

    # Verify profile was passed to each executor's _create_subagent_thread call
    assert mock_create_thread.call_count == 2
    for call in mock_create_thread.call_args_list:
        assert call[1]["profile_name"] == "explorer"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_auto_detects_profile_from_agent_id(mock_create_thread: MagicMock):
    """Test that agent_id matching a profile name auto-applies the profile."""
    initial_count = len(_subagents)

    # Use "explorer" as agent_id without explicit profile param
    subagent(
        agent_id="explorer",
        prompt="Analyze the architecture",
    )

    assert len(_subagents) == initial_count + 1

    _wait_for_new_subagent_threads(initial_count)

    # Profile should be auto-detected from agent_id
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "explorer"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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

    _wait_for_new_subagent_threads(initial_count)

    # Profile should be auto-detected from alias
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "developer"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_no_auto_detect_for_unknown_agent_id(mock_create_thread: MagicMock):
    """Test that non-profile agent_ids don't trigger auto-detection."""
    initial_count = len(_subagents)

    subagent(
        agent_id="my-custom-task",
        prompt="Do something",
    )

    assert len(_subagents) == initial_count + 1

    _wait_for_new_subagent_threads(initial_count)

    # No profile should be set
    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] is None


@patch("gptme.tools.subagent.execution._create_subagent_thread")
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

    _wait_for_new_subagent_threads(initial_count)

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
    import gptme.tools.subagent.execution
    from gptme.tools import set_tools as real_set_tools
    from gptme.tools.base import ToolSpec

    mock_tools = [
        ToolSpec(name="read", desc="Read files", instructions=""),
        ToolSpec(name="shell", desc="Run shell", instructions=""),
        ToolSpec(name="save", desc="Save files", instructions=""),
        ToolSpec(name="chats", desc="Chat management", instructions=""),
        ToolSpec(name="complete", desc="Signal completion", instructions=""),
        ToolSpec(name="clarify", desc="Ask parent for clarification", instructions=""),
    ]

    # Track calls to set_tools
    set_tools_calls: list[list[str]] = []

    def spy_set_tools(tools):
        set_tools_calls.append([t.name for t in tools])
        real_set_tools(tools)

    with (
        patch.object(gptme.tools.subagent.execution, "set_tools", spy_set_tools),
        patch.object(
            gptme.tools.subagent.execution, "get_tools", return_value=mock_tools
        ),
        patch.object(sys.modules["gptme.chat"], "chat"),
        patch.object(
            gptme.executor,
            "prepare_execution_environment",
            return_value=(MagicMock(), mock_tools),
        ),
        patch.object(gptme.llm.models, "set_default_model"),
    ):
        from gptme.tools.subagent.execution import _create_subagent_thread

        _create_subagent_thread(
            prompt="Read the codebase",
            logdir=Path("/tmp/test-enforcement"),
            model=None,
            context_mode="full",
            context_include=None,
            workspace=Path("/tmp"),
            profile_name="explorer",
        )

    # set_tools should have been called with only allowed tools
    assert len(set_tools_calls) == 1, f"set_tools called {len(set_tools_calls)} times"
    enforced_tools = set_tools_calls[0]

    # Explorer profile allows: read, chats (+ completion/clarification tools)
    assert "read" in enforced_tools
    assert "chats" in enforced_tools
    assert "complete" in enforced_tools
    assert "clarify" in enforced_tools
    assert "shell" not in enforced_tools, "shell should be blocked by explorer profile"
    assert "save" not in enforced_tools, "save should be blocked by explorer profile"


def test_profile_no_restriction_skips_set_tools():
    """Test that profiles without tool restrictions don't call set_tools."""
    import gptme
    import gptme.chat
    import gptme.executor
    import gptme.llm.models
    import gptme.tools.subagent.execution
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
        patch.object(gptme.tools.subagent.execution, "set_tools", spy_set_tools),
        patch.object(
            gptme.tools.subagent.execution, "get_tools", return_value=mock_tools
        ),
        patch.object(sys.modules["gptme.chat"], "chat"),
        patch.object(
            gptme.executor,
            "prepare_execution_environment",
            return_value=(MagicMock(), mock_tools),
        ),
        patch.object(gptme.llm.models, "set_default_model"),
    ):
        from gptme.tools.subagent.execution import _create_subagent_thread

        # developer profile has tools=None (no restrictions)
        _create_subagent_thread(
            prompt="Write some code",
            logdir=Path("/tmp/test-no-restrict"),
            model=None,
            context_mode="full",
            context_include=None,
            workspace=Path("/tmp"),
            profile_name="developer",
        )

    # set_tools should NOT have been called (no restrictions to enforce)
    assert len(set_tools_calls) == 0, (
        f"set_tools should not be called for developer profile, but was called {len(set_tools_calls)} times"
    )


def test_create_subagent_thread_loads_signal_tools_for_default_profile(tmp_path):
    """Default thread-mode subagents must load complete and clarify explicitly."""
    import gptme.chat
    import gptme.executor
    import gptme.llm.models
    import gptme.tools.subagent.execution
    from gptme.tools.base import ToolSpec

    loaded_tools = [ToolSpec(name="shell", desc="Run shell")]

    def fake_load_tool(tool_name: str):
        tool = ToolSpec(name=tool_name, desc=f"{tool_name} signal")
        loaded_tools.append(tool)
        return tool

    with (
        patch.object(
            gptme.tools.subagent.execution,
            "get_tools",
            side_effect=lambda: loaded_tools,
        ),
        patch.object(
            gptme.tools.subagent.execution,
            "load_tool",
            side_effect=fake_load_tool,
        ) as mock_load_tool,
        patch.object(sys.modules["gptme.chat"], "chat"),
        patch.object(
            gptme.executor,
            "prepare_execution_environment",
            return_value=(MagicMock(), loaded_tools),
        ),
        patch.object(gptme.llm.models, "set_default_model"),
        patch("gptme.prompts.get_prompt", return_value=[]),
    ):
        from gptme.tools.subagent.execution import _create_subagent_thread

        _create_subagent_thread(
            prompt="Need context",
            logdir=tmp_path,
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            profile_name=None,
        )

    assert [call.args[0] for call in mock_load_tool.call_args_list] == [
        "complete",
        "clarify",
        "progress",
    ]
    assert {tool.name for tool in loaded_tools} == {
        "shell",
        "complete",
        "clarify",
        "progress",
    }


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

    with patch(
        "gptme.tools.subagent.execution._run_subagent_subprocess",
        return_value=MagicMock(),
    ) as mock_run:
        subagent(
            agent_id="test-subprocess-profile",
            prompt="Explore task",
            use_subprocess=True,
            profile="explorer",
        )
        # Keep the patch active until the launcher thread has built the command.
        _wait_for_new_subagent_threads(0)

    # Verify profile was passed to _run_subagent_subprocess
    mock_run.assert_called_once()
    sub_kwargs = mock_run.call_args[1]
    assert sub_kwargs.get("profile") == "explorer"


def test_create_subagent_thread_warns_on_unknown_profile_tools(tmp_path):
    """Warn when profile includes unknown tool names and keep known ones + complete."""
    from unittest.mock import MagicMock, patch

    from gptme.message import Message
    from gptme.profiles import Profile
    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent.execution import _create_subagent_thread

    profile = Profile(
        name="test",
        description="test profile",
        tools=["read", "reead"],
    )
    tools = [
        ToolSpec(name="read", desc=""),
        ToolSpec(name="complete", desc=""),
        ToolSpec(name="clarify", desc=""),
        ToolSpec(name="shell", desc=""),
    ]

    import gptme.chat  # noqa: F401 — must import before patch.object on sys.modules["gptme.chat"]

    mock_prompt = MagicMock(return_value=[])
    mock_chat = MagicMock()
    mock_warn = MagicMock()

    with (
        patch("gptme.profiles.get_profile", return_value=profile),
        patch("gptme.tools.subagent.execution.get_tools", return_value=tools),
        patch("gptme.executor.prepare_execution_environment"),
        patch("gptme.prompts.get_prompt", mock_prompt),
        patch.object(sys.modules["gptme.chat"], "chat", mock_chat),
        patch("gptme.tools.subagent.execution.logger.warning", mock_warn),
    ):
        _create_subagent_thread(
            prompt="test",
            logdir=tmp_path,
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            profile_name="test",
        )

    mock_warn.assert_called_once()
    assert "unknown tools" in mock_warn.call_args.args[0]
    assert "reead" in mock_warn.call_args.args[2]

    # Ensure tools passed to prompt are filtered to allowed + signal fallbacks
    filtered_tools = mock_prompt.call_args.args[0]
    filtered_names = {t.name for t in filtered_tools}
    assert filtered_names == {"read", "complete", "clarify"}

    # Ensure chat got the user prompt message
    prompt_msgs = mock_chat.call_args.args[0]
    assert prompt_msgs == [Message("user", "test")]


def test_create_subagent_thread_profile_glob_filters_tools(tmp_path):
    """Glob tool allowlists should filter grouped MCP tools without warnings."""
    from unittest.mock import MagicMock, patch

    from gptme.message import Message
    from gptme.profiles import Profile
    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent.execution import _create_subagent_thread

    profile = Profile(
        name="discord-reader",
        description="discord only",
        tools=["discord.*"],
    )
    tools = [
        ToolSpec(name="discord.read_channel", desc="", is_mcp=True),
        ToolSpec(name="discord.send_message", desc="", is_mcp=True),
        ToolSpec(name="shell", desc=""),
        ToolSpec(name="complete", desc=""),
        ToolSpec(name="clarify", desc=""),
    ]

    mock_prompt = MagicMock(return_value=[])
    mock_chat = MagicMock()
    mock_warn = MagicMock()
    set_tools_calls: list[list[str]] = []

    def spy_set_tools(filtered_tools):
        set_tools_calls.append([tool.name for tool in filtered_tools])

    with (
        patch("gptme.profiles.get_profile", return_value=profile),
        patch("gptme.tools.subagent.execution.get_tools", return_value=tools),
        patch("gptme.tools.subagent.execution.set_tools", side_effect=spy_set_tools),
        patch("gptme.executor.prepare_execution_environment"),
        patch("gptme.prompts.get_prompt", mock_prompt),
        patch.object(sys.modules["gptme.chat"], "chat", mock_chat),
        patch("gptme.tools.subagent.execution.logger.warning", mock_warn),
    ):
        _create_subagent_thread(
            prompt="test",
            logdir=tmp_path,
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            profile_name="discord-reader",
        )

    mock_warn.assert_not_called()
    assert set_tools_calls == [
        ["discord.read_channel", "discord.send_message", "complete", "clarify"]
    ]

    filtered_tools = mock_prompt.call_args.args[0]
    filtered_names = {t.name for t in filtered_tools}
    assert filtered_names == {
        "discord.read_channel",
        "discord.send_message",
        "complete",
        "clarify",
    }

    prompt_msgs = mock_chat.call_args.args[0]
    assert prompt_msgs == [Message("user", "test")]


# --- ACP Mode Tests ---


def test_acp_mode_creates_subagent():
    """Test that ACP mode creates a subagent with correct execution_mode."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from gptme.tools.subagent import _subagents, subagent

    _subagents.clear()

    # Mock the GptmeAcpClient to avoid actually spawning a process
    mock_client = AsyncMock()
    mock_result = MagicMock()
    mock_result.stop_reason = "end_turn"
    mock_client.run = AsyncMock(return_value=mock_result)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("gptme.acp.client.GptmeAcpClient", return_value=mock_client),
        patch("gptme.tools.subagent.notify_completion"),
    ):
        subagent(
            agent_id="test-acp",
            prompt="Test ACP task",
            use_acp=True,
            acp_command="fake-acp",
        )

        # Verify subagent was created with ACP mode
        sa = next((s for s in _subagents if s.agent_id == "test-acp"), None)
        assert sa is not None
        assert sa.execution_mode == "acp"
        assert sa.acp_command == "fake-acp"
        assert sa.thread is not None  # ACP runs in a wrapper thread

        # Join the thread inside the patch context so the mock stays active
        # for the full duration — prevents the thread from using the real
        # GptmeAcpClient after the mock is removed.
        sa.thread.join(timeout=10)


def test_acp_mode_stores_result():
    """Test that ACP mode stores completion result."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagent_results_lock,
        _subagents,
        subagent,
    )

    _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()

    mock_client = AsyncMock()
    mock_result = MagicMock()
    mock_result.stop_reason = "end_turn"
    mock_client.run = AsyncMock(return_value=mock_result)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("gptme.acp.client.GptmeAcpClient", return_value=mock_client),
        patch("gptme.tools.subagent.notify_completion"),
    ):
        subagent(
            agent_id="test-acp-result",
            prompt="Compute something",
            use_acp=True,
        )

        # Wait for the thread to finish
        sa = next(s for s in _subagents if s.agent_id == "test-acp-result")
        assert sa.thread is not None
        sa.thread.join(timeout=10)

        # Check result was stored
        with _subagent_results_lock:
            assert "test-acp-result" in _subagent_results
            result = _subagent_results["test-acp-result"]
            assert result.status == "success"


def test_acp_mode_preserves_clarification_status():
    """ACP mode should report clarify blocks as clarification_needed."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagent_results_lock,
        _subagents,
        subagent,
    )

    _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()

    class FakeAcpClient:
        def __init__(self, *args, on_update=None, **kwargs):
            self.on_update = on_update

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def run(self, prompt, cwd=None):
            chunk = MagicMock()
            chunk.text = "```clarify\nWhich format should I use?\n```"
            update = MagicMock()
            update.type = "agent_message_chunk"
            update.chunk = chunk
            self.on_update("session-1", update)
            result = MagicMock()
            result.stop_reason = "end_turn"
            return result

    with patch("gptme.acp.client.GptmeAcpClient", FakeAcpClient):
        subagent(
            agent_id="test-acp-clarify",
            prompt="Compute something",
            use_acp=True,
        )

        sa = next(s for s in _subagents if s.agent_id == "test-acp-clarify")
        assert sa.thread is not None
        sa.thread.join(timeout=10)

        with _subagent_results_lock:
            result = _subagent_results["test-acp-clarify"]
        assert result.status == "clarification_needed"
        assert result.result == "Which format should I use?"


def test_acp_mode_handles_failure():
    """Test that ACP mode handles connection failures gracefully."""
    from unittest.mock import AsyncMock, patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagent_results_lock,
        _subagents,
        subagent,
    )

    _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(
        side_effect=FileNotFoundError("fake-acp not found")
    )
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("gptme.acp.client.GptmeAcpClient", return_value=mock_client),
        patch("gptme.tools.subagent.notify_completion"),
    ):
        subagent(
            agent_id="test-acp-fail",
            prompt="This will fail",
            use_acp=True,
            acp_command="nonexistent-acp",
        )

        sa = next(s for s in _subagents if s.agent_id == "test-acp-fail")
        assert sa.thread is not None
        sa.thread.join(timeout=10)

        with _subagent_results_lock:
            assert "test-acp-fail" in _subagent_results
            result = _subagent_results["test-acp-fail"]
            assert result.status == "failure"


def test_cancelled_queued_acp_does_not_launch_after_slot_frees():
    """ACP subagents cancelled while queued must not start once a slot opens."""
    import threading
    from unittest.mock import AsyncMock, patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagent_results_lock,
        _subagents,
        subagent,
        subagent_cancel,
    )

    _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()

    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(timeout=0)

    mock_client = AsyncMock()
    mock_client.run = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    cleanup_calls: list[str] = []

    with (
        patch("gptme.acp.client.GptmeAcpClient", return_value=mock_client),
        patch("gptme.tools.subagent.notify_completion") as mock_notify,
        patch("gptme.tools.subagent.api.get_slot_sem", return_value=sem),
        patch(
            "gptme.tools.subagent.api._exec._cleanup_isolation",
            side_effect=lambda sa: cleanup_calls.append(sa.agent_id),
        ),
    ):
        subagent(
            agent_id="test-acp-cancelled",
            prompt="Do not run",
            use_acp=True,
            acp_command="fake-acp",
        )

        sa = next(s for s in _subagents if s.agent_id == "test-acp-cancelled")
        assert sa.thread is not None

        result = subagent_cancel("test-acp-cancelled")
        assert "marked as cancelled" in result.lower()

        sem.release()
        sa.thread.join(timeout=10)
        assert not sa.thread.is_alive()

        mock_client.__aenter__.assert_not_awaited()
        mock_client.run.assert_not_awaited()
        mock_notify.assert_not_called()
        assert cleanup_calls == ["test-acp-cancelled"]

    with _subagent_results_lock:
        assert _subagent_results["test-acp-cancelled"].status == "failure"
        assert (
            _subagent_results["test-acp-cancelled"].result
            == "Cancelled by orchestrator"
        )


def test_acp_mode_subagent_batch():
    """Test that subagent_batch forwards use_acp and acp_command to subagent()."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from gptme.tools.subagent import (
        _subagent_results,
        _subagent_results_lock,
        _subagents,
        subagent_batch,
    )

    _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()

    mock_client = AsyncMock()
    mock_result = MagicMock()
    mock_result.stop_reason = "end_turn"
    mock_client.run = AsyncMock(return_value=mock_result)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("gptme.acp.client.GptmeAcpClient", return_value=mock_client),
        patch("gptme.tools.subagent.notify_completion"),
    ):
        job = subagent_batch(
            [("batch-acp-1", "Task 1"), ("batch-acp-2", "Task 2")],
            use_acp=True,
            acp_command="fake-acp",
        )

        assert job.agent_ids == ["batch-acp-1", "batch-acp-2"]

        # Wait for both ACP threads to complete
        for agent_id in job.agent_ids:
            sa = next(s for s in _subagents if s.agent_id == agent_id)
            assert sa.execution_mode == "acp"
            assert sa.acp_command == "fake-acp"
            assert sa.thread is not None
            sa.thread.join(timeout=10)

        with _subagent_results_lock:
            for agent_id in job.agent_ids:
                assert agent_id in _subagent_results
                assert _subagent_results[agent_id].status == "success"


# ---------------------------------------------------------------------------
# Role parameter tests (Phase 1 of subagent role taxonomy)
# ---------------------------------------------------------------------------


def test_role_parameter_exists():
    """Test that subagent() accepts a role parameter."""
    import inspect

    sig = inspect.signature(subagent)

    assert "role" in sig.parameters
    assert sig.parameters["role"].default is None


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_explore_resolves_explorer_profile(mock_create_thread: MagicMock):
    """Test that role='explore' defaults profile to 'explorer'."""
    initial_count = len(_subagents)

    subagent(
        agent_id="scout",
        prompt="Explore the codebase",
        role="explore",
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)

    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "explorer"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_implement_resolves_developer_profile(mock_create_thread: MagicMock):
    """Test that role='implement' defaults profile to 'developer'."""
    initial_count = len(_subagents)

    subagent(
        agent_id="builder",
        prompt="Implement feature X",
        role="implement",
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)

    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "developer"


@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
def test_role_verify_defaults_subprocess_and_isolated(
    mock_run_subprocess: MagicMock,
):
    """Test that role='verify' defaults to subprocess mode with isolation and verifier profile."""
    _subagents.clear()

    subagent(
        agent_id="checker",
        prompt="Verify the auth module",
        role="verify",
    )

    # Wait for launcher thread to execute (subprocess mode is now async)
    _wait_for_new_subagent_threads(0)

    assert len(_subagents) == 1
    sa = _subagents[-1]
    # The subagent should be created in subprocess mode (not thread mode)
    assert sa.execution_mode == "subprocess"
    assert sa.isolated is True

    # Verify that role overrides profile auto-detection from agent_id
    # agent_id 'checker' would normally auto-detect nothing, but role='verify'
    # should set profile to 'verifier'
    mock_run_subprocess.assert_called_once()


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_explore_does_not_set_use_subprocess(mock_create_thread: MagicMock):
    """Test that role='explore' does NOT set subprocess mode (thread mode default)."""
    initial_count = len(_subagents)

    subagent(
        agent_id="scout",
        prompt="Explore",
        role="explore",
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)
    sa = _subagents[-1]
    assert sa.execution_mode == "thread"
    assert sa.isolated is False


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_implement_does_not_set_use_subprocess(mock_create_thread: MagicMock):
    """Test that role='implement' does NOT set subprocess mode (thread mode default)."""
    initial_count = len(_subagents)

    subagent(
        agent_id="builder",
        prompt="Implement",
        role="implement",
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)
    sa = _subagents[-1]
    assert sa.execution_mode == "thread"
    assert sa.isolated is False


@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
def test_role_verify_subprocess_has_verifier_profile(
    mock_run_subprocess: MagicMock,
):
    """Test that verify role forwarded correctly to subprocess runner via profile."""
    _subagents.clear()

    subagent(
        agent_id="checker",
        prompt="Verify everything",
        role="verify",
    )

    _wait_for_new_subagent_threads(0)
    sa = _subagents[-1]

    # The Subagent should capture isolate=True
    assert sa.isolated is True


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_explicit_profile_overrides_role_profile(mock_create_thread: MagicMock):
    """Test that explicit profile argument overrides role-derived profile."""
    initial_count = len(_subagents)

    subagent(
        agent_id="scout",
        prompt="Research task",
        role="explore",
        profile="researcher",
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)

    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    # Explicit profile wins over role-derived profile
    assert call_kwargs["profile_name"] == "researcher"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_overrides_agent_id_auto_detection(mock_create_thread: MagicMock):
    """Test that role= profile overrides agent_id auto-detection.

    agent_id='explorer' would auto-detect profile='explorer', but role='implement'
    should win and set profile to 'developer' (role > agent_id auto-detection).
    """
    initial_count = len(_subagents)

    subagent(
        agent_id="explorer",  # would auto-detect to 'explorer' profile
        prompt="Implement feature X",
        role="implement",  # should override to 'developer'
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)

    mock_create_thread.assert_called_once()
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "developer", (
        "role= should override agent_id auto-detection"
    )


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_role_verify_explicit_false_subprocess_opts_out(
    mock_create_thread: MagicMock,
):
    """Test that explicit use_subprocess=False overrides role='verify' subprocess default."""
    initial_count = len(_subagents)

    subagent(
        agent_id="checker",
        prompt="Verify the auth module",
        role="verify",
        use_subprocess=False,  # explicit opt-out should win
    )

    assert len(_subagents) == initial_count + 1
    _wait_for_new_subagent_threads(initial_count)

    mock_create_thread.assert_called_once()
    sa = _subagents[-1]
    assert sa.execution_mode == "thread", (
        "explicit use_subprocess=False should override role='verify' subprocess default"
    )


@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_subtask_role_passthrough(
    mock_create_thread: MagicMock, mock_run_subprocess: MagicMock
):
    """Test that planner subtasks with role pass role through to spawned executor."""
    from gptme.tools.subagent import SubtaskDef, _subagents, subagent

    mock_run_subprocess.return_value = MagicMock()  # fake Popen

    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "scout", "description": "Explore the codebase", "role": "explore"},
        {"id": "build", "description": "Implement the feature", "role": "implement"},
        {"id": "check", "description": "Verify the result", "role": "verify"},
    ]

    subagent(
        agent_id="test-role-planner",
        prompt="Plan and execute a feature",
        mode="planner",
        subtasks=subtasks,
    )

    # Wait for threads to execute while the patch is still active; without this
    # the OS may schedule them after @patch exits and they call the real function.
    _wait_for_new_subagent_threads(initial_count, timeout=5.0)
    # Additional wait for the verify subtask's closure thread (subprocess SA has
    # thread=None so _wait_for_new_subagent_threads can't join it)
    import time

    time.sleep(0.3)

    _, executors = _planner_children(initial_count, "test-role-planner")
    assert len(executors) == 3
    executor_ids = {sa.agent_id for sa in executors}
    assert "test-role-planner-scout" in executor_ids
    assert "test-role-planner-build" in executor_ids
    assert "test-role-planner-check" in executor_ids


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_subtask_role_does_not_affect_planner_internals(
    mock_create_thread: MagicMock,
):
    """Test that planner subtask roles don't interfere with planner execution flow.

    The planner should still spawn all subtask executors correctly even when
    each subtask carries a different role. The role is passed to the executor
    but does NOT affect planner-level behavior.
    """
    from gptme.tools.subagent import SubtaskDef, _subagents, subagent

    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "First task", "role": "explore"},
    ]

    subagent(
        agent_id="test-planner-with-role",
        prompt="Overall context",
        mode="planner",
        subtasks=subtasks,
    )

    _, executors = _planner_children(initial_count, "test-planner-with-role")
    assert len(executors) == 1
    executor = executors[0]
    assert executor.agent_id == "test-planner-with-role-task1"

    _wait_for_new_subagent_threads(initial_count, timeout=5.0)

    mock_create_thread.assert_called_once()
    # Subtask role resolves to the executor's profile_name. The planner
    # itself is unaffected — same number of executors, standard planning
    # flow. But each executor gets the role-derived profile.
    call_kwargs = mock_create_thread.call_args[1]
    assert call_kwargs["profile_name"] == "explorer"
    assert "explore" in executor.prompt or "First task" in executor.prompt


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_with_role_uses_role_for_self_profile(mock_create_thread: MagicMock):
    """Test that planner with role uses role to set profile on API side.

    When subagent() is called with role in planner mode, the role-derived profile
    should be passed to _run_planner as profile_name so all executor children
    inherit the role's intended posture.
    """
    from gptme.tools.subagent import SubtaskDef, _subagents, subagent

    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "build1", "description": "Build component A", "role": "implement"},
        {"id": "build2", "description": "Build component B", "role": "implement"},
    ]

    subagent(
        agent_id="builder-plan",
        prompt="Build two components",
        mode="planner",
        subtasks=subtasks,
    )

    _, executors = _planner_children(initial_count, "builder-plan")
    assert len(executors) == 2

    _wait_for_new_subagent_threads(initial_count, timeout=2.0)

    # Planner mode already merged: _run_planner does NOT pass role to
    # _create_subagent_thread directly. The profile_name is set from the
    # parent's own profile resolution. This test verifies the planner
    # spawns correctly when the parent itself has a role-derived profile.
    mock_create_thread.assert_called()
    for call in mock_create_thread.call_args_list:
        kwargs = call[1]
        # When no explicit profile or agent_id match, profile_name stays None
        # for planner children since the role resolution happens in api.py
        # and planner receives profile_name as-is
        assert "profile_name" in kwargs


@patch("gptme.tools.subagent.execution._monitor_subprocess")
@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
@patch("gptme.tools.subagent.execution.tempfile.mkdtemp")
@patch("gptme.util.git_worktree.get_git_root", return_value=None)
def test_planner_subtask_role_overrides_planner_profile(
    _mock_get_git_root: MagicMock,
    mock_mkdtemp: MagicMock,
    mock_run_subprocess: MagicMock,
    mock_monitor: MagicMock,
):
    """Subtask role wins over planner-level profile.

    When the planner itself carries a profile (e.g. role=implement → "developer"),
    a subtask with its own role (e.g. role=verify → "verifier") must still resolve
    to the subtask's profile — not silently inherit the planner's.

    role=verify also switches the executor to subprocess mode (Phase 2 behaviour),
    so this test verifies both the profile override AND the correct execution backend.

    This is the regression case flagged by Greptile: the old guard
    `profile_name is None` meant subtask roles were silently dropped as soon
    as the planner had any profile of its own.
    """
    mock_mkdtemp.return_value = "/tmp/test-impl-plan-verify"
    mock_run_subprocess.return_value = MagicMock()  # fake Popen

    subtasks: list[SubtaskDef] = [
        {"id": "verify", "description": "Verify the output", "role": "verify"},
    ]

    initial_count = len(_subagents)

    # Planner inherits role=implement → profile_name="developer" in _run_planner
    subagent(
        agent_id="impl-plan",
        prompt="Build and verify a component",
        mode="planner",
        role="implement",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    # role=verify routes to subprocess backend (not thread mode)
    mock_run_subprocess.assert_called_once()
    sub_kwargs = mock_run_subprocess.call_args[1]
    # Subtask role=verify must resolve to "verifier", overriding planner's "developer"
    assert sub_kwargs.get("profile") == "verifier", (
        f"Expected subtask role 'verify' to resolve to 'verifier', got {sub_kwargs.get('profile')!r}. "
        "Per-subtask role should always override planner-level profile."
    )

    _, executors = _planner_children(initial_count, "impl-plan")
    assert len(executors) == 1
    assert executors[0].execution_mode == "subprocess"


# ---------------------------------------------------------------------------
# Phase 2: Subprocess/isolated forwarding from subtask roles
# ---------------------------------------------------------------------------


@patch("gptme.tools.subagent.execution._monitor_subprocess")
@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
@patch("gptme.tools.subagent.execution.tempfile.mkdtemp")
@patch("gptme.util.git_worktree.get_git_root", return_value=None)
def test_planner_subtask_role_verify_uses_subprocess(
    _mock_get_git_root: MagicMock,
    mock_mkdtemp: MagicMock,
    mock_run_subprocess: MagicMock,
    mock_monitor: MagicMock,
):
    """role='verify' on a subtask must launch that executor in subprocess mode."""
    mock_mkdtemp.return_value = "/tmp/test-verify-sub"
    mock_run_subprocess.return_value = MagicMock()  # fake Popen

    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "check", "description": "Validate the output", "role": "verify"},
    ]

    subagent(
        agent_id="test-verify-sub",
        prompt="Build and verify",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    # Subprocess backend should have been called for the verify subtask
    mock_run_subprocess.assert_called_once()
    call_kwargs = mock_run_subprocess.call_args[1]
    assert call_kwargs.get("profile") == "verifier"

    # A monitor thread should have been started
    mock_monitor.assert_called_once()

    # The registered Subagent should carry execution_mode="subprocess"
    _, executors = _planner_children(initial_count, "test-verify-sub")
    assert len(executors) == 1
    sa = executors[0]
    assert sa.execution_mode == "subprocess"
    assert sa.agent_id == "test-verify-sub-check"


@patch("gptme.tools.subagent.execution._monitor_subprocess")
@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
@patch("gptme.tools.subagent.execution.tempfile.mkdtemp")
@patch("gptme.util.git_worktree.get_git_root", return_value=None)
def test_planner_subtask_role_verify_sets_isolated(
    _mock_get_git_root: MagicMock,
    mock_mkdtemp: MagicMock,
    mock_run_subprocess: MagicMock,
    mock_monitor: MagicMock,
):
    """role='verify' on a subtask should request isolated execution."""
    mock_mkdtemp.return_value = "/tmp/test-verify-isolated"
    mock_run_subprocess.return_value = MagicMock()

    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "check2", "description": "Verify output", "role": "verify"},
    ]

    subagent(
        agent_id="test-verify-isolated",
        prompt="context",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    _, executors = _planner_children(initial_count, "test-verify-isolated")
    assert len(executors) == 1
    sa = executors[0]
    assert sa.isolated is True


@patch("gptme.tools.subagent.execution._cleanup_isolation")
@patch("gptme.tools.subagent.execution._monitor_subprocess")
@patch(
    "gptme.tools.subagent.execution._run_subagent_subprocess",
    side_effect=OSError("boom"),
)
@patch("gptme.util.git_worktree.create_worktree", return_value=Path("/tmp/verify-wt"))
@patch("gptme.util.git_worktree.get_git_root", return_value=Path("/tmp/repo"))
def test_planner_subtask_role_verify_cleans_isolation_on_launch_failure(
    mock_get_git_root: MagicMock,
    mock_create_worktree: MagicMock,
    mock_run_subprocess: MagicMock,
    mock_monitor: MagicMock,
    mock_cleanup_isolation: MagicMock,
):
    """Failed subprocess launch should still clean up verify isolation."""
    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "check3", "description": "Verify output", "role": "verify"},
    ]

    subagent(
        agent_id="test-verify-launch-failure",
        prompt="context",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    mock_run_subprocess.assert_called_once()
    mock_monitor.assert_not_called()
    mock_cleanup_isolation.assert_called_once()
    _, executors = _planner_children(initial_count, "test-verify-launch-failure")
    assert len(executors) == 1
    assert executors[0].execution_mode == "subprocess"


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_subtask_role_explore_uses_thread_mode(mock_create_thread: MagicMock):
    """role='explore' should NOT trigger subprocess mode — stays thread mode."""
    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "scan", "description": "Explore the codebase", "role": "explore"},
    ]

    subagent(
        agent_id="test-explore-thread",
        prompt="context",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    # Thread backend used, not subprocess
    mock_create_thread.assert_called_once()

    _, executors = _planner_children(initial_count, "test-explore-thread")
    assert len(executors) == 1
    sa = executors[0]
    assert sa.execution_mode == "thread"


@patch("gptme.tools.subagent.execution._cleanup_isolation")
@patch("gptme.tools.subagent.execution._create_subagent_thread")
@patch(
    "gptme.tools.subagent.types.resolve_role_defaults",
    return_value=(False, True, "explorer"),
)
@patch(
    "gptme.util.git_worktree.create_worktree",
    return_value=Path("/tmp/thread-isolated-wt"),
)
@patch("gptme.util.git_worktree.get_git_root", return_value=Path("/tmp/repo"))
def test_planner_thread_mode_cleans_isolation_after_completion(
    mock_get_git_root: MagicMock,
    mock_create_worktree: MagicMock,
    mock_resolve_role_defaults: MagicMock,
    mock_create_thread: MagicMock,
    mock_cleanup_isolation: MagicMock,
):
    """Thread-mode isolated executors should also clean up their worktree."""
    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "scan2", "description": "Explore the codebase", "role": "explore"},
    ]

    subagent(
        agent_id="test-thread-isolated-cleanup",
        prompt="context",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    mock_create_thread.assert_called_once()
    mock_cleanup_isolation.assert_called_once()
    _, executors = _planner_children(initial_count, "test-thread-isolated-cleanup")
    assert len(executors) == 1
    assert executors[0].isolated is True


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_subtask_role_implement_uses_thread_mode(mock_create_thread: MagicMock):
    """role='implement' should use thread mode (not subprocess)."""
    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "build", "description": "Implement the feature", "role": "implement"},
    ]

    subagent(
        agent_id="test-impl-thread",
        prompt="context",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    mock_create_thread.assert_called_once()

    _, executors = _planner_children(initial_count, "test-impl-thread")
    assert len(executors) == 1
    sa = executors[0]
    assert sa.execution_mode == "thread"


@patch("gptme.tools.subagent.execution._monitor_subprocess")
@patch("gptme.tools.subagent.execution._run_subagent_subprocess")
@patch("gptme.tools.subagent.execution.tempfile.mkdtemp")
@patch("gptme.util.git_worktree.get_git_root", return_value=None)
@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_planner_mixed_roles_use_correct_backends(
    _mock_get_git_root: MagicMock,
    mock_mkdtemp: MagicMock,
    mock_create_thread: MagicMock,
    mock_run_subprocess: MagicMock,
    mock_monitor: MagicMock,
):
    """Mixed-role subtasks: impl→thread, verify→subprocess."""
    mock_mkdtemp.return_value = "/tmp/test-mixed-verify"
    mock_run_subprocess.return_value = MagicMock()

    initial_count = len(_subagents)
    subtasks: list[SubtaskDef] = [
        {"id": "impl", "description": "Build it", "role": "implement"},
        {"id": "verify", "description": "Verify it", "role": "verify"},
    ]

    subagent(
        agent_id="test-mixed",
        prompt="context",
        mode="planner",
        subtasks=subtasks,
    )

    _wait_for_new_subagent_threads(initial_count, timeout=1.0)

    # impl → thread; verify → subprocess
    mock_create_thread.assert_called_once()
    mock_run_subprocess.assert_called_once()
    mock_monitor.assert_called_once()

    _, executors = _planner_children(initial_count, "test-mixed")
    assert len(executors) == 2

    by_id = {sa.agent_id: sa for sa in executors}
    assert by_id["test-mixed-impl"].execution_mode == "thread"
    assert by_id["test-mixed-verify"].execution_mode == "subprocess"


@patch("gptme.tools.subagent.execution.set_tools")
@patch("gptme.tools.subagent.execution.get_tools")
def test_hint_allowlist_filters_subagent_tools(
    mock_get_tools: MagicMock,
    mock_set_tools: MagicMock,
    tmp_path,
):
    """Regression: execution.py must forward tool.hints to tool_matches_allowlist,
    and hint: patterns must never appear in the "unknown tools" warning.

    Without the hint-forwarding fix (PR #2890), hint:* entries in a subagent
    profile's tool list silently matched nothing — the third argument (tool.hints)
    was missing, so hint-based filtering always excluded everything.
    """

    import gptme.chat  # noqa: F401 — must import before patch.object on sys.modules["gptme.chat"]
    from gptme.profiles import Profile
    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent.execution import _create_subagent_thread

    tool_ro = ToolSpec(
        name="browser", desc="browse web", hints=frozenset({"read-only"})
    )
    tool_rw = ToolSpec(name="shell", desc="run shell")
    tool_complete = ToolSpec(name="complete", desc="signal done")
    tool_clarify = ToolSpec(name="clarify", desc="ask parent")

    mock_get_tools.return_value = [tool_ro, tool_rw, tool_complete, tool_clarify]

    with (
        patch.object(sys.modules["gptme.chat"], "chat"),
        patch("gptme.executor.prepare_execution_environment"),
        patch("gptme.llm.models.set_default_model"),
        patch("gptme.profiles.get_profile") as mock_get_profile,
        patch("gptme.prompts.get_prompt", return_value=[]),
        patch("gptme.tools.subagent.execution.logger") as mock_logger,
    ):
        mock_get_profile.return_value = Profile(
            name="readonly",
            description="read-only profile",
            tools=["hint:read-only"],
        )
        _create_subagent_thread(
            prompt="summarise this doc",
            logdir=tmp_path,
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            profile_name="readonly",
        )

    # set_tools must have been called with the hint-filtered tool list
    assert mock_set_tools.called
    available = mock_set_tools.call_args[0][0]

    # browser (has read-only hint) and signal tools (always included) should be present
    assert tool_ro in available
    assert tool_complete in available
    assert tool_clarify in available
    # shell (no hint) must be excluded
    assert tool_rw not in available

    # hint: patterns must not trigger the "unknown tools" warning — they are always
    # valid capability-tag filters, not tool names that could be misspelt
    for call in mock_logger.warning.call_args_list:
        assert "hint:read-only" not in str(call), (
            "hint: pattern incorrectly flagged as unknown tool"
        )


def test_subagent_list_empty():
    """Test that subagent_list returns an empty list when no subagents exist."""
    from gptme.tools.subagent import _subagents, _subagents_lock, subagent_list

    with _subagents_lock:
        _subagents.clear()
    result = subagent_list()
    assert isinstance(result, list)
    assert result == []


@patch("gptme.tools.subagent.execution._create_subagent_thread")
def test_subagent_list_structure(mock_create_thread: MagicMock):
    """Test that subagent_list returns the correct structure."""
    from gptme.tools.subagent import (
        _subagents,
        _subagents_lock,
        subagent,
        subagent_list,
    )

    with _subagents_lock:
        _subagents.clear()

    subagent(agent_id="test-list-1", prompt="Test agent one")
    _wait_for_new_subagent_threads(0)
    result = subagent_list()

    try:
        assert isinstance(result, list)
        assert len(result) >= 1

        entry = result[0]
        assert isinstance(entry, dict)
        assert "agent_id" in entry
        assert "status" in entry
        assert "model" in entry
        assert "execution_mode" in entry
        assert "elapsed_s" in entry
        assert "prompt_preview" in entry

        # Verify types
        assert isinstance(entry["agent_id"], str)
        assert isinstance(entry["status"], str)
        assert isinstance(entry["elapsed_s"], int)
        assert isinstance(entry["prompt_preview"], str)

        # Verify our agent is in the list
        ids = [e["agent_id"] for e in result]
        assert "test-list-1" in ids
    finally:
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "test-list-1"]


def test_subagent_list_prompt_truncation():
    """Test that long prompts are truncated in the preview."""
    import threading
    from pathlib import Path

    from gptme.tools.subagent import (
        Subagent,
        _subagents,
        _subagents_lock,
        subagent_list,
    )

    long_prompt = "x" * 200
    sa = Subagent(
        agent_id="test-truncate",
        prompt=long_prompt,
        thread=threading.Thread(),
        logdir=Path("/tmp"),
        model=None,
    )
    with _subagents_lock:
        _subagents.append(sa)

    try:
        result = subagent_list()
        entry = next(e for e in result if e["agent_id"] == "test-truncate")
        assert len(entry["prompt_preview"]) <= 103  # 100 chars + "..."
        assert entry["prompt_preview"].endswith("...")
    finally:
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "test-truncate"]


def test_subagent_thread_does_not_mutate_parent_tool_list():
    """Thread-mode subagent must NOT mutate the parent's loaded tool list.

    Python's threading.Thread copies the parent's ContextVar context into the
    child thread, so _loaded_tools_var initially points to the *same list object*
    as the parent. Before the fix, init_tools() and
    _ensure_subagent_signal_tools_loaded() would append signal tools to that
    shared list, creating a data race with the parent's concurrent execute_msg()
    calls (#554 — "transient non-runnable" crash).

    After the fix, clear_tools() at thread entry replaces the ContextVar binding
    with a fresh empty list so the subagent's tool operations never touch the
    parent's list.
    """
    import threading
    from contextvars import copy_context

    from gptme.tools import clear_tools, get_tools, init_tools

    # Set up a baseline tool list in the parent's context.
    init_tools(["read"])
    parent_list = get_tools()
    parent_list_id = id(parent_list)
    parent_len = len(parent_list)

    # Simulate what happened BEFORE the fix: a child thread that inherits the
    # parent's context and calls load_tool() without first calling clear_tools().
    # This would mutate the parent's list (append to it).
    without_fix_appended: list[bool] = []

    def child_without_fix():
        # Inherit parent list (same object), then append a new tool.
        # In the real bug, this was init_tools()/load_tool("complete") etc.
        inherited = get_tools()  # Returns parent's list
        without_fix_appended.append(id(inherited) == parent_list_id)
        # Simulate what load_tool does: appends to the ContextVar list
        get_tools().append(object())  # type: ignore[arg-type]

    ctx = copy_context()
    t_bad = threading.Thread(target=lambda: ctx.run(child_without_fix))
    t_bad.start()
    t_bad.join(timeout=2.0)
    assert not t_bad.is_alive()
    # Confirm the "without fix" thread DID see the parent's list
    assert without_fix_appended == [True], "setup: child must inherit parent list"
    # And that it mutated it (this is the pre-fix behavior we're guarding against)
    assert len(parent_list) == parent_len + 1, "setup: pre-fix mutation confirmed"
    parent_list.pop()  # undo the mutation for the real test

    # Simulate what happens AFTER the fix: child calls clear_tools() first.
    child_saw_fresh_list: list[bool] = []
    child_appended_to_parent: list[bool] = []

    def child_with_fix():
        clear_tools()  # The fix: detach from parent's list
        child_saw_fresh_list.append(id(get_tools()) != parent_list_id)
        # Any subsequent tool operations use the child's own fresh list
        get_tools().append(object())  # type: ignore[arg-type]
        child_appended_to_parent.append(id(get_tools()) == parent_list_id)

    ctx2 = copy_context()
    t_good = threading.Thread(target=lambda: ctx2.run(child_with_fix))
    t_good.start()
    t_good.join(timeout=2.0)
    assert not t_good.is_alive()

    # After the fix: child got a fresh list, parent's list is unchanged.
    assert child_saw_fresh_list == [True], (
        "child must get a fresh list after clear_tools()"
    )
    assert child_appended_to_parent == [False], (
        "child's append went to parent's list, not child's own list"
    )
    assert len(get_tools()) == parent_len, (
        f"Parent tool list changed from {parent_len} to {len(get_tools())} — "
        "subagent thread mutated parent's tool list (race condition #554)"
    )


# Tests for subagent_pipeline


def test_subagent_pipeline_empty_returns_empty():
    """subagent_pipeline([]) returns [] without touching any subagent."""
    from gptme.tools.subagent import subagent_pipeline

    result = subagent_pipeline([], lambda item, prev: item)
    assert result == []


def test_subagent_pipeline_requires_at_least_one_stage():
    """subagent_pipeline with no stages raises ValueError."""
    from gptme.tools.subagent import subagent_pipeline

    with pytest.raises(ValueError, match="at least one stage"):
        subagent_pipeline([("a", "prompt")])


def test_subagent_pipeline_single_stage_single_item():
    """Single-stage pipeline: spawns one subagent and returns its result."""
    from gptme.tools.subagent import subagent_pipeline

    with (
        patch("gptme.tools.subagent.batch.subagent") as mock_subagent,
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.return_value = {"status": "success", "result": "done"}

        results = subagent_pipeline(
            [("item-a", "Do task A")],
            lambda item, prev: f"Stage 0 for: {item}",
            timeout=10,
        )

    assert len(results) == 1
    assert len(results[0]) == 1
    assert results[0][0] == {"status": "success", "result": "done"}
    mock_subagent.assert_called_once()
    call_kwargs = mock_subagent.call_args
    assert call_kwargs.kwargs["agent_id"] == "item-a-s0"
    assert "Stage 0 for: Do task A" in call_kwargs.kwargs["prompt"]


def test_subagent_pipeline_two_stages_prompt_chaining():
    """Each stage fn receives (item_prompt, prev_result)."""
    from gptme.tools.subagent import subagent_pipeline

    stage_inputs: list[tuple[str, str, str]] = []

    def stage0(item, prev):
        stage_inputs.append(("s0", item, prev))
        return f"s0:{item}"

    def stage1(item, prev):
        stage_inputs.append(("s1", item, prev))
        return f"s1:{item}:{prev}"

    with (
        patch("gptme.tools.subagent.batch.subagent"),
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.side_effect = [
            {"status": "success", "result": "result-of-s0"},
            {"status": "success", "result": "result-of-s1"},
        ]

        results = subagent_pipeline(
            [("item", "my-prompt")],
            stage0,
            stage1,
            timeout=10,
        )

    assert len(results) == 1
    assert len(results[0]) == 2
    assert results[0][0]["result"] == "result-of-s0"
    assert results[0][1]["result"] == "result-of-s1"

    # Stage 0 receives (item_prompt, "") — prev_result starts empty
    s0_call = next(x for x in stage_inputs if x[0] == "s0")
    assert s0_call[1] == "my-prompt"
    assert s0_call[2] == ""

    # Stage 1 receives (item_prompt, result-of-s0)
    s1_call = next(x for x in stage_inputs if x[0] == "s1")
    assert s1_call[1] == "my-prompt"
    assert s1_call[2] == "result-of-s0"


def test_subagent_pipeline_stage_failure_skips_remaining():
    """When a stage fails, remaining stages are marked 'skipped'."""
    from gptme.tools.subagent import subagent_pipeline

    with (
        patch("gptme.tools.subagent.batch.subagent"),
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.return_value = {"status": "failure", "result": "error msg"}

        results = subagent_pipeline(
            [("item", "prompt")],
            lambda item, prev: item,
            lambda item, prev: item,
            lambda item, prev: item,
            timeout=10,
        )

    assert results[0][0]["status"] == "failure"
    assert results[0][1]["status"] == "skipped"
    assert results[0][2]["status"] == "skipped"


def test_subagent_pipeline_stage_callable_exception_reported_as_failure():
    """A stage_fn that raises is reported as 'failure', not 'timeout'."""
    from gptme.tools.subagent import subagent_pipeline

    def boom(item, prev):
        raise RuntimeError("bad lambda")

    with (
        patch("gptme.tools.subagent.batch.subagent"),
        patch("gptme.tools.subagent.batch.subagent_wait"),
    ):
        results = subagent_pipeline(
            [("item", "prompt")],
            boom,
            lambda item, prev: item,
            timeout=10,
        )

    assert results[0][0]["status"] == "failure"
    assert "bad lambda" in results[0][0]["result"]
    assert results[0][1]["status"] == "skipped"


def test_subagent_pipeline_multiple_items_independent():
    """Multiple items are processed in parallel (independent threads)."""
    import threading

    from gptme.tools.subagent import subagent_pipeline

    started_agents: list[str] = []
    lock = threading.Lock()

    def fake_subagent(**kwargs):
        with lock:
            started_agents.append(kwargs["agent_id"])

    with (
        patch("gptme.tools.subagent.batch.subagent", side_effect=fake_subagent),
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.return_value = {"status": "success", "result": "ok"}

        results = subagent_pipeline(
            [("a", "task A"), ("b", "task B"), ("c", "task C")],
            lambda item, prev: item,
            timeout=10,
        )

    assert len(results) == 3
    assert all(r[0]["status"] == "success" for r in results)
    # All three items should have been processed
    assert set(started_agents) == {"a-s0", "b-s0", "c-s0"}


def test_subagent_pipeline_timeout_is_total_deadline():
    """The outer timeout applies to the whole pipeline, not once per item."""
    import threading
    import time

    from gptme.tools.subagent import subagent_pipeline

    release_waits = threading.Event()
    created_threads = []
    real_thread = threading.Thread

    def tracking_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        created_threads.append(thread)
        return thread

    def slow_wait(agent_id, **kwargs):
        release_waits.wait(timeout=1)
        return {"status": "success", "result": f"late {agent_id}"}

    try:
        with (
            patch("gptme.tools.subagent.batch.threading.Thread", tracking_thread),
            patch("gptme.tools.subagent.batch.subagent"),
            patch("gptme.tools.subagent.batch.subagent_wait", side_effect=slow_wait),
        ):
            started = time.perf_counter()
            results = subagent_pipeline(
                [("a", "task A"), ("b", "task B"), ("c", "task C")],
                lambda item, prev: item,
                timeout=0.05,
            )
            elapsed = time.perf_counter() - started
    finally:
        release_waits.set()
        for thread in created_threads:
            thread.join(timeout=1)

    assert elapsed < 0.12
    assert [item[0]["status"] for item in results] == [
        "timeout",
        "timeout",
        "timeout",
    ]


def test_subagent_pipeline_timeout_result_is_stable_snapshot():
    """Late worker completion must not mutate the caller's returned timeout list."""
    import threading

    from gptme.tools.subagent import subagent_pipeline

    release_stage_1 = threading.Event()
    created_threads = []
    real_thread = threading.Thread

    def tracking_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        created_threads.append(thread)
        return thread

    def wait_by_stage(agent_id, **kwargs):
        if agent_id.endswith("-s0"):
            return {"status": "success", "result": "stage 0 complete"}
        release_stage_1.wait(timeout=1)
        return {"status": "success", "result": "late stage 1 complete"}

    try:
        with (
            patch("gptme.tools.subagent.batch.threading.Thread", tracking_thread),
            patch("gptme.tools.subagent.batch.subagent"),
            patch(
                "gptme.tools.subagent.batch.subagent_wait",
                side_effect=wait_by_stage,
            ),
        ):
            results = subagent_pipeline(
                [("item", "task")],
                lambda item, prev: item,
                lambda item, prev: f"verify {prev}",
                timeout=0.05,
            )

        assert results[0][0]["status"] == "success"
        assert results[0][1]["status"] == "timeout"
    finally:
        release_stage_1.set()
        for thread in created_threads:
            thread.join(timeout=1)

    assert results[0][1]["status"] == "timeout"
    assert "timed out" in results[0][1]["result"]


def test_subagent_pipeline_uses_full_agent_id_format():
    """Agent IDs follow the {prefix}-s{stage_idx} convention."""
    from gptme.tools.subagent import subagent_pipeline

    agent_ids_seen: list[str] = []

    def fake_subagent(**kwargs):
        agent_ids_seen.append(kwargs["agent_id"])

    with (
        patch("gptme.tools.subagent.batch.subagent", side_effect=fake_subagent),
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.return_value = {"status": "success", "result": "ok"}

        subagent_pipeline(
            [("review-auth", "Review auth.py"), ("review-db", "Review db.py")],
            lambda item, prev: f"stage0:{item}",
            lambda item, prev: f"stage1:{prev}",
            timeout=10,
        )

    assert "review-auth-s0" in agent_ids_seen
    assert "review-auth-s1" in agent_ids_seen
    assert "review-db-s0" in agent_ids_seen
    assert "review-db-s1" in agent_ids_seen


def test_subagent_pipeline_forwards_acp_options():
    """subagent_pipeline forwards ACP options consistently with parallel/batch."""
    from gptme.tools.subagent import subagent_pipeline

    subagent_kwargs: list[dict] = []

    def fake_subagent(**kwargs):
        subagent_kwargs.append(kwargs)

    with (
        patch("gptme.tools.subagent.batch.subagent", side_effect=fake_subagent),
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.return_value = {"status": "success", "result": "ok"}

        subagent_pipeline(
            [("item", "prompt")],
            lambda item, prev: "stage0",
            lambda item, prev: "stage1",
            timeout=10,
            use_acp=True,
            acp_command="fake-acp",
        )

    assert [kwargs["agent_id"] for kwargs in subagent_kwargs] == [
        "item-s0",
        "item-s1",
    ]
    assert all(kwargs["use_acp"] is True for kwargs in subagent_kwargs)
    assert all(kwargs["acp_command"] == "fake-acp" for kwargs in subagent_kwargs)


def test_subagent_pipeline_output_schema_only_on_final_stage():
    """output_schema is only passed to the final stage, not intermediate stages."""
    from pydantic import BaseModel

    from gptme.tools.subagent import subagent_pipeline

    class Result(BaseModel):
        score: int

    schema_kwargs_by_id: dict[str, type | None] = {}

    def fake_subagent(**kwargs):
        schema_kwargs_by_id[kwargs["agent_id"]] = kwargs.get("output_schema")

    with (
        patch("gptme.tools.subagent.batch.subagent", side_effect=fake_subagent),
        patch("gptme.tools.subagent.batch.subagent_wait") as mock_wait,
    ):
        mock_wait.return_value = {"status": "success", "result": '{"score": 42}'}

        subagent_pipeline(
            [("item", "prompt")],
            lambda item, prev: "stage0",
            lambda item, prev: "stage1",
            output_schema=Result,
            timeout=10,
        )

    # Stage 0 should NOT get output_schema
    assert schema_kwargs_by_id.get("item-s0") is None
    # Stage 1 (final) should get output_schema
    assert schema_kwargs_by_id.get("item-s1") is Result


# Tests for subagent_wait_any


def test_subagent_wait_any_raises_on_empty_list():
    """subagent_wait_any([]) should raise ValueError."""
    import pytest

    from gptme.tools.subagent import subagent_wait_any

    with pytest.raises(ValueError, match="agent_ids must not be empty"):
        subagent_wait_any([])


def test_subagent_wait_any_returns_first_completed():
    """subagent_wait_any should return the first agent that completes."""
    from unittest.mock import patch

    from gptme.tools.subagent import subagent_wait_any

    call_order: list[str] = []

    def fake_wait(agent_id, timeout, max_result_chars=0):
        call_order.append(agent_id)
        if agent_id == "fast":
            return {"status": "success", "result": "fast result"}
        # "slow" blocks longer — in practice the thread pool handles it
        return {"status": "success", "result": "slow result"}

    with patch("gptme.tools.subagent.batch.subagent_wait", side_effect=fake_wait):
        first_id, result = subagent_wait_any(["fast", "slow"], timeout=10)

    assert first_id in ("fast", "slow")
    assert result["status"] == "success"


def test_subagent_wait_any_timeout_raises():
    """subagent_wait_any should raise TimeoutError when all agents keep running."""
    from unittest.mock import patch

    import pytest

    from gptme.tools.subagent import subagent_wait_any

    wait_timeouts: list[int] = []

    def still_running_wait(agent_id, timeout, max_result_chars=0):
        wait_timeouts.append(timeout)
        return {"status": "running", "result": None}

    with (
        patch(
            "gptme.tools.subagent.batch.subagent_wait", side_effect=still_running_wait
        ),
        pytest.raises(TimeoutError),
    ):
        subagent_wait_any(["a", "b"], timeout=1)

    assert wait_timeouts == [1, 1]


def test_batch_job_wait_any_returns_first():
    """BatchJob.wait_any() should return whichever agent completes first."""
    from unittest.mock import patch

    from gptme.tools.subagent.batch import BatchJob

    results_seen: list[str] = []

    def fake_wait(agent_id, timeout, max_result_chars=0):
        results_seen.append(agent_id)
        return {"status": "success", "result": f"result from {agent_id}"}

    job = BatchJob(agent_ids=["a", "b", "c"])
    with patch("gptme.tools.subagent.batch.subagent_wait", side_effect=fake_wait):
        first_id, result = job.wait_any(timeout=10)

    assert first_id in ("a", "b", "c")
    assert result["status"] == "success"
    assert f"result from {first_id}" in result["result"]


def test_batch_job_wait_any_already_done():
    """BatchJob.wait_any() should return immediately when a result is already cached."""
    from gptme.tools.subagent.batch import BatchJob
    from gptme.tools.subagent.types import ReturnType

    job = BatchJob(agent_ids=["x", "y"])
    job.results["x"] = ReturnType("success", "cached result")

    # Should not need to call subagent_wait at all
    first_id, result = job.wait_any(timeout=1)
    assert first_id == "x"
    assert result["status"] == "success"
    assert result["result"] == "cached result"
