"""Practical eval tests (batch 25) — sliding window maximum, decode ways, and meeting rooms."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- sliding window maximum checks ---

_TEST_SWM_PY = """\
import sys
try:
    from sliding_window_max import sliding_window_max
except ImportError:
    print("ERROR: sliding_window_max.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic example
r = sliding_window_max([1, 3, -1, -3, 5, 3, 6, 7], 3)
if r != [3, 3, 5, 5, 6, 7]:
    errors.append(f"case 1: [1,3,-1,-3,5,3,6,7] k=3 -> [3,3,5,5,6,7], got {r}")

# Case 2: single element, window size 1
r = sliding_window_max([1], 1)
if r != [1]:
    errors.append(f"case 2: [1] k=1 -> [1], got {r}")

# Case 3: mixed values
r = sliding_window_max([1, 3, 1, 2, 0, 5], 3)
if r != [3, 3, 2, 5]:
    errors.append(f"case 3: [1,3,1,2,0,5] k=3 -> [3,3,2,5], got {r}")

# Case 4: ascending sequence, window size 2
r = sliding_window_max([1, 2, 3, 4, 5], 2)
if r != [2, 3, 4, 5]:
    errors.append(f"case 4: [1,2,3,4,5] k=2 -> [2,3,4,5], got {r}")

# Case 5: descending sequence
r = sliding_window_max([5, 4, 3, 2, 1], 3)
if r != [5, 4, 3]:
    errors.append(f"case 5: [5,4,3,2,1] k=3 -> [5,4,3], got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 5 sliding-window-max test cases passed.")
print("Sliding window maximum implementation correct.")
"""


def check_swm_file(ctx):
    """sliding_window_max.py should exist."""
    return "sliding_window_max.py" in ctx.files


def check_swm_all_pass(ctx):
    """All 5 sliding-window-max cases should pass."""
    return "All 5 sliding-window-max test cases passed" in ctx.stdout


def check_swm_has_function(ctx):
    """Should define sliding_window_max function."""
    src = ctx.files.get("sliding_window_max.py", "")
    return "def sliding_window_max(" in src


def check_swm_uses_deque(ctx):
    """Should use a deque for O(n) efficiency."""
    src = ctx.files.get("sliding_window_max.py", "")
    return "deque" in src


def check_swm_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- decode ways checks ---

_TEST_DECODE_PY = """\
import sys
try:
    from decode_ways import decode_ways
except ImportError:
    print("ERROR: decode_ways.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: "12" -> "AB" or "L" => 2 ways
r = decode_ways("12")
if r != 2:
    errors.append(f"case 1: '12' -> 2, got {r}")

# Case 2: "226" -> "BBF", "BZ", "VF" => 3 ways
r = decode_ways("226")
if r != 3:
    errors.append(f"case 2: '226' -> 3, got {r}")

# Case 3: "0" -> no valid decoding
r = decode_ways("0")
if r != 0:
    errors.append(f"case 3: '0' -> 0, got {r}")

# Case 4: "06" -> no valid decoding (leading zero in two-digit code)
r = decode_ways("06")
if r != 0:
    errors.append(f"case 4: '06' -> 0, got {r}")

# Case 5: "10" -> "J" only => 1 way
r = decode_ways("10")
if r != 1:
    errors.append(f"case 5: '10' -> 1, got {r}")

# Case 6: "11106" -> 2 ways
r = decode_ways("11106")
if r != 2:
    errors.append(f"case 6: '11106' -> 2, got {r}")

# Case 7: "27" -> only "2","7" (27 > 26) => 1 way
r = decode_ways("27")
if r != 1:
    errors.append(f"case 7: '27' -> 1, got {r}")

# Case 8: "111" -> "AAA", "AK", "KA" => 3 ways
r = decode_ways("111")
if r != 3:
    errors.append(f"case 8: '111' -> 3, got {r}")

# Case 9: single valid digit
r = decode_ways("1")
if r != 1:
    errors.append(f"case 9: '1' -> 1, got {r}")

# Case 10: "30" -> no valid decoding (30 > 26)
r = decode_ways("30")
if r != 0:
    errors.append(f"case 10: '30' -> 0, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 10 decode-ways test cases passed.")
print("Decode ways implementation correct.")
"""


def check_decode_file(ctx):
    """decode_ways.py should exist."""
    return "decode_ways.py" in ctx.files


def check_decode_all_pass(ctx):
    """All 10 decode-ways cases should pass."""
    return "All 10 decode-ways test cases passed" in ctx.stdout


def check_decode_has_function(ctx):
    """Should define decode_ways function."""
    src = ctx.files.get("decode_ways.py", "")
    return "def decode_ways(" in src


def check_decode_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- meeting rooms checks ---

_TEST_ROOMS_PY = """\
import sys
try:
    from meeting_rooms import min_meeting_rooms
except ImportError:
    print("ERROR: meeting_rooms.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic overlapping meetings
r = min_meeting_rooms([[0, 30], [5, 10], [15, 20]])
if r != 2:
    errors.append(f"case 1: [[0,30],[5,10],[15,20]] -> 2, got {r}")

# Case 2: non-overlapping meetings
r = min_meeting_rooms([[7, 10], [2, 4]])
if r != 1:
    errors.append(f"case 2: [[7,10],[2,4]] -> 1, got {r}")

# Case 3: all meetings at same time
r = min_meeting_rooms([[0, 10], [0, 10], [0, 10]])
if r != 3:
    errors.append(f"case 3: [[0,10],[0,10],[0,10]] -> 3, got {r}")

# Case 4: chain of overlapping meetings
r = min_meeting_rooms([[0, 10], [5, 15], [10, 20]])
if r != 2:
    errors.append(f"case 4: [[0,10],[5,15],[10,20]] -> 2, got {r}")

# Case 5: empty list
r = min_meeting_rooms([])
if r != 0:
    errors.append(f"case 5: [] -> 0, got {r}")

# Case 6: single meeting
r = min_meeting_rooms([[1, 5]])
if r != 1:
    errors.append(f"case 6: [[1,5]] -> 1, got {r}")

# Case 7: back-to-back meetings (end == start, no overlap)
r = min_meeting_rooms([[0, 5], [5, 10]])
if r != 1:
    errors.append(f"case 7: [[0,5],[5,10]] -> 1, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 7 meeting-rooms test cases passed.")
print("Meeting rooms implementation correct.")
"""


def check_rooms_file(ctx):
    """meeting_rooms.py should exist."""
    return "meeting_rooms.py" in ctx.files


def check_rooms_all_pass(ctx):
    """All 7 meeting-rooms cases should pass."""
    return "All 7 meeting-rooms test cases passed" in ctx.stdout


def check_rooms_has_function(ctx):
    """Should define min_meeting_rooms function."""
    src = ctx.files.get("meeting_rooms.py", "")
    return "def min_meeting_rooms(" in src


def check_rooms_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "sliding-window-max",
        "files": {"test_swm.py": _TEST_SWM_PY},
        "run": "python test_swm.py",
        "prompt": (
            "A test script `test_swm.py` is provided. Write `sliding_window_max.py` "
            "that implements `sliding_window_max(nums, k)` where `nums` is a list of "
            "integers and `k` is the window size. Return a list of the maximum values "
            "in each sliding window of size `k` as it moves from left to right across "
            "`nums`. Use a deque-based approach for O(n) time complexity — maintain "
            "indices of useful elements in decreasing order of their values. "
            "Run `python test_swm.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "sliding_window_max.py exists": check_swm_file,
            "all 5 cases pass": check_swm_all_pass,
            "defines sliding_window_max function": check_swm_has_function,
            "uses deque for O(n) efficiency": check_swm_uses_deque,
            "clean exit": check_swm_exit,
        },
    },
    {
        "name": "decode-ways",
        "files": {"test_decode.py": _TEST_DECODE_PY},
        "run": "python test_decode.py",
        "prompt": (
            "A test script `test_decode.py` is provided. Write `decode_ways.py` that "
            "implements `decode_ways(s)` where `s` is a string of digits. Count the "
            "number of ways to decode it given the mapping A=1, B=2, ..., Z=26. Each "
            "digit or valid two-digit number (10-26) may map to a letter. Leading "
            "zeros make a decoding invalid. Use dynamic programming. "
            "Run `python test_decode.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "decode_ways.py exists": check_decode_file,
            "all 10 cases pass": check_decode_all_pass,
            "defines decode_ways function": check_decode_has_function,
            "clean exit": check_decode_exit,
        },
    },
    {
        "name": "meeting-rooms",
        "files": {"test_rooms.py": _TEST_ROOMS_PY},
        "run": "python test_rooms.py",
        "prompt": (
            "A test script `test_rooms.py` is provided. Write `meeting_rooms.py` that "
            "implements `min_meeting_rooms(intervals)` where `intervals` is a list of "
            "[start, end] pairs representing meetings. Return the minimum number of "
            "conference rooms required so that no two overlapping meetings share a "
            "room. Meetings that end exactly when another starts do not overlap. "
            "Use a min-heap to track the earliest ending room. "
            "Run `python test_rooms.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "meeting_rooms.py exists": check_rooms_file,
            "all 7 cases pass": check_rooms_all_pass,
            "defines min_meeting_rooms function": check_rooms_has_function,
            "clean exit": check_rooms_exit,
        },
    },
]
