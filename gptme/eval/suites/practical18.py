"""Practical eval tests (batch 18) — classic data structures and algorithms.

Tests requiring correct implementation of:
- MinStack: stack with O(1) minimum retrieval
- Knight moves: BFS shortest path on chessboard
- Largest rectangle in histogram using stack-based algorithm
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- min-stack checks ---

_TEST_MIN_STACK_PY = """\
import sys
try:
    from min_stack import MinStack
except ImportError:
    print("ERROR: min_stack.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Test 1: basic push/top/get_min
s = MinStack()
s.push(5)
s.push(3)
s.push(7)
v = s.top()
if v != 7:
    errors.append(f"top() should return 7, got {v!r}")
m = s.get_min()
if m != 3:
    errors.append(f"get_min() should return 3, got {m!r}")

# Test 2: pop updates min correctly
s.pop()  # remove 7
s.pop()  # remove 3, min should revert to 5
m = s.get_min()
if m != 5:
    errors.append(f"get_min() after popping 7 and 3 should return 5, got {m!r}")
v = s.top()
if v != 5:
    errors.append(f"top() after pops should return 5, got {v!r}")

# Test 3: duplicate minimums
s2 = MinStack()
s2.push(2)
s2.push(2)
s2.push(2)
m = s2.get_min()
if m != 2:
    errors.append(f"get_min() with duplicates should return 2, got {m!r}")
s2.pop()
m = s2.get_min()
if m != 2:
    errors.append(f"get_min() after popping one duplicate should still return 2, got {m!r}")

# Test 4: interleaved pushes, min tracks correctly
s3 = MinStack()
s3.push(10)
s3.push(1)
s3.push(8)
s3.push(1)
s3.push(6)
m = s3.get_min()
if m != 1:
    errors.append(f"get_min() should return 1, got {m!r}")
s3.pop()  # remove 6
s3.pop()  # remove 1 (duplicate)
m = s3.get_min()
if m != 1:
    errors.append(f"get_min() after popping 6 and one 1 should return 1, got {m!r}")
s3.pop()  # remove 8
s3.pop()  # remove 1 (last 1)
m = s3.get_min()
if m != 10:
    errors.append(f"get_min() after all pops should return 10, got {m!r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 9 assertions passed.")
print("MinStack push/pop/top/get_min semantics correct.")
"""


def check_minstack_file(ctx):
    """min_stack.py should exist."""
    return "min_stack.py" in ctx.files


def check_minstack_all_pass(ctx):
    """All 9 assertions should pass."""
    return "All 9 assertions passed" in ctx.stdout


def check_minstack_has_class(ctx):
    """Should define a MinStack class."""
    src = ctx.files.get("min_stack.py", "")
    return "class MinStack" in src


def check_minstack_has_methods(ctx):
    """Should have push, pop, top, and get_min methods."""
    src = ctx.files.get("min_stack.py", "")
    return (
        "def push(" in src
        and "def pop(" in src
        and "def top(" in src
        and "def get_min(" in src
    )


def check_minstack_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- knight-moves checks ---

_TEST_KNIGHT_PY = """\
import sys
try:
    from knight import min_knight_moves
except ImportError:
    print("ERROR: knight.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

cases = [
    ("a1", "a1", 0),
    ("a1", "b3", 1),
    ("a1", "c2", 1),
    ("a1", "h8", 6),
    ("d4", "d4", 0),
    ("a1", "d4", 2),
]

for src, dst, expected in cases:
    result = min_knight_moves(src, dst)
    if result != expected:
        errors.append(
            f"min_knight_moves({src!r}, {dst!r}) = {result}, expected {expected}"
        )

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 6 cases passed.")
print("Knight BFS minimum-moves on 8x8 board correct.")
"""


def check_knight_file(ctx):
    """knight.py should exist."""
    return "knight.py" in ctx.files


def check_knight_all_pass(ctx):
    """All 6 cases should pass."""
    return "All 6 cases passed" in ctx.stdout


def check_knight_has_function(ctx):
    """Should define min_knight_moves function."""
    src = ctx.files.get("knight.py", "")
    return "def min_knight_moves(" in src


def check_knight_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- histogram-area checks ---

_HISTOGRAM_JSON = "[2, 1, 5, 6, 2, 3]\n"


def check_histogram_file(ctx):
    """histogram.py should exist."""
    return "histogram.py" in ctx.files


def check_histogram_result(ctx):
    """Output should contain the integer 10 (largest rectangle in [2,1,5,6,2,3])."""
    out = ctx.stdout.strip()
    # Accept the answer anywhere in output, but must be present as a standalone number
    return bool(re.search(r"\b10\b", out))


def check_histogram_only_int(ctx):
    """Output should be a single integer (possibly with trailing newline)."""
    out = ctx.stdout.strip()
    return out.isdigit()


def check_histogram_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "min-stack",
        "files": {"test_min_stack.py": _TEST_MIN_STACK_PY},
        "run": "python test_min_stack.py",
        "prompt": (
            "A test script `test_min_stack.py` is provided. Write `min_stack.py` that "
            "implements a `MinStack` class with these methods:\n"
            "- `push(val)` — push element onto the stack\n"
            "- `pop()` — remove the top element\n"
            "- `top()` — return the top element without removing it\n"
            "- `get_min()` — return the minimum element currently in the stack in O(1)\n\n"
            "The minimum must be tracked in O(1) time — do not iterate over all elements. "
            "The stack must handle duplicate values and correctly restore the previous "
            "minimum after popping. "
            "Run `python test_min_stack.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "min_stack.py exists": check_minstack_file,
            "all 9 assertions pass": check_minstack_all_pass,
            "defines MinStack class": check_minstack_has_class,
            "has push/pop/top/get_min methods": check_minstack_has_methods,
            "clean exit": check_minstack_exit,
        },
    },
    {
        "name": "knight-moves",
        "files": {"test_knight.py": _TEST_KNIGHT_PY},
        "run": "python test_knight.py",
        "prompt": (
            "A test script `test_knight.py` is provided. Write `knight.py` that "
            "implements `min_knight_moves(source: str, dest: str) -> int`. "
            "The function takes two squares on a standard 8x8 chessboard in algebraic "
            "notation (e.g. 'e2', 'a1', 'h8') and returns the minimum number of knight "
            "moves required to travel from source to destination. A knight moves in an "
            "L-shape: 2 squares in one direction and 1 square perpendicular. "
            "Same square returns 0. Use BFS for correctness. "
            "Run `python test_knight.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "knight.py exists": check_knight_file,
            "all 6 cases pass": check_knight_all_pass,
            "defines min_knight_moves function": check_knight_has_function,
            "clean exit": check_knight_exit,
        },
    },
    {
        "name": "histogram-area",
        "files": {"histogram.json": _HISTOGRAM_JSON},
        "run": "python histogram.py",
        "prompt": (
            "Write a Python script `histogram.py` that reads `histogram.json` "
            "(a JSON array of non-negative integers representing bar heights) and "
            "prints the area of the largest rectangle that fits entirely within the "
            "histogram bars. Each bar has width 1. "
            "For example, [2, 1, 5, 6, 2, 3] has a largest rectangle of area 10 "
            "(the two bars of height 5 and 6 give a 2×5 rectangle). "
            "Print only the integer result to stdout. "
            "Use an efficient stack-based algorithm (O(n))."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "histogram.py exists": check_histogram_file,
            "output is 10": check_histogram_result,
            "output is a single integer": check_histogram_only_int,
            "clean exit": check_histogram_exit,
        },
    },
]
