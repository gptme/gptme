"""Behavioral eval suite — multi-step workflow tasks.

Tests agent workflow behaviors (git ops, debugging loops, multi-file edits)
rather than single-function coding ability.  These are the scenarios where
lessons about *how to work* should have a measurable effect on outcomes:

- git-selective-commit: stage and commit only the relevant files
- multi-file-rename: rename a function consistently across a project
- iterative-debug: find and fix a bug through test feedback
- stage-new-files: stage an untracked file before committing it
- merge-conflict-resolution: resolve a git merge conflict preserving both features
- extract-function-refactor: extract duplicated code into a shared module

Each scenario comes with a deterministic checker so results can be used in
lesson holdout A/B experiments (idea #19, eval-to-lesson feedback loop).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


# ── git-selective-commit ─────────────────────────────────────────────────────


def check_git_selective_commit_msg(ctx):
    """Last commit message should be a conventional commit for the divide feature."""
    # stdout = git log --oneline -1
    # e.g. "abc1234 feat(calc): add divide function"
    line = ctx.stdout.partition("__GPTME_SEP__")[0].strip()
    # "git log --oneline -1" format: "<hash> <message>" — split on first space
    msg = line.split(" ", 1)[1] if " " in line else line
    msg_lower = msg.lower()
    return (
        ("feat" in msg_lower or "add" in msg_lower)
        and "divide" in msg_lower
        and ":" in msg_lower
    )


def check_git_selective_config_not_committed(ctx):
    """config.py should NOT appear in the last commit (diff is non-empty means not committed)."""
    # stdout contains: <git log -1>\n__GPTME_SEP__\n<git show HEAD -- config.py>\n__GPTME_SEP__\n<pytest>
    # Use a unique separator that never appears in git/pytest output.
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 2:
        return False
    # git show HEAD -- config.py is empty if not committed
    committed_content = parts[1].strip()
    return committed_content == ""


def check_git_selective_tests_pass(ctx):
    """Tests should pass after the commit."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 3:
        return False
    pytest_output = parts[2]
    return "failed" not in pytest_output.lower() and "passed" in pytest_output.lower()


# ── multi-file-rename ────────────────────────────────────────────────────────


def check_rename_no_old_name(ctx):
    """calcArea should not appear in any .py file after the rename."""
    for name, content in ctx.files.items():
        if not name.endswith(".py"):
            continue
        text = content if isinstance(content, str) else content.decode()
        if "calcArea" in text:
            return False
    return True


