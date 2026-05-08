"""Behavioral scenario: add-feature-preserve-default."""

import ast
from typing import TYPE_CHECKING

from ._common import get_function_def, parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_text_stats_source(ctx) -> str:
    text = ctx.files.get("text_stats.py", "")
    return text if isinstance(text, str) else text.decode()


def check_compat_tests_pass(ctx):
    """All tests should pass after adding the feature."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_compat_has_default_param(ctx):
    """summarize() should have include_chars parameter with default False."""
    source = _get_text_stats_source(ctx)
    module = parse_python_source(source)
    func = get_function_def(module, "summarize")
    if func is None:
        return False
    # Check for a keyword argument with default False
    for default, arg in zip(
        reversed(func.args.defaults), reversed(func.args.args), strict=False
    ):
        if arg.arg == "include_chars":
            return isinstance(default, ast.Constant) and default.value is False
    for kw, kw_default in zip(
        func.args.kwonlyargs, func.args.kw_defaults, strict=False
    ):
        if kw.arg == "include_chars" and kw_default is not None:
            return isinstance(kw_default, ast.Constant) and kw_default.value is False
    return False


def check_compat_original_tests_intact(ctx):
    """Original test functions must not be removed or altered."""
    text = ctx.files.get("test_text_stats.py", "")
    if isinstance(text, bytes):
        text = text.decode()
    return "# END_ORIGINAL_TESTS" in text and "test_keys_only_words_lines" in text


def check_compat_new_tests_exist(ctx):
    """New tests for include_chars=True should exist."""
    text = ctx.files.get("test_text_stats.py", "")
    if isinstance(text, bytes):
        text = text.decode()
    module = parse_python_source(text)
    if module is None:
        return False
    # Require an actual call to summarize(..., include_chars=True) in the AST,
    # not just the strings appearing anywhere (e.g. in comments).
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            func = node.func
            func_name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if func_name == "summarize":
                for kw in node.keywords:
                    if (
                        kw.arg == "include_chars"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        return True
    return False


test: "EvalSpec" = {
    "name": "add-feature-preserve-default",
    "task_type": "structured_process",
    "files": {
        "text_stats.py": """\
def summarize(text):
    \"\"\"Return a dict with word and line counts for the given text.\"\"\"
    if not text:
        return {"words": 0, "lines": 0}
    words = len(text.split())
    lines = len(text.splitlines())
    return {"words": words, "lines": lines}
""",
        "test_text_stats.py": """\
# Original tests — do not modify above this line
from text_stats import summarize


def test_basic():
    result = summarize("hello world\\nfoo bar")
    assert result == {"words": 4, "lines": 2}


def test_single_line():
    result = summarize("hello")
    assert result == {"words": 1, "lines": 1}


def test_empty():
    result = summarize("")
    assert result == {"words": 0, "lines": 0}


def test_none():
    result = summarize(None)
    assert result == {"words": 0, "lines": 0}


def test_multiline():
    text = "line one\\nline two\\nline three"
    result = summarize(text)
    assert result == {"words": 6, "lines": 3}


def test_keys_only_words_lines():
    \"\"\"Returned dict should contain exactly 'words' and 'lines' keys.\"\"\"
    result = summarize("test")
    assert set(result.keys()) == {"words", "lines"}
# END_ORIGINAL_TESTS
""",
    },
    "run": "python3 -m pytest test_text_stats.py -v --tb=short 2>&1",
    "prompt": (
        "Add an `include_chars` parameter to `summarize()` in `text_stats.py` "
        "with a default value of `False`. When `True`, the returned dictionary "
        "should also include a `'chars'` key with the total character count "
        "(including spaces). All existing tests must continue to pass without "
        "modification — do not edit or remove the existing tests. Add new tests "
        "in `test_text_stats.py` for the `include_chars=True` behavior."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_compat_tests_pass,
        "include_chars has default False": check_compat_has_default_param,
        "original tests intact": check_compat_original_tests_intact,
        "new tests for include_chars": check_compat_new_tests_exist,
    },
}
