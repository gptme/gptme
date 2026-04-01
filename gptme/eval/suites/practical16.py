"""Practical eval tests (batch 16) — harder tasks requiring multi-step reasoning.

Tests that require more sophisticated implementations than practical15:
- Async producer-consumer with bounded queue and graceful shutdown (concurrency)
- JSON schema validation collecting ALL errors (parsing + validation logic)
- Trie data structure with prefix search (custom data structure + algorithms)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- async-queue-workers checks ---

_QUEUE_CONFIG = """\
{
  "num_producers": 3,
  "num_consumers": 2,
  "items_per_producer": 4,
  "queue_maxsize": 3
}
"""


def check_queue_file(ctx):
    """queue_workers.py should exist."""
    return "queue_workers.py" in ctx.files


def check_queue_imports_asyncio(ctx):
    """Script must import asyncio."""
    src = ctx.files.get("queue_workers.py", "")
    return "import asyncio" in src


def check_queue_uses_queue(ctx):
    """Script must use asyncio.Queue."""
    src = ctx.files.get("queue_workers.py", "")
    return "asyncio.Queue" in src or "Queue(" in src


def check_queue_total_items(ctx):
    """Output should report 12 total items processed (3 producers * 4 items each)."""
    return bool(
        re.search(
            r"\b12\b.*\bitem|\bitem.*\b12\b|\btotal.*\b12\b|\bprocessed.*\b12\b|\b12\b.*\bprocess",
            ctx.stdout,
            re.IGNORECASE,
        )
    )


def check_queue_uses_sentinel(ctx):
    """Script should use sentinel values (None) to signal shutdown to consumers."""
    src = ctx.files.get("queue_workers.py", "")
    return "None" in src and (
        "sentinel" in src.lower()
        or src.count("put(None)") >= 1
        or src.count("await q.put(None)") >= 1
        or src.count("put_nowait(None)") >= 1
        or "is None" in src
    )


def check_queue_all_producers_mentioned(ctx):
    """Output should show work from all 3 producers."""
    out = ctx.stdout.lower()
    # Accept producer IDs 0-2 or 1-3
    has_three = (
        ("producer 0" in out or "producer-0" in out or "p0" in out or "prod 0" in out)
        and (
            "producer 1" in out or "producer-1" in out or "p1" in out or "prod 1" in out
        )
        and (
            "producer 2" in out or "producer-2" in out or "p2" in out or "prod 2" in out
        )
    ) or (
        ("producer 1" in out or "producer-1" in out)
        and ("producer 2" in out or "producer-2" in out)
        and ("producer 3" in out or "producer-3" in out)
    )
    return has_three


def check_queue_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- json-schema-validate checks ---

_SCHEMA_JSON = """\
{
  "type": "object",
  "required": ["name", "age", "email"],
  "properties": {
    "name": {"type": "string", "minLength": 2},
    "age": {"type": "integer", "minimum": 0, "maximum": 150},
    "email": {"type": "string", "pattern": ".*@.*\\\\..*"},
    "score": {"type": "number", "minimum": 0.0, "maximum": 100.0}
  }
}
"""

_DATA_JSON = """\
[
  {"name": "Alice", "age": 30, "email": "alice@example.com", "score": 95.5},
  {"name": "B", "age": -5, "email": "notanemail"},
  {"name": "Charlie", "age": 25, "score": 101.0}
]
"""


def check_schema_file(ctx):
    """validate_schema.py should exist."""
    return "validate_schema.py" in ctx.files


def check_schema_record0_valid(ctx):
    """Record 0 (Alice) should be reported as VALID."""
    lines = ctx.stdout.splitlines()
    for line in lines:
        low = line.lower()
        if (
            "alice" in low
            or "record 0" in low
            or "item 0" in low
            or "#0" in low
            or "[0]" in low
        ) and ("valid" in low and "invalid" not in low):
            return True
        # Also accept if first non-empty line says valid and no errors
        if "alice" in low and re.search(r"\bvalid\b", low) and "invalid" not in low:
            return True
    # Check if output structure reports first record valid
    out = ctx.stdout
    return bool(
        re.search(
            r"(?:record|item|entry)\s*[#\[]?0[#\]]?[^:]*:\s*(?:valid|ok|pass)",
            out,
            re.IGNORECASE,
        )
    )


def check_schema_record1_invalid(ctx):
    """Record 1 (B/-5/notanemail) should be reported as INVALID with errors."""
    out = ctx.stdout.lower()
    # Must mention invalid for second record
    has_invalid = bool(
        re.search(
            r"(?:record|item|entry)\s*[#\[]?1[#\]]?.*invalid|invalid.*(?:record|item|entry)\s*[#\[]?1",
            out,
            re.IGNORECASE,
        )
    ) or (
        bool(re.search(r"\bb\b", out))
        and "invalid" in out
        and ("age" in out or "name" in out or "email" in out)
    )
    return has_invalid


def check_schema_record1_multiple_errors(ctx):
    """Record 1 should have at least 2 errors reported (name too short, age below 0, email missing @.domain)."""
    out = ctx.stdout.lower()
    # Check for presence of multiple error indicators near the second record
    error_indicators = [
        bool(
            re.search(
                r"min.?length|too short|length.*\b1\b|\bname\b.*\berror\b|\bname\b.*\binvalid\b",
                out,
            )
        ),
        bool(
            re.search(
                r"minimum|below 0|negative|age.*\berror\b|age.*\binvalid\b|-5", out
            )
        ),
        bool(
            re.search(
                r"pattern|email.*invalid|email.*error|not.*email|invalid.*email|@", out
            )
        ),
    ]
    return sum(error_indicators) >= 2


def check_schema_record2_missing_email(ctx):
    """Record 2 (Charlie) should report missing required field 'email'."""
    out = ctx.stdout.lower()
    return bool(
        re.search(r"email.*missing|missing.*email|required.*email|email.*required", out)
    )


def check_schema_record2_score_invalid(ctx):
    """Record 2 (Charlie) should report score 101.0 exceeds maximum of 100."""
    out = ctx.stdout.lower()
    return bool(
        re.search(
            r"score.*maximum|maximum.*score|score.*exceed|score.*101|101.*score|above.*100|exceed.*100",
            out,
        )
    )


def check_schema_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- implement-trie checks ---

_TEST_TRIE_PY = """\
import sys
try:
    import trie
