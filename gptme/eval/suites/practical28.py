"""Practical eval tests (batch 28) — minimum path sum, gas station, next permutation."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- minimum path sum checks ---

_TEST_MPS_PY = """\
import sys
try:
    from min_path_sum import min_path_sum
except ImportError:
    print("ERROR: min_path_sum.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: 3x3 grid from LeetCode example
grid = [[1,3,1],[1,5,1],[4,2,1]]
r = min_path_sum(grid)
if r != 7:
    errors.append(f"case 1: 3x3 grid -> 7, got {r}")

# Case 2: 2x3 grid
grid = [[1,2,3],[4,5,6]]
r = min_path_sum(grid)
if r != 12:
    errors.append(f"case 2: 2x3 grid -> 12, got {r}")

# Case 3: single row
grid = [[1,2,5]]
r = min_path_sum(grid)
if r != 8:
    errors.append(f"case 3: single row [1,2,5] -> 8, got {r}")

# Case 4: single column
grid = [[3],[2],[1]]
r = min_path_sum(grid)
if r != 6:
    errors.append(f"case 4: single column [3,2,1] -> 6, got {r}")

# Case 5: 1x1 grid
grid = [[7]]
r = min_path_sum(grid)
if r != 7:
    errors.append(f"case 5: 1x1 grid -> 7, got {r}")

# Case 6: all zeros except one cell
grid = [[0,0,0],[0,1,0],[0,0,0]]
r = min_path_sum(grid)
if r != 0:
    errors.append(f"case 6: all-zero path exists -> 0, got {r}")

# Case 7: larger grid where greedy fails
grid = [[1,2,5],[3,2,1]]
r = min_path_sum(grid)
if r != 6:
    errors.append(f"case 7: 2x3 -> 6, got {r}")

# Case 8: 4x4 grid — optimal path 1→2→2→2→2→3→1 = 13
grid = [[1,2,3,4],[2,2,2,2],[3,3,3,3],[4,4,4,1]]
r = min_path_sum(grid)
if r != 13:
    errors.append(f"case 8: 4x4 grid -> 13, got {r}")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 cases passed.")
"""

# --- gas station checks ---

_TEST_GS_PY = """\
import sys
try:
    from gas_station import can_complete_circuit
except ImportError:
    print("ERROR: gas_station.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: LeetCode example 1 — station 3 works
gas  = [1, 2, 3, 4, 5]
cost = [3, 4, 5, 1, 2]
r = can_complete_circuit(gas, cost)
if r != 3:
    errors.append(f"case 1: expected 3, got {r}")

# Case 2: LeetCode example 2 — impossible
gas  = [2, 3, 4]
cost = [3, 4, 3]
r = can_complete_circuit(gas, cost)
if r != -1:
    errors.append(f"case 2: expected -1, got {r}")

# Case 3: single station with surplus
gas  = [5]
cost = [4]
r = can_complete_circuit(gas, cost)
if r != 0:
    errors.append(f"case 3: expected 0, got {r}")

# Case 4: single station insufficient
gas  = [1]
cost = [2]
r = can_complete_circuit(gas, cost)
if r != -1:
    errors.append(f"case 4: expected -1, got {r}")

# Case 5: start at first station works
gas  = [3, 1, 1]
cost = [1, 2, 2]
r = can_complete_circuit(gas, cost)
if r != 0:
    errors.append(f"case 5: expected 0, got {r}")

# Case 6: only last station has enough surplus to start
gas  = [0, 0, 0, 5]
cost = [2, 1, 1, 1]
r = can_complete_circuit(gas, cost)
if r != 3:
    errors.append(f"case 6: expected 3, got {r}")

# Case 7: all equal (first station works)
gas  = [2, 2, 2]
cost = [2, 2, 2]
r = can_complete_circuit(gas, cost)
if r != 0:
    errors.append(f"case 7: all equal -> 0, got {r}")

# Case 8: unique solution not at zero
gas  = [1, 2, 3, 4, 5, 5]
cost = [3, 4, 5, 1, 2, 1]
r = can_complete_circuit(gas, cost)
if r != 3:
    errors.append(f"case 8: expected 3, got {r}")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 cases passed.")
"""

# --- next permutation checks ---

_TEST_NP_PY = """\
import sys
try:
    from next_permutation import next_permutation
except ImportError:
    print("ERROR: next_permutation.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

def check(nums, expected, label):
    got = nums[:]
    next_permutation(got)
    if got != expected:
        errors.append(f"{label}: {nums} -> {expected}, got {got}")

# Case 1: LeetCode example 1
check([1,2,3], [1,3,2], "case 1")

# Case 2: LeetCode example 2 — descending wraps to ascending
check([3,2,1], [1,2,3], "case 2")

# Case 3: LeetCode example 3
check([1,1,5], [1,5,1], "case 3")

# Case 4: single element — stays same
check([1], [1], "case 4")

# Case 5: two elements ascending
check([1,2], [2,1], "case 5")

# Case 6: two elements descending — wraps
check([2,1], [1,2], "case 6")

# Case 7: mid-sequence
check([1,3,2], [2,1,3], "case 7")

# Case 8: longer sequence
check([2,3,1,3,3], [2,3,3,1,3], "case 8")

if errors:
    for e in errors:
        print("FAIL:", e)
    sys.exit(1)
else:
    print("All 8 cases passed.")
"""


def check_mps_file(ctx) -> bool:
    return "min_path_sum.py" in ctx.files


def check_mps_has_function(ctx) -> bool:
    return "def min_path_sum" in ctx.files.get("min_path_sum.py", "")


def check_mps_all_pass(ctx) -> bool:
    return "All 8 cases passed." in ctx.stdout


def check_mps_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_gs_file(ctx) -> bool:
    return "gas_station.py" in ctx.files


def check_gs_has_function(ctx) -> bool:
    return "def can_complete_circuit" in ctx.files.get("gas_station.py", "")


def check_gs_all_pass(ctx) -> bool:
    return "All 8 cases passed." in ctx.stdout


def check_gs_exit(ctx) -> bool:
    return ctx.exit_code == 0


def check_np_file(ctx) -> bool:
    return "next_permutation.py" in ctx.files


def check_np_has_function(ctx) -> bool:
    return "def next_permutation" in ctx.files.get("next_permutation.py", "")


def check_np_all_pass(ctx) -> bool:
    return "All 8 cases passed." in ctx.stdout


def check_np_exit(ctx) -> bool:
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "minimum-path-sum",
        "files": {"test_mps.py": _TEST_MPS_PY},
        "run": "python test_mps.py",
        "prompt": (
            "A test script `test_mps.py` is provided. Write `min_path_sum.py` that "
            "implements `min_path_sum(grid: list[list[int]]) -> int`. Given an m×n grid "
            "of non-negative integers, find the path from top-left to bottom-right that "
            "minimises the sum of all numbers along the path (you may only move right or "
            "down). Use DP: update each cell to hold the minimum cost to reach it — "
            "first row is a prefix sum, first column is a prefix sum, every other cell "
            "is grid[i][j] + min(grid[i-1][j], grid[i][j-1]). Modify the grid in-place "
            "or use a 1D rolling array. O(m*n) time, O(1) or O(n) space. "
            "Run `python test_mps.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "min_path_sum.py exists": check_mps_file,
            "defines min_path_sum function": check_mps_has_function,
            "all 8 cases pass": check_mps_all_pass,
            "clean exit": check_mps_exit,
        },
    },
    {
        "name": "gas-station",
        "files": {"test_gs.py": _TEST_GS_PY},
        "run": "python test_gs.py",
        "prompt": (
            "A test script `test_gs.py` is provided. Write `gas_station.py` that "
            "implements `can_complete_circuit(gas: list[int], cost: list[int]) -> int`. "
            "There are n gas stations in a circle; gas[i] is the fuel available at station i "
            "and cost[i] is the fuel needed to travel from i to i+1. Find the starting "
            "station index to complete the full circuit, or return -1 if impossible. "
            "The answer is guaranteed unique when it exists. "
            "Use the greedy one-pass approach: if total_gas < total_cost return -1; "
            "otherwise track a running tank — when it goes negative, reset to 0 and "
            "set the next station as the new candidate start. "
            "O(n) time, O(1) space. "
            "Run `python test_gs.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "gas_station.py exists": check_gs_file,
            "defines can_complete_circuit function": check_gs_has_function,
            "all 8 cases pass": check_gs_all_pass,
            "clean exit": check_gs_exit,
        },
    },
    {
        "name": "next-permutation",
        "files": {"test_np.py": _TEST_NP_PY},
        "run": "python test_np.py",
        "prompt": (
            "A test script `test_np.py` is provided. Write `next_permutation.py` that "
            "implements `next_permutation(nums: list[int]) -> None`. Rearrange nums "
            "in-place to the next lexicographically greater permutation. If no such "
            "permutation exists (the array is descending), rearrange to the smallest "
            "permutation (ascending order). "
            "Algorithm: (1) scan right-to-left to find the first index i where "
            "nums[i] < nums[i+1]; (2) scan right-to-left again to find the first j "
            "where nums[j] > nums[i], then swap nums[i] and nums[j]; "
            "(3) reverse the suffix starting at i+1. If no such i exists (whole array "
            "is non-increasing), just reverse the entire array. "
            "O(n) time, O(1) space. "
            "Run `python test_np.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "next_permutation.py exists": check_np_file,
            "defines next_permutation function": check_np_has_function,
            "all 8 cases pass": check_np_all_pass,
            "clean exit": check_np_exit,
        },
    },
]
