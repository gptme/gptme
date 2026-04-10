"""Practical eval tests (batch 32) — combination sum, generate parentheses, single number."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- combination sum checks (LC39) ---

_TEST_CS_PY = """\
import sys
try:
    from combination_sum import combination_sum
except ImportError:
    print("ERROR: combination_sum.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def normalize(combos):
    return sorted(sorted(c) for c in combos)

def check(candidates, target, expected, label):
    result = normalize(combination_sum(candidates, target))
    exp = normalize(expected)
    if result != exp:
        errors.append(f"{label}: combination_sum({candidates}, {target}) -> {exp}, got {result}")

# Case 1: LeetCode example 1
check([2, 3, 6, 7], 7, [[2, 2, 3], [7]], "case 1")

# Case 2: LeetCode example 2
check([2, 3, 5], 8, [[2, 2, 2, 2], [2, 3, 3], [3, 5]], "case 2")

# Case 3: no solution
check([2], 3, [], "case 3")

# Case 4: single candidate that is the target
check([5], 5, [[5]], "case 4")

# Case 5: single candidate repeated
check([3], 9, [[3, 3, 3]], "case 5")

# Case 6: multiple paths
check([1, 2], 4, [[1, 1, 1, 1], [1, 1, 2], [2, 2]], "case 6")

# Case 7: target unreachable with given candidates
check([3, 5], 4, [], "case 7")

# Case 8: larger candidates
check([2, 3, 4, 5], 5, [[2, 3], [5]], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 combination-sum test cases passed.")
    print("Combination sum implementation correct.")
"""

# --- generate parentheses checks (LC22) ---

_TEST_GP_PY = """\
import sys
try:
    from generate_parentheses import generate_parentheses
except ImportError:
    print("ERROR: generate_parentheses.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(n, expected_count, expected_set, label):
    result = generate_parentheses(n)
    result_set = set(result)
    exp_set = set(expected_set)
    if len(result) != expected_count or result_set != exp_set:
        errors.append(f"{label}: n={n} expected {expected_count} unique combos {sorted(exp_set)[:3]}..., got {len(result)} {sorted(result_set)[:3]}...")

# Case 1: n=1
check(1, 1, ["()"], "case 1")

# Case 2: n=2
check(2, 2, ["(())", "()()"], "case 2")

# Case 3: n=3 — 5 combinations (Catalan number C3=5)
check(3, 5, ["((()))", "(()())", "(())()", "()(())", "()()()"], "case 3")

# Case 4: n=4 — 14 combinations
check(4, 14, [
    "(((())))", "((()()))", "((())())", "((()))()", "(()(()))",
    "(()()())", "(()())()", "(())(())", "(())()()", "()((()))",
    "()(()())", "()(())()", "()()(())", "()()()()"
], "case 4")

# Case 5: count only for n=5 (42 combos)
result5 = generate_parentheses(5)
if len(result5) != 42:
    errors.append(f"case 5: n=5 expected 42 combinations, got {len(result5)}")

# Case 6: all results are valid (equal open/close, never more close than open)
def is_valid(s):
    count = 0
    for c in s:
        if c == '(': count += 1
        else: count -= 1
        if count < 0: return False
    return count == 0

invalid = [s for s in generate_parentheses(4) if not is_valid(s)]
if invalid:
    errors.append(f"case 6: n=4 generated invalid strings: {invalid}")

# Case 7: no duplicates
r3 = generate_parentheses(3)
if len(r3) != len(set(r3)):
    errors.append(f"case 7: n=3 contains duplicates: {r3}")

# Case 8: all strings have length 2*n
r4 = generate_parentheses(4)
wrong_len = [s for s in r4 if len(s) != 8]
if wrong_len:
    errors.append(f"case 8: n=4 some strings not length 8: {wrong_len}")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 generate-parentheses test cases passed.")
    print("Generate parentheses implementation correct.")
"""

# --- single number checks (LC136) ---

_TEST_SN_PY = """\
import sys
try:
    from single_number import single_number
except ImportError:
    print("ERROR: single_number.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    r = single_number(nums)
    if r != expected:
        errors.append(f"{label}: single_number({nums}) -> {expected}, got {r}")

# Case 1: LeetCode example 1
check([2, 2, 1], 1, "case 1")

# Case 2: LeetCode example 2
check([4, 1, 2, 1, 2], 4, "case 2")

# Case 3: single element
check([1], 1, "case 3")

# Case 4: negative numbers
check([-1, -1, -2], -2, "case 4")

# Case 5: unique at start
check([5, 3, 3], 5, "case 5")

# Case 6: unique at middle
check([9, 7, 9], 7, "case 6")

# Case 7: larger array
check([0, 4, 0, 2, 4, 3, 2], 3, "case 7")

# Case 8: all positive, unique is zero
check([0, 1, 1], 0, "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 single-number test cases passed.")
    print("Single number implementation correct.")
"""


def check_cs_file(ctx) -> bool:
    return "combination_sum.py" in ctx.files


def check_cs_has_function(ctx) -> bool:
    return "def combination_sum" in ctx.files.get("combination_sum.py", "")


def check_cs_all_pass(ctx) -> bool:
    return "All 8 combination-sum test cases passed." in ctx.stdout


def check_cs_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_gp_file(ctx) -> bool:
    return "generate_parentheses.py" in ctx.files


def check_gp_has_function(ctx) -> bool:
    return "def generate_parentheses" in ctx.files.get("generate_parentheses.py", "")


def check_gp_all_pass(ctx) -> bool:
    return "All 8 generate-parentheses test cases passed." in ctx.stdout


def check_gp_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_sn_file(ctx) -> bool:
    return "single_number.py" in ctx.files


def check_sn_has_function(ctx) -> bool:
    return "def single_number" in ctx.files.get("single_number.py", "")


def check_sn_all_pass(ctx) -> bool:
    return "All 8 single-number test cases passed." in ctx.stdout


def check_sn_exit(ctx) -> bool:
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "combination-sum",
        "files": {"test_cs.py": _TEST_CS_PY},
        "run": "python test_cs.py",
        "prompt": (
            "A test script `test_cs.py` is provided. Write `combination_sum.py` that "
            "implements `combination_sum(candidates: list[int], target: int) -> list[list[int]]`. "
            "Return all unique combinations of candidates where the numbers sum to target. "
            "The same number may be reused any number of times. "
            "The solution set must not contain duplicate combinations. "
            "Use backtracking: at each step, try each candidate >= current start index; "
            "recurse with remaining = target - candidate; stop when remaining==0 (add "
            "current path) or remaining<0. Sort candidates first for pruning. "
            "O(n^(t/m)) time where t=target, m=min candidate. "
            "Run `python test_cs.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "combination_sum.py exists": check_cs_file,
            "defines combination_sum function": check_cs_has_function,
            "all 8 cases pass": check_cs_all_pass,
            "clean exit": check_cs_exit,
        },
    },
    {
        "name": "generate-parentheses",
        "files": {"test_gp.py": _TEST_GP_PY},
        "run": "python test_gp.py",
        "prompt": (
            "A test script `test_gp.py` is provided. Write `generate_parentheses.py` "
            "that implements `generate_parentheses(n: int) -> list[str]`. "
            "Return all combinations of n pairs of well-formed parentheses. "
            "Use backtracking: track open and close counts; add '(' if open < n; "
            "add ')' if close < open; when len(current)==2*n, add to results. "
            "This naturally generates all valid combinations without needing to "
            "validate afterward. Total combinations = Catalan(n). "
            "Run `python test_gp.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "generate_parentheses.py exists": check_gp_file,
            "defines generate_parentheses function": check_gp_has_function,
            "all 8 cases pass": check_gp_all_pass,
            "clean exit": check_gp_exit,
        },
    },
    {
        "name": "single-number",
        "files": {"test_sn.py": _TEST_SN_PY},
        "run": "python test_sn.py",
        "prompt": (
            "A test script `test_sn.py` is provided. Write `single_number.py` that "
            "implements `single_number(nums: list[int]) -> int`. "
            "Every element appears exactly twice except for one element which appears once. "
            "Return the element that appears only once. "
            "Use XOR: a ^ a = 0 for any a, and 0 ^ a = a. "
            "XOR all elements together — paired elements cancel out, leaving the single one. "
            "O(n) time, O(1) space — no extra data structures needed. "
            "Run `python test_sn.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "single_number.py exists": check_sn_file,
            "defines single_number function": check_sn_has_function,
            "all 8 cases pass": check_sn_all_pass,
            "clean exit": check_sn_exit,
        },
    },
]
