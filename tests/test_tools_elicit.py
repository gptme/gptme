"""Tests for the elicit tool parsing logic."""

import json

from gptme.tools.elicit import execute_elicit, parse_elicitation_spec

# --- parse_elicitation_spec: valid specs ---


def test_parse_text_spec():
    """Parse a basic text elicitation spec."""
    spec = json.dumps({"type": "text", "prompt": "What is your name?"})
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "text"
    assert result.prompt == "What is your name?"


def test_parse_choice_spec():
    """Parse a choice spec with options."""
    spec = json.dumps(
        {
            "type": "choice",
            "prompt": "Pick a database:",
            "options": ["PostgreSQL", "SQLite", "MySQL"],
        }
    )
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "choice"
    assert result.prompt == "Pick a database:"
    assert result.options == ["PostgreSQL", "SQLite", "MySQL"]


def test_parse_multi_choice_spec():
    """Parse a multi-choice spec."""
    spec = json.dumps(
        {
            "type": "multi_choice",
            "prompt": "Select features:",
            "options": ["auth", "logging", "caching"],
        }
    )
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "multi_choice"
    assert result.options == ["auth", "logging", "caching"]


def test_parse_secret_spec():
    """Parse a secret spec with description."""
    spec = json.dumps(
        {
            "type": "secret",
            "prompt": "Enter API key:",
            "description": "Required for auth",
        }
    )
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "secret"
    assert result.description == "Required for auth"


def test_parse_confirmation_spec():
    """Parse a confirmation spec."""
    spec = json.dumps({"type": "confirmation", "prompt": "Proceed?"})
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "confirmation"


def test_parse_form_spec():
    """Parse a form spec with multiple fields."""
    spec = json.dumps(
        {
            "type": "form",
            "prompt": "Project setup:",
            "fields": [
                {"name": "project_name", "prompt": "Name?", "type": "text"},
                {
                    "name": "language",
                    "prompt": "Language?",
                    "type": "choice",
                    "options": ["python", "rust"],
                },
                {
                    "name": "tests",
                    "prompt": "Include tests?",
                    "type": "boolean",
                    "default": True,
                },
            ],
        }
    )
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "form"
    assert result.fields is not None
    assert len(result.fields) == 3
    assert result.fields[0].name == "project_name"
    assert result.fields[0].type == "text"
    assert result.fields[1].name == "language"
    assert result.fields[1].options == ["python", "rust"]
    assert result.fields[2].name == "tests"
    assert result.fields[2].default is True


def test_parse_spec_default_type():
    """Missing type defaults to 'text'."""
    spec = json.dumps({"prompt": "Enter something:"})
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.type == "text"


def test_parse_spec_with_default_value():
    """Default value is preserved."""
    spec = json.dumps({"type": "text", "prompt": "Name?", "default": "my-project"})
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.default == "my-project"


def test_parse_form_field_defaults():
    """Form fields have correct defaults for required and default."""
    spec = json.dumps(
        {
            "type": "form",
            "prompt": "Setup:",
            "fields": [{"name": "x", "prompt": "X?"}],
        }
    )
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.fields is not None
    assert result.fields[0].required is True
    assert result.fields[0].default is None
    assert result.fields[0].type == "text"


def test_parse_form_skips_non_dict_fields():
    """Non-dict entries in fields array are skipped."""
    spec = json.dumps(
        {
            "type": "form",
            "prompt": "Setup:",
            "fields": [
                {"name": "good", "prompt": "Good?"},
                "not-a-dict",
                42,
                {"name": "also_good", "prompt": "Also good?"},
            ],
        }
    )
    result = parse_elicitation_spec(spec)
    assert result is not None
    assert result.fields is not None
    assert len(result.fields) == 2
    assert result.fields[0].name == "good"
    assert result.fields[1].name == "also_good"


# --- parse_elicitation_spec: invalid specs ---


def test_parse_invalid_json():
    """Invalid JSON returns None."""
    result = parse_elicitation_spec("not json {{{")
    assert result is None


def test_parse_non_object_json():
    """JSON array (not object) returns None."""
    result = parse_elicitation_spec("[1, 2, 3]")
    assert result is None


def test_parse_invalid_type():
    """Unknown elicitation type returns None."""
    spec = json.dumps({"type": "invalid_type", "prompt": "Test"})
    result = parse_elicitation_spec(spec)
    assert result is None


def test_parse_missing_prompt():
    """Missing prompt returns None."""
    spec = json.dumps({"type": "text"})
    result = parse_elicitation_spec(spec)
    assert result is None


def test_parse_empty_prompt():
    """Empty prompt string returns None."""
    spec = json.dumps({"type": "text", "prompt": ""})
    result = parse_elicitation_spec(spec)
    assert result is None


# --- execute_elicit ---


def test_execute_no_code():
    """No code produces error message."""
    messages = list(execute_elicit(None, None, None))
    assert len(messages) == 1
    assert "No elicitation spec provided" in messages[0].content


def test_execute_empty_code():
    """Empty code produces error message."""
    messages = list(execute_elicit("", None, None))
    assert len(messages) == 1
    assert "No elicitation spec provided" in messages[0].content


def test_execute_invalid_json():
    """Invalid JSON spec produces error message."""
    messages = list(execute_elicit("{bad json", None, None))
    assert len(messages) == 1
    assert "Invalid elicitation spec" in messages[0].content


def test_execute_invalid_type():
    """Invalid type in spec produces error message."""
    spec = json.dumps({"type": "bogus", "prompt": "Test"})
    messages = list(execute_elicit(spec, None, None))
    assert len(messages) == 1
    assert "Invalid elicitation spec" in messages[0].content


# --- Tool registration ---


def test_elicit_tool_registered():
    """Test that the elicit tool is properly registered as available."""
    from gptme.tools import get_available_tools, init_tools

    init_tools([])
    available = get_available_tools()
    available_names = [t.name for t in available]
    assert "elicit" in available_names
