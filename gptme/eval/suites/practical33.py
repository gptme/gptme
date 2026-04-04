"""Practical eval tests (batch 33) — product except self, find duplicate, missing number."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- product of array except self checks (LC238) ---

_TEST_PES_PY = """\
import sys
try:
    from product_except_self import product_except_self
except ImportError:
    print("ERROR: product_except_self.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    r = product_except_self(nums)
    if r != expected:
        errors.append(f"{label}: product_except_self({nums}) -> {expected}, got {r}")

# Case 1: LeetCode example 1
check([1, 2, 3, 4], [24, 12, 8, 6], "case 1")

# Case 2: LeetCode example 2 — contains zero
check([-1, 1, 0, -3, 3], [0, 0, 9, 0, 0], "case 2")

# Case 3: two elements
check([3, 4], [4, 3], "case 3")

# Case 4: single zero
check([0, 1, 2, 3], [6, 0, 0, 0], "case 4")

# Case 5: two zeros — all products are zero
check([0, 0, 2, 3], [0, 0, 0, 0], "case 5")

# Case 6: all ones
check([1, 1, 1, 1], [1, 1, 1, 1], "case 6")

# Case 7: negative numbers
check([-1, -2, -3, -4], [-24, -12, -8, -6], "case 7")

# Case 8: mixed sign
check([1, -1, 2, -2], [4, -4, 2, -2], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 product-except-self test cases passed.")
    print("Product except self implementation correct.")
"""

# --- find duplicate number checks (LC287) ---

_TEST_FD_PY = """\
import sys
try:
    from find_duplicate import find_duplicate
except ImportError:
    print("ERROR: find_duplicate.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    r = find_duplicate(nums)
    if r != expected:
        errors.append(f"{label}: find_duplicate({nums}) -> {expected}, got {r}")

# Case 1: LeetCode example 1
check([1, 3, 4, 2, 2], 2, "case 1")

# Case 2: LeetCode example 2
check([3, 1, 3, 4, 2], 3, "case 2")

# Case 3: duplicate at end
check([1, 2, 3, 4, 4], 4, "case 3")

# Case 4: duplicate at start
check([1, 1, 2, 3, 4], 1, "case 4")

# Case 5: large duplicate
check([1, 2, 3, 4, 5, 5], 5, "case 5")

# Case 6: only two elements, both same
check([1, 1], 1, "case 6")

# Case 7: duplicate is 9 in larger array
check([2, 5, 9, 6, 9, 3, 8, 9, 7, 1], 9, "case 7")

# Case 8: n=6, duplicate is 3
check([3, 1, 3, 4, 2, 5], 3, "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 find-duplicate test cases passed.")
    print("Find duplicate implementation correct.")
"""

# --- missing number checks (LC268) ---

_TEST_MN_PY = """\
import sys
try:
    from missing_number import missing_number
except ImportError:
    print("ERROR: missing_number.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    r = missing_number(nums)
    if r != expected:
        errors.append(f"{label}: missing_number({nums}) -> {expected}, got {r}")

# Case 1: LeetCode example 1
check([3, 0, 1], 2, "case 1")

# Case 2: LeetCode example 2
check([0, 1], 2, "case 2")

# Case 3: LeetCode example 3
check([9, 6, 4, 2, 3, 5, 7, 0, 1], 8, "case 3")

# Case 4: missing first
check([1, 2, 3], 0, "case 4")

# Case 5: single element zero
check([0], 1, "case 5")

# Case 6: single element one
check([1], 0, "case 6")

# Case 7: missing in middle of small range
check([0, 1, 3], 2, "case 7")

# Case 8: large missing at start
check([1, 2, 3, 4, 5], 0, "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 missing-number test cases passed.")
    print("Missing number implementation correct.")
"""


def check_pes_file(ctx) -> bool:
    return "product_except_self.py" in ctx.files


def check_pes_has_function(ctx) -> bool:
    return "def product_except_self" in ctx.files.get("product_except_self.py", "")


def check_pes_all_pass(ctx) -> bool:
    return "All 8 product-except-self test cases passed." in ctx.stdout


def check_pes_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_fd_file(ctx) -> bool:
    return "find_duplicate.py" in ctx.files


def check_fd_has_function(ctx) -> bool:
    return "def find_duplicate" in ctx.files.get("find_duplicate.py", "")


def check_fd_all_pass(ctx) -> bool:
    return "All 8 find-duplicate test cases passed." in ctx.stdout


def check_fd_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_mn_file(ctx) -> bool:
    return "missing_number.py" in ctx.files


def check_mn_has_function(ctx) -> bool:
    return "def missing_number" in ctx.files.get("missing_number.py", "")


def check_mn_all_pass(ctx) -> bool:
    return "All 8 missing-number test cases passed." in ctx.stdout


def check_mn_exit(ctx) -> bool:
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "product-except-self",
        "files": {"test_pes.py": _TEST_PES_PY},
        "run": "python test_pes.py",
        "prompt": (
            "A test script `test_pes.py` is provided. Write `product_except_self.py` "
            "that implements `product_except_self(nums: list[int]) -> list[int]`. "
            "Return an array where result[i] equals the product of all elements in nums "
            "except nums[i]. Must run in O(n) time and NOT use division. "
            "Approach: two-pass prefix/suffix. First pass: result[i] = product of all "
            "elements to the left of i (result[0]=1). Second pass: multiply from the "
            "right using a running suffix product (start at 1, multiply into result[i], "
            "then update suffix *= nums[i]). O(n) time, O(1) extra space. "
            "Run `python test_pes.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "product_except_self.py exists": check_pes_file,
            "defines product_except_self function": check_pes_has_function,
            "all 8 cases pass": check_pes_all_pass,
            "clean exit": check_pes_exit,
        },
    },
    {
        "name": "find-duplicate",
        "files": {"test_fd.py": _TEST_FD_PY},
        "run": "python test_fd.py",
        "prompt": (
            "A test script `test_fd.py` is provided. Write `find_duplicate.py` that "
            "implements `find_duplicate(nums: list[int]) -> int`. "
            "Given n+1 integers in range [1, n], find the one duplicate. "
            "Must not modify the array and use only O(1) extra space. "
            "Use Floyd's cycle detection: treat nums as a linked list where index i "
            "points to nums[i]. The duplicate creates a cycle. "
            "Phase 1: fast (two steps) and slow (one step) both enter the cycle. "
            "Phase 2: reset slow to 0; move both one step until they meet — that's "
            "the entry point (the duplicate). O(n) time, O(1) space. "
            "Run `python test_fd.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "find_duplicate.py exists": check_fd_file,
            "defines find_duplicate function": check_fd_has_function,
            "all 8 cases pass": check_fd_all_pass,
            "clean exit": check_fd_exit,
        },
    },
    {
        "name": "missing-number",
        "files": {"test_mn.py": _TEST_MN_PY},
        "run": "python test_mn.py",
        "prompt": (
            "A test script `test_mn.py` is provided. Write `missing_number.py` that "
            "implements `missing_number(nums: list[int]) -> int`. "
            "Given an array of n distinct numbers in range [0, n], return the one "
            "missing number. "
            "Use the Gauss formula: expected sum = n*(n+1)//2 where n=len(nums). "
            "Return expected_sum - sum(nums). O(n) time, O(1) space. "
            "Alternatively, XOR all indices 0..n with all elements — pairs cancel. "
            "Run `python test_mn.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "missing_number.py exists": check_mn_file,
            "defines missing_number function": check_mn_has_function,
            "all 8 cases pass": check_mn_all_pass,
            "clean exit": check_mn_exit,
        },
    },
]
