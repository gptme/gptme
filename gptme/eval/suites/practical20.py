"""Practical eval tests (batch 20) — graph algorithms and matrix traversal.

Tests requiring correct implementation of:
- Dijkstra: weighted shortest-path from a source node
- Spiral matrix: traverse a 2D matrix in spiral order
- Number of islands: count connected components in a binary grid
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- dijkstra checks ---

_TEST_DIJKSTRA_PY = """\
import sys
try:
    from dijkstra import dijkstra
except ImportError:
    print("ERROR: dijkstra.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Graph 1: simple 4-node directed weighted graph
#   A --4--> B   A --2--> C   C --1--> B   B --3--> D   C --5--> D
# Shortest paths from A: B=3 (A->C->B), C=2, D=6 (A->C->B->D)
g1 = {
    'A': [('B', 4), ('C', 2)],
    'B': [('D', 3)],
    'C': [('B', 1), ('D', 5)],
    'D': [],
}
d1 = dijkstra(g1, 'A')

# Case 1: source distance is 0
if d1.get('A') != 0:
    errors.append(f"case 1: source A should have distance 0, got {d1.get('A')}")

# Case 2: direct edge
if d1.get('C') != 2:
    errors.append(f"case 2: A->C should be 2, got {d1.get('C')}")

# Case 3: relax through C
if d1.get('B') != 3:
    errors.append(f"case 3: A->C->B should be 3 (not 4), got {d1.get('B')}")

# Case 4: longer path through relaxed node
if d1.get('D') != 6:
    errors.append(f"case 4: A->C->B->D should be 6, got {d1.get('D')}")

# Graph 2: CLRS-style textbook example (5 nodes)
#   s -10-> u, s -5-> x
#   u -1-> v, u -2-> x
#   x -3-> u, x -9-> v, x -2-> y
#   v -4-> y, y -7-> s, y -6-> v
g2 = {
    's': [('u', 10), ('x', 5)],
    'u': [('v', 1), ('x', 2)],
    'x': [('u', 3), ('v', 9), ('y', 2)],
    'v': [('y', 4)],
    'y': [('s', 7), ('v', 6)],
}
d2 = dijkstra(g2, 's')

# Case 5: shortest to x
if d2.get('x') != 5:
    errors.append(f"case 5: s->x should be 5, got {d2.get('x')}")

# Case 6: s->x->y
if d2.get('y') != 7:
    errors.append(f"case 6: s->x->y should be 7, got {d2.get('y')}")

# Case 7: relaxed through x then u
if d2.get('u') != 8:
    errors.append(f"case 7: s->x->u should be 8, got {d2.get('u')}")

# Case 8: relaxed through u->v
if d2.get('v') != 9:
    errors.append(f"case 8: s->x->u->v should be 9, got {d2.get('v')}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 Dijkstra test cases passed.")
print("Dijkstra shortest path implementation correct.")
"""


def check_dijkstra_file(ctx):
    """dijkstra.py should exist."""
    return "dijkstra.py" in ctx.files


def check_dijkstra_all_pass(ctx):
    """All 8 Dijkstra cases should pass."""
    return "All 8 Dijkstra test cases passed" in ctx.stdout


def check_dijkstra_has_function(ctx):
    """Should define dijkstra function."""
    src = ctx.files.get("dijkstra.py", "")
    return "def dijkstra(" in src


def check_dijkstra_uses_heap(ctx):
    """Should use heapq for efficiency."""
    src = ctx.files.get("dijkstra.py", "")
    return "heapq" in src


def check_dijkstra_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- spiral-matrix checks ---

_TEST_SPIRAL_PY = """\
import sys
try:
    from spiral_matrix import spiral_order
except ImportError:
    print("ERROR: spiral_matrix.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: 1x1
r = spiral_order([[1]])
if r != [1]:
    errors.append(f"case 1: [[1]] -> [1], got {r}")

# Case 2: 1-row matrix
r = spiral_order([[1, 2, 3, 4]])
if r != [1, 2, 3, 4]:
    errors.append(f"case 2: 1x4 row -> [1,2,3,4], got {r}")

# Case 3: 1-column matrix
r = spiral_order([[1], [2], [3], [4]])
if r != [1, 2, 3, 4]:
    errors.append(f"case 3: 4x1 column -> [1,2,3,4], got {r}")

# Case 4: 2x2
r = spiral_order([[1, 2], [3, 4]])
if r != [1, 2, 4, 3]:
    errors.append(f"case 4: 2x2 -> [1,2,4,3], got {r}")

# Case 5: 3x3
r = spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
if r != [1, 2, 3, 6, 9, 8, 7, 4, 5]:
    errors.append(f"case 5: 3x3 -> [1,2,3,6,9,8,7,4,5], got {r}")

# Case 6: 3x4 (more columns than rows)
r = spiral_order([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]])
if r != [1, 2, 3, 4, 8, 12, 11, 10, 9, 5, 6, 7]:
    errors.append(f"case 6: 3x4 -> [1..4,8,12,11,10,9,5,6,7], got {r}")

# Case 7: 4x3 (more rows than columns)
r = spiral_order([[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]])
if r != [1, 2, 3, 6, 9, 12, 11, 10, 7, 4, 5, 8]:
    errors.append(f"case 7: 4x3 -> [1,2,3,6,9,12,11,10,7,4,5,8], got {r}")

# Case 8: 2x3
r = spiral_order([[1, 2, 3], [4, 5, 6]])
if r != [1, 2, 3, 6, 5, 4]:
    errors.append(f"case 8: 2x3 -> [1,2,3,6,5,4], got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 spiral-matrix cases passed.")
print("Spiral matrix traversal implementation correct.")
"""


def check_spiral_file(ctx):
    """spiral_matrix.py should exist."""
    return "spiral_matrix.py" in ctx.files


def check_spiral_all_pass(ctx):
    """All 8 spiral-matrix cases should pass."""
    return "All 8 spiral-matrix cases passed" in ctx.stdout


def check_spiral_has_function(ctx):
    """Should define spiral_order function."""
    src = ctx.files.get("spiral_matrix.py", "")
    return "def spiral_order(" in src


def check_spiral_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- num-islands checks ---

_TEST_ISLANDS_PY = """\
import sys
try:
    from num_islands import num_islands
except ImportError:
    print("ERROR: num_islands.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Case 1: no islands
r = num_islands([["0","0"],["0","0"]])
if r != 0:
    errors.append(f"case 1: all water -> 0, got {r}")

# Case 2: one island (single cell)
r = num_islands([["1"]])
if r != 1:
    errors.append(f"case 2: single land -> 1, got {r}")

# Case 3: one large island
r = num_islands([["1","1"],["1","1"]])
if r != 1:
    errors.append(f"case 3: 2x2 land -> 1, got {r}")

# Case 4: four islands (isolated corners)
r = num_islands([["1","0","1"],["0","0","0"],["1","0","1"]])
if r != 4:
    errors.append(f"case 4: corners -> 4, got {r}")

# Case 5: three islands
r = num_islands([
    ["1","1","0","0","0"],
    ["1","1","0","0","0"],
    ["0","0","1","0","0"],
    ["0","0","0","1","1"],
])
if r != 3:
    errors.append(f"case 5: 3 islands -> 3, got {r}")

# Case 6: classic LeetCode example 1 (1 island)
r = num_islands([
    ["1","1","1","1","0"],
    ["1","1","0","1","0"],
    ["1","1","0","0","0"],
    ["0","0","0","0","0"],
])
if r != 1:
    errors.append(f"case 6: one big island -> 1, got {r}")

# Case 7: isolated cells (checkerboard-like) — 5 islands
r = num_islands([
    ["1","0","0","0","1"],
    ["0","1","0","1","0"],
    ["0","0","1","0","0"],
])
if r != 5:
    errors.append(f"case 7: 5 isolated cells -> 5, got {r}")

# Case 8: diagonal cells are NOT connected (only 4-directional)
r = num_islands([["1","0"],["0","1"]])
if r != 2:
    errors.append(f"case 8: diagonal cells -> 2 separate islands, got {r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 num-islands cases passed.")
print("Number of islands implementation correct.")
"""


def check_islands_file(ctx):
    """num_islands.py should exist."""
    return "num_islands.py" in ctx.files


def check_islands_all_pass(ctx):
    """All 8 num-islands cases should pass."""
    return "All 8 num-islands cases passed" in ctx.stdout


def check_islands_has_function(ctx):
    """Should define num_islands function."""
    src = ctx.files.get("num_islands.py", "")
    return "def num_islands(" in src


def check_islands_uses_traversal(ctx):
    """Should use BFS or DFS for traversal (named or unnamed recursive helper)."""
    src = ctx.files.get("num_islands.py", "")
    return (
        "deque" in src
        or "queue" in src.lower()
        or "stack" in src.lower()
        or "visited" in src
        or "seen" in src
        or "def dfs" in src
        or "def bfs" in src
        or "    def " in src  # any nested/recursive helper function
    )


def check_islands_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "dijkstra",
        "files": {"test_dijkstra.py": _TEST_DIJKSTRA_PY},
        "run": "python test_dijkstra.py",
        "prompt": (
            "A test script `test_dijkstra.py` is provided. Write "
            "`dijkstra.py` that implements `dijkstra(graph, source)` where "
            "`graph` is a dict mapping each node to a list of `(neighbor, weight)` "
            "tuples, and `source` is the starting node. The function should return "
            "a dict mapping each reachable node to its shortest distance from "
            "`source`. Nodes that are unreachable may be omitted or set to "
            "`float('inf')`. Use a min-heap (heapq) for an efficient O((V+E) log V) "
            "implementation. Run `python test_dijkstra.py` to verify — it exits 0 "
            "on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "dijkstra.py exists": check_dijkstra_file,
            "all 8 cases pass": check_dijkstra_all_pass,
            "defines dijkstra function": check_dijkstra_has_function,
            "uses heapq": check_dijkstra_uses_heap,
            "clean exit": check_dijkstra_exit,
        },
    },
    {
        "name": "spiral-matrix",
        "files": {"test_spiral_matrix.py": _TEST_SPIRAL_PY},
        "run": "python test_spiral_matrix.py",
        "prompt": (
            "A test script `test_spiral_matrix.py` is provided. Write "
            "`spiral_matrix.py` that implements `spiral_order(matrix)` where "
            "`matrix` is a non-empty 2D list of integers. The function should "
            "return a flat list of all elements in spiral order: right along the "
            "top row, down the right column, left along the bottom row, up the "
            "left column, then repeat for the inner submatrix. "
            "Run `python test_spiral_matrix.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "spiral_matrix.py exists": check_spiral_file,
            "all 8 cases pass": check_spiral_all_pass,
            "defines spiral_order function": check_spiral_has_function,
            "clean exit": check_spiral_exit,
        },
    },
    {
        "name": "num-islands",
        "files": {"test_num_islands.py": _TEST_ISLANDS_PY},
        "run": "python test_num_islands.py",
        "prompt": (
            "A test script `test_num_islands.py` is provided. Write "
            "`num_islands.py` that implements `num_islands(grid)` where `grid` is "
            "a 2D list of strings `'1'` (land) and `'0'` (water). The function "
            "should return the number of islands — connected groups of `'1'` cells "
            "connected horizontally or vertically (not diagonally). You may modify "
            "the grid in-place or use a visited set. Use BFS or DFS to traverse "
            "each island. Run `python test_num_islands.py` to verify — it exits 0 "
            "on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "num_islands.py exists": check_islands_file,
            "all 8 cases pass": check_islands_all_pass,
            "defines num_islands function": check_islands_has_function,
            "uses BFS/DFS": check_islands_uses_traversal,
            "clean exit": check_islands_exit,
        },
    },
]
