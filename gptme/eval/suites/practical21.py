"""Practical eval tests (batch 21) — dynamic programming and graph traversal.

Tests requiring correct implementation of:
- Kadane's algorithm: maximum subarray sum in a 1D array
- 0/1 knapsack: classic DP weight-capacity optimization
- Flood fill: BFS/DFS replacement of connected cells in a 2D grid
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- kadane checks ---

_TEST_KADANE_PY = """\
import sys
try:
    from kadane import max_subarray
except ImportError:
    print("ERROR: kadane.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: all positive
r = max_subarray([1, 2, 3, 4, 5])
if r != 15:
    errors.append(f"case 1: all-positive -> 15, got {r}")

# Case 2: all negative — return the largest single element
r = max_subarray([-3, -1, -2])
if r != -1:
    errors.append(f"case 2: all-negative -> -1 (max element), got {r}")

# Case 3: mixed with a clear subarray winner
r = max_subarray([-2, 1, -3, 4, -1, 2, 1, -5, 4])
if r != 6:
    errors.append(f"case 3: classic example -> 6, got {r}")

# Case 4: single element
r = max_subarray([7])
if r != 7:
    errors.append(f"case 4: single element -> 7, got {r}")

# Case 5: two elements, negative first
r = max_subarray([-1, 5])
if r != 5:
    errors.append(f"case 5: [-1, 5] -> 5, got {r}")

# Case 6: large negative interior
r = max_subarray([3, -10, 5, 6])
if r != 11:
    errors.append(f"case 6: [3,-10,5,6] -> 11 (5+6), got {r}")

# Case 7: zeros interspersed
r = max_subarray([0, -2, 0, 3, 0])
if r != 3:
    errors.append(f"case 7: [0,-2,0,3,0] -> 3, got {r}")

# Case 8: wrap-around NOT allowed (contiguous only)
r = max_subarray([5, -9, 6])
if r != 6:
    errors.append(f"case 8: [5,-9,6] -> 6 (not 5+6=11), got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 Kadane test cases passed.")
print("Maximum subarray implementation correct.")
"""


def check_kadane_file(ctx):
    """kadane.py should exist."""
    return "kadane.py" in ctx.files


def check_kadane_all_pass(ctx):
    """All 8 Kadane cases should pass."""
    return "All 8 Kadane test cases passed" in ctx.stdout


def check_kadane_has_function(ctx):
    """Should define max_subarray function."""
    src = ctx.files.get("kadane.py", "")
    return "def max_subarray(" in src


def check_kadane_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- knapsack checks ---

_TEST_KNAPSACK_PY = """\
import sys
try:
    from knapsack import knapsack
except ImportError:
    print("ERROR: knapsack.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: empty items
r = knapsack([], [], 10)
if r != 0:
    errors.append(f"case 1: no items -> 0, got {r}")

# Case 2: zero capacity
r = knapsack([1, 2, 3], [10, 20, 30], 0)
if r != 0:
    errors.append(f"case 2: zero capacity -> 0, got {r}")

# Case 3: single item fits
r = knapsack([2], [5], 3)
if r != 5:
    errors.append(f"case 3: single item fits -> 5, got {r}")

# Case 4: single item too heavy
r = knapsack([5], [10], 4)
if r != 0:
    errors.append(f"case 4: single item too heavy -> 0, got {r}")

# Case 5: classic textbook example
# weights=[1,3,4,5] values=[1,4,5,7] capacity=7
# Best: items 1+2+3 = weight 1+3+4=8 too heavy; items 1+4=6, val=8; items 2+3=7,val=9
r = knapsack([1, 3, 4, 5], [1, 4, 5, 7], 7)
if r != 9:
    errors.append(f"case 5: classic textbook -> 9, got {r}")

# Case 6: all items fit
r = knapsack([1, 2, 3], [6, 10, 12], 10)
if r != 28:
    errors.append(f"case 6: all items fit -> 28, got {r}")

# Case 7: greedy-vs-DP tie (greedy accidentally picks the optimal here)
# weights=[3,4,5] values=[4,5,6] capacity=7
# greedy by val/weight: item0(1.33), item1(1.25), item2(1.2)
# greedy picks item0+item1=7, val=9; DP also gives item0+item1=9
r = knapsack([3, 4, 5], [4, 5, 6], 7)
if r != 9:
    errors.append(f"case 7: greedy-vs-dp tie -> 9, got {r}")

# Case 8: true greedy failure
# weights=[1,2,3] values=[6,10,12] capacity=5
# greedy: item0(6), item1(5), item2(4) → pick item0+item1=3, val=16;
# but item1+item2=5, val=22 is better
r = knapsack([1, 2, 3], [6, 10, 12], 5)
if r != 22:
    errors.append(f"case 8: greedy-fails -> 22 (items 1+2), got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 knapsack test cases passed.")
print("0/1 knapsack DP implementation correct.")
"""


def check_knapsack_file(ctx):
    """knapsack.py should exist."""
    return "knapsack.py" in ctx.files


def check_knapsack_all_pass(ctx):
    """All 8 knapsack cases should pass."""
    return "All 8 knapsack test cases passed" in ctx.stdout


def check_knapsack_has_function(ctx):
    """Should define knapsack function."""
    src = ctx.files.get("knapsack.py", "")
    return "def knapsack(" in src


def check_knapsack_uses_dp(ctx):
    """Should use a DP table (2D list or 1D array)."""
    src = ctx.files.get("knapsack.py", "")
    return bool(
        re.search(r"\bdp\b", src)
        or "table" in src
        or "memo" in src
        or "lru_cache" in src
        or "@cache" in src
    )


def check_knapsack_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- flood-fill checks ---

_TEST_FLOOD_FILL_PY = """\
import sys
try:
    from flood_fill import flood_fill
except ImportError:
    print("ERROR: flood_fill.py not found or import failed", file=sys.stderr)
    sys.exit(1)

import copy

errors = []

# Case 1: basic 3x3, replace 1 with 2
grid = [[1, 1, 1], [1, 1, 0], [1, 0, 0]]
r = flood_fill(grid, 1, 1, 2)
expected = [[2, 2, 2], [2, 2, 0], [2, 0, 0]]
if r != expected:
    errors.append(f"case 1: basic fill -> {expected}, got {r}")

# Case 2: target color equals fill color — return unchanged
grid = [[0, 0, 0], [0, 0, 0]]
original = copy.deepcopy(grid)
r = flood_fill(grid, 0, 0, 0)
if r != original:
    errors.append(f"case 2: same color noop -> unchanged, got {r}")

# Case 3: single cell
r = flood_fill([[3]], 0, 0, 7)
if r != [[7]]:
    errors.append(f"case 3: single cell -> [[7]], got {r}")

# Case 4: no connectivity — only starting cell changes
grid = [[1, 0, 1], [0, 1, 0], [1, 0, 1]]
r = flood_fill(grid, 0, 0, 9)
if r[0][0] != 9 or r[0][2] != 1:
    errors.append(f"case 4: isolated start -> only (0,0) changes, got {r}")

# Case 5: fill stops at boundary (0 cells block spread)
grid = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]
r = flood_fill(grid, 0, 0, 5)
# Only the ring of 1s connected to (0,0) should change
if r[1][1] != 0:
    errors.append(f"case 5: center 0 unchanged -> r[1][1]=0, got r[1][1]={r[1][1]}")
if r[0][0] != 5:
    errors.append(f"case 5: corners -> 5, got r[0][0]={r[0][0]}")

# Case 6: diagonal NOT connected (4-directional only)
grid = [[1, 0], [0, 1]]
r = flood_fill(grid, 0, 0, 3)
if r != [[3, 0], [0, 1]]:
    errors.append(f"case 6: diagonal not connected -> [[3,0],[0,1]], got {r}")

# Case 7: entire grid same color
grid = [[2, 2], [2, 2]]
r = flood_fill(grid, 0, 0, 4)
if r != [[4, 4], [4, 4]]:
    errors.append(f"case 7: full fill -> all 4s, got {r}")

# Case 8: start on 0 cell surrounded by 1s — only start cell changes
grid = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]
r = flood_fill(grid, 1, 1, 6)
if r[1][1] != 6 or r[0][0] != 1:
    errors.append(f"case 8: interior 0 -> only center changes, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 flood-fill test cases passed.")
