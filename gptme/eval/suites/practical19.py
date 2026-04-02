"""Practical eval tests (batch 19) — dynamic programming and tree structures.

Tests requiring correct implementation of:
- Edit distance: Levenshtein distance between two strings (DP)
- BST operations: binary search tree insert, search, in-order traversal
- Coin change: minimum coins to make a given amount (DP)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- edit-distance checks ---

_TEST_EDIT_DISTANCE_PY = """\
import sys
try:
    from edit_distance import edit_distance
except ImportError:
    print("ERROR: edit_distance.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

cases = [
    ("", "", 0),
    ("abc", "abc", 0),
    ("kitten", "sitting", 3),
    ("saturday", "sunday", 3),
    ("", "hello", 5),
    ("hello", "", 5),
    ("intention", "execution", 5),
    ("abcdef", "azced", 3),
]

for s1, s2, expected in cases:
    result = edit_distance(s1, s2)
    if result != expected:
        errors.append(
            f"edit_distance({s1!r}, {s2!r}) = {result}, expected {expected}"
        )

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 edit-distance cases passed.")
print("Levenshtein edit distance implementation correct.")
"""


def check_editdist_file(ctx):
    """edit_distance.py should exist."""
    return "edit_distance.py" in ctx.files


def check_editdist_all_pass(ctx):
    """All 8 edit-distance cases should pass."""
    return "All 8 edit-distance cases passed" in ctx.stdout


def check_editdist_has_function(ctx):
    """Should define edit_distance function."""
    src = ctx.files.get("edit_distance.py", "")
    return "def edit_distance(" in src


def check_editdist_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- bst-operations checks ---

_TEST_BST_PY = """\
import sys
try:
    from bst import BST
except ImportError:
    print("ERROR: bst.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Test 1: insert and in_order traversal
t = BST()
for v in [5, 3, 7, 1, 4, 6, 8]:
    t.insert(v)
result = t.in_order()
if result != [1, 3, 4, 5, 6, 7, 8]:
    errors.append(f"in_order() should return [1,3,4,5,6,7,8], got {result!r}")

# Test 2: search
if not t.search(4):
    errors.append("search(4) should return True")
if t.search(9):
    errors.append("search(9) should return False")
if not t.search(1):
    errors.append("search(1) should return True")
if not t.search(8):
    errors.append("search(8) should return True")

# Test 3: empty tree
t2 = BST()
if t2.in_order() != []:
    errors.append(f"in_order() on empty tree should return [], got {t2.in_order()!r}")
if t2.search(1):
    errors.append("search on empty tree should return False")

# Test 4: duplicate handling (should not add duplicates)
t3 = BST()
t3.insert(5)
t3.insert(5)
t3.insert(5)
result = t3.in_order()
if result != [5]:
    errors.append(f"in_order() with duplicates should return [5], got {result!r}")

# Test 5: single element
t4 = BST()
t4.insert(42)
if t4.in_order() != [42]:
    errors.append(f"single element in_order() should return [42], got {t4.in_order()!r}")
if not t4.search(42):
    errors.append("search(42) should return True")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 10 assertions passed.")
print("BST insert/search/in_order operations correct.")
"""


def check_bst_file(ctx):
    """bst.py should exist."""
    return "bst.py" in ctx.files


def check_bst_all_pass(ctx):
    """All 10 assertions should pass."""
    return "All 10 assertions passed" in ctx.stdout


def check_bst_has_class(ctx):
    """Should define a BST class."""
    src = ctx.files.get("bst.py", "")
    return "class BST" in src


def check_bst_has_methods(ctx):
    """Should have insert, search, and in_order methods."""
    src = ctx.files.get("bst.py", "")
    return "def insert(" in src and "def search(" in src and "def in_order(" in src


def check_bst_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- coin-change checks ---

_TEST_COIN_CHANGE_PY = """\
import sys
try:
    from coin_change import coin_change
except ImportError:
    print("ERROR: coin_change.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

cases = [
    ([1, 5, 10, 25], 30, 2),       # 25 + 5
    ([1, 5, 10, 25], 11, 2),       # 10 + 1
    ([2], 3, -1),                   # impossible
    ([1], 0, 0),                    # zero amount
    ([1, 2, 5], 11, 3),            # 5 + 5 + 1
    ([3, 7], 14, 2),               # 7 + 7
    ([3, 7], 5, -1),               # impossible
    ([1, 3, 4], 6, 2),            # 3 + 3
]

for coins, amount, expected in cases:
    result = coin_change(coins, amount)
    if result != expected:
        errors.append(
            f"coin_change({coins!r}, {amount}) = {result}, expected {expected}"
        )

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 coin-change cases passed.")
print("Coin change minimum-coins DP correct.")
"""


def check_coinchange_file(ctx):
    """coin_change.py should exist."""
    return "coin_change.py" in ctx.files


def check_coinchange_all_pass(ctx):
    """All 8 coin-change cases should pass."""
    return "All 8 coin-change cases passed" in ctx.stdout


def check_coinchange_has_function(ctx):
    """Should define coin_change function."""
    src = ctx.files.get("coin_change.py", "")
    return "def coin_change(" in src


def check_coinchange_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "edit-distance",
        "files": {"test_edit_distance.py": _TEST_EDIT_DISTANCE_PY},
        "run": "python test_edit_distance.py",
        "prompt": (
            "A test script `test_edit_distance.py` is provided. Write "
            "`edit_distance.py` that implements `edit_distance(s1: str, s2: str) "
            "-> int`. The function should return the minimum number of "
            "single-character edits (insertions, deletions, or substitutions) "
            "needed to transform s1 into s2 (Levenshtein distance). "
            "Use dynamic programming for an efficient O(m*n) solution. "
            "Run `python test_edit_distance.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "edit_distance.py exists": check_editdist_file,
            "all 8 cases pass": check_editdist_all_pass,
            "defines edit_distance function": check_editdist_has_function,
            "clean exit": check_editdist_exit,
        },
    },
    {
        "name": "bst-operations",
        "files": {"test_bst.py": _TEST_BST_PY},
        "run": "python test_bst.py",
        "prompt": (
            "A test script `test_bst.py` is provided. Write `bst.py` that "
            "implements a `BST` class (binary search tree) with these methods:\n"
            "- `insert(val)` — insert a value (ignore duplicates)\n"
            "- `search(val) -> bool` — return True if value exists\n"
            "- `in_order() -> list[int]` — return all values in sorted order "
            "(in-order traversal)\n\n"
            "The tree should use a node-based structure (not a sorted list). "
            "An empty tree returns [] for in_order() and False for search(). "
            "Run `python test_bst.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "bst.py exists": check_bst_file,
            "all 10 assertions pass": check_bst_all_pass,
            "defines BST class": check_bst_has_class,
            "has insert/search/in_order methods": check_bst_has_methods,
            "clean exit": check_bst_exit,
        },
    },
    {
        "name": "coin-change",
        "files": {"test_coin_change.py": _TEST_COIN_CHANGE_PY},
        "run": "python test_coin_change.py",
        "prompt": (
            "A test script `test_coin_change.py` is provided. Write "
            "`coin_change.py` that implements `coin_change(coins: list[int], "
            "amount: int) -> int`. The function should return the minimum "
            "number of coins needed to make the given amount, or -1 if "
            "it cannot be made with the given coin denominations. "
            "Each coin denomination can be used unlimited times. "
            "Use dynamic programming for an efficient solution. "
            "Run `python test_coin_change.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "coin_change.py exists": check_coinchange_file,
            "all 8 cases pass": check_coinchange_all_pass,
            "defines coin_change function": check_coinchange_has_function,
            "clean exit": check_coinchange_exit,
        },
    },
]
