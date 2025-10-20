import json

import pytest
from pydantic import BaseModel

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


class TestSchema(BaseModel):
    """Test schema for planner mode tests."""

    result: str
    count: int


def test_planner_mode_requires_subtasks():
    """Test that planner mode requires subtasks parameter."""
    with pytest.raises(ValueError, match="Planner mode requires subtasks"):
        subagent(agent_id="test-planner", prompt="Test task", mode="planner")


def test_planner_mode_spawns_executors():
    """Test that planner mode spawns executor subagents."""
    initial_count = len(_subagents)

    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "First task", "schema": None},
        {"id": "task2", "description": "Second task", "schema": TestSchema},
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

    # Check schemas are assigned
    executors = _subagents[-2:]
    schemas = [e.output_schema for e in executors]
    assert None in schemas  # task1 has no schema
    assert TestSchema in schemas  # task2 has TestSchema


def test_planner_mode_executor_prompts():
    """Test that executor prompts include context and subtask description."""
    subtasks: list[SubtaskDef] = [
        {"id": "task1", "description": "Do something specific", "schema": None}
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

    subagent(agent_id="test-executor", prompt="Simple task", output_schema=TestSchema)

    # Should spawn 1 executor
    assert len(_subagents) == initial_count + 1

    # Check it has the schema
    executor = _subagents[-1]
    assert executor.output_schema == TestSchema
    assert executor.agent_id == "test-executor"
