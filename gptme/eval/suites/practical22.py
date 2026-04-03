"""Practical eval tests (batch 22) — greedy, DP, and backtracking.

Tests requiring correct implementation of:
- Trapping rain water: compute total water trapped between elevation bars (two pointers)
- Word break: determine if a string can be segmented into dictionary words (DP)
- Permutations: generate all distinct permutations of a list (backtracking)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- trapping-rain-water checks ---

_TEST_RAIN_PY = """\
import sys
try:
    from rain import trap
except ImportError:
    print("ERROR: rain.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic leetcode example
r = trap([0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1])
if r != 6:
    errors.append(f"case 1: classic -> 6, got {r}")

# Case 2: another classic example
r = trap([4, 2, 0, 3, 2, 5])
if r != 9:
    errors.append(f"case 2: [4,2,0,3,2,5] -> 9, got {r}")

# Case 3: no water trapped (ascending)
r = trap([1, 2, 3, 4, 5])
if r != 0:
    errors.append(f"case 3: ascending -> 0, got {r}")

# Case 4: no water trapped (descending)
r = trap([5, 4, 3, 2, 1])
if r != 0:
    errors.append(f"case 4: descending -> 0, got {r}")

# Case 5: single valley
r = trap([3, 0, 3])
if r != 3:
    errors.append(f"case 5: [3,0,3] -> 3, got {r}")

# Case 6: empty array
r = trap([])
if r != 0:
    errors.append(f"case 6: empty -> 0, got {r}")

# Case 7: single bar
r = trap([5])
if r != 0:
    errors.append(f"case 7: single bar -> 0, got {r}")

# Case 8: flat plateau
r = trap([2, 2, 2])
if r != 0:
    errors.append(f"case 8: flat -> 0, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 rain-water test cases passed.")
print("Trapping rain water implementation correct.")
"""


def check_rain_file(ctx):
    """rain.py should exist."""
    return "rain.py" in ctx.files


def check_rain_all_pass(ctx):
    """All 8 rain-water cases should pass."""
    return "All 8 rain-water test cases passed" in ctx.stdout


def check_rain_has_function(ctx):
    """Should define trap function."""
    src = ctx.files.get("rain.py", "")
    return "def trap(" in src


def check_rain_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- word-break checks ---

_TEST_WORDBREAK_PY = """\
import sys
try:
    from wordbreak import word_break
except ImportError:
    print("ERROR: wordbreak.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic example — can segment
r = word_break("leetcode", ["leet", "code"])
if r is not True:
    errors.append(f"case 1: 'leetcode' with [leet,code] -> True, got {r}")

# Case 2: can segment — reuse words (apple + pen + apple)
r = word_break("applepenapple", ["apple", "pen"])
if r is not True:
    errors.append(f"case 2: 'applepenapple' with [apple,pen] -> True, got {r}")

# Case 3: cannot segment
r = word_break("catsandog", ["cats", "dog", "sand", "and", "cat"])
if r is not False:
    errors.append(f"case 3: 'catsandog' -> False, got {r}")

# Case 4: empty string is always segmentable
r = word_break("", ["hello"])
if r is not True:
    errors.append(f"case 4: empty string -> True, got {r}")

# Case 5: single word in dict
r = word_break("hello", ["hello"])
if r is not True:
    errors.append(f"case 5: 'hello' in dict -> True, got {r}")

# Case 6: word not in dict at all
r = word_break("abc", ["de", "fg"])
if r is not False:
    errors.append(f"case 6: 'abc' not in dict -> False, got {r}")

# Case 7: repeated word usage allowed
r = word_break("aaaa", ["a", "aa"])
if r is not True:
    errors.append(f"case 7: 'aaaa' with [a,aa] -> True (reuse allowed), got {r}")

# Case 8: longer multi-word segmentation
r = word_break("pineapplepenapple", ["apple", "pen", "applepen", "pine", "pineapple"])
if r is not True:
    errors.append(f"case 8: 'pineapplepenapple' -> True, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 word-break test cases passed.")
print("Word break implementation correct.")
"""


def check_wordbreak_file(ctx):
    """wordbreak.py should exist."""
    return "wordbreak.py" in ctx.files


def check_wordbreak_all_pass(ctx):
    """All 8 word-break cases should pass."""
    return "All 8 word-break test cases passed" in ctx.stdout


def check_wordbreak_has_function(ctx):
    """Should define word_break function."""
    src = ctx.files.get("wordbreak.py", "")
    return "def word_break(" in src


def check_wordbreak_uses_dp(ctx):
    """Should use DP (dp array or memoization)."""
    src = ctx.files.get("wordbreak.py", "")
    return bool(re.search(r"\bdp\b|\bmemo\b|cache|functools", src))


def check_wordbreak_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- permutations checks ---

_TEST_PERMS_PY = """\
import sys
try:
    from perms import permutations
except ImportError:
    print("ERROR: perms.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def sorted_perms(result):
    return sorted(tuple(p) for p in result)

# Case 1: three elements — 6 permutations
r = sorted_perms(permutations([1, 2, 3]))
expected = sorted_perms([[1,2,3],[1,3,2],[2,1,3],[2,3,1],[3,1,2],[3,2,1]])
if r != expected:
    errors.append(f"case 1: [1,2,3] -> 6 perms, got {len(r)} perms")

# Case 2: two elements — 2 permutations
r = sorted_perms(permutations([1, 2]))
if len(r) != 2:
    errors.append(f"case 2: [1,2] -> 2 perms, got {len(r)}")

# Case 3: single element — 1 permutation
r = permutations([42])
if len(r) != 1 or list(r[0]) != [42]:
    errors.append(f"case 3: [42] -> [[42]], got {r}")

# Case 4: empty list — 1 permutation (the empty permutation)
r = permutations([])
if len(r) != 1:
    errors.append(f"case 4: [] -> 1 perm (empty), got {len(r)}")

# Case 5: four elements — 24 permutations
r = permutations([1, 2, 3, 4])
if len(r) != 24:
    errors.append(f"case 5: [1,2,3,4] -> 24 perms, got {len(r)}")

# Case 6: no duplicates in result
r = permutations([1, 2, 3])
seen = set()
dups = []
for p in r:
    key = tuple(p)
    if key in seen:
        dups.append(key)
    seen.add(key)
if dups:
    errors.append(f"case 6: no duplicate perms, found {dups}")

# Case 7: each permutation has correct length
r = permutations([5, 6, 7])
wrong_len = [p for p in r if len(p) != 3]
if wrong_len:
    errors.append(f"case 7: each perm has len 3, got {wrong_len}")

# Case 8: all original elements present in each permutation
r = permutations([10, 20, 30])
original = sorted([10, 20, 30])
bad = [p for p in r if sorted(p) != original]
if bad:
    errors.append(f"case 8: each perm contains original elements, bad: {bad}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 permutations test cases passed.")
print("Permutations implementation correct.")
"""


def check_perms_file(ctx):
    """perms.py should exist."""
    return "perms.py" in ctx.files


def check_perms_all_pass(ctx):
    """All 8 permutations cases should pass."""
    return "All 8 permutations test cases passed" in ctx.stdout


def check_perms_has_function(ctx):
    """Should define permutations function."""
    src = ctx.files.get("perms.py", "")
    return "def permutations(" in src


def check_perms_uses_backtracking(ctx):
    """Should use backtracking or recursive approach."""
    src = ctx.files.get("perms.py", "")
    return bool(re.search(r"\brecur|\bbacktrack|\bswap\b", src))


def check_perms_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "trapping-rain-water",
        "files": {"test_rain.py": _TEST_RAIN_PY},
        "run": "python test_rain.py",
        "prompt": (
            "A test script `test_rain.py` is provided. Write `rain.py` that "
            "implements `trap(height)` where `height` is a list of non-negative "
            "integers representing an elevation map — each element is the height "
            "of a bar with width 1. Compute how much water can be trapped after "
            "raining. Use a two-pointer approach for O(n) time and O(1) space. "
            "Run `python test_rain.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "rain.py exists": check_rain_file,
            "all 8 cases pass": check_rain_all_pass,
            "defines trap function": check_rain_has_function,
            "clean exit": check_rain_exit,
        },
    },
    {
        "name": "word-break",
        "files": {"test_wordbreak.py": _TEST_WORDBREAK_PY},
        "run": "python test_wordbreak.py",
        "prompt": (
            "A test script `test_wordbreak.py` is provided. Write `wordbreak.py` "
            "that implements `word_break(s, word_dict)` where `s` is a string and "
            "`word_dict` is a list of strings. Return True if `s` can be segmented "
            "into a space-separated sequence of one or more dictionary words (words "
            "may be reused). Use dynamic programming. "
            "Run `python test_wordbreak.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "wordbreak.py exists": check_wordbreak_file,
            "all 8 cases pass": check_wordbreak_all_pass,
            "defines word_break function": check_wordbreak_has_function,
            "uses DP": check_wordbreak_uses_dp,
            "clean exit": check_wordbreak_exit,
        },
    },
    {
        "name": "permutations",
        "files": {"test_perms.py": _TEST_PERMS_PY},
        "run": "python test_perms.py",
        "prompt": (
            "A test script `test_perms.py` is provided. Write `perms.py` that "
            "implements `permutations(nums)` where `nums` is a list of distinct "
            "integers. Return all possible permutations as a list of lists. "
            "The order of permutations in the result does not matter. "
            "Use a backtracking (recursive) approach. "
            "Run `python test_perms.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "perms.py exists": check_perms_file,
            "all 8 cases pass": check_perms_all_pass,
            "defines permutations function": check_perms_has_function,
            "uses backtracking": check_perms_uses_backtracking,
            "clean exit": check_perms_exit,
        },
    },
]
