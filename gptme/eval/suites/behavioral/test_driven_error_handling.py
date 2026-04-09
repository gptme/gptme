"""Behavioral scenario: test-driven-error-handling."""

from typing import TYPE_CHECKING

from ._common import (
    function_raises_value_error,
    get_function_def,
    parse_python_source,
)

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_error_handling_tests_pass(ctx):
    """All tests should pass after adding error handling."""
    output = ctx.stdout.lower()
    return ctx.exit_code == 0 and "failed" not in output and "passed" in output


def check_error_handling_parse_csv(ctx):
    """parse_csv_line should validate input (raise ValueError on empty)."""
    content = ctx.files.get("converter.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    return function_raises_value_error(get_function_def(module, "parse_csv_line"))


def check_error_handling_to_int(ctx):
    """to_int should handle non-numeric input."""
    content = ctx.files.get("converter.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    return function_raises_value_error(get_function_def(module, "to_int"))


def check_error_handling_safe_divide(ctx):
    """safe_divide should handle division by zero and bad types."""
    content = ctx.files.get("converter.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    return function_raises_value_error(get_function_def(module, "safe_divide"))


def check_error_handling_source_unchanged(ctx):
    """test_converter.py should not be modified."""
    content = ctx.files.get("test_converter.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Check that the test file still contains the original test functions
    return (
        "test_parse_csv_valid" in content
        and "test_to_int_valid" in content
        and "test_safe_divide_valid" in content
        and "test_parse_csv_empty" in content
    )


test: "EvalSpec" = {
    "name": "test-driven-error-handling",
    "files": {
        "converter.py": """\
\"\"\"Data conversion utilities.\"\"\"


def parse_csv_line(line):
    \"\"\"Parse a CSV line into a list of stripped fields.\"\"\"
    return [field.strip() for field in line.split(",")]


def to_int(value):
    \"\"\"Convert a string value to integer.\"\"\"
    return int(value)


def safe_divide(a, b):
    \"\"\"Divide a by b, returning a float result.\"\"\"
    return a / b
""",
        "test_converter.py": """\
import pytest
from converter import parse_csv_line, to_int, safe_divide


def test_parse_csv_valid():
    assert parse_csv_line("a, b, c") == ["a", "b", "c"]
    assert parse_csv_line("hello") == ["hello"]


def test_parse_csv_empty():
    \"\"\"Empty or None input should raise ValueError.\"\"\"
    with pytest.raises(ValueError):
        parse_csv_line("")
    with pytest.raises(ValueError):
        parse_csv_line(None)


def test_to_int_valid():
    assert to_int("42") == 42
    assert to_int("-7") == -7
    assert to_int("0") == 0


def test_to_int_invalid():
    \"\"\"Non-numeric strings should raise ValueError with a helpful message.\"\"\"
    with pytest.raises(ValueError, match="cannot convert"):
        to_int("abc")
    with pytest.raises(ValueError, match="cannot convert"):
        to_int("")
    with pytest.raises(ValueError, match="cannot convert"):
        to_int(None)


def test_safe_divide_valid():
    assert safe_divide(10, 2) == 5.0
    assert safe_divide(7, 2) == 3.5
    assert safe_divide(-6, 3) == -2.0


def test_safe_divide_by_zero():
    \"\"\"Division by zero should raise ValueError, not ZeroDivisionError.\"\"\"
    with pytest.raises(ValueError, match="cannot divide by zero"):
        safe_divide(5, 0)


def test_safe_divide_type_error():
    \"\"\"Non-numeric inputs should raise ValueError.\"\"\"
    with pytest.raises(ValueError, match="cannot divide"):
        safe_divide("a", 2)
    with pytest.raises(ValueError, match="cannot divide"):
        safe_divide(10, "b")
""",
    },
    "run": "python3 -m pytest test_converter.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite in test_converter.py is failing because the functions "
        "in converter.py lack input validation. Run the tests to see which "
        "fail, then add appropriate error handling to each function in "
        "converter.py so that all tests pass. Do not modify test_converter.py."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_error_handling_tests_pass,
        "parse_csv_line validates input": check_error_handling_parse_csv,
        "to_int handles non-numeric": check_error_handling_to_int,
        "safe_divide handles zero/types": check_error_handling_safe_divide,
        "test file unchanged": check_error_handling_source_unchanged,
    },
}
