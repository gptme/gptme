import pytest

from gptme.tools.subagent import SubtaskDef, _subagents, subagent


@pytest.fixture
def mock_chat(monkeypatch):
    """Mock the chat function to prevent actual execution in threads."""

    def mock_chat_impl(*args, **kwargs):
        # Simulate a simple chat completion without actual LLM calls
        # This allows threads to complete successfully for testing
        return None

    # Mock both import locations where chat is used
    monkeypatch.setattr("gptme.chat", mock_chat_impl)
    monkeypatch.setattr("gptme.tools.subagent.chat", mock_chat_impl)

    # Also need to mock in the nested import inside _run_planner
    import gptme

    monkeypatch.setattr(gptme, "chat", mock_chat_impl)

    return mock_chat_impl


def test_planner_mode_requires_subtasks():
    """Test that planner mode requires subtasks parameter."""
    with pytest.raises(ValueError, match="Planner mode requires subtasks"):
        subagent(agent_id="test-planner", prompt="Test task", mode="planner")


def test_planner_mode_spawns_executors(mock_chat):
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


def test_planner_mode_executor_prompts(mock_chat):
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


def test_executor_mode_still_works(mock_chat):
    """Test that default executor mode still works as before."""
    initial_count = len(_subagents)

    subagent(agent_id="test-executor", prompt="Simple task")

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    # Check basic properties
    executor = _subagents[-1]
    assert executor.agent_id == "test-executor"
    assert executor.prompt == "Simple task"


def test_planner_parallel_mode(mock_chat):
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


def test_planner_sequential_mode(mock_chat):
    """Test that sequential mode spawns executors one by one."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "seq1", "description": "First sequential task"},
        {"id": "seq2", "description": "Second sequential task"},
    ]

    # Note: In real usage, threads would complete. In tests, they may still be running.
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


def test_context_mode_selective_with_workspace(mock_chat):
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


def test_context_mode_selective_multiple_components(mock_chat):
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