print("Flood fill implementation correct.")
"""


def check_flood_fill_file(ctx):
    """flood_fill.py should exist."""
    return "flood_fill.py" in ctx.files


def check_flood_fill_all_pass(ctx):
    """All 8 flood-fill cases should pass."""
    return "All 8 flood-fill test cases passed" in ctx.stdout


def check_flood_fill_has_function(ctx):
    """Should define flood_fill function."""
    src = ctx.files.get("flood_fill.py", "")
    return "def flood_fill(" in src


def check_flood_fill_uses_traversal(ctx):
    """Should use BFS or DFS (including direct recursion)."""
    src = ctx.files.get("flood_fill.py", "")
    # Check for explicit BFS/iterative-DFS data structures
    if (
        "deque" in src
        or "queue" in src.lower()
        or re.search(r"\bstack\s*=\s*\[", src)  # stack variable assignment, not keyword
        or "visited" in src
        or "def _fill" in src
        or "def fill" in src
        or re.search(r"    def \w+fill", src)  # nested fill helper
    ):
        return True
    # Check for direct self-recursion — flood_fill calling itself
    func_body = src.split("def flood_fill(", 1)[-1] if "def flood_fill(" in src else ""
    return "flood_fill(" in func_body


def check_flood_fill_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "kadane",
        "files": {"test_kadane.py": _TEST_KADANE_PY},
        "run": "python test_kadane.py",
        "prompt": (
            "A test script `test_kadane.py` is provided. Write `kadane.py` that "
            "implements `max_subarray(nums)` which returns the maximum sum of any "
            "contiguous subarray of `nums` (a non-empty list of integers). "
            "If all elements are negative, return the largest single element. "
            "Use Kadane's algorithm for an O(n) solution. "
            "Run `python test_kadane.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "kadane.py exists": check_kadane_file,
            "all 8 cases pass": check_kadane_all_pass,
            "defines max_subarray function": check_kadane_has_function,
            "clean exit": check_kadane_exit,
        },
    },
    {
        "name": "knapsack",
        "files": {"test_knapsack.py": _TEST_KNAPSACK_PY},
        "run": "python test_knapsack.py",
        "prompt": (
            "A test script `test_knapsack.py` is provided. Write `knapsack.py` that "
            "implements `knapsack(weights, values, capacity)` where `weights` and "
            "`values` are lists of equal length and `capacity` is a non-negative "
            "integer. The function should return the maximum total value achievable "
            "by selecting a subset of items such that the sum of their weights does "
            "not exceed `capacity` (0/1 knapsack — each item may be used at most "
            "once). Use dynamic programming for an O(n * capacity) solution. "
            "Run `python test_knapsack.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "knapsack.py exists": check_knapsack_file,
            "all 8 cases pass": check_knapsack_all_pass,
            "defines knapsack function": check_knapsack_has_function,
            "uses DP": check_knapsack_uses_dp,
            "clean exit": check_knapsack_exit,
        },
    },
    {
        "name": "flood-fill",
        "files": {"test_flood_fill.py": _TEST_FLOOD_FILL_PY},
        "run": "python test_flood_fill.py",
        "prompt": (
            "A test script `test_flood_fill.py` is provided. Write `flood_fill.py` "
            "that implements `flood_fill(image, sr, sc, color)` where `image` is a "
            "2D list of integers, `(sr, sc)` is the starting cell, and `color` is "
            "the new fill color. Starting from `(sr, sc)`, replace the color of all "
            "4-directionally connected cells that share the same original color as "
            "`(sr, sc)` with `color`. If the starting cell already has `color`, "
            "return the image unchanged. Return the modified image. Use BFS or DFS "
            "for traversal. "
            "Run `python test_flood_fill.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "flood_fill.py exists": check_flood_fill_file,
            "all 8 cases pass": check_flood_fill_all_pass,
            "defines flood_fill function": check_flood_fill_has_function,
            "uses BFS/DFS": check_flood_fill_uses_traversal,
            "clean exit": check_flood_fill_exit,
        },
    },
]
