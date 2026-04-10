"""Practical eval tests (batch 27) — house robber, maximum product subarray, and find all anagrams."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- house robber checks ---

_TEST_HR_PY = """\
import sys
try:
    from house_robber import rob
except ImportError:
    print("ERROR: house_robber.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: basic example -- rob indices 0 and 2 (1+3=4) or indices 1 and 3 (2+1=3), max=4
r = rob([1, 2, 3, 1])
if r != 4:
    errors.append(f"case 1: [1,2,3,1] -> 4, got {r}")

# Case 2: mixed values -- rob indices 0,2,4 (2+9+1=12) beats adjacent pairs
r = rob([2, 7, 9, 3, 1])
if r != 12:
    errors.append(f"case 2: [2,7,9,3,1] -> 12, got {r}")

# Case 3: single house
r = rob([5])
if r != 5:
    errors.append(f"case 3: [5] -> 5, got {r}")

# Case 4: two houses -- take the max
r = rob([3, 10])
if r != 10:
    errors.append(f"case 4: [3,10] -> 10, got {r}")

# Case 5: take first and skip second pair -> [2,1,1,2] -> rob[0]+rob[3]=4 or rob[1]+... -> 4
r = rob([2, 1, 1, 2])
if r != 4:
    errors.append(f"case 5: [2,1,1,2] -> 4, got {r}")

# Case 6: greedy fails -- need DP -> [6,7,1,30,8,2,4]
r = rob([6, 7, 1, 30, 8, 2, 4])
if r != 41:
    errors.append(f"case 6: [6,7,1,30,8,2,4] -> 41 (7+30+4=41), got {r}")

# Case 7: empty (if supported) or single zero
r = rob([0])
if r != 0:
    errors.append(f"case 7: [0] -> 0, got {r}")

# Case 8: all zeros
r = rob([0, 0, 0])
if r != 0:
    errors.append(f"case 8: [0,0,0] -> 0, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 house-robber test cases passed.")
print("House robber implementation correct.")
"""


def check_hr_file(ctx):
    """house_robber.py should exist."""
    return "house_robber.py" in ctx.files


def check_hr_has_function(ctx):
    """Should define rob function."""
    src = ctx.files.get("house_robber.py", "")
    return "def rob(" in src


def check_hr_all_pass(ctx):
    """All 8 test cases should pass."""
    return "All 8 house-robber test cases passed" in ctx.stdout


def check_hr_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- maximum product subarray checks ---

_TEST_MPS_PY = """\
import sys
try:
    from max_product_subarray import max_product
except ImportError:
    print("ERROR: max_product_subarray.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: positive and negative -- product of first 3 or all-positive subarray
r = max_product([2, 3, -2, 4])
if r != 6:
    errors.append(f"case 1: [2,3,-2,4] -> 6, got {r}")

# Case 2: zero in the middle resets product -- best subarray is [-2] or [-1], both <0, but [0] gives 0
r = max_product([-2, 0, -1])
if r != 0:
    errors.append(f"case 2: [-2,0,-1] -> 0, got {r}")

# Case 3: all positive
r = max_product([1, 2, 3, 4])
if r != 24:
    errors.append(f"case 3: [1,2,3,4] -> 24, got {r}")

# Case 4: single negative
r = max_product([-3])
if r != -3:
    errors.append(f"case 4: [-3] -> -3, got {r}")

# Case 5: two negatives -- product is positive
r = max_product([-2, -3])
if r != 6:
    errors.append(f"case 5: [-2,-3] -> 6, got {r}")

# Case 6: zeros split the array -- [3,5] -> 15 or [2] -> 2, max=15
r = max_product([3, 5, 0, 2])
if r != 15:
    errors.append(f"case 6: [3,5,0,2] -> 15, got {r}")

# Case 7: alternating signs -- [-2,3,-4] -> (-2)*3*(-4)=24
r = max_product([-2, 3, -4])
if r != 24:
    errors.append(f"case 7: [-2,3,-4] -> 24, got {r}")

# Case 8: single zero
r = max_product([0])
if r != 0:
    errors.append(f"case 8: [0] -> 0, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 max-product-subarray test cases passed.")
print("Max product subarray implementation correct.")
"""


def check_mps_file(ctx):
    """max_product_subarray.py should exist."""
    return "max_product_subarray.py" in ctx.files


def check_mps_has_function(ctx):
    """Should define max_product function."""
    src = ctx.files.get("max_product_subarray.py", "")
    return "def max_product(" in src


def check_mps_all_pass(ctx):
    """All 8 test cases should pass."""
    return "All 8 max-product-subarray test cases passed" in ctx.stdout


def check_mps_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- find all anagrams checks ---

_TEST_FAA_PY = """\
import sys
try:
    from find_anagrams import find_anagrams
except ImportError:
    print("ERROR: find_anagrams.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic example -- "cbaebabacd", p="abc" -> [0, 6]
r = find_anagrams("cbaebabacd", "abc")
if sorted(r) != [0, 6]:
    errors.append(f'case 1: "cbaebabacd","abc" -> [0,6], got {r}')

# Case 2: overlapping anagrams -- "abab", p="ab" -> [0,1,2]
r = find_anagrams("abab", "ab")
if sorted(r) != [0, 1, 2]:
    errors.append(f'case 2: "abab","ab" -> [0,1,2], got {sorted(r)}')

# Case 3: no anagram found
r = find_anagrams("af", "be")
if r != []:
    errors.append(f'case 3: "af","be" -> [], got {r}')

# Case 4: p longer than s
r = find_anagrams("ab", "abc")
if r != []:
    errors.append(f'case 4: "ab","abc" -> [], got {r}')

# Case 5: exact match -- whole string is an anagram
r = find_anagrams("cba", "abc")
if r != [0]:
    errors.append(f'case 5: "cba","abc" -> [0], got {r}')

# Case 6: repeated chars -- "aaa", p="a" -> [0,1,2]
r = find_anagrams("aaa", "a")
if sorted(r) != [0, 1, 2]:
    errors.append(f'case 6: "aaa","a" -> [0,1,2], got {sorted(r)}')

# Case 7: single anagram -- "eidbaooo", p="ab" -> [3] ("ba" at index 3)
r = find_anagrams("eidbaooo", "ab")
if sorted(r) != [3]:
    errors.append(f'case 7: "eidbaooo","ab" -> [3], got {sorted(r)}')

# Case 8: single char pattern and string
r = find_anagrams("z", "z")
if r != [0]:
    errors.append(f'case 8: "z","z" -> [0], got {r}')

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 find-all-anagrams test cases passed.")
print("Find all anagrams implementation correct.")
"""


def check_faa_file(ctx):
    """find_anagrams.py should exist."""
    return "find_anagrams.py" in ctx.files


def check_faa_has_function(ctx):
    """Should define find_anagrams function."""
    src = ctx.files.get("find_anagrams.py", "")
    return "def find_anagrams(" in src


def check_faa_all_pass(ctx):
    """All 8 test cases should pass."""
    return "All 8 find-all-anagrams test cases passed" in ctx.stdout


def check_faa_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "house-robber",
        "files": {"test_hr.py": _TEST_HR_PY},
        "run": "python test_hr.py",
        "prompt": (
            "A test script `test_hr.py` is provided. Write `house_robber.py` that "
            "implements `rob(nums: list[int]) -> int`. Given an array of non-negative "
            "integers representing money at each house, return the maximum amount you "
            "can rob without robbing two adjacent houses. "
            "Use dynamic programming: at each step, choose to either rob the current "
            "house (current value + dp[i-2]) or skip it (dp[i-1]). "
            "O(n) time, O(1) space using two variables. "
            "Run `python test_hr.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "house_robber.py exists": check_hr_file,
            "defines rob function": check_hr_has_function,
            "all 8 cases pass": check_hr_all_pass,
            "clean exit": check_hr_exit,
        },
    },
    {
        "name": "max-product-subarray",
        "files": {"test_mps.py": _TEST_MPS_PY},
        "run": "python test_mps.py",
        "prompt": (
            "A test script `test_mps.py` is provided. Write `max_product_subarray.py` "
            "that implements `max_product(nums: list[int]) -> int`. Find the contiguous "
            "subarray with the largest product and return that product. "
            "Track both current_max and current_min (negatives can become large positives "
            "when multiplied by another negative); update both at each step, then update "
            "the global max. Reset to the current element when it is larger/smaller. "
            "O(n) time, O(1) space. "
            "Run `python test_mps.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "max_product_subarray.py exists": check_mps_file,
            "defines max_product function": check_mps_has_function,
            "all 8 cases pass": check_mps_all_pass,
            "clean exit": check_mps_exit,
        },
    },
    {
        "name": "find-all-anagrams",
        "files": {"test_faa.py": _TEST_FAA_PY},
        "run": "python test_faa.py",
        "prompt": (
            "A test script `test_faa.py` is provided. Write `find_anagrams.py` that "
            "implements `find_anagrams(s: str, p: str) -> list[int]`. Return all start "
            "indices in s where a substring is an anagram of p. "
            "Use a sliding window of length len(p): maintain frequency counts of "
            "characters in the current window and p; slide the window one character at "
            "a time, adding the new character and removing the old one. When the counts "
            "match, record the start index. O(n) time, O(1) space (fixed alphabet). "
            "Run `python test_faa.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "find_anagrams.py exists": check_faa_file,
            "defines find_anagrams function": check_faa_has_function,
            "all 8 cases pass": check_faa_all_pass,
            "clean exit": check_faa_exit,
        },
    },
]
