"""Practical eval tests (batch 23) — 2D DP, state-machine DP, and matrix manipulation.

Tests requiring correct implementation of:
- Longest common subsequence: find LCS length between two strings (2D DP)
- Stock trading with cooldown: maximize profit with buy/sell and mandatory cooldown (state-machine DP)
- Rotate image: rotate NxN matrix 90 degrees clockwise in-place
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- longest common subsequence checks ---

_TEST_LCS_PY = """\
import sys
try:
    from lcs import longest_common_subsequence
except ImportError:
    print("ERROR: lcs.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic example
r = longest_common_subsequence("abcde", "ace")
if r != 3:
    errors.append(f"case 1: 'abcde','ace' -> 3, got {r}")

# Case 2: identical strings
r = longest_common_subsequence("abc", "abc")
if r != 3:
    errors.append(f"case 2: 'abc','abc' -> 3, got {r}")

# Case 3: no common subsequence
r = longest_common_subsequence("abc", "def")
if r != 0:
    errors.append(f"case 3: 'abc','def' -> 0, got {r}")

# Case 4: one empty string
r = longest_common_subsequence("", "abc")
if r != 0:
    errors.append(f"case 4: '','abc' -> 0, got {r}")

# Case 5: both empty
r = longest_common_subsequence("", "")
if r != 0:
    errors.append(f"case 5: '','\\'' -> 0, got {r}")

# Case 6: single character match
r = longest_common_subsequence("a", "a")
if r != 1:
    errors.append(f"case 6: 'a','a' -> 1, got {r}")

# Case 7: single character no match
r = longest_common_subsequence("a", "b")
if r != 0:
    errors.append(f"case 7: 'a','b' -> 0, got {r}")

# Case 8: longer interleaved example
r = longest_common_subsequence("oxcpqrsvwf", "shmtulqrypy")
if r != 2:
    errors.append(f"case 8: 'oxcpqrsvwf','shmtulqrypy' -> 2, got {r}")

# Case 9: subsequence at boundaries
r = longest_common_subsequence("abcba", "abcbcba")
if r != 5:
    errors.append(f"case 9: 'abcba','abcbcba' -> 5, got {r}")

# Case 10: repeated characters
r = longest_common_subsequence("aaa", "aaaa")
if r != 3:
    errors.append(f"case 10: 'aaa','aaaa' -> 3, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 10 LCS test cases passed.")
print("Longest common subsequence implementation correct.")
"""


def check_lcs_file(ctx):
    """lcs.py should exist."""
    return "lcs.py" in ctx.files


def check_lcs_all_pass(ctx):
    """All 10 LCS cases should pass."""
    return "All 10 LCS test cases passed" in ctx.stdout


def check_lcs_has_function(ctx):
    """Should define longest_common_subsequence function."""
    src = ctx.files.get("lcs.py", "")
    return "def longest_common_subsequence(" in src


def check_lcs_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- stock trading with cooldown checks ---

_TEST_STOCK_PY = """\
import sys
try:
    from stock import max_profit
except ImportError:
    print("ERROR: stock.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic example with cooldown
r = max_profit([1, 2, 3, 0, 2])
if r != 3:
    errors.append(f"case 1: [1,2,3,0,2] -> 3, got {r}")

# Case 2: single price
r = max_profit([5])
if r != 0:
    errors.append(f"case 2: [5] -> 0, got {r}")

# Case 3: empty prices
r = max_profit([])
if r != 0:
    errors.append(f"case 3: [] -> 0, got {r}")

# Case 4: strictly decreasing — no profit possible
r = max_profit([5, 4, 3, 2, 1])
if r != 0:
    errors.append(f"case 4: decreasing -> 0, got {r}")

# Case 5: strictly increasing — one transaction (buy day 0, sell last day)
r = max_profit([1, 2, 3, 4, 5])
if r != 4:
    errors.append(f"case 5: [1,2,3,4,5] -> 4, got {r}")

# Case 6: two prices ascending
r = max_profit([1, 3])
if r != 2:
    errors.append(f"case 6: [1,3] -> 2, got {r}")

# Case 7: two prices descending
r = max_profit([3, 1])
if r != 0:
    errors.append(f"case 7: [3,1] -> 0, got {r}")

# Case 8: alternating up/down with cooldown constraint
r = max_profit([1, 2, 4, 2, 5, 7, 2, 4, 9, 0])
if r != 11:
    errors.append(f"case 8: [1,2,4,2,5,7,2,4,9,0] -> 11, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 stock-cooldown test cases passed.")
print("Stock trading with cooldown implementation correct.")
"""


def check_stock_file(ctx):
    """stock.py should exist."""
    return "stock.py" in ctx.files


def check_stock_all_pass(ctx):
    """All 8 stock-cooldown cases should pass."""
    return "All 8 stock-cooldown test cases passed" in ctx.stdout


def check_stock_has_function(ctx):
    """Should define max_profit function."""
    src = ctx.files.get("stock.py", "")
    return "def max_profit(" in src


def check_stock_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- rotate image checks ---

_TEST_ROTATE_PY = """\
import sys
try:
    from rotate import rotate_image
except ImportError:
    print("ERROR: rotate.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: 3x3 matrix
m = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
rotate_image(m)
if m != [[7, 4, 1], [8, 5, 2], [9, 6, 3]]:
    errors.append(f"case 1: 3x3 -> {m}")

# Case 2: 4x4 matrix
m = [[5, 1, 9, 11], [2, 4, 8, 10], [13, 3, 6, 7], [15, 14, 12, 16]]
rotate_image(m)
if m != [[15, 13, 2, 5], [14, 3, 4, 1], [12, 6, 8, 9], [16, 7, 10, 11]]:
    errors.append(f"case 2: 4x4 -> {m}")

# Case 3: 1x1 matrix
m = [[42]]
rotate_image(m)
if m != [[42]]:
    errors.append(f"case 3: 1x1 -> {m}")

# Case 4: 2x2 matrix
m = [[1, 2], [3, 4]]
rotate_image(m)
if m != [[3, 1], [4, 2]]:
    errors.append(f"case 4: 2x2 -> {m}")

# Case 5: 5x5 matrix
m = [
    [1, 2, 3, 4, 5],
    [6, 7, 8, 9, 10],
    [11, 12, 13, 14, 15],
    [16, 17, 18, 19, 20],
    [21, 22, 23, 24, 25],
]
rotate_image(m)
expected = [
    [21, 16, 11, 6, 1],
    [22, 17, 12, 7, 2],
    [23, 18, 13, 8, 3],
    [24, 19, 14, 9, 4],
    [25, 20, 15, 10, 5],
]
if m != expected:
    errors.append(f"case 5: 5x5 -> {m}")

# Case 6: matrix with zeros
m = [[0, 0], [0, 0]]
rotate_image(m)
if m != [[0, 0], [0, 0]]:
    errors.append(f"case 6: zeros 2x2 -> {m}")

# Case 7: matrix with negative numbers
m = [[-1, -2], [-3, -4]]
rotate_image(m)
if m != [[-3, -1], [-4, -2]]:
    errors.append(f"case 7: negatives 2x2 -> {m}")

# Case 8: verify in-place modification (function returns None)
m = [[1, 2], [3, 4]]
ret = rotate_image(m)
if ret is not None:
    errors.append(f"case 8: should return None (in-place), got {ret}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 rotate-image test cases passed.")
print("Rotate image implementation correct.")
"""


def check_rotate_file(ctx):
    """rotate.py should exist."""
    return "rotate.py" in ctx.files


def check_rotate_all_pass(ctx):
    """All 8 rotate-image cases should pass."""
    return "All 8 rotate-image test cases passed" in ctx.stdout


def check_rotate_has_function(ctx):
    """Should define rotate_image function."""
    src = ctx.files.get("rotate.py", "")
    return "def rotate_image(" in src


def check_rotate_in_place(ctx):
    """Should modify matrix in-place (no return or return None)."""
    src = ctx.files.get("rotate.py", "")
    # Should not contain "return [" or "return [[" — in-place means no new matrix returned
    return "return [" not in src and "return [[" not in src


def check_rotate_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "lcs",
        "files": {"test_lcs.py": _TEST_LCS_PY},
        "run": "python test_lcs.py",
        "prompt": (
            "A test script `test_lcs.py` is provided. Write `lcs.py` that "
            "implements `longest_common_subsequence(text1, text2)` where both "
            "arguments are strings. Return the length of their longest common "
            "subsequence. A subsequence is a sequence derived by deleting some "
            "or no elements without changing the order of the remaining elements. "
            "Use a 2D dynamic programming table. "
            "Run `python test_lcs.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "lcs.py exists": check_lcs_file,
            "all 10 cases pass": check_lcs_all_pass,
            "defines longest_common_subsequence function": check_lcs_has_function,
            "clean exit": check_lcs_exit,
        },
    },
    {
        "name": "stock-cooldown",
        "files": {"test_stock.py": _TEST_STOCK_PY},
        "run": "python test_stock.py",
        "prompt": (
            "A test script `test_stock.py` is provided. Write `stock.py` that "
            "implements `max_profit(prices)` where `prices` is a list of integers "
            "representing stock prices on consecutive days. You may complete as "
            "many buy-sell transactions as you like, but after selling you must "
            "wait one day (cooldown) before buying again. You cannot hold more "
            "than one share at a time. Return the maximum profit achievable. "
            "Use a state-machine DP approach with states: hold, sold, cooldown. "
            "Run `python test_stock.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "stock.py exists": check_stock_file,
            "all 8 cases pass": check_stock_all_pass,
            "defines max_profit function": check_stock_has_function,
            "clean exit": check_stock_exit,
        },
    },
    {
        "name": "rotate-image",
        "files": {"test_rotate.py": _TEST_ROTATE_PY},
        "run": "python test_rotate.py",
        "prompt": (
            "A test script `test_rotate.py` is provided. Write `rotate.py` that "
            "implements `rotate_image(matrix)` where `matrix` is an NxN list of "
            "lists of integers. Rotate the matrix 90 degrees clockwise **in-place** "
            "(modify the input matrix directly, do not return a new one). "
            "The function should return None. "
            "Run `python test_rotate.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "rotate.py exists": check_rotate_file,
            "all 8 cases pass": check_rotate_all_pass,
            "defines rotate_image function": check_rotate_has_function,
            "modifies in-place": check_rotate_in_place,
            "clean exit": check_rotate_exit,
        },
    },
]
