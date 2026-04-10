"""Practical eval tests (batch 31) — 3sum, majority element, counting bits."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- 3sum checks (LC15) ---

_TEST_3S_PY = """\
import sys
try:
    from three_sum import three_sum
except ImportError:
    print("ERROR: three_sum.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def normalize(triplets):
    return sorted(sorted(t) for t in triplets)

def check(nums, expected, label):
    result = normalize(three_sum(nums))
    exp = normalize(expected)
    if result != exp:
        errors.append(f"{label}: three_sum({nums}) -> {exp}, got {result}")

# Case 1: LeetCode example 1
check([-1, 0, 1, 2, -1, -4], [[-1, -1, 2], [-1, 0, 1]], "case 1")

# Case 2: no valid triplet
check([0, 1, 1], [], "case 2")

# Case 3: all zeros
check([0, 0, 0], [[0, 0, 0]], "case 3")

# Case 4: large span
check([-4, -1, -1, 0, 1, 2], [[-1, -1, 2], [-1, 0, 1]], "case 4")

# Case 5: duplicates, still unique triplets
check([-2, 0, 0, 2, 2], [[-2, 0, 2]], "case 5")

# Case 6: positive only — no triplets
check([1, 2, 3, 4], [], "case 6")

# Case 7: negative + zero
check([-3, -2, -1, 0, 0, 1, 2, 3], [[-3, 0, 3], [-3, 1, 2], [-2, -1, 3], [-2, 0, 2], [-1, -1, 2], [-1, 0, 1]], "case 7")

# Case 8: two elements only
check([-1, 1], [], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 three-sum test cases passed.")
    print("3sum implementation correct.")
"""

# --- majority element checks (LC169) ---

_TEST_ME_PY = """\
import sys
try:
    from majority_element import majority_element
except ImportError:
    print("ERROR: majority_element.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    r = majority_element(nums)
    if r != expected:
        errors.append(f"{label}: majority_element({nums}) -> {expected}, got {r}")

# Case 1: LeetCode example 1
check([3, 2, 3], 3, "case 1")

# Case 2: LeetCode example 2
check([2, 2, 1, 1, 1, 2, 2], 2, "case 2")

# Case 3: single element
check([1], 1, "case 3")

# Case 4: two elements, first is majority
check([5, 5, 4], 5, "case 4")

# Case 5: all same
check([7, 7, 7, 7], 7, "case 5")

# Case 6: large majority in last half
check([1, 2, 3, 3, 3, 3, 3], 3, "case 6")

# Case 7: majority is negative
check([-1, -1, -1, 2, 2], -1, "case 7")

# Case 8: two-element array
check([6, 6], 6, "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 majority-element test cases passed.")
    print("Majority element implementation correct.")
"""

# --- counting bits checks (LC338) ---

_TEST_CB_PY = """\
import sys
try:
    from counting_bits import count_bits
except ImportError:
    print("ERROR: counting_bits.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(n, expected, label):
    r = count_bits(n)
    if r != expected:
        errors.append(f"{label}: count_bits({n}) -> {expected}, got {r}")

# Case 1: n=2 — [0,1,1]
check(2, [0, 1, 1], "case 1")

# Case 2: n=5 — [0,1,1,2,1,2]
check(5, [0, 1, 1, 2, 1, 2], "case 2")

# Case 3: n=0 — [0]
check(0, [0], "case 3")

# Case 4: n=1 — [0,1]
check(1, [0, 1], "case 4")

# Case 5: n=7 — powers of 2 reset to 1
check(7, [0, 1, 1, 2, 1, 2, 2, 3], "case 5")

# Case 6: n=4 — [0,1,1,2,1]
check(4, [0, 1, 1, 2, 1], "case 6")

# Case 7: n=10 — verify a few
check(10, [0,1,1,2,1,2,2,3,1,2,2], "case 7")

# Case 8: n=15 — all single nibble values
check(15, [0,1,1,2,1,2,2,3,1,2,2,3,2,3,3,4], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 counting-bits test cases passed.")
    print("Counting bits implementation correct.")
"""


def check_3s_file(ctx) -> bool:
    return "three_sum.py" in ctx.files


def check_3s_has_function(ctx) -> bool:
    return "def three_sum" in ctx.files.get("three_sum.py", "")


def check_3s_all_pass(ctx) -> bool:
    return "All 8 three-sum test cases passed." in ctx.stdout


def check_3s_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_me_file(ctx) -> bool:
    return "majority_element.py" in ctx.files


def check_me_has_function(ctx) -> bool:
    return "def majority_element" in ctx.files.get("majority_element.py", "")


def check_me_all_pass(ctx) -> bool:
    return "All 8 majority-element test cases passed." in ctx.stdout


def check_me_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_cb_file(ctx) -> bool:
    return "counting_bits.py" in ctx.files


def check_cb_has_function(ctx) -> bool:
    return "def count_bits" in ctx.files.get("counting_bits.py", "")


def check_cb_all_pass(ctx) -> bool:
    return "All 8 counting-bits test cases passed." in ctx.stdout


def check_cb_exit(ctx) -> bool:
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "three-sum",
        "files": {"test_3s.py": _TEST_3S_PY},
        "run": "python test_3s.py",
        "prompt": (
            "A test script `test_3s.py` is provided. Write `three_sum.py` that "
            "implements `three_sum(nums: list[int]) -> list[list[int]]`. "
            "Return all unique triplets [a, b, c] such that a + b + c == 0. "
            "The solution set must not contain duplicate triplets. "
            "Approach: sort nums, then for each i use two pointers lo=i+1 and hi=len-1; "
            "move them inward based on the current sum; skip duplicates at each step. "
            "O(n^2) time, O(n) space (for output). "
            "Run `python test_3s.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "three_sum.py exists": check_3s_file,
            "defines three_sum function": check_3s_has_function,
            "all 8 cases pass": check_3s_all_pass,
            "clean exit": check_3s_exit,
        },
    },
    {
        "name": "majority-element",
        "files": {"test_me.py": _TEST_ME_PY},
        "run": "python test_me.py",
        "prompt": (
            "A test script `test_me.py` is provided. Write `majority_element.py` that "
            "implements `majority_element(nums: list[int]) -> int`. "
            "Return the element that appears more than n//2 times. "
            "Guaranteed: a majority element always exists. "
            "Use Boyer-Moore voting: maintain a candidate and a count; iterate through "
            "nums — if count==0 set candidate=num; if num==candidate increment count, "
            "else decrement. The last surviving candidate is the answer. "
            "O(n) time, O(1) space. "
            "Run `python test_me.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "majority_element.py exists": check_me_file,
            "defines majority_element function": check_me_has_function,
            "all 8 cases pass": check_me_all_pass,
            "clean exit": check_me_exit,
        },
    },
    {
        "name": "counting-bits",
        "files": {"test_cb.py": _TEST_CB_PY},
        "run": "python test_cb.py",
        "prompt": (
            "A test script `test_cb.py` is provided. Write `counting_bits.py` that "
            "implements `count_bits(n: int) -> list[int]`. "
            "Return an array of length n+1 where result[i] is the number of 1-bits "
            "in the binary representation of i. "
            "Use DP: result[0]=0; for i>=1, result[i] = result[i >> 1] + (i & 1). "
            "This uses the fact that i>>1 has one fewer bit examined, and (i&1) adds "
            "the least-significant bit. O(n) time, O(n) space. "
            "Run `python test_cb.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "counting_bits.py exists": check_cb_file,
            "defines count_bits function": check_cb_has_function,
            "all 8 cases pass": check_cb_all_pass,
            "clean exit": check_cb_exit,
        },
    },
]
