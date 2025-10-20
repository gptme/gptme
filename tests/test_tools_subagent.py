import json

import pytest

from gptme.tools.subagent import SubtaskDef, _extract_json, _subagents, subagent


def test_extract_json_block():
    s = """
Here is a result:
```json
{ "status": "ok" }
```
"""
    assert _extract_json(s) == '{ "status": "ok" }'


def test_extract_json_raw():
    s = """
{
  "result": "The 49th Fibonacci number is 7778742049.",
  "status": "success"
}
"""
    assert json.loads(_extract_json(s)) == json.loads(s)


def test_extract_json_empty():
    s = ""
    assert _extract_json(s) == ""


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
