"""Behavioral scenario: validate-user-input."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_type_validation(ctx):
    """Should validate input types (str, int, float checks)."""
    content = ctx.files.get("user_service.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "isinstance" in content or "type(" in content or "TypeError" in content


def check_range_validation(ctx):
    """Should validate numeric ranges (age 0-150, score 0-100, etc.)."""
    content = ctx.files.get("user_service.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    if "ValueError" not in content:
        return False
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(
                    comparator.value, int
                ):
                    return True
    return False


def check_string_sanitization(ctx):
    """Should sanitize or validate string input (strip, length check, etc.)."""
    content = ctx.files.get("user_service.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return (
        "strip" in content
        or "len(" in content
        or "whitespace" in content.lower()
        or "empty" in content.lower()
    )


def check_custom_exception(ctx):
    """Should raise descriptive exceptions with meaningful messages."""
    content = ctx.files.get("user_service.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.Raise):
            if isinstance(node.exc, ast.Call):
                return True
    return False


def check_tests_pass(ctx):
    """All tests should pass after adding validation."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


test: "EvalSpec" = {
    "name": "validate-user-input",
    "task_type": "structured_process",
    "files": {
        "user_service.py": """\
\"\"\"User service for managing user profiles.\"\"\"


def create_user(name: str, age: int, email: str) -> dict:
    \"\"\"Create a new user profile.

    Args:
        name: Full name of the user.
        age: Age in years.
        email: Email address.

    Returns:
        Dict with user info.
    \"\"\"
    return {"name": name, "age": age, "email": email}
""",
        "test_user_service.py": """\
import pytest
from user_service import create_user


def test_create_user_valid():
    \"\"\"Should create user with valid inputs.\"\"\"
    user = create_user("Alice Smith", 30, "alice@example.com")
    assert user["name"] == "Alice Smith"
    assert user["age"] == 30
    assert user["email"] == "alice@example.com"


def test_reject_negative_age():
    \"\"\"Should reject negative age.\"\"\"
    with pytest.raises(ValueError):
        create_user("Bob", -5, "bob@example.com")


def test_reject_unrealistic_age():
    \"\"\"Should reject age above 150.\"\"\"
    with pytest.raises(ValueError):
        create_user("Charlie", 200, "charlie@example.com")


def test_reject_empty_name():
    \"\"\"Should reject empty or whitespace-only name.\"\"\"
    with pytest.raises(ValueError):
        create_user("", 25, "test@example.com")
    with pytest.raises(ValueError):
        create_user("   ", 25, "test@example.com")


def test_reject_non_string_name():
    \"\"\"Should reject non-string name.\"\"\"
    with pytest.raises(TypeError):
        create_user(123, 25, "test@example.com")


def test_reject_non_integer_age():
    \"\"\"Should reject non-integer age.\"\"\"
    with pytest.raises(TypeError):
        create_user("Dave", "thirty", "dave@example.com")


def test_valid_edge_cases():
    \"\"\"Should accept boundary values.\"\"\"
    user = create_user("Eve", 0, "eve@example.com")
    assert user["age"] == 0
    user = create_user("Frank", 150, "frank@example.com")
    assert user["age"] == 150
""",
    },
    "run": "python3 -m pytest test_user_service.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_user_service.py` is failing because `create_user()` "
        "in `user_service.py` does not validate its inputs. Five tests fail:\\n"
        "1. `test_reject_negative_age` — expects ValueError for age < 0\\n"
        "2. `test_reject_unrealistic_age` — expects ValueError for age > 150\\n"
        "3. `test_reject_empty_name` — expects ValueError for empty/whitespace name\\n"
        "4. `test_reject_non_string_name` — expects TypeError for non-string name\\n"
        "5. `test_reject_non_integer_age` — expects TypeError for non-integer age\\n\\n"
        "Add input validation to `create_user()` in `user_service.py`:\\n"
        "- Type checks: name must be str, age must be int\\n"
        "- Range check: age must be 0-150 (inclusive)\\n"
        "- String check: name must not be empty or whitespace-only\\n"
        "- Use descriptive error messages in ValueError/TypeError\\n\\n"
        "After implementing, run the tests to verify they all pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "type validation": check_type_validation,
        "range validation": check_range_validation,
        "string sanitization": check_string_sanitization,
        "custom exceptions": check_custom_exception,
        "all tests pass": check_tests_pass,
    },
}
