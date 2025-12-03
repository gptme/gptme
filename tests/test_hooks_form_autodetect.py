"""Tests for form auto-detection hook."""

import pytest
from gptme.hooks.form_autodetect import (
    _detect_options_heuristic,
    _create_form_message,
    COMPILED_PATTERNS,
)


class TestHeuristicDetection:
    """Test the heuristic detection function."""

    def test_numbered_list(self):
        """Detect numbered list options."""
        content = """Please choose one of the following:
1. Option A
2. Option B
3. Option C
"""
        assert _detect_options_heuristic(content)

    def test_lettered_list(self):
        """Detect lettered list options."""
        content = """Select your preference:
a) First choice
b) Second choice
c) Third choice
"""
        assert _detect_options_heuristic(content)

    def test_bullet_list(self):
        """Detect bullet point options."""
        content = """Available options:
- React with TypeScript
- Vue with JavaScript
- Svelte with TypeScript
"""
        assert _detect_options_heuristic(content)

    def test_choose_keyword(self):
        """Detect 'please choose' pattern."""
        content = "Please choose one of the available options for deployment."
        assert _detect_options_heuristic(content)

    def test_select_keyword(self):
        """Detect 'select' pattern."""
        content = "Please select an option from the list."
        assert _detect_options_heuristic(content)

    def test_which_prefer(self):
        """Detect 'which would you prefer' pattern."""
        content = "Which would you prefer for the project structure?"
        assert _detect_options_heuristic(content)

    def test_options_header(self):
        """Detect 'Options:' header."""
        content = """Here are your Options:
Some option text here."""
        assert _detect_options_heuristic(content)

    def test_no_options_regular_text(self):
        """Regular text should not trigger detection."""
        content = """This is just a regular message explaining how things work.
There are no options to select here, just information."""
        assert not _detect_options_heuristic(content)

    def test_short_text_ignored(self):
        """Very short text should not trigger."""
        content = "Ok"
        assert not _detect_options_heuristic(content)


class TestFormMessageCreation:
    """Test form message creation from parsed data."""

    def test_create_form_message(self):
        """Create form message from parsed options."""
        parsed = {
            "detected": True,
            "question": "Which framework?",
            "options": ["React", "Vue", "Svelte"],
        }
        msg = _create_form_message(parsed)
        assert msg is not None
        assert "```form" in msg.content
        assert "selection" in msg.content
        assert "React, Vue, Svelte" in msg.content

    def test_create_form_message_no_question(self):
        """Create form message with default question."""
        parsed = {
            "detected": True,
            "options": ["A", "B", "C"],
        }
        msg = _create_form_message(parsed)
        assert msg is not None
        assert "Please select an option" in msg.content

    def test_create_form_message_empty(self):
        """Return None for empty parsed data."""
        assert _create_form_message(None) is None
        assert _create_form_message({}) is None
        assert _create_form_message({"detected": False}) is None
        assert _create_form_message({"detected": True, "options": []}) is None


class TestPatternCompilation:
    """Test that patterns compile correctly."""

    def test_patterns_compiled(self):
        """All patterns should be compiled."""
        assert len(COMPILED_PATTERNS) > 0
        for pattern in COMPILED_PATTERNS:
            assert hasattr(pattern, "search")

    def test_patterns_match_expected(self):
        """Patterns should match expected formats."""
        # Test numbered list pattern
        text = "1. First\n2. Second\n3. Third\n"
        assert any(p.search(text) for p in COMPILED_PATTERNS)

        # Test question with options
        text = "?\n- Option A\n- Option B\n"
        assert any(p.search(text) for p in COMPILED_PATTERNS)
