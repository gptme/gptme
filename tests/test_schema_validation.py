"""Test output schema validation for subagent tool."""

import pytest
from pydantic import BaseModel, Field, ValidationError


def test_schema_validation_logic():
    """Test the schema validation logic directly."""

    class TestSchema(BaseModel):
        name: str
        age: int = Field(gt=0)
        status: str

    # Test valid data
    valid_data = {"name": "Alice", "age": 30, "status": "success"}
    result = TestSchema.model_validate(valid_data)
    assert result.name == "Alice"
    assert result.age == 30
    assert result.status == "success"

    # Test invalid data (missing required field)
    invalid_data = {
        "name": "Bob",
        "status": "success",
        # age is missing
    }
    with pytest.raises(ValidationError):
        TestSchema.model_validate(invalid_data)

    # Test invalid data (wrong type)
    invalid_type_data = {
        "name": "Charlie",
        "age": "thirty",  # Should be int
        "status": "success",
    }
    with pytest.raises(ValidationError):
        TestSchema.model_validate(invalid_type_data)

    # Test invalid data (constraint violation)
    invalid_constraint_data = {
        "name": "Dave",
        "age": -5,  # Should be > 0
        "status": "success",
    }
    with pytest.raises(ValidationError):
        TestSchema.model_validate(invalid_constraint_data)


def test_subagent_signature():
    """Test that subagent function accepts output_schema parameter."""
    from gptme.tools.subagent import subagent
    import inspect

    sig = inspect.signature(subagent)
    params = list(sig.parameters.keys())

    assert "agent_id" in params
    assert "prompt" in params
    assert "output_schema" in params

    # Verify output_schema has None default
    assert sig.parameters["output_schema"].default is None