except ImportError:
    print("ERROR: trie.py not found or import failed", file=sys.stderr)
    sys.exit(1)

t = trie.Trie()

words = ["apple", "app", "application", "banana", "band"]
for w in words:
    t.insert(w)

errors = []

if not t.search("apple"):
    errors.append("search('apple') should return True")
if not t.search("app"):
    errors.append("search('app') should return True")
if t.search("ap"):
    errors.append("search('ap') should return False (not a complete word)")
if not t.search("banana"):
    errors.append("search('banana') should return True")
if t.search("ban"):
    errors.append("search('ban') should return False (not a complete word)")
if t.search("xyz"):
    errors.append("search('xyz') should return False")

matches = sorted(t.starts_with("app"))
expected = ["app", "apple", "application"]
if matches != expected:
    errors.append(f"starts_with('app') returned {matches}, expected {expected}")

band_matches = sorted(t.starts_with("ban"))
if band_matches != ["banana", "band"]:
    errors.append(f"starts_with('ban') returned {band_matches}, expected ['banana', 'band']")

count = t.count()
if count != 5:
    errors.append(f"count() returned {count}, expected 5")

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)

print(f"All {9} assertions passed.")
print(f"Words with prefix 'app': {', '.join(sorted(t.starts_with('app')))}")
print(f"Words with prefix 'ban': {', '.join(sorted(t.starts_with('ban')))}")
print(f"Total words: {t.count()}")
"""


def check_trie_file(ctx):
    """trie.py should exist."""
    return "trie.py" in ctx.files


def check_trie_all_pass(ctx):
    """All 9 assertions should pass."""
    return "All 9 assertions passed" in ctx.stdout


def check_trie_app_prefix(ctx):
    """starts_with('app') should return app, apple, application."""
    out = ctx.stdout
    return "app, apple, application" in out or all(
        w in out for w in ["app", "apple", "application"]
    )


def check_trie_ban_prefix(ctx):
    """starts_with('ban') should return banana, band."""
    out = ctx.stdout
    return "banana, band" in out


def check_trie_count(ctx):
    """count() should return 5."""
    return bool(re.search(r"total words:\s*5\b", ctx.stdout, re.IGNORECASE))


def check_trie_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "async-queue-workers",
        "files": {"config.json": _QUEUE_CONFIG},
        "run": "python queue_workers.py",
        "prompt": (
            "Write a Python script `queue_workers.py` that reads `config.json` "
            "which has keys: `num_producers`, `num_consumers`, `items_per_producer`, "
            "and `queue_maxsize`. Create `num_producers` producer coroutines and "
            "`num_consumers` consumer coroutines sharing a bounded "
            "`asyncio.Queue(maxsize=queue_maxsize)`. Each producer enqueues "
            "`items_per_producer` items (simple integers or tuples tagged with the "
            "producer index), then signals it is done. After all producers finish, "
            "send `num_consumers` sentinel values (None) into the queue so each "
            "consumer knows to stop. Each consumer processes items until it receives "
            "the sentinel. Print a line for each item consumed (e.g. "
            "'Consumer 1 processed item (producer=0, value=3)'). "
            "After everything finishes, print "
            "'Done: N items processed' where N = num_producers * items_per_producer. "
            "Use asyncio.gather to run all producers and consumers concurrently."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "queue_workers.py exists": check_queue_file,
            "imports asyncio": check_queue_imports_asyncio,
            "uses asyncio.Queue": check_queue_uses_queue,
            "12 total items": check_queue_total_items,
            "sentinel shutdown": check_queue_uses_sentinel,
            "all 3 producers mentioned": check_queue_all_producers_mentioned,
            "clean exit": check_queue_exit,
        },
    },
    {
        "name": "json-schema-validate",
        "files": {
            "schema.json": _SCHEMA_JSON,
            "data.json": _DATA_JSON,
        },
        "run": "python validate_schema.py",
        "prompt": (
            "Write a Python script `validate_schema.py` that reads `schema.json` "
            "and `data.json`. The schema describes an object with required fields, "
            "property types, and constraints (minLength, minimum, maximum, pattern). "
            "The data is a JSON array of objects to validate. "
            "For each object in the array, validate it against the schema and print "
            "whether it is VALID or INVALID. For INVALID records, list ALL validation "
            "errors found (do not stop at the first error). Errors to detect:\n"
            "- Missing required fields\n"
            "- Wrong type (e.g. string where integer expected)\n"
            "- String shorter than minLength\n"
            "- Number outside minimum/maximum range\n"
            "- String not matching the pattern (use re.match)\n"
            "Output format: one section per record, e.g.:\n"
            "  Record 0: VALID\n"
            "  Record 1: INVALID\n"
            "    - name: length 1 is less than minLength 2\n"
            "    - age: -5 is less than minimum 0\n"
            "    - email: does not match pattern"
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "validate_schema.py exists": check_schema_file,
            "Record 0 is VALID": check_schema_record0_valid,
            "Record 1 is INVALID": check_schema_record1_invalid,
            "Record 1 has ≥2 errors": check_schema_record1_multiple_errors,
            "Record 2 missing email": check_schema_record2_missing_email,
            "Record 2 score > max": check_schema_record2_score_invalid,
            "clean exit": check_schema_exit,
        },
    },
    {
        "name": "implement-trie",
        "files": {"test_trie.py": _TEST_TRIE_PY},
        "run": "python test_trie.py",
        "prompt": (
            "A test script `test_trie.py` is provided. Write `trie.py` that implements "
            "a `Trie` class with these methods:\n"
            "- `insert(word: str)` — insert a word into the trie\n"
            "- `search(word: str) -> bool` — return True only if the exact word was inserted\n"
            "- `starts_with(prefix: str) -> list[str]` — return all inserted words that "
            "start with the given prefix (in any order)\n"
            "- `count() -> int` — return the total number of words inserted\n"
            "The test inserts: 'apple', 'app', 'application', 'banana', 'band'. "
            "It checks exact-match search (ap → False, app → True), prefix search "
            "('app' → ['app', 'apple', 'application'], 'ban' → ['banana', 'band']), "
            "and count (5). Run `python test_trie.py` to verify — it exits 0 on success."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "trie.py exists": check_trie_file,
            "all 9 assertions pass": check_trie_all_pass,
            "app prefix correct": check_trie_app_prefix,
            "ban prefix correct": check_trie_ban_prefix,
            "count is 5": check_trie_count,
            "clean exit": check_trie_exit,
        },
    },
]
