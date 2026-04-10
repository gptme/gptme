"""Practical eval tests (batch 26) — longest palindromic substring, jump game, and task scheduler."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- longest palindromic substring checks ---

_TEST_LPS_PY = """\
import sys
try:
    from longest_palindrome import longest_palindrome
except ImportError:
    print("ERROR: longest_palindrome.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def is_palindrome(s):
    return s == s[::-1]

# Case 1: "babad" -> length 3, valid palindrome ("bab" or "aba")
r = longest_palindrome("babad")
if len(r) != 3 or not is_palindrome(r) or r not in ("bab", "aba"):
    errors.append(f'case 1: "babad" -> "bab" or "aba", got {r!r}')

# Case 2: "cbbd" -> "bb"
r = longest_palindrome("cbbd")
if r != "bb":
    errors.append(f'case 2: "cbbd" -> "bb", got {r!r}')

# Case 3: single character
r = longest_palindrome("a")
if r != "a":
    errors.append(f'case 3: "a" -> "a", got {r!r}')

# Case 4: "racecar" -> the whole string
r = longest_palindrome("racecar")
if r != "racecar":
    errors.append(f'case 4: "racecar" -> "racecar", got {r!r}')

# Case 5: "noon" -> "noon"
r = longest_palindrome("noon")
if r != "noon":
    errors.append(f'case 5: "noon" -> "noon", got {r!r}')

# Case 6: "abcba" -> "abcba"
r = longest_palindrome("abcba")
if r != "abcba":
    errors.append(f'case 6: "abcba" -> "abcba", got {r!r}')

# Case 7: no palindrome longer than 1 -> length 1
r = longest_palindrome("abcd")
if len(r) != 1 or not is_palindrome(r):
    errors.append(f'case 7: "abcd" -> any single-char, got {r!r}')

# Case 8: full string is a palindrome
r = longest_palindrome("xyzabacabazyx")
if r != "xyzabacabazyx":
    errors.append(f'case 8: "xyzabacabazyx" -> "xyzabacabazyx", got {r!r}')

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 longest-palindromic-substring test cases passed.")
print("Longest palindromic substring implementation correct.")
"""


def check_lps_file(ctx):
    """longest_palindrome.py should exist."""
    return "longest_palindrome.py" in ctx.files


def check_lps_has_function(ctx):
    """Should define longest_palindrome function."""
    src = ctx.files.get("longest_palindrome.py", "")
    return "def longest_palindrome(" in src


def check_lps_all_pass(ctx):
    """All 8 test cases should pass."""
    return "All 8 longest-palindromic-substring test cases passed" in ctx.stdout


def check_lps_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- jump game checks ---

_TEST_JG_PY = """\
import sys
try:
    from jump_game import can_jump
except ImportError:
    print("ERROR: jump_game.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: can reach end via multiple paths
r = can_jump([2, 3, 1, 1, 4])
if r is not True:
    errors.append(f"case 1: [2,3,1,1,4] -> True, got {r}")

# Case 2: stuck at position 3, can never reach end
r = can_jump([3, 2, 1, 0, 4])
if r is not False:
    errors.append(f"case 2: [3,2,1,0,4] -> False, got {r}")

# Case 3: single element -- already at end
r = can_jump([0])
if r is not True:
    errors.append(f"case 3: [0] -> True, got {r}")

# Case 4: first element is zero, can't move
r = can_jump([0, 1])
if r is not False:
    errors.append(f"case 4: [0,1] -> False, got {r}")

# Case 5: jump of 2 lands exactly at the last index
r = can_jump([2, 0, 0])
if r is not True:
    errors.append(f"case 5: [2,0,0] -> True, got {r}")

# Case 6: each step allows exactly 1 hop
r = can_jump([1, 1, 1, 1])
if r is not True:
    errors.append(f"case 6: [1,1,1,1] -> True, got {r}")

# Case 7: can only reach index 1, then stuck
r = can_jump([1, 0, 0])
if r is not False:
    errors.append(f"case 7: [1,0,0] -> False, got {r}")

# Case 8: last element is 0 but it is the goal -- reachable
r = can_jump([1, 1, 0])
if r is not True:
    errors.append(f"case 8: [1,1,0] -> True (last element is goal), got {r}")

# Case 9: two elements, first hop reaches end
r = can_jump([1, 0])
if r is not True:
    errors.append(f"case 9: [1,0] -> True, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 9 jump-game test cases passed.")
print("Jump game implementation correct.")
"""


def check_jg_file(ctx):
    """jump_game.py should exist."""
    return "jump_game.py" in ctx.files


def check_jg_has_function(ctx):
    """Should define can_jump function."""
    src = ctx.files.get("jump_game.py", "")
    return "def can_jump(" in src


def check_jg_all_pass(ctx):
    """All 9 test cases should pass."""
    return "All 9 jump-game test cases passed" in ctx.stdout


def check_jg_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- task scheduler checks ---

_TEST_TS_PY = """\
import sys
try:
    from task_scheduler import least_interval
except ImportError:
    print("ERROR: task_scheduler.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: A:3, B:3, n=2 -> "AB_AB_AB" = 8
r = least_interval(["A","A","A","B","B","B"], 2)
if r != 8:
    errors.append(f"case 1: AAA BBB n=2 -> 8, got {r}")

# Case 2: no cooldown -- just total task count
r = least_interval(["A","A","A","B","B","B"], 0)
if r != 6:
    errors.append(f"case 2: AAA BBB n=0 -> 6, got {r}")

# Case 3: one task type dominates -- lots of idle slots needed
# A:6, n=2 -> "A__A__A__A__A__A" = 16
r = least_interval(["A","A","A","A","A","A"], 2)
if r != 16:
    errors.append(f"case 3: 6*A n=2 -> 16, got {r}")

# Case 4: many task types, no idle needed -- answer equals len(tasks)
# A:2, B:2, C:2, D:1, E:1 -> max frame = 1*3+3=6 < 8 tasks -> answer=8
r = least_interval(["A","B","C","D","E","A","B","C"], 2)
if r != 8:
    errors.append(f"case 4: ABCDEABC n=2 -> 8, got {r}")

# Case 5: single task, no idle needed
r = least_interval(["A"], 2)
if r != 1:
    errors.append(f"case 5: [A] n=2 -> 1, got {r}")

# Case 6: two of same task with cooldown 2 -> "A__A" = 4
r = least_interval(["A","A"], 2)
if r != 4:
    errors.append(f"case 6: AA n=2 -> 4, got {r}")

# Case 7: A:3, B:2, C:1, n=2 -> (3-1)*(2+1)+1 = 7, len=6, answer=7
r = least_interval(["A","A","A","B","B","C"], 2)
if r != 7:
    errors.append(f"case 7: AAABBC n=2 -> 7, got {r}")

# Case 8: all unique tasks with large n -- max_freq=1 so cooldown never activates, answer = len(tasks)
r = least_interval(["A","B","C"], 10)
if r != 3:
    errors.append(f"case 8: ABC n=10 -> 3, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 task-scheduler test cases passed.")
print("Task scheduler implementation correct.")
"""


def check_ts_file(ctx):
    """task_scheduler.py should exist."""
    return "task_scheduler.py" in ctx.files


def check_ts_has_function(ctx):
    """Should define least_interval function."""
    src = ctx.files.get("task_scheduler.py", "")
    return "def least_interval(" in src


def check_ts_all_pass(ctx):
    """All 8 test cases should pass."""
    return "All 8 task-scheduler test cases passed" in ctx.stdout


def check_ts_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "longest-palindromic-substring",
        "files": {"test_lps.py": _TEST_LPS_PY},
        "run": "python test_lps.py",
        "prompt": (
            "A test script `test_lps.py` is provided. Write `longest_palindrome.py` "
            "that implements `longest_palindrome(s: str) -> str`. Return the longest "
            "palindromic substring of s. A palindrome reads the same forwards and "
            "backwards. If multiple substrings share the maximum length, return any one. "
            "Use the expand-around-center approach: for each index, expand outward to "
            "find odd-length and even-length palindromes. O(n²) time, O(1) space. "
            "Run `python test_lps.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "longest_palindrome.py exists": check_lps_file,
            "defines longest_palindrome function": check_lps_has_function,
            "all 8 cases pass": check_lps_all_pass,
            "clean exit": check_lps_exit,
        },
    },
    {
        "name": "jump-game",
        "files": {"test_jg.py": _TEST_JG_PY},
        "run": "python test_jg.py",
        "prompt": (
            "A test script `test_jg.py` is provided. Write `jump_game.py` that "
            "implements `can_jump(nums: list[int]) -> bool`. Given an array where "
            "nums[i] is the maximum jump length from position i, return True if you "
            "can reach the last index starting from index 0, False otherwise. "
            "Use the greedy approach: track the maximum reachable index as you scan "
            "left to right; return False if you ever find current index > max_reach. "
            "Run `python test_jg.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "jump_game.py exists": check_jg_file,
            "defines can_jump function": check_jg_has_function,
            "all 9 cases pass": check_jg_all_pass,
            "clean exit": check_jg_exit,
        },
    },
    {
        "name": "task-scheduler",
        "files": {"test_ts.py": _TEST_TS_PY},
        "run": "python test_ts.py",
        "prompt": (
            "A test script `test_ts.py` is provided. Write `task_scheduler.py` that "
            "implements `least_interval(tasks: list[str], n: int) -> int`. "
            "Given CPU tasks (capital letters) and cooldown n (same task can't run "
            "within n slots of its last execution), return the minimum total intervals "
            "needed to finish all tasks. CPU may be idle when no task is available. "
            "Use the greedy formula: count task frequencies, then compute "
            "max((max_freq - 1) * (n + 1) + count_at_max_freq, len(tasks)). "
            "Run `python test_ts.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "task_scheduler.py exists": check_ts_file,
            "defines least_interval function": check_ts_has_function,
            "all 8 cases pass": check_ts_all_pass,
            "clean exit": check_ts_exit,
        },
    },
]
