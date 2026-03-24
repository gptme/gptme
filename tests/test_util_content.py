"""Tests for gptme.util.content — message command detection and content summarization."""

from gptme.util.content import extract_content_summary, is_message_command

# ──────────────────────────────────────────────
# is_message_command
# ──────────────────────────────────────────────


class TestIsMessageCommand:
    """Test command vs file-path detection."""

    def test_shell_command(self):
        assert is_message_command("/shell") is True

    def test_python_command(self):
        assert is_message_command("/python") is True

    def test_log_command(self):
        assert is_message_command("/log") is True

    def test_command_with_args(self):
        assert is_message_command("/shell echo hello") is True

    def test_file_path(self):
        """File paths have multiple slashes — not commands."""
        assert is_message_command("/path/to/file.md") is False

    def test_home_path(self):
        assert is_message_command("/home/user/project/") is False

    def test_nested_path(self):
        assert is_message_command("/usr/local/bin/python3") is False

    def test_root_slash(self):
        """Single / with nothing after — still exactly one slash."""
        assert is_message_command("/") is True

    def test_empty_string(self):
        assert is_message_command("") is False

    def test_no_leading_slash(self):
        assert is_message_command("shell") is False

    def test_regular_text(self):
        assert is_message_command("Hello, how are you?") is False

    def test_slash_in_middle(self):
        """Slash not at start is not a command."""
        assert is_message_command("run /shell please") is False

    def test_whitespace_before_slash(self):
        """Leading whitespace means no leading slash."""
        assert is_message_command(" /shell") is False

    def test_command_tools(self):
        assert is_message_command("/tools") is True

    def test_command_help(self):
        assert is_message_command("/help") is True

    def test_double_slash(self):
        """// has two slashes in first word — not a command."""
        assert is_message_command("//comment") is False

    def test_url_like(self):
        """URLs are not commands."""
        assert is_message_command("/api/v1/users") is False


# ──────────────────────────────────────────────
# extract_content_summary
# ──────────────────────────────────────────────


class TestExtractContentSummary:
    """Test content summarization and artifact stripping."""

    def test_plain_text(self):
        result = extract_content_summary("Hello world, this is a test.")
        assert result == "Hello world, this is a test."

    def test_empty_string(self):
        assert extract_content_summary("") == ""

    def test_removes_code_blocks(self):
        content = "Before\n```python\nprint('hello')\n```\nAfter"
        result = extract_content_summary(content)
        assert "print" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_four_backtick_blocks(self):
        content = "Before\n````\nsome code\n````\nAfter"
        result = extract_content_summary(content)
        assert "some code" not in result
        assert "Before" in result

    def test_removes_inline_code(self):
        content = "Use `pip install` to set up"
        result = extract_content_summary(content)
        assert "pip install" not in result
        assert "Use" in result
        assert "to set up" in result

    def test_removes_xml_tags(self):
        content = "Before <thinking>internal reasoning</thinking> After"
        result = extract_content_summary(content)
        assert "internal reasoning" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_standalone_xml(self):
        content = "Text <br/> more text"
        result = extract_content_summary(content)
        assert "<br/>" not in result

    def test_removes_shell_substitutions(self):
        content = "Run $(date +%Y) to get year"
        result = extract_content_summary(content)
        assert "$(date +%Y)" not in result

    def test_removes_template_content(self):
        content = "Config is {key: value} and done"
        result = extract_content_summary(content)
        assert "key: value" not in result

    def test_removes_bold_markers(self):
        content = "This is **important** text"
        result = extract_content_summary(content)
        assert "**" not in result
        assert "important" in result

    def test_removes_underline_markers(self):
        content = "This is __underlined__ text"
        result = extract_content_summary(content)
        assert "__" not in result
        assert "underlined" in result

    def test_truncation_at_max_words(self):
        content = " ".join(f"word{i}" for i in range(150))
        result = extract_content_summary(content, max_words=100)
        assert result.endswith("...")
        # Should have at most 100 words + "..."
        words = result.removesuffix("...").split()
        assert len(words) <= 100

    def test_no_truncation_under_limit(self):
        content = "Short message"
        result = extract_content_summary(content, max_words=100)
        assert not result.endswith("...")

    def test_custom_max_words(self):
        content = "one two three four five six seven eight nine ten"
        result = extract_content_summary(content, max_words=5)
        assert result.endswith("...")
        assert "six" not in result

    def test_whitespace_normalization(self):
        content = "Hello   \n\n  world   \t  test"
        result = extract_content_summary(content)
        # All whitespace collapsed to single spaces
        assert "  " not in result

    def test_interrupted_pattern_removed(self):
        content = "Working on task... ^C Interrupted"
        result = extract_content_summary(content)
        assert "Interrupted" not in result

    def test_aborted_suffix_removed(self):
        content = "Running process aborted"
        result = extract_content_summary(content)
        assert "aborted" not in result

    def test_cancelled_suffix_removed(self):
        content = "Operation cancelled"
        result = extract_content_summary(content)
        assert "cancelled" not in result

    def test_complex_content(self):
        """Test with realistic mixed content."""
        content = """I analyzed the code and found the issue.

```python
def broken_function():
    return None  # bug here
```

The **problem** is in the `return` statement. We should fix it by <thinking>let me reason about this</thinking> returning the correct value.

$(echo "debug output")
"""
        result = extract_content_summary(content)
        assert "broken_function" not in result
        assert "thinking" not in result
        assert "echo" not in result
        assert "problem" in result
        assert "analyzed" in result

    def test_removes_brackets_and_parens(self):
        content = "List [item1] and (item2) here"
        result = extract_content_summary(content)
        assert "[" not in result
        assert "]" not in result
        assert "(" not in result
        assert ")" not in result

    def test_eof_pattern_removed(self):
        content = 'Some text EOF")'
        result = extract_content_summary(content)
        assert "EOF" not in result
