"""Practical eval tests (batch 3) — testing and database skills.

Tests capabilities not covered by the basic and practical suites:
- Writing unittest test suites for existing Python code
- Implementing a SQLite-backed CLI tool
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- write-tests checks ---


def check_test_file_exists(ctx):
    """test_calculator.py should exist."""
    return "test_calculator.py" in ctx.files


def check_tests_pass(ctx):
    """unittest should exit with code 0 (all tests pass)."""
    return ctx.exit_code == 0


def check_test_has_multiple_tests(ctx):
    """test file should contain at least 4 test methods."""
    content = ctx.files.get("test_calculator.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    fns = re.findall(r"def test_\w+", content)
    return len(fns) >= 4


def check_tests_cover_all_ops(ctx):
    """Tests should reference all four calculator operations."""
    content = ctx.files.get("test_calculator.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    content_lower = content.lower()
    # Use word-boundary match for "add" to avoid false positives from common
    # English words like "additional" or "adding".
    return bool(re.search(r"\badd\b", content_lower)) and all(
        op in content_lower for op in ["subtract", "multiply", "divide"]
    )


def check_tests_cover_zero_division(ctx):
    """Tests should verify that divide(x, 0) raises ZeroDivisionError."""
    content = ctx.files.get("test_calculator.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "zerodivisionerror" in content.lower()


# --- sqlite-store checks ---


def check_notes_file_exists(ctx):
    """notes.py should exist."""
    return "notes.py" in ctx.files


def check_notes_clean_exit(ctx):
    """Final list operation exits cleanly (exit code 0)."""
    return ctx.exit_code == 0


def check_notes_before_delete(ctx):
    """Before deletion, both notes should appear in the listing."""
    output = ctx.stdout.lower()
    sections = output.split("=== before delete ===")
    if len(sections) < 2:
        return False
    # Content between BEFORE marker and AFTER marker
    before_section = sections[1].split("=== after delete ===")[0]
    return "buy milk" in before_section and "call dentist" in before_section


def check_notes_after_delete(ctx):
    """After deleting 'buy milk', only 'call dentist' should remain."""
    output = ctx.stdout.lower()
    sections = output.split("=== after delete ===")
    if len(sections) < 2:
        return False
    after_section = sections[1]
    return "call dentist" in after_section and "buy milk" not in after_section


tests: list["EvalSpec"] = [
    {
        "name": "write-tests-calculator",
        "files": {
            "calculator.py": (
                "def add(a, b):\n"
                "    return a + b\n"
                "\n"
                "\n"
                "def subtract(a, b):\n"
                "    return a - b\n"
                "\n"
                "\n"
                "def multiply(a, b):\n"
                "    return a * b\n"
                "\n"
                "\n"
                "def divide(a, b):\n"
                "    if b == 0:\n"
                "        raise ZeroDivisionError('Cannot divide by zero')\n"
                "    return a / b\n"
            ),
        },
        "run": "python -m unittest test_calculator -v",
        "prompt": (
            "Write a test suite in test_calculator.py for the functions in calculator.py. "
            "Use Python's built-in unittest module.\n"
            "The test suite must:\n"
            "1. Test add() with at least two cases (positive numbers, negatives)\n"
            "2. Test subtract() with at least two cases\n"
            "3. Test multiply() including multiplication by zero\n"
            "4. Test divide() for a normal case\n"
            "5. Test that divide(x, 0) raises ZeroDivisionError\n"
            "\n"
            "All tests must pass when run with: python -m unittest test_calculator -v"
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "test_calculator.py exists": check_test_file_exists,
            "all tests pass": check_tests_pass,
            "≥4 test methods": check_test_has_multiple_tests,
            "all ops covered": check_tests_cover_all_ops,
            "ZeroDivisionError tested": check_tests_cover_zero_division,
        },
    },
    {
        "name": "sqlite-store",
        "files": {
            "test_notes.py": (
                "import os\n"
                "import subprocess\n"
                "import sys\n"
                "\n"
                "# Clean up any existing database\n"
                "if os.path.exists('notes.db'):\n"
                "    os.remove('notes.db')\n"
                "\n"
                "\n"
                "def run(args):\n"
                "    return subprocess.run(\n"
                "        [sys.executable, 'notes.py'] + args,\n"
                "        capture_output=True,\n"
                "        text=True,\n"
                "    )\n"
                "\n"
                "\n"
                "def check(result, label):\n"
                "    if result.returncode != 0:\n"
                "        print(f'{label} failed (exit {result.returncode})', file=sys.stderr)\n"
                "        sys.exit(result.returncode)\n"
                "\n"
                "\n"
                "# Add two notes ('buy milk' contains a space — passed as one argument)\n"
                "check(run(['add', 'buy milk']), 'add buy milk')\n"
                "check(run(['add', 'call dentist']), 'add call dentist')\n"
                "\n"
                "# List before delete\n"
                "print('=== before delete ===')\n"
                "print(run(['list']).stdout)\n"
                "\n"
                "# Delete one note\n"
                "check(run(['delete', 'buy milk']), 'delete buy milk')\n"
                "\n"
                "# List after delete\n"
                "print('=== after delete ===')\n"
                "result = run(['list'])\n"
                "print(result.stdout)\n"
                "\n"
                "sys.exit(result.returncode)\n"
            ),
        },
        "run": "python test_notes.py",
        "prompt": (
            "Implement notes.py — a command-line note store backed by SQLite.\n"
            "The script should support three subcommands:\n"
            "1. python notes.py add <text>    — save a note to notes.db\n"
            "2. python notes.py list          — print all notes, one per line\n"
            "3. python notes.py delete <text> — remove the note matching <text> exactly\n"
            "\n"
            "The text argument may contain spaces (it is passed as a single argument). "
            "A test_notes.py file already exists — read it carefully before implementing. "
            "Do not modify test_notes.py. "
            "All operations should exit with code 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "notes.py exists": check_notes_file_exists,
            "clean exit": check_notes_clean_exit,
            "before-delete lists both notes": check_notes_before_delete,
            "after-delete removes 'buy milk'": check_notes_after_delete,
        },
    },
]