def check_rename_new_name_in_geometry(ctx):
    """calculate_area should be defined in src/geometry.py."""
    content = ctx.files.get("src/geometry.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def calculate_area" in content


def check_rename_tests_pass(ctx):
    """Tests should pass after the rename."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_rename_test_uses_new_name(ctx):
    """tests/test_geometry.py should call calculate_area, not calcArea."""
    content = ctx.files.get("tests/test_geometry.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "calculate_area" in content and "calcArea" not in content


# ── iterative-debug ──────────────────────────────────────────────────────────


def check_debug_tests_pass(ctx):
    """All tests should pass after the fix."""
    return ctx.exit_code == 0


def check_debug_no_syntax_error(ctx):
    """No SyntaxError or ImportError in test output."""
    lower = ctx.stdout.lower() + ctx.stderr.lower()
    return "syntaxerror" not in lower and "importerror" not in lower


def check_debug_fix_in_file(ctx):
    """calculator.py divide() should not silently swallow ZeroDivisionError."""
    content = ctx.files.get("calculator.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Detect the original bug by its unique pattern rather than by the fix.
    # The bug: catching ZeroDivisionError and returning None silently.
    # Valid fixes include: removing the try/except so Python raises naturally,
    # re-raising with `raise`, or guarding with `if b == 0: raise ...`.
    # All of these are accepted because they eliminate the buggy pattern.
    still_buggy = "except ZeroDivisionError" in content and "return None" in content
    return not still_buggy


# ── stage-new-files ──────────────────────────────────────────────────────────


def check_stage_new_file_committed(ctx):
    """new_feature.py should appear in the HEAD commit's file list."""
    # stdout: <git log --oneline>\n__GPTME_SEP__\n<git show HEAD --name-only --format="">
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 2:
        return False
    files_in_commit = parts[1]
    return "new_feature.py" in files_in_commit


def check_stage_two_commits(ctx):
    """There should be at least 2 commits (initial + new file)."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if not parts[0].strip():
        return False
    log_lines = [line for line in parts[0].strip().split("\n") if line.strip()]
    return len(log_lines) >= 2


def check_stage_file_has_double(ctx):
    """new_feature.py contains the double function as requested."""
    content = ctx.files.get("new_feature.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def double" in content


# ── merge-conflict-resolution ────────────────────────────────────────────────


def check_merge_no_conflict_markers(ctx):
    """No conflict markers should remain in any file."""
    for content in ctx.files.values():
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


# ── extract-function-refactor ────────────────────────────────────────────────


def check_extract_tests_pass(ctx):
    """All tests should pass after the refactor."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_extract_shared_module_exists(ctx):
    """A shared validation module should exist with the extracted function."""
    content = ctx.files.get("validation.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def validate_email" in content or "def normalize_email" in content


def check_extract_no_duplication(ctx):
    """The inline validation logic should be removed from all 3 service modules."""
    for name in ("user_service.py", "newsletter.py", "contact.py"):
        content = ctx.files.get(name, "")
        if isinstance(content, bytes):
            content = content.decode()
        if '"@"' in content or "'@'" in content:
            return False
    return True


def check_extract_callers_import(ctx):
    """All three service modules should import from the shared module."""
    import_count = 0
    for name in ("user_service.py", "newsletter.py", "contact.py"):
        content = ctx.files.get(name, "")
        if isinstance(content, bytes):
            content = content.decode()
        if "from validation import" in content or "import validation" in content:
            import_count += 1
    return import_count == 3


# ── test list ────────────────────────────────────────────────────────────────

tests: list["EvalSpec"] = [
    # -------------------------------------------------------------------
    # Scenario 1: Selective git commit
    # Agent must commit only the relevant changes (new divide function
    # in calc.py + test) and leave unrelated debug line in config.py
    # uncommitted.  Tests git workflow discipline: explicit file staging,
    # conventional commit messages, not committing everything at once.
    # -------------------------------------------------------------------
    {
        "name": "git-selective-commit",
        "files": {
            "setup.sh": """\
#!/usr/bin/env bash
set -e
git init -q
git config user.email "test@example.com"
git config user.name "Test"
# Disable global git hooks — eval workspaces don't have gptme.toml [agent],
# which can cause globally configured pre-commit hooks to block master commits.
git config core.hooksPath /dev/null

# Initial state: calc.py + config.py already tracked
cat > calc.py << 'PYEOF'
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
PYEOF

cat > config.py << 'PYEOF'
DEBUG = False
MAX_RETRIES = 3
PYEOF

cat > test_calc.py << 'PYEOF'
from calc import add, subtract, divide

def test_add():
    assert add(2, 3) == 5

def test_subtract():
    assert subtract(5, 2) == 3

def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5
PYEOF

git add calc.py config.py test_calc.py
git commit -q -m "initial: add calc and config"

# Simulate two pending changes:
# 1. New divide function in calc.py (feature to commit)
cat >> calc.py << 'PYEOF'

def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b
PYEOF

# 2. Unrelated debug change in config.py (should NOT be committed)
sed -i 's/DEBUG = False/DEBUG = True  # temporary debug/' config.py
""",
        },
        "run": "git log --oneline -1 && echo __GPTME_SEP__ && git show HEAD -- config.py && echo __GPTME_SEP__ && uv run --with pytest python3 -m pytest test_calc.py -q 2>&1",
        "prompt": (
            "Run `bash setup.sh` to initialise the git repository. "
            "Then commit only the new `divide` function added to calc.py "
            "(and the existing test for it in test_calc.py) as a conventional "
            "commit. The debug change in config.py must NOT be included in "
            "the commit — it is a temporary local change. "
            "Use an appropriate conventional commit message (e.g. "
            "`feat(calc): add divide function`)."
        ),
        "tools": ["shell", "save", "read"],
        "expect": {
            "conventional commit message": check_git_selective_commit_msg,
            "config.py not committed": check_git_selective_config_not_committed,
            "tests pass": check_git_selective_tests_pass,
        },
    },
    # -------------------------------------------------------------------
    # Scenario 2: Multi-file rename
    # Agent must rename calcArea → calculate_area in src/geometry.py AND
    # update all callers: src/utils.py and tests/test_geometry.py.
    # Tests multi-file consistency, scope discipline, and test-green discipline.
    # -------------------------------------------------------------------
    {
        "name": "multi-file-rename",
        "files": {
            "src/__init__.py": "",
            "src/geometry.py": """\
\"\"\"Geometry utilities.\"\"\"
import math


def calcArea(shape, *args):
    \"\"\"Calculate area of a shape.

    Supports: circle (r), rectangle (w, h), triangle (b, h).
    \"\"\"
    if shape == "circle":
        return math.pi * args[0] ** 2
    elif shape == "rectangle":
        return args[0] * args[1]
    elif shape == "triangle":
        return 0.5 * args[0] * args[1]
    else:
        raise ValueError(f"Unknown shape: {shape}")
""",
            "src/utils.py": """\
\"\"\"Utility wrappers around geometry functions.\"\"\"
from src.geometry import calcArea


def room_area(width, height):
    \"\"\"Return floor area of a rectangular room.\"\"\"
    return calcArea("rectangle", width, height)


def circular_pool_area(radius):
    \"\"\"Return surface area of a circular pool.\"\"\"
    return calcArea("circle", radius)
""",
            "tests/__init__.py": "",
            "tests/test_geometry.py": """\
import math
import pytest
from src.geometry import calcArea


def test_rectangle():
    assert calcArea("rectangle", 4, 5) == 20


def test_circle():
    assert abs(calcArea("circle", 3) - math.pi * 9) < 1e-9


def test_triangle():
    assert calcArea("triangle", 6, 4) == 12.0


def test_unknown_shape():
    with pytest.raises(ValueError):
        calcArea("hexagon", 5)
""",
        },
        "run": "uv run --with pytest python3 -m pytest tests/ -q 2>&1",
        "prompt": (
            "The function `calcArea` in src/geometry.py should be renamed to "
            "`calculate_area` to follow Python naming conventions (PEP 8 snake_case). "
            "Update the function definition in src/geometry.py AND every place it is "
            "imported or called (src/utils.py, tests/test_geometry.py). "
            "Make sure all tests still pass after the rename."
        ),
        "tools": ["shell", "save", "read"],
        "expect": {
            "old name gone from all files": check_rename_no_old_name,
            "new name defined in geometry.py": check_rename_new_name_in_geometry,
            "test file uses new name": check_rename_test_uses_new_name,
            "tests pass": check_rename_tests_pass,
        },
    },
    # -------------------------------------------------------------------
    # Scenario 3: Iterative debug loop
    # calculator.py has a bug: divide() returns None on ZeroDivisionError
    # instead of raising.  Agent must run the tests, read the failure,
    # fix the code, and verify.
    # Tests: reading error output, targeted single-file fix, CI green discipline.
    # -------------------------------------------------------------------
    {
        "name": "iterative-debug",
        "files": {
            "calculator.py": """\
\"\"\"Simple calculator module.\"\"\"


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None  # BUG: should raise, not return None
""",
            "test_calculator.py": """\
import pytest
from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply():
    assert multiply(3, 7) == 21


def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5


def test_divide_by_zero():
    \"\"\"divide() must raise ZeroDivisionError, not return None.\"\"\"
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
""",
        },
        "run": "uv run --with pytest python3 -m pytest test_calculator.py -v --tb=short 2>&1",
        "prompt": (
            "The test suite in test_calculator.py is failing. "
            "Run the tests to see which test fails, then fix the bug in "
            "calculator.py so that all tests pass. "
            "Do not modify the test file."
        ),
        "tools": ["shell", "save", "read"],
        "expect": {
            "tests pass": check_debug_tests_pass,
            "no syntax/import errors": check_debug_no_syntax_error,
            "fix present in calculator.py": check_debug_fix_in_file,
        },
    },
    # -------------------------------------------------------------------
    # Scenario 4: Stage new file before committing
    # Agent must create a new file and commit it. Common failure mode:
    # `git commit new_feature.py -m "msg"` errors with "pathspec did not
    # match any file(s) known to git" because new files must be staged
    # with `git add` first.  Tests: git staging discipline for new files.
    # -------------------------------------------------------------------
    {
        "name": "stage-new-files",
        "files": {
            "setup.sh": """\
#!/usr/bin/env bash
set -e
git init -q
git config user.email "test@example.com"
git config user.name "Test"
git config core.hooksPath /dev/null

cat > main.py << 'PYEOF'
def greet(name):
    return f"Hello, {name}!"
PYEOF

git add main.py
git commit -q -m "initial: add greet function"
""",
        },
        "run": "git log --oneline && echo __GPTME_SEP__ && git show HEAD --name-only --format=''",
        "prompt": (
            "Run `bash setup.sh` to initialise the git repository. "
            "Then create a new file `new_feature.py` with a simple Python function "
            "`def double(x): return x * 2`, and commit it with a conventional "
            "commit message (e.g. `feat: add double function`). "
            "The file does not exist yet — you need to create it and stage it "
            "before committing."
        ),
        "tools": ["shell", "save", "read"],
        "expect": {
            "new_feature.py in HEAD commit": check_stage_new_file_committed,
            "at least 2 commits": check_stage_two_commits,
            "new_feature.py contains double function": check_stage_file_has_double,
        },
    },
    # -------------------------------------------------------------------
    # Scenario 5: Merge conflict resolution
    # Two branches modify the same file differently: main adds a new function
    # (format_name_upper), feature branch adds null-safety to format_name.
    # Agent starts with an in-progress merge that has conflicts and must
    # resolve them so both features are preserved and all tests pass.
    # Tests: conflict comprehension, preserving intent from both sides,
    # completing the merge, and verifying with tests.
    # -------------------------------------------------------------------
    {
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
        "run": "uv run --with pytest python3 -m pytest test_utils.py test_null_safety.py test_upper.py -q 2>&1",
        "prompt": (
            "Run `bash setup.sh` to initialise the repository. It creates two "
            "branches that both modify `utils.py` and starts a merge that "
            "conflicts. Resolve the merge conflict in `utils.py` so that BOTH "
            "features are preserved:\n"
            "1. `format_name` should handle `None` arguments (return 'Unknown')\n"
            "2. `format_name_upper` should exist and work correctly\n"
            "After resolving, complete the merge commit and verify all tests pass."
        ),
        "tools": ["shell", "save", "read"],
        "expect": {
            "no conflict markers": check_merge_no_conflict_markers,
            "tests pass": check_merge_tests_pass,
            "null safety preserved": check_merge_null_safety,
            "upper function preserved": check_merge_upper_function,
            "merge commit completed": check_merge_commit_completed,
        },
    },
    # -------------------------------------------------------------------
    # Scenario 6: Extract duplicated function
    # Three service modules each contain identical email validation logic.
    # Agent must extract it into a shared validation.py module and update
    # all callers.  Tests: DRY refactoring, creating new modules, updating
    # imports, maintaining test coverage across the change.
    # -------------------------------------------------------------------
    {
        "name": "extract-function-refactor",
        "files": {
            "user_service.py": """\
\"\"\"User account service.\"\"\"


def create_user(email, name):
    \"\"\"Create a new user account.\"\"\"
    if not email or "@" not in email or "." not in email.split("@")[1]:
        raise ValueError("Invalid email address")
    clean_email = email.lower().strip()
    return {"email": clean_email, "name": name}
""",
            "newsletter.py": """\
\"\"\"Newsletter subscription service.\"\"\"


def subscribe(email):
    \"\"\"Subscribe an email to the newsletter.\"\"\"
    if not email or "@" not in email or "." not in email.split("@")[1]:
        raise ValueError("Invalid email address")
    clean_email = email.lower().strip()
    return {"email": clean_email, "subscribed": True}
""",
            "contact.py": """\
\"\"\"Contact form service.\"\"\"


def send_message(email, message):
    \"\"\"Send a contact form message.\"\"\"
    if not email or "@" not in email or "." not in email.split("@")[1]:
        raise ValueError("Invalid email address")
    clean_email = email.lower().strip()
    return {"email": clean_email, "message": message, "sent": True}
""",
            "test_services.py": """\
import pytest
from user_service import create_user
from newsletter import subscribe
from contact import send_message


def test_create_user():
    result = create_user("Alice@Example.com", "Alice")
    assert result == {"email": "alice@example.com", "name": "Alice"}


def test_subscribe():
    result = subscribe("Bob@Example.com")
    assert result == {"email": "bob@example.com", "subscribed": True}


def test_send_message():
    result = send_message("Carol@Example.com", "Hi")
    assert result == {"email": "carol@example.com", "message": "Hi", "sent": True}


def test_create_user_invalid():
    with pytest.raises(ValueError):
        create_user("not-an-email", "Bad")


def test_subscribe_empty():
    with pytest.raises(ValueError):
        subscribe("")


def test_send_message_no_dot():
    with pytest.raises(ValueError):
        send_message("user@nodot", "Hi")
""",
        },
        "run": "uv run --with pytest python3 -m pytest test_services.py -q 2>&1",
        "prompt": (
            "The three service modules (`user_service.py`, `newsletter.py`, "
            "`contact.py`) each contain identical email validation and "
            "normalisation logic. Extract the shared logic into a new "
            "`validation.py` module with a `validate_email(email)` function "
            "that raises `ValueError` for invalid emails and returns the "
            "cleaned (lowercased, stripped) email string. Update all three "
            "service modules to import and use `validate_email` from "
            "`validation.py` instead of duplicating the logic. "
            "Make sure all tests in `test_services.py` still pass."
        ),
        "tools": ["shell", "save", "read"],
        "expect": {
            "tests pass": check_extract_tests_pass,
            "shared module exists": check_extract_shared_module_exists,
            "no duplication across services": check_extract_no_duplication,
            "all callers import from validation": check_extract_callers_import,
        },
    },
]
