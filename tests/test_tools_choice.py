"""Tests for the choice tool parsing logic."""

from gptme.tools.choice import (
    execute_choice,
    parse_options_from_content,
    parse_options_from_kwargs,
)

# --- parse_options_from_content ---


def test_parse_content_with_question():
    """First line ending with ? is treated as question."""
    question, options = parse_options_from_content(
        "What should we do?\nOption A\nOption B\nOption C"
    )
    assert question == "What should we do?"
    assert options == ["Option A", "Option B", "Option C"]


def test_parse_content_without_question():
    """Lines not ending with ? are all treated as options."""
    question, options = parse_options_from_content("Option A\nOption B\nOption C")
    assert question is None
    assert options == ["Option A", "Option B", "Option C"]


def test_parse_content_empty():
    """Empty content returns no question and no options."""
    question, options = parse_options_from_content("")
    assert question is None
    assert options == []


def test_parse_content_whitespace_only():
    """Whitespace-only content returns no question and no options."""
    question, options = parse_options_from_content("   \n  \n   ")
    assert question is None
    assert options == []


def test_parse_content_single_option():
    """Single line without question mark is one option."""
    question, options = parse_options_from_content("Only option")
    assert question is None
    assert options == ["Only option"]


def test_parse_content_single_question():
    """Single line with question mark is question, no options."""
    question, options = parse_options_from_content("What to do?")
    assert question == "What to do?"
    assert options == []


def test_parse_content_strips_whitespace():
    """Leading/trailing whitespace on lines is stripped."""
    question, options = parse_options_from_content("  Pick one?  \n  Alpha  \n  Beta  ")
    assert question == "Pick one?"
    assert options == ["Alpha", "Beta"]


def test_parse_content_skips_blank_lines():
    """Blank lines between options are skipped."""
    question, options = parse_options_from_content("Choose?\n\nA\n\nB\n\nC\n")
    assert question == "Choose?"
    assert options == ["A", "B", "C"]


def test_parse_content_numbered_options():
    """Numbered options (1. 2. 3.) are returned as-is by parser."""
    question, options = parse_options_from_content(
        "Example question?\n1. Option one\n2. Option two\n3. Option three"
    )
    assert question == "Example question?"
    assert options == ["1. Option one", "2. Option two", "3. Option three"]


# --- parse_options_from_kwargs ---


def test_parse_kwargs_with_question_and_options():
    """Both question and options provided as kwargs."""
    question, options = parse_options_from_kwargs(
        {"question": "Pick one:", "options": "Alpha\nBeta\nGamma"}
    )
    assert question == "Pick one:"
    assert options == ["Alpha", "Beta", "Gamma"]


def test_parse_kwargs_options_only():
    """Options without explicit question, first option not a question."""
    question, options = parse_options_from_kwargs({"options": "Foo\nBar\nBaz"})
    assert question is None
    assert options == ["Foo", "Bar", "Baz"]


def test_parse_kwargs_question_in_options():
    """First option ending with ? is extracted as question."""
    question, options = parse_options_from_kwargs(
        {"options": "What to pick?\nFoo\nBar"}
    )
    assert question == "What to pick?"
    assert options == ["Foo", "Bar"]


def test_parse_kwargs_empty_options():
    """Empty options string returns empty list."""
    question, options = parse_options_from_kwargs({"options": ""})
    assert question is None
    assert options == []


def test_parse_kwargs_empty_dict():
    """Empty kwargs returns no question and no options."""
    question, options = parse_options_from_kwargs({})
    assert question is None
    assert options == []


def test_parse_kwargs_strips_option_whitespace():
    """Whitespace in individual options is stripped."""
    question, options = parse_options_from_kwargs({"options": "  A  \n  B  \n  C  "})
    assert question is None
    assert options == ["A", "B", "C"]


# --- execute_choice ---


def test_execute_no_options():
    """No input produces 'No options provided' message."""
    messages = list(execute_choice(None, None, None))
    assert len(messages) == 1
    assert "No options provided" in messages[0].content


def test_execute_empty_content():
    """Empty content produces 'No options provided' message."""
    messages = list(execute_choice("", None, None))
    assert len(messages) == 1
    assert "No options provided" in messages[0].content


def test_execute_empty_kwargs():
    """Empty kwargs produces 'No options provided' message."""
    messages = list(execute_choice(None, None, {}))
    assert len(messages) == 1
    assert "No options provided" in messages[0].content


# --- Tool registration ---


def test_choice_tool_registered():
    """Test that the choice tool is properly registered as available."""
    from gptme.tools import get_available_tools, init_tools

    init_tools([])
    available = get_available_tools()
    available_names = [t.name for t in available]
    assert "choice" in available_names
