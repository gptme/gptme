"""Practical eval tests (batch 17) — data structures and text processing.

Tests requiring correct implementation of:
- LRU cache data structure with eviction (get/put/capacity)
- Interval merging algorithm (overlapping range consolidation)
- Base conversion between number systems with validation
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- lru-cache checks ---

_TEST_LRU_PY = """\
import sys
try:
    from lru_cache import LRUCache
except ImportError:
    print("ERROR: lru_cache.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# Test 1: basic get/put
cache = LRUCache(2)
cache.put(1, "one")
cache.put(2, "two")
v = cache.get(1)
if v != "one":
    errors.append(f"get(1) should return 'one', got {v!r}")

# Test 2: eviction on capacity overflow
cache.put(3, "three")  # should evict key 2 (least recently used)
v = cache.get(2)
if v != -1:
    errors.append(f"get(2) should return -1 after eviction, got {v!r}")
v = cache.get(3)
if v != "three":
    errors.append(f"get(3) should return 'three', got {v!r}")

# Test 3: access updates recency
cache2 = LRUCache(2)
cache2.put(1, "a")
cache2.put(2, "b")
cache2.get(1)           # access key 1, making key 2 the LRU
cache2.put(3, "c")      # should evict key 2, not key 1
v = cache2.get(1)
if v != "a":
    errors.append(f"get(1) should return 'a' after access, got {v!r}")
v = cache2.get(2)
if v != -1:
    errors.append(f"get(2) should return -1 (evicted), got {v!r}")

# Test 4: overwrite existing key
cache3 = LRUCache(2)
cache3.put(1, "old")
cache3.put(2, "two")
cache3.put(1, "new")   # overwrite, should not evict
v = cache3.get(1)
if v != "new":
    errors.append(f"get(1) should return 'new' after overwrite, got {v!r}")
v = cache3.get(2)
if v != "two":
    errors.append(f"get(2) should return 'two' (not evicted), got {v!r}")

# Test 5: miss returns -1
cache4 = LRUCache(1)
v = cache4.get(99)
if v != -1:
    errors.append(f"get(99) on empty cache should return -1, got {v!r}")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 8 assertions passed.")
print("LRU cache eviction and access-order semantics correct.")
"""


def check_lru_file(ctx):
    """lru_cache.py should exist."""
    return "lru_cache.py" in ctx.files


def check_lru_all_pass(ctx):
    """All 8 assertions should pass."""
    return "All 8 assertions passed" in ctx.stdout


def check_lru_has_class(ctx):
    """Should define an LRUCache class."""
    src = ctx.files.get("lru_cache.py", "")
    return "class LRUCache" in src


def check_lru_has_get_put(ctx):
    """Should have get and put methods."""
    src = ctx.files.get("lru_cache.py", "")
    return "def get(" in src and "def put(" in src


def check_lru_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- interval-merge checks ---

_INTERVALS_JSON = """\
[
  [1, 3],
  [2, 6],
  [8, 10],
  [15, 18],
  [17, 20],
  [5, 7],
  [25, 25]
]
"""


def check_merge_file(ctx):
    """merge_intervals.py should exist."""
    return "merge_intervals.py" in ctx.files


def check_merge_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


def check_merge_first_interval(ctx):
    """First merged interval should be [1, 7] (merging [1,3], [2,6], [5,7])."""
    out = ctx.stdout
    # Accept various formats: [1, 7], (1, 7), 1-7, etc.
    return bool(re.search(r"\[1,\s*7\]|\(1,\s*7\)|1\s*[-–]\s*7", out))


def check_merge_isolated(ctx):
    """Interval [8, 10] should remain isolated (no overlap)."""
    out = ctx.stdout
    return bool(re.search(r"\[8,\s*10\]|\(8,\s*10\)|8\s*[-–]\s*10", out))


def check_merge_second_overlap(ctx):
    """Intervals [15, 18] and [17, 20] should merge to [15, 20]."""
    out = ctx.stdout
    return bool(re.search(r"\[15,\s*20\]|\(15,\s*20\)|15\s*[-–]\s*20", out))


def check_merge_point_interval(ctx):
    """Point interval [25, 25] should be preserved."""
    out = ctx.stdout
    return bool(re.search(r"\[25,\s*25\]|\(25,\s*25\)|25\s*[-–]\s*25", out))


def check_merge_count(ctx):
    """Should produce exactly 4 merged intervals."""
    out = ctx.stdout
    # Try to parse JSON output
    try:
        data = json.loads(out.strip())
        if isinstance(data, list):
            return len(data) == 4
    except (json.JSONDecodeError, ValueError):
        pass
    # Count interval-like patterns in output
    intervals = re.findall(
        r"\[\s*\d+\s*,\s*\d+\s*\]|\(\s*\d+\s*,\s*\d+\s*\)|\d+\s*[-–]\s*\d+",
        out,
    )
    return len(intervals) == 4


# --- base-converter checks ---

_TEST_CONVERT_PY = """\
import sys
try:
    from base_convert import convert
except ImportError:
    print("ERROR: base_convert.py not found or import failed", file=sys.stderr)
    sys.exit(1)

errors = []

# decimal to binary
r = convert("255", 10, 2)
if r != "11111111":
    errors.append(f"convert('255', 10, 2) = {r!r}, expected '11111111'")

# binary to decimal
r = convert("1010", 2, 10)
if r != "10":
    errors.append(f"convert('1010', 2, 10) = {r!r}, expected '10'")

# decimal to hex
r = convert("255", 10, 16)
if r.upper() != "FF":
    errors.append(f"convert('255', 10, 16) = {r!r}, expected 'FF' (case-insensitive)")

# hex to decimal
r = convert("1A", 16, 10)
if r != "26":
    errors.append(f"convert('1A', 16, 10) = {r!r}, expected '26'")

# octal to binary
r = convert("17", 8, 2)
if r != "1111":
    errors.append(f"convert('17', 8, 2) = {r!r}, expected '1111'")

# zero
r = convert("0", 10, 16)
if r != "0":
    errors.append(f"convert('0', 10, 16) = {r!r}, expected '0'")

# hex to octal
r = convert("FF", 16, 8)
if r != "377":
    errors.append(f"convert('FF', 16, 8) = {r!r}, expected '377'")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print("All 7 assertions passed.")
print("Base conversion between binary, octal, decimal, hexadecimal correct.")
"""


def check_base_file(ctx):
    """base_convert.py should exist."""
    return "base_convert.py" in ctx.files


def check_base_all_pass(ctx):
    """All 7 assertions should pass."""
    return "All 7 assertions passed" in ctx.stdout


def check_base_has_function(ctx):
    """Should define a convert function."""
    src = ctx.files.get("base_convert.py", "")
    return "def convert(" in src


def check_base_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "lru-cache",
        "files": {"test_lru.py": _TEST_LRU_PY},
        "run": "python test_lru.py",
        "prompt": (
            "A test script `test_lru.py` is provided. Write `lru_cache.py` that "
            "implements an `LRUCache` class with these methods:\n"
            "- `__init__(self, capacity: int)` — create cache with given capacity\n"
            "- `get(self, key) -> value` — return the value for key, or -1 if not "
            "found. Accessing a key makes it the most recently used.\n"
            "- `put(self, key, value)` — insert or update key-value pair. If the "
            "cache exceeds capacity, evict the least recently used item.\n\n"
            "Both get and put must run in O(1) average time. "
            "Run `python test_lru.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "lru_cache.py exists": check_lru_file,
            "all 8 assertions pass": check_lru_all_pass,
            "defines LRUCache class": check_lru_has_class,
            "has get and put methods": check_lru_has_get_put,
            "clean exit": check_lru_exit,
        },
    },
    {
        "name": "interval-merge",
        "files": {"intervals.json": _INTERVALS_JSON},
        "run": "python merge_intervals.py",
        "prompt": (
            "Write a Python script `merge_intervals.py` that reads `intervals.json` "
            "(a JSON array of [start, end] integer pairs) and merges all overlapping "
            "intervals. Two intervals overlap if one starts before or when the other "
            "ends. Print the merged intervals as a JSON array, sorted by start value. "
            "For example, [1,3] and [2,6] merge into [1,6]; with [5,7] also present "
            "they all merge into [1,7]. "
            "Point intervals like [25,25] are valid and should be preserved."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "merge_intervals.py exists": check_merge_file,
            "[1,7] merged correctly": check_merge_first_interval,
            "[8,10] isolated": check_merge_isolated,
            "[15,20] merged correctly": check_merge_second_overlap,
            "[25,25] preserved": check_merge_point_interval,
            "exactly 4 intervals": check_merge_count,
            "clean exit": check_merge_exit,
        },
    },
    {
        "name": "base-converter",
        "files": {"test_convert.py": _TEST_CONVERT_PY},
        "run": "python test_convert.py",
        "prompt": (
            "A test script `test_convert.py` is provided. Write `base_convert.py` "
            "that implements a `convert(value: str, from_base: int, to_base: int) "
            "-> str` function. It should:\n"
            "- Accept a string representation of a number in `from_base`\n"
            "- Convert it to `to_base` and return the string representation\n"
            "- Support bases 2, 8, 10, and 16\n"
            "- Handle hex digits A-F (case-insensitive input, uppercase output)\n"
            "- Handle zero correctly\n"
            "Run `python test_convert.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "base_convert.py exists": check_base_file,
            "all 7 assertions pass": check_base_all_pass,
            "defines convert function": check_base_has_function,
            "clean exit": check_base_exit,
        },
    },
]
