"""Practical eval tests (batch 24) — backtracking, 1D DP, and graph algorithms.

Tests requiring correct implementation of:
- N-Queens: place N queens on an NxN board with no conflicts (backtracking)
- Longest increasing subsequence: find LIS length in an array (DP + binary search)
- Cycle detection in directed graph: detect if a directed graph has a cycle (DFS coloring)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- N-Queens checks ---

_TEST_NQUEENS_PY = """\
import sys
try:
    from nqueens import solve_n_queens
except ImportError:
    print("ERROR: nqueens.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []


def is_valid_solution(board, n):
    \"\"\"Verify a board placement is valid: no two queens attack each other.\"\"\"
    if len(board) != n:
        return False
    for i in range(n):
        for j in range(i + 1, n):
            # Same column
            if board[i] == board[j]:
                return False
            # Same diagonal
            if abs(board[i] - board[j]) == abs(i - j):
                return False
    return True


# Case 1: n=1 — trivial
r = solve_n_queens(1)
if not isinstance(r, list) or len(r) != 1:
    errors.append(f"case 1: n=1 should have 1 solution, got {len(r) if isinstance(r, list) else r}")
elif not is_valid_solution(r[0], 1):
    errors.append(f"case 1: n=1 solution invalid: {r[0]}")

# Case 2: n=4 — should have 2 solutions
r = solve_n_queens(4)
if not isinstance(r, list) or len(r) != 2:
    errors.append(f"case 2: n=4 should have 2 solutions, got {len(r) if isinstance(r, list) else r}")
else:
    for i, sol in enumerate(r):
        if not is_valid_solution(sol, 4):
            errors.append(f"case 2: n=4 solution {i} invalid: {sol}")

# Case 3: n=5 — should have 10 solutions
r = solve_n_queens(5)
if not isinstance(r, list) or len(r) != 10:
    errors.append(f"case 3: n=5 should have 10 solutions, got {len(r) if isinstance(r, list) else r}")
else:
    for i, sol in enumerate(r):
        if not is_valid_solution(sol, 5):
            errors.append(f"case 3: n=5 solution {i} invalid: {sol}")

# Case 4: n=8 — should have 92 solutions
r = solve_n_queens(8)
if not isinstance(r, list) or len(r) != 92:
    errors.append(f"case 4: n=8 should have 92 solutions, got {len(r) if isinstance(r, list) else r}")
else:
    for i, sol in enumerate(r):
        if not is_valid_solution(sol, 8):
            errors.append(f"case 4: n=8 solution {i} invalid: {sol}")

# Case 5: n=2 — no solutions
r = solve_n_queens(2)
if not isinstance(r, list) or len(r) != 0:
    errors.append(f"case 5: n=2 should have 0 solutions, got {len(r) if isinstance(r, list) else r}")

# Case 6: n=3 — no solutions
r = solve_n_queens(3)
if not isinstance(r, list) or len(r) != 0:
    errors.append(f"case 6: n=3 should have 0 solutions, got {len(r) if isinstance(r, list) else r}")

# Case 7: n=6 — should have 4 solutions
r = solve_n_queens(6)
if not isinstance(r, list) or len(r) != 4:
    errors.append(f"case 7: n=6 should have 4 solutions, got {len(r) if isinstance(r, list) else r}")

# Case 8: solutions should be unique
r = solve_n_queens(5)
if isinstance(r, list):
    unique = set(tuple(s) for s in r)
    if len(unique) != len(r):
        errors.append(f"case 8: n=5 has duplicate solutions ({len(r)} total, {len(unique)} unique)")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 N-Queens test cases passed.")
print("N-Queens implementation correct.")
"""


def check_nqueens_file(ctx):
    """nqueens.py should exist."""
    return "nqueens.py" in ctx.files


def check_nqueens_all_pass(ctx):
    """All 8 N-Queens cases should pass."""
    return "All 8 N-Queens test cases passed" in ctx.stdout


def check_nqueens_has_function(ctx):
    """Should define solve_n_queens function."""
    src = ctx.files.get("nqueens.py", "")
    return "def solve_n_queens(" in src


def check_nqueens_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- longest increasing subsequence checks ---

_TEST_LIS_PY = """\
import sys
try:
    from lis import longest_increasing_subsequence
except ImportError:
    print("ERROR: lis.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: classic example
r = longest_increasing_subsequence([10, 9, 2, 5, 3, 7, 101, 18])
if r != 4:
    errors.append(f"case 1: [10,9,2,5,3,7,101,18] -> 4, got {r}")

# Case 2: already sorted
r = longest_increasing_subsequence([1, 2, 3, 4, 5])
if r != 5:
    errors.append(f"case 2: [1,2,3,4,5] -> 5, got {r}")

# Case 3: reverse sorted
r = longest_increasing_subsequence([5, 4, 3, 2, 1])
if r != 1:
    errors.append(f"case 3: [5,4,3,2,1] -> 1, got {r}")

# Case 4: single element
r = longest_increasing_subsequence([42])
if r != 1:
    errors.append(f"case 4: [42] -> 1, got {r}")

# Case 5: empty array
r = longest_increasing_subsequence([])
if r != 0:
    errors.append(f"case 5: [] -> 0, got {r}")

# Case 6: all equal
r = longest_increasing_subsequence([7, 7, 7, 7])
if r != 1:
    errors.append(f"case 6: [7,7,7,7] -> 1, got {r}")

# Case 7: two elements increasing
r = longest_increasing_subsequence([1, 3])
if r != 2:
    errors.append(f"case 7: [1,3] -> 2, got {r}")

# Case 8: two elements decreasing
r = longest_increasing_subsequence([3, 1])
if r != 1:
    errors.append(f"case 8: [3,1] -> 1, got {r}")

# Case 9: longer example with multiple LIS paths
r = longest_increasing_subsequence([0, 1, 0, 3, 2, 3])
if r != 4:
    errors.append(f"case 9: [0,1,0,3,2,3] -> 4, got {r}")

# Case 10: negative numbers
r = longest_increasing_subsequence([-5, -2, -8, 0, 1, -1, 3])
if r != 5:
    errors.append(f"case 10: [-5,-2,-8,0,1,-1,3] -> 5, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 10 LIS test cases passed.")
print("Longest increasing subsequence implementation correct.")
"""


def check_lis_file(ctx):
    """lis.py should exist."""
    return "lis.py" in ctx.files


def check_lis_all_pass(ctx):
    """All 10 LIS cases should pass."""
    return "All 10 LIS test cases passed" in ctx.stdout


def check_lis_has_function(ctx):
    """Should define longest_increasing_subsequence function."""
    src = ctx.files.get("lis.py", "")
    return "def longest_increasing_subsequence(" in src


def check_lis_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- cycle detection in directed graph checks ---

_TEST_CYCLE_PY = """\
import sys
try:
    from cycle_detect import has_cycle
except ImportError:
    print("ERROR: cycle_detect.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: simple cycle (A -> B -> C -> A)
r = has_cycle({0: [1], 1: [2], 2: [0]})
if r is not True:
    errors.append(f"case 1: simple cycle -> True, got {r}")

# Case 2: no cycle (DAG)
r = has_cycle({0: [1, 2], 1: [3], 2: [3], 3: []})
if r is not False:
    errors.append(f"case 2: DAG -> False, got {r}")

# Case 3: self-loop
r = has_cycle({0: [0]})
if r is not True:
    errors.append(f"case 3: self-loop -> True, got {r}")

# Case 4: empty graph
r = has_cycle({})
if r is not False:
    errors.append(f"case 4: empty -> False, got {r}")

# Case 5: single node, no edges
r = has_cycle({0: []})
if r is not False:
    errors.append(f"case 5: single node -> False, got {r}")

# Case 6: disconnected components, one has cycle
r = has_cycle({0: [1], 1: [], 2: [3], 3: [4], 4: [2]})
if r is not True:
    errors.append(f"case 6: disconnected with cycle -> True, got {r}")

# Case 7: disconnected components, no cycle
r = has_cycle({0: [1], 1: [], 2: [3], 3: []})
if r is not False:
    errors.append(f"case 7: disconnected DAG -> False, got {r}")

# Case 8: longer cycle in complex graph
r = has_cycle({0: [1], 1: [2], 2: [3], 3: [4], 4: [1], 5: [0]})
if r is not True:
    errors.append(f"case 8: longer cycle -> True, got {r}")

# Case 9: linear chain (no cycle)
r = has_cycle({0: [1], 1: [2], 2: [3], 3: [4], 4: []})
if r is not False:
    errors.append(f"case 9: linear chain -> False, got {r}")

# Case 10: wider convergence (5 nodes, three paths to sink, no cycle)
r = has_cycle({0: [1, 2], 1: [4], 2: [3, 4], 3: [4], 4: []})
if r is not False:
    errors.append(f"case 10: wider convergence DAG -> False, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 10 cycle-detection test cases passed.")
print("Directed graph cycle detection implementation correct.")
"""


def check_cycle_file(ctx):
    """cycle_detect.py should exist."""
    return "cycle_detect.py" in ctx.files


def check_cycle_all_pass(ctx):
    """All 10 cycle-detection cases should pass."""
    return "All 10 cycle-detection test cases passed" in ctx.stdout


def check_cycle_has_function(ctx):
    """Should define has_cycle function."""
    src = ctx.files.get("cycle_detect.py", "")
    return "def has_cycle(" in src


def check_cycle_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "n-queens",
        "files": {"test_nqueens.py": _TEST_NQUEENS_PY},
        "run": "python test_nqueens.py",
        "prompt": (
            "A test script `test_nqueens.py` is provided. Write `nqueens.py` that "
            "implements `solve_n_queens(n)` which returns a list of all distinct "
            "solutions to the N-Queens puzzle. Each solution is a list of N integers "
            "where the value at index i is the column position of the queen in row i "
            "(0-indexed). Two queens attack each other if they share a row, column, "
            "or diagonal. Use a backtracking approach. "
            "Run `python test_nqueens.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "nqueens.py exists": check_nqueens_file,
            "all 8 cases pass": check_nqueens_all_pass,
            "defines solve_n_queens function": check_nqueens_has_function,
            "clean exit": check_nqueens_exit,
        },
    },
    {
        "name": "lis",
        "files": {"test_lis.py": _TEST_LIS_PY},
        "run": "python test_lis.py",
        "prompt": (
            "A test script `test_lis.py` is provided. Write `lis.py` that "
            "implements `longest_increasing_subsequence(nums)` where `nums` is a "
            "list of integers. Return the length of the longest strictly increasing "
            "subsequence. A subsequence is derived by deleting some or no elements "
            "without changing the order of remaining elements. An efficient O(n log n) "
            "approach using patience sorting (maintain tails array with binary search) "
            "is preferred but O(n^2) DP is also acceptable. "
            "Run `python test_lis.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "lis.py exists": check_lis_file,
            "all 10 cases pass": check_lis_all_pass,
            "defines longest_increasing_subsequence function": check_lis_has_function,
            "clean exit": check_lis_exit,
        },
    },
    {
        "name": "cycle-detect",
        "files": {"test_cycle.py": _TEST_CYCLE_PY},
        "run": "python test_cycle.py",
        "prompt": (
            "A test script `test_cycle.py` is provided. Write `cycle_detect.py` that "
            "implements `has_cycle(graph)` where `graph` is a dictionary mapping each "
            "node (integer) to a list of its neighbors (directed edges). Return True "
            "if the directed graph contains a cycle, False otherwise. Use DFS with "
            "three-color marking (white/gray/black) to detect back edges. Handle "
            "disconnected components by checking all nodes. "
            "Run `python test_cycle.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "cycle_detect.py exists": check_cycle_file,
            "all 10 cases pass": check_cycle_all_pass,
            "defines has_cycle function": check_cycle_has_function,
            "clean exit": check_cycle_exit,
        },
    },
]
