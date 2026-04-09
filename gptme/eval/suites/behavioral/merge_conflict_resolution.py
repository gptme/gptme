"""Behavioral scenario: merge-conflict-resolution."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_merge_no_conflict_markers(ctx):
    """No conflict markers should remain in tracked source/test files."""
    for name, content in ctx.files.items():
        if name.startswith(".git/") or name == "setup.sh":
            continue
        text = content if isinstance(content, str) else content.decode()
        if "<<<<<<<" in text or "=======" in text or ">>>>>>>" in text:
            return False
    return True


def check_merge_tests_pass(ctx):
    """All tests should pass after conflict resolution."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_merge_null_safety(ctx):
    """format_name should guard both None inputs and return Unknown."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return (
        "if first is None or last is None" in content and 'return "Unknown"' in content
    )


def check_merge_upper_function(ctx):
    """format_name_upper should exist (main branch change)."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def format_name_upper" in content


def check_merge_commit_completed(ctx):
    """The merge should be finalized with a commit, not left in-progress."""
    git_head = ctx.files.get(".git/HEAD", "")
    if isinstance(git_head, bytes):
        git_head = git_head.decode()
    merge_head = ctx.files.get(".git/MERGE_HEAD")
    return bool(git_head.strip()) and merge_head is None


test: "EvalSpec" = {
    "name": "merge-conflict-resolution",
    "files": {
        "setup.sh": """\
#!/usr/bin/env bash
set -e
git init -q
git config user.email "test@example.com"
git config user.name "Test"
git config core.hooksPath /dev/null

# Base version — compact so both branch diffs overlap in the same region
cat > utils.py << 'PYEOF'
def format_name(first, last):
    return f"{first} {last}"
PYEOF

cat > test_utils.py << 'PYEOF'
from utils import format_name

def test_format_name():
    assert format_name("Alice", "Smith") == "Alice Smith"
PYEOF

git add utils.py test_utils.py
git commit -q -m "initial: add utils module"

# Feature branch: add null-safety (inserts lines inside format_name body)
git checkout -q -b feature-null-safety
cat > utils.py << 'PYEOF'
def format_name(first, last):
    if first is None or last is None:
        return "Unknown"
    return f"{first} {last}"
PYEOF

cat > test_null_safety.py << 'PYEOF'
from utils import format_name

def test_format_name_none_first():
    assert format_name(None, "Smith") == "Unknown"

def test_format_name_none_last():
    assert format_name("Alice", None) == "Unknown"
PYEOF

git add utils.py test_null_safety.py
git commit -q -m "feat: add null safety to format_name"

# Back to master: refactor body + add format_name_upper (overlapping region)
git checkout -q master
cat > utils.py << 'PYEOF'
def format_name(first, last):
    \"\"\"Format a person's full name.\"\"\"
    full = f"{first} {last}"
    return full


def format_name_upper(first, last):
    \"\"\"Format a person's full name in uppercase.\"\"\"
    return format_name(first, last).upper()
PYEOF

cat > test_upper.py << 'PYEOF'
from utils import format_name_upper

def test_format_name_upper():
    assert format_name_upper("Alice", "Smith") == "ALICE SMITH"
PYEOF

git add utils.py test_upper.py
git commit -q -m "feat: add format_name_upper"

# Start the merge — will conflict in utils.py (both branches modify body)
git merge feature-null-safety --no-edit || true

grep -q '^<<<<<<< ' utils.py
""",
    },
    "run": "python3 -m pytest test_utils.py test_null_safety.py test_upper.py -q 2>&1",
    "prompt": (
        "Run `bash setup.sh` to initialise the repository. It creates two "
        "branches that both modify `utils.py` and starts a merge that "
        "conflicts. Read `utils.py` to see the conflict markers, then write "
        "the resolved file without any `<<<<<<<`, `=======`, or `>>>>>>>` "
        "lines, preserving BOTH features:\n"
        "1. `format_name` should handle `None` arguments (return 'Unknown')\n"
        "2. `format_name_upper` should exist and work correctly\n"
        "After writing the resolved file, run "
        "`git add utils.py && git commit --no-edit` to complete the merge, "
        "then verify all tests pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "no conflict markers": check_merge_no_conflict_markers,
        "tests pass": check_merge_tests_pass,
        "null safety preserved": check_merge_null_safety,
        "upper function preserved": check_merge_upper_function,
        "merge commit completed": check_merge_commit_completed,
    },
}
