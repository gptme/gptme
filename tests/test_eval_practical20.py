from types import SimpleNamespace

from gptme.eval.suites.practical20 import (
    check_dijkstra_all_pass,
    check_dijkstra_has_function,
    check_dijkstra_uses_heap,
    check_islands_all_pass,
    check_islands_has_function,
    check_islands_uses_traversal,
    check_spiral_all_pass,
    check_spiral_has_function,
)


def _ctx(stdout: str, *, files: dict[str, str] | None = None, exit_code: int = 0):
    return SimpleNamespace(stdout=stdout, files=files or {}, exit_code=exit_code)


# --- dijkstra checks ---


def test_dijkstra_all_pass():
    assert check_dijkstra_all_pass(_ctx("All 8 Dijkstra test cases passed."))
    assert not check_dijkstra_all_pass(_ctx("FAIL: case 3: A->C->B should be 3"))
    # must not match spiral-matrix output
    assert not check_dijkstra_all_pass(_ctx("All 8 spiral-matrix cases passed."))


def test_dijkstra_has_function():
    src = "def dijkstra(graph, source):\n    pass"
    assert check_dijkstra_has_function(_ctx("", files={"dijkstra.py": src}))
    assert not check_dijkstra_has_function(
        _ctx("", files={"dijkstra.py": "def solve():"})
    )


def test_dijkstra_uses_heap():
    src = "import heapq\ndef dijkstra(graph, source):\n    heapq.heappush([], (0, source))"
    assert check_dijkstra_uses_heap(_ctx("", files={"dijkstra.py": src}))
    # No heapq import at all
    src_no_heap = "def dijkstra(graph, source):\n    for node in graph: pass"
    assert not check_dijkstra_uses_heap(_ctx("", files={"dijkstra.py": src_no_heap}))
    # False-positive guard: 'cheapest' contains 'heap' as substring but is NOT heapq
    src_false_pos = "def dijkstra(graph, source):\n    cheapest_dist = {source: 0}\n    return cheapest_dist"
    assert not check_dijkstra_uses_heap(_ctx("", files={"dijkstra.py": src_false_pos}))


# --- spiral-matrix checks ---


def test_spiral_all_pass():
    assert check_spiral_all_pass(_ctx("All 8 spiral-matrix cases passed."))
    assert not check_spiral_all_pass(_ctx("FAIL: case 5: 3x3"))
    # must not match dijkstra output
    assert not check_spiral_all_pass(_ctx("All 8 Dijkstra test cases passed."))


def test_spiral_has_function():
    src = "def spiral_order(matrix):\n    pass"
    assert check_spiral_has_function(_ctx("", files={"spiral_matrix.py": src}))
    assert not check_spiral_has_function(
        _ctx("", files={"spiral_matrix.py": "def solve():"})
    )


# --- num-islands checks ---


def test_islands_all_pass():
    assert check_islands_all_pass(_ctx("All 8 num-islands cases passed."))
    assert not check_islands_all_pass(_ctx("FAIL: case 4: corners -> 4"))
    # must not match spiral output
    assert not check_islands_all_pass(_ctx("All 8 spiral-matrix cases passed."))


def test_islands_has_function():
    src = "def num_islands(grid):\n    pass"
    assert check_islands_has_function(_ctx("", files={"num_islands.py": src}))
    assert not check_islands_has_function(
        _ctx("", files={"num_islands.py": "def solve():"})
    )


def test_islands_uses_traversal():
    src_bfs = "from collections import deque\ndef num_islands(grid):\n    q = deque()"
    assert check_islands_uses_traversal(_ctx("", files={"num_islands.py": src_bfs}))

    src_dfs = "def num_islands(grid):\n    visited = set()\n    def dfs(r, c): pass"
    assert check_islands_uses_traversal(_ctx("", files={"num_islands.py": src_dfs}))

    src_plain = "def num_islands(grid):\n    count = 0\n    return count"
    assert not check_islands_uses_traversal(
        _ctx("", files={"num_islands.py": src_plain})
    )
