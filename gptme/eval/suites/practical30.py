"""Practical eval tests (batch 30) — decode string, top-k frequent elements, partition equal subset sum."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- decode string checks (LC394) ---

_TEST_DS_PY = """\
import sys
try:
    from decode_string import decode_string
except ImportError:
    print("ERROR: decode_string.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(s, expected, label):
    r = decode_string(s)
    if r != expected:
        errors.append(f"{label}: decode_string({s!r}) -> {expected!r}, got {r!r}")

# Case 1: LeetCode example 1
check("3[a]2[bc]", "aaabcbc", "case 1")

# Case 2: LeetCode example 2 — nested
check("3[a2[c]]", "accaccacc", "case 2")

# Case 3: LeetCode example 3 — mixed suffix
check("2[abc]3[cd]ef", "abcabccdcdcdef", "case 3")

# Case 4: prefix and repetition
check("abc3[d]", "abcddd", "case 4")

# Case 5: no repetition
check("abc", "abc", "case 5")

# Case 6: two-digit multiplier
check("10[a]", "a" * 10, "case 6")

# Case 7: multiplier of 1
check("1[ab]", "ab", "case 7")

# Case 8: three-level nesting — 2[a2[b3[c]]] -> (abcccbccc) x2
check("2[a2[b3[c]]]", "abcccbcccabcccbccc", "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 decode-string test cases passed.")
    print("Decode string implementation correct.")
"""

# --- top-k frequent elements checks (LC347) ---

_TEST_TK_PY = """\
import sys
try:
    from top_k_frequent import top_k_frequent
except ImportError:
    print("ERROR: top_k_frequent.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, k, expected_set, label):
    result = sorted(top_k_frequent(nums, k))
    exp = sorted(expected_set)
    if result != exp:
        errors.append(f"{label}: top_k_frequent({nums}, {k}) -> {exp}, got {result}")

# Case 1: LeetCode example 1
check([1,1,1,2,2,3], 2, [1,2], "case 1")

# Case 2: LeetCode example 2 — single element
check([1], 1, [1], "case 2")

# Case 3: k=2 from uneven distribution
check([1,2,3,4,1,2,1], 2, [1,2], "case 3")

# Case 4: top 3 from 4 tiers
check([4,4,4,4,3,3,3,2,2,1], 3, [4,3,2], "case 4")

# Case 5: most frequent is 0
check([3,0,1,0], 1, [0], "case 5")

# Case 6: negative numbers
check([-1,-1,2], 1, [-1], "case 6")

# Case 7: k=1 clear winner
check([1,1,2,2,3,3,3], 1, [3], "case 7")

# Case 8: k=4; 1/2/3/4 each appear twice, 5 appears once — unique answer
check([1,1,2,2,3,3,4,4,5], 4, [1,2,3,4], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 top-k-frequent test cases passed.")
    print("Top-k frequent elements implementation correct.")
"""

# --- partition equal subset sum checks (LC416) ---

_TEST_PE_PY = """\
import sys
try:
    from partition_equal_subset import can_partition
except ImportError:
    print("ERROR: partition_equal_subset.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    r = can_partition(nums)
    if bool(r) != expected:
        errors.append(f"{label}: can_partition({nums}) -> {expected}, got {r}")

# Case 1: LeetCode example 1
check([1,5,11,5], True, "case 1")

# Case 2: LeetCode example 2 — impossible
check([1,2,3,5], False, "case 2")

# Case 3: two equal elements
check([1,1], True, "case 3")

# Case 4: sum=8 but no subset reaches target 4 — always False
check([1,2,5], False, "case 4")

# Case 5: can split with reuse of value
check([1,5,5,11], True, "case 5")

# Case 6: three elements sum to half
check([3,3,3,4,5], True, "case 6")

# Case 7: single element — can't split
check([100], False, "case 7")

# Case 8: larger array with valid split (14+4+2=20)
check([14,9,8,4,3,2], True, "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 partition-equal-subset test cases passed.")
    print("Partition equal subset sum implementation correct.")
"""


def check_ds_file(ctx) -> bool:
    return "decode_string.py" in ctx.files


def check_ds_has_function(ctx) -> bool:
    return "def decode_string" in ctx.files.get("decode_string.py", "")


def check_ds_all_pass(ctx) -> bool:
    return "All 8 decode-string test cases passed." in ctx.stdout


def check_ds_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_tk_file(ctx) -> bool:
    return "top_k_frequent.py" in ctx.files


def check_tk_has_function(ctx) -> bool:
    return "def top_k_frequent" in ctx.files.get("top_k_frequent.py", "")


def check_tk_all_pass(ctx) -> bool:
    return "All 8 top-k-frequent test cases passed." in ctx.stdout


def check_tk_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_pe_file(ctx) -> bool:
    return "partition_equal_subset.py" in ctx.files


def check_pe_has_function(ctx) -> bool:
    return "def can_partition" in ctx.files.get("partition_equal_subset.py", "")


def check_pe_all_pass(ctx) -> bool:
    return "All 8 partition-equal-subset test cases passed." in ctx.stdout


def check_pe_exit(ctx) -> bool:
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "decode-string",
        "files": {"test_ds.py": _TEST_DS_PY},
        "run": "python test_ds.py",
        "prompt": (
            "A test script `test_ds.py` is provided. Write `decode_string.py` that "
            "implements `decode_string(s: str) -> str`. Given an encoded string like "
            "'3[a2[c]]', decode it to 'accaccacc'. The encoding rule is: k[encoded_string] "
            "means encoded_string repeated k times. k is always a positive integer; "
            "brackets can be nested. "
            "Use a stack: push characters onto the stack until ']' is seen; then pop "
            "to collect the inner string, pop the multiplier digits, and push the "
            "expanded string back. Handle multi-digit numbers (e.g. '10[a]'). "
            "O(n * max_k) time, O(n) space. "
            "Run `python test_ds.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "decode_string.py exists": check_ds_file,
            "defines decode_string function": check_ds_has_function,
            "all 8 cases pass": check_ds_all_pass,
            "clean exit": check_ds_exit,
        },
    },
    {
        "name": "top-k-frequent",
        "files": {"test_tk.py": _TEST_TK_PY},
        "run": "python test_tk.py",
        "prompt": (
            "A test script `test_tk.py` is provided. Write `top_k_frequent.py` that "
            "implements `top_k_frequent(nums: list[int], k: int) -> list[int]`. Return "
            "the k most frequently occurring elements in nums (in any order). "
            "Use a hash map to count frequencies, then a heap or sort: build a "
            "Counter, then use heapq.nlargest(k, count, key=count.get) for O(n log k), "
            "or sort by frequency descending and return the first k keys. "
            "The result order does not matter — the test sorts both sides. "
            "O(n log k) time, O(n) space. "
            "Run `python test_tk.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "top_k_frequent.py exists": check_tk_file,
            "defines top_k_frequent function": check_tk_has_function,
            "all 8 cases pass": check_tk_all_pass,
            "clean exit": check_tk_exit,
        },
    },
    {
        "name": "partition-equal-subset",
        "files": {"test_pe.py": _TEST_PE_PY},
        "run": "python test_pe.py",
        "prompt": (
            "A test script `test_pe.py` is provided. Write `partition_equal_subset.py` "
            "that implements `can_partition(nums: list[int]) -> bool`. Return True if "
            "you can partition nums into two subsets with equal sum. "
            "Key insight: if total is odd, return False immediately; otherwise find "
            "whether any subset sums to total // 2. "
            "Use DP with a boolean set: start with {0}, and for each number n add "
            "(existing + n) to the set. Return target in dp. "
            "O(n * target) time, O(target) space. "
            "Run `python test_pe.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "partition_equal_subset.py exists": check_pe_file,
            "defines can_partition function": check_pe_has_function,
            "all 8 cases pass": check_pe_all_pass,
            "clean exit": check_pe_exit,
        },
    },
]
