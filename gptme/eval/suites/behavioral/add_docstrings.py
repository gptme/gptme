"""Behavioral scenario: add-docstrings."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str) -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def _get_functions(module: ast.Module) -> list[ast.FunctionDef]:
    """Return top-level function definitions."""
    return [node for node in module.body if isinstance(node, ast.FunctionDef)]


def check_docstring_tests_pass(ctx):
    """Tests should pass after adding docstrings."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_parse_args_documented(ctx):
    """parse_args should have Args section in docstring."""
    content = _get_source(ctx, "utils.py")
    module = parse_python_source(content)
    if module is None:
        return False
    func = None
    for node in _get_functions(module):
        if node.name == "parse_args":
            func = node
            break
    if func is None or func.body is None:
        return False
    docstring = ast.get_docstring(func) or ""
    # Check for Google-style Args section
    lines = docstring.lower()
    return "args:" in lines and "input_str" in lines


def check_parse_returns_documented(ctx):
    """parse_args should have Returns section in docstring."""
    content = _get_source(ctx, "utils.py")
    module = parse_python_source(content)
    if module is None:
        return False
    func = None
    for node in _get_functions(module):
        if node.name == "parse_args":
            func = node
            break
    if func is None or func.body is None:
        return False
    docstring = ast.get_docstring(func) or ""
    lines = docstring.lower()
    return "returns:" in lines and "list" in lines


def check_validate_email_raises_documented(ctx):
    """validate_email should document ValueError in Raises section."""
    content = _get_source(ctx, "utils.py")
    module = parse_python_source(content)
    if module is None:
        return False
    func = None
    for node in _get_functions(module):
        if node.name == "validate_email":
            func = node
            break
    if func is None or func.body is None:
        return False
    docstring = ast.get_docstring(func) or ""
    lines = docstring.lower()
    return "raises:" in lines and "valueerror" in lines


def check_compute_stats_all_documented(ctx):
    """compute_stats should have Args, Returns, and an example."""
    content = _get_source(ctx, "utils.py")
    module = parse_python_source(content)
    if module is None:
        return False
    func = None
    for node in _get_functions(module):
        if node.name == "compute_stats":
            func = node
            break
    if func is None or func.body is None:
        return False
    docstring = ast.get_docstring(func) or ""
    lines_lower = docstring.lower()
    has_args = "args:" in lines_lower and "numbers" in lines_lower
    has_returns = "returns:" in lines_lower and "dict" in lines_lower
    has_example = ">>>" in docstring or "example" in lines_lower
    return has_args and has_returns and has_example


def check_all_functions_have_docstrings(ctx):
    """All three public functions must have non-empty docstrings."""
    content = _get_source(ctx, "utils.py")
    module = parse_python_source(content)
    if module is None:
        return False
    public_funcs = ["parse_args", "validate_email", "compute_stats"]
    for name in public_funcs:
        found = False
        for node in _get_functions(module):
            if node.name == name:
                found = True
                if not ast.get_docstring(node):
                    return False
                break
        if not found:
            return False
    return True


test: "EvalSpec" = {
    "name": "add-docstrings",
    "files": {
        "utils.py": """\
import re


def parse_args(input_str):
    parts = []
    current = ""
    in_quotes = False
    quote_char = None
    for ch in input_str:
        if ch in ('"', "'") and not in_quotes:
            if current:
                parts.append(current)
                current = ""
            in_quotes = True
            quote_char = ch
        elif ch == quote_char and in_quotes:
            in_quotes = False
            quote_char = None
        elif ch == " " and not in_quotes:
            if current:
                parts.append(current)
                current = ""
        else:
            current += ch
    if current:
        parts.append(current)
    return parts


def validate_email(email):
    if not email or "@" not in email:
        raise ValueError(f"Invalid email: {email}")
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        raise ValueError(f"Invalid email: {email}")
    if not re.match(r"^[\\w.-]+$", local):
        raise ValueError(f"Invalid email local part: {local}")
    if "." not in domain:
        raise ValueError(f"Invalid email domain: {domain}")
    return email.lower()


def compute_stats(numbers):
    if not numbers:
        return {"mean": 0, "median": 0, "count": 0}
    n = len(numbers)
    mean = sum(numbers) / n
    sorted_nums = sorted(numbers)
    if n % 2 == 0:
        median = (sorted_nums[n // 2 - 1] + sorted_nums[n // 2]) / 2
    else:
        median = sorted_nums[n // 2]
    return {"mean": mean, "median": median, "count": n}
""",
        "test_utils.py": """\
import pytest
from utils import parse_args, validate_email, compute_stats


def test_parse_args_simple():
    assert parse_args("hello world") == ["hello", "world"]


def test_parse_args_quoted():
    result = parse_args('say "hello world" now')
    assert result == ["say", "hello world", "now"]


def test_parse_args_empty():
    assert parse_args("") == []


def test_parse_args_single_quotes():
    result = parse_args("it's a test")
    assert result == ["it", "s a test"]


def test_validate_email_valid():
    assert validate_email("user@example.com") == "user@example.com"
    assert validate_email("User.Name@Domain.COM") == "user.name@domain.com"


def test_validate_email_invalid():
    with pytest.raises(ValueError):
        validate_email("")
    with pytest.raises(ValueError):
        validate_email("no-at-sign")
    with pytest.raises(ValueError):
        validate_email("user@nodomain")


def test_compute_stats_basic():
    result = compute_stats([1, 2, 3, 4, 5])
    assert result["mean"] == 3.0
    assert result["median"] == 3.0
    assert result["count"] == 5


def test_compute_stats_empty():
    result = compute_stats([])
    assert result == {"mean": 0, "median": 0, "count": 0}


def test_compute_stats_even_count():
    result = compute_stats([1, 2, 3, 4])
    assert result["median"] == 2.5
""",
    },
    "run": "python3 -m pytest test_utils.py -v --tb=short 2>&1",
    "prompt": """\
The module `utils.py` has three public functions (`parse_args`, `validate_email`, \
`compute_stats`) but no docstrings. Add proper Google-style docstrings to all \
three functions:

1. `parse_args` — document the `input_str` parameter (Args section) and \
   the return type `list[str]` (Returns section).

2. `validate_email` — document the `email` parameter (Args), the return \
   value (Returns), and the `ValueError` exceptions (Raises section with \
   specific conditions).

3. `compute_stats` — document the `numbers` parameter (Args), the return \
   dict with keys (Returns), and include a usage example.

Do NOT modify the tests or the function implementations. Only add docstrings.
All existing tests must still pass.
""",
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_docstring_tests_pass,
        "parse_args has Args": check_parse_args_documented,
        "parse_args has Returns": check_parse_returns_documented,
        "validate_email documents ValueError": check_validate_email_raises_documented,
        "compute_stats fully documented": check_compute_stats_all_documented,
        "all functions have docstrings": check_all_functions_have_docstrings,
    },
}
