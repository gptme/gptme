"""Behavioral scenario: optimize-n-squared."""

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_tests_pass(ctx):
    """All tests should pass after the optimization."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_no_nested_loop(ctx):
    """find_duplicates should not use nested for/while loops (O(n²) pattern)."""
    content = ctx.files.get("analytics.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "find_duplicates":
            # Walk all children; if we find a loop inside a loop, fail
            for child in ast.walk(node):
                if child is not node and isinstance(child, (ast.For, ast.While)):
                    for grandchild in ast.walk(child):
                        if grandchild is not child and isinstance(
                            grandchild, (ast.For, ast.While)
                        ):
                            return False
    return True


def check_uses_efficient_structure(ctx):
    """analytics.py should use Counter, defaultdict, set, or dict for O(n) counting."""
    content = ctx.files.get("analytics.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return bool(
        re.search(r"\bCounter\b|\bdefaultdict\b", content)
        or re.search(r"\bset\b|\bdict\b|\{\}", content)
    )


def check_signature_preserved(ctx):
    """find_duplicates(items) must still exist with the same name."""
    content = ctx.files.get("analytics.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def find_duplicates(" in content


test: "EvalSpec" = {
    "name": "optimize-n-squared",
    "files": {
        "analytics.py": """\
\"\"\"Analytics utilities.\"\"\"


def find_duplicates(items: list) -> list:
    \"\"\"Return a sorted list of items that appear more than once in *items*.

    The current implementation is O(n²) and too slow for large inputs.
    Rewrite it to run in O(n) time.
    \"\"\"
    duplicates = []
    for i, item in enumerate(items):
        if item not in duplicates:
            for j, other in enumerate(items):
                if i != j and item == other:
                    duplicates.append(item)
                    break
    return sorted(duplicates)
""",
        "test_analytics.py": """\
from analytics import find_duplicates


def test_basic():
    assert find_duplicates([1, 2, 1, 3]) == [1]


def test_multiple_duplicates():
    assert find_duplicates([1, 2, 1, 2, 3]) == [1, 2]


def test_no_duplicates():
    assert find_duplicates([1, 2, 3]) == []


def test_all_same():
    assert find_duplicates([7, 7, 7]) == [7]


def test_strings():
    assert find_duplicates(["a", "b", "a", "c", "b"]) == ["a", "b"]


def test_empty():
    assert find_duplicates([]) == []


def test_single():
    assert find_duplicates([42]) == []


def test_large_input():
    # 500 duplicates scattered across 1500 items
    items = list(range(500)) + list(range(1000))
    assert find_duplicates(items) == list(range(500))
""",
    },
    "run": "python3 -m pytest test_analytics.py -v --tb=short 2>&1",
    "prompt": (
        "The `find_duplicates` function in `analytics.py` is correct but "
        "uses an O(n²) algorithm: for each item it scans the entire list "
        "to check for a matching element.  Rewrite the function body to "
        "run in O(n) time using Python built-ins or standard library tools "
        "(e.g. `collections.Counter`).  "
        "All existing tests must still pass and the function signature must "
        "remain unchanged."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_tests_pass,
        "no nested loop": check_no_nested_loop,
        "uses efficient structure": check_uses_efficient_structure,
        "signature preserved": check_signature_preserved,
    },
}
