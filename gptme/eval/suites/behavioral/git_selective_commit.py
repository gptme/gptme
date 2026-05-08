"""Behavioral scenario: git-selective-commit."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


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


test: "EvalSpec" = {
    "name": "git-selective-commit",
    "task_type": "structured_process",
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
    "run": "git log --oneline -1 && echo __GPTME_SEP__ && git show HEAD -- config.py && echo __GPTME_SEP__ && python3 -m pytest test_calc.py -q 2>&1",
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
}
