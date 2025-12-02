"""Tests for the form tool."""

from gptme.tools import init_tools
from gptme.tools.form import parse_field, parse_form_content


def test_parse_field_text():
    """Test parsing a simple text field."""
    field = parse_field("name: What is your name?")
    assert field is not None
    assert field["name"] == "name"
    assert field["prompt"] == "What is your name?"
    assert field["type"] == "text"


def test_parse_field_select():
    """Test parsing a select field with options."""
    field = parse_field("priority: Priority level [low, medium, high]")
    assert field is not None
    assert field["name"] == "priority"
    assert field["prompt"] == "Priority level"
    assert field["type"] == "select"
    assert field["options"] == ["low", "medium", "high"]


def test_parse_field_boolean():
    """Test parsing a boolean field."""
    field = parse_field("confirm: Proceed? [yes, no]")
    assert field is not None
    assert field["name"] == "confirm"
    assert field["prompt"] == "Proceed?"
    assert field["type"] == "boolean"


def test_parse_field_number():
    """Test parsing a number field."""
    field = parse_field("count: How many items? (number)")
    assert field is not None
    assert field["name"] == "count"
    assert field["prompt"] == "How many items?"
    assert field["type"] == "number"


def test_parse_field_empty():
    """Test parsing empty or invalid lines."""
    assert parse_field("") is None
    assert parse_field("   ") is None
    assert parse_field("no colon here") is None
    assert parse_field(":") is None
    assert parse_field("field:") is None


def test_parse_form_content():
    """Test parsing a complete form definition."""
    content = """
    name: Project name?
    description: Brief description?
    language: Primary language [python, javascript, rust]
    priority: Priority [low, medium, high]
    count: Number of tasks (number)
    confirm: Ready to proceed? [yes, no]
    """
    fields = parse_form_content(content)
    
    assert len(fields) == 6
    assert fields[0]["name"] == "name"
    assert fields[0]["type"] == "text"
    assert fields[1]["name"] == "description"
    assert fields[1]["type"] == "text"
    assert fields[2]["name"] == "language"
    assert fields[2]["type"] == "select"
    assert fields[3]["name"] == "priority"
    assert fields[3]["type"] == "select"
    assert fields[4]["name"] == "count"
    assert fields[4]["type"] == "number"
    assert fields[5]["name"] == "confirm"
    assert fields[5]["type"] == "boolean"


def test_form_tool_registered():
    """Test that the form tool is properly registered."""
    init_tools([])
    # Form tool is disabled by default, so it won't be in the loaded tools
    # but it should be in available tools
    from gptme.tools import get_available_tools
    
    available = get_available_tools()
    available_names = [t.name for t in available]
    assert "form" in available_names


def test_parse_field_select_with_spaces():
    """Test parsing select field with spaces in options."""
    field = parse_field("status: Current status [in progress, on hold, completed]")
    assert field is not None
    assert field["type"] == "select"
    assert field["options"] == ["in progress", "on hold", "completed"]


def test_parse_field_boolean_case_insensitive():
    """Test that yes/no detection is case insensitive."""
    field1 = parse_field("ok: Continue? [Yes, No]")
    assert field1 is not None
    assert field1["type"] == "boolean"
    
    field2 = parse_field("ok: Continue? [YES, NO]")
    assert field2 is not None
    assert field2["type"] == "boolean"
