"""Behavioral scenario: fix-mutable-default.

Tests whether the agent correctly diagnoses and fixes the classic Python
"mutable default argument" bug, where a list or dict used as a default
parameter is shared across all calls to the function.
"""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_mutable_default_tests_pass(ctx) -> bool:
    """All tests must pass after the fix."""
    return ctx.exit_code == 0 and "passed" in ctx.stdout


def check_no_mutable_default_arg(ctx) -> bool:
    """Neither function should have a mutable default argument ([], {}, or set literals).

    Note: ast.Set matches set *literals* like {1, 2}, not set() calls.
    The scenario only uses [] defaults, so this covers the relevant cases.
    """
    content = ctx.files.get("records.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for default in node.args.defaults:
                if isinstance(default, ast.List | ast.Dict | ast.Set):
                    return False  # Still has mutable default
    return True


def check_uses_none_sentinel(ctx) -> bool:
    """Fix uses None as sentinel (standard Python idiom for optional mutable defaults)."""
    content = ctx.files.get("records.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Accept both "= None" (with spaces) and "=None" (no spaces around =)
    has_none_default = "= None" in content or "=None" in content
    has_none_check = "is None" in content or "is not None" in content
    return has_none_default and has_none_check


def check_independent_calls_verified(ctx) -> bool:
    """Tests for independent calls (shared-state tests) must pass."""
    output = ctx.stdout
    # These two tests directly verify the bug is fixed
    return (
        "test_collect_independent_calls PASSED" in output
        and "test_deduplicate_independent_calls PASSED" in output
    )


test: "EvalSpec" = {
    "name": "fix-mutable-default",
    "task_type": "structured_process",
    "files": {
        "records.py": """\
def collect_records(items, result=[]):
    \"\"\"Collect non-empty, stripped records into result list.\"\"\"
    for item in items:
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return result


def deduplicate(items, seen=[]):
    \"\"\"Return unique items preserving insertion order.\"\"\"
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
""",
        "test_records.py": """\
from records import collect_records, deduplicate


def test_collect_basic():
    result = collect_records(["hello", "world"])
    assert result == ["hello", "world"]


def test_collect_strips_whitespace():
    result = collect_records(["  hello  ", " world "])
    assert result == ["hello", "world"]


def test_collect_skips_empty():
    result = collect_records(["a", "  ", "", "b"])
    assert result == ["a", "b"]


def test_collect_independent_calls():
    \"\"\"Each call must return a fresh result — no shared state across calls.\"\"\"
    first = collect_records(["alpha"])
    second = collect_records(["beta"])
    assert first == ["alpha"], f"Expected [\'alpha\'], got {first!r}"
    assert second == ["beta"], f"Expected [\'beta\'], got {second!r} (shared state bug!)"


def test_deduplicate_basic():
    result = deduplicate(["a", "b", "a", "c"])
    assert result == ["a", "b", "c"]


def test_deduplicate_preserves_order():
    result = deduplicate([3, 1, 4, 1, 5, 9, 2, 6, 5])
    assert result == [3, 1, 4, 5, 9, 2, 6]


def test_deduplicate_independent_calls():
    \"\"\"Each call must start with a fresh seen-set — no shared state.\"\"\"
    first = deduplicate(["x", "y"])
    second = deduplicate(["z"])
    assert first == ["x", "y"], f"Expected [\'x\', \'y\'], got {first!r}"
    assert second == ["z"], f"Expected [\'z\'], got {second!r} (shared state bug!)"
""",
    },
    "run": "python3 -m pytest test_records.py -v --tb=short 2>&1",
    "prompt": (
        "The file `records.py` has two functions — `collect_records` and "
        "`deduplicate` — both with a **classic Python bug**: mutable default "
        "arguments (``result=[]`` and ``seen=[]``).\n\n"
        "In Python, default argument values are evaluated **once at function "
        "definition time**, not on each call. This means the same list object "
        "is reused across all calls that don't pass that argument, silently "
        "accumulating state from previous invocations.\n\n"
        "Fix both functions using the standard Python idiom: use ``None`` as "
        "the default, and create a fresh list at the start of the function "
        "when ``None`` is received.\n\n"
        "All 7 tests in `test_records.py` must pass after your fix — "
        "including ``test_collect_independent_calls`` and "
        "``test_deduplicate_independent_calls``, which directly verify that "
        "calls don't share state."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_mutable_default_tests_pass,
        "no mutable default arg": check_no_mutable_default_arg,
        "uses None sentinel pattern": check_uses_none_sentinel,
        "independent calls verified": check_independent_calls_verified,
    },
}
