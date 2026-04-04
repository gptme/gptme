"""Practical eval tests (batch 29) — word break II, unique paths, and rotate array."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- word break II checks (LC140 — return all segmentations) ---

_TEST_WB_PY = """\
import sys
try:
    from word_break_ii import word_break_ii
except ImportError:
    print("ERROR: word_break_ii.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(s, word_dict, expected, label):
    result = sorted(word_break_ii(s, word_dict))
    exp = sorted(expected)
    if result != exp:
        errors.append(f"{label}: word_break_ii({s!r}, ...) -> {exp}, got {result}")

# Case 1: two valid segmentations
check("catsanddog", ["cat","cats","and","sand","dog"],
      ["cat sand dog", "cats and dog"], "case 1")

# Case 2: three valid segmentations
check("pineapplepenapple", ["apple","pen","applepen","pine","pineapple"],
      ["pine apple pen apple", "pine applepen apple", "pineapple pen apple"], "case 2")

# Case 3: impossible — empty result
check("catsandog", ["cats","dog","sand","and","cat"], [], "case 3")

# Case 4: single character
check("a", ["a"], ["a"], "case 4")

# Case 5: repeated single word
check("aa", ["a"], ["a a"], "case 5")

# Case 6: three results with word reuse
check("aaa", ["a","aa"], ["a a a", "a aa", "aa a"], "case 6")

# Case 7: three results with partial overlaps
check("abc", ["a","b","c","ab","bc"],
      ["a b c", "a bc", "ab c"], "case 7")

# Case 8: two results including full-word match
check("leetcode", ["leet","code","leetcode"],
      ["leet code", "leetcode"], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 word-break-ii test cases passed.")
    print("Word Break II implementation correct.")
"""

# --- unique paths checks ---

_TEST_UP_PY = """\
import sys
try:
    from unique_paths import unique_paths
except ImportError:
    print("ERROR: unique_paths.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: LeetCode example 1
r = unique_paths(3, 7)
if r != 28:
    errors.append(f"case 1: 3x7 -> 28, got {r}")

# Case 2: LeetCode example 2
r = unique_paths(3, 2)
if r != 3:
    errors.append(f"case 2: 3x2 -> 3, got {r}")

# Case 3: 1x1 grid
r = unique_paths(1, 1)
if r != 1:
    errors.append(f"case 3: 1x1 -> 1, got {r}")

# Case 4: transposed 3x7
r = unique_paths(7, 3)
if r != 28:
    errors.append(f"case 4: 7x3 -> 28, got {r}")

# Case 5: 2x2 grid
r = unique_paths(2, 2)
if r != 2:
    errors.append(f"case 5: 2x2 -> 2, got {r}")

# Case 6: single row
r = unique_paths(1, 5)
if r != 1:
    errors.append(f"case 6: 1x5 -> 1, got {r}")

# Case 7: single column
r = unique_paths(5, 1)
if r != 1:
    errors.append(f"case 7: 5x1 -> 1, got {r}")

# Case 8: 4x4 grid
r = unique_paths(4, 4)
if r != 20:
    errors.append(f"case 8: 4x4 -> 20, got {r}")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 unique-paths test cases passed.")
    print("Unique paths implementation correct.")
"""

# --- rotate array checks ---

_TEST_RA_PY = """\
import sys
try:
    from rotate_array import rotate
except ImportError:
    print("ERROR: rotate_array.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums_in, k, expected, label):
    nums = nums_in[:]
    result = rotate(nums, k)
    actual = result if result is not None else nums
    if actual != expected:
        errors.append(f"{label}: rotate({nums_in}, {k}) -> {expected}, got {actual}")

# Case 1: LeetCode example 1
check([1,2,3,4,5,6,7], 3, [5,6,7,1,2,3,4], "case 1")

# Case 2: LeetCode example 2
check([-1,-100,3,99], 2, [3,99,-1,-100], "case 2")

# Case 3: k > len (k % 3 = 1)
check([1,2,3], 4, [3,1,2], "case 3")

# Case 4: single element, k=0
check([1], 0, [1], "case 4")

# Case 5: two elements, k=3 (k % 2 = 1)
check([1,2], 3, [2,1], "case 5")

# Case 6: k=0 — no rotation
check([1,2,3,4,5], 0, [1,2,3,4,5], "case 6")

# Case 7: k = len — full rotation, same array
check([1,2,3,4,5,6], 6, [1,2,3,4,5,6], "case 7")

# Case 8: rotate right by 2
check([1,2,3,4,5], 2, [4,5,1,2,3], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 rotate-array test cases passed.")
    print("Rotate array implementation correct.")
"""


def check_wb_file(ctx) -> bool:
    return "word_break_ii.py" in ctx.files


def check_wb_has_function(ctx) -> bool:
    return "def word_break_ii" in ctx.files.get("word_break_ii.py", "")


def check_wb_all_pass(ctx) -> bool:
    return "All 8 word-break-ii test cases passed." in ctx.stdout


def check_wb_uses_memo(ctx) -> bool:
    """Should use memoization or caching (DFS + memo as instructed)."""
    src = ctx.files.get("word_break_ii.py", "")
    return bool(re.search(r"\bmemo\b|\bcache\b|lru_cache|functools", src))


def check_wb_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_up_file(ctx) -> bool:
    return "unique_paths.py" in ctx.files


def check_up_has_function(ctx) -> bool:
    return "def unique_paths" in ctx.files.get("unique_paths.py", "")


def check_up_all_pass(ctx) -> bool:
    return "All 8 unique-paths test cases passed." in ctx.stdout


def check_up_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_ra_file(ctx) -> bool:
    return "rotate_array.py" in ctx.files


def check_ra_has_function(ctx) -> bool:
    return "def rotate(" in ctx.files.get("rotate_array.py", "")


def check_ra_all_pass(ctx) -> bool:
    return "All 8 rotate-array test cases passed." in ctx.stdout


def check_ra_exit(ctx) -> bool:
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "word-break-ii",
        "files": {"test_wb.py": _TEST_WB_PY},
        "run": "python test_wb.py",
        "prompt": (
            "A test script `test_wb.py` is provided. Write `word_break_ii.py` that "
            "implements `word_break_ii(s: str, word_dict: list[str]) -> list[str]`. "
            "Given a string s and a word dictionary, return all possible ways to "
            "segment s into space-separated words from the dictionary (words may be "
            "reused). Return the sentences in any order. "
            "Use recursive DFS with memoization: for each start index, try every prefix "
            "that is in the word set, then recurse on the remainder; memoize results "
            "keyed by start index to avoid exponential recomputation. "
            "O(n^2 * results) time, O(n * results) space. "
            "Run `python test_wb.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "word_break_ii.py exists": check_wb_file,
            "defines word_break_ii function": check_wb_has_function,
            "uses memoization": check_wb_uses_memo,
            "all 8 cases pass": check_wb_all_pass,
            "clean exit": check_wb_exit,
        },
    },
    {
        "name": "unique-paths",
        "files": {"test_up.py": _TEST_UP_PY},
        "run": "python test_up.py",
        "prompt": (
            "A test script `test_up.py` is provided. Write `unique_paths.py` that "
            "implements `unique_paths(m: int, n: int) -> int`. A robot starts at the "
            "top-left corner of an m×n grid and must reach the bottom-right corner, "
            "moving only right or down. Return the number of distinct paths. "
            "Use DP: create an m×n grid initialised to 1 for the first row and first "
            "column (only one way to reach any cell in those); for every other cell "
            "dp[i][j] = dp[i-1][j] + dp[i][j-1]. Return dp[m-1][n-1]. "
            "Alternatively use combinatorics: C(m+n-2, m-1). O(m*n) time, O(m*n) or "
            "O(n) space. "
            "Run `python test_up.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "unique_paths.py exists": check_up_file,
            "defines unique_paths function": check_up_has_function,
            "all 8 cases pass": check_up_all_pass,
            "clean exit": check_up_exit,
        },
    },
    {
        "name": "rotate-array",
        "files": {"test_ra.py": _TEST_RA_PY},
        "run": "python test_ra.py",
        "prompt": (
            "A test script `test_ra.py` is provided. Write `rotate_array.py` that "
            "implements `rotate(nums: list[int], k: int) -> None`. Rotate the array "
            "to the right by k steps in-place (modifying nums directly). "
            "Algorithm: first normalise k = k % len(nums) to handle k >= len. Then "
            "reverse the entire array, reverse the first k elements, then reverse the "
            "remaining n-k elements. This gives an O(n) time, O(1) space solution. "
            "The test handles both in-place (returns None) and returns-list styles. "
            "Run `python test_ra.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "rotate_array.py exists": check_ra_file,
            "defines rotate function": check_ra_has_function,
            "all 8 cases pass": check_ra_all_pass,
            "clean exit": check_ra_exit,
        },
    },
]
