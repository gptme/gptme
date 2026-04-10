"""Behavioral scenario: write-test-suite."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_write_tests_file_exists(ctx):
    """test_text_processor.py should exist."""
    content = ctx.files.get("test_text_processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return len(content.strip()) > 0


def check_write_tests_pass(ctx):
    """All written tests should pass."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_write_tests_covers_word_count(ctx):
    """Tests should cover the word_count function."""
    content = ctx.files.get("test_text_processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "word_count" in content


def check_write_tests_covers_truncate(ctx):
    """Tests should cover the truncate function."""
    content = ctx.files.get("test_text_processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "truncate" in content


def check_write_tests_covers_extract_emails(ctx):
    """Tests should cover the extract_emails function."""
    content = ctx.files.get("test_text_processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "extract_emails" in content


def check_write_tests_sufficient_count(ctx):
    """Should have at least 6 test functions for reasonable coverage."""
    content = ctx.files.get("test_text_processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return content.count("def test_") >= 6


test: "EvalSpec" = {
    "name": "write-test-suite",
    "files": {
        "text_processor.py": """\
\"\"\"Text processing utilities.\"\"\"
import re


def word_count(text):
    \"\"\"Count words in text. Returns 0 for empty/None input.\"\"\"
    if not text:
        return 0
    return len(text.split())


def truncate(text, max_length, suffix="..."):
    \"\"\"Truncate text to max_length, adding suffix if truncated.

    Returns empty string for empty/None input.
    If text fits within max_length, returns it unchanged.
    Otherwise returns text[:max_length - len(suffix)] + suffix.
    \"\"\"
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def extract_emails(text):
    \"\"\"Extract all email addresses from text. Returns list.

    Returns empty list for empty/None input.
    \"\"\"
    if not text:
        return []
    return re.findall(r'[\\w.+-]+@[\\w-]+\\.[\\w.]+', text)
""",
    },
    "run": "python3 -m pytest test_text_processor.py -q 2>&1",
    "prompt": (
        "The module `text_processor.py` has three utility functions but no "
        "tests. Write a focused test suite in `test_text_processor.py` "
        "that covers all three functions (`word_count`, `truncate`, "
        "`extract_emails`) including edge cases such as empty strings, None "
        "input, and boundary conditions. Write at least 6 tests total "
        "(2+ per function), keep the suite concise, and make sure all tests pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "test file exists": check_write_tests_file_exists,
        "tests pass": check_write_tests_pass,
        "covers word_count": check_write_tests_covers_word_count,
        "covers truncate": check_write_tests_covers_truncate,
        "covers extract_emails": check_write_tests_covers_extract_emails,
        "sufficient test count": check_write_tests_sufficient_count,
    },
}
