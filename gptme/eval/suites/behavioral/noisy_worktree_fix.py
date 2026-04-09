"""Behavioral scenario: noisy-worktree-fix."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_noisy_worktree_tests_pass(ctx):
    """Tests should pass after fixing the auth bug."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 3:
        return False
    return "failed" not in parts[2].lower() and "passed" in parts[2].lower()


def check_noisy_worktree_auth_committed(ctx):
    """auth.py must appear in the HEAD commit."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 2:
        return False
    return "auth.py" in parts[1]


def check_noisy_worktree_api_not_committed(ctx):
    """api.py must NOT appear in the HEAD commit — it is a WIP change."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 2:
        return False
    return "api.py" not in parts[1]


def check_noisy_worktree_config_not_committed(ctx):
    """config.py must NOT appear in the HEAD commit — it is a debug-only change."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 2:
        return False
    return "config.py" not in parts[1]


def check_noisy_worktree_fix_correct(ctx):
    """validate_email must no longer use the over-simple @ presence check."""
    content = ctx.files.get("auth.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # The bug: return "@" in email and len(email) > 3 — no domain-dot check
    buggy_pattern = '"@" in email and len(email) > 3'
    return buggy_pattern not in content


test: "EvalSpec" = {
    "name": "noisy-worktree-fix",
    "files": {
        "setup.sh": """\
#!/usr/bin/env bash
set -e
git init -q
git config user.email "test@example.com"
git config user.name "Test"
git config core.hooksPath /dev/null

# Initial clean state — auth.py is correct, tests pass
cat > auth.py << 'PYEOF'
\"\"\"Authentication utilities.\"\"\"


def validate_email(email: str) -> bool:
    \"\"\"Return True if email looks valid (must have @ and a dot in the domain).\"\"\"
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    return bool(local) and "." in domain
PYEOF

cat > api.py << 'PYEOF'
\"\"\"API endpoint handlers.\"\"\"


def get_user(user_id: int) -> dict:
    \"\"\"Retrieve a user by ID.\"\"\"
    return {"id": user_id, "name": "user"}
PYEOF

cat > config.py << 'PYEOF'
\"\"\"Application configuration.\"\"\"
DEBUG = False
MAX_RETRIES = 3
PYEOF

cat > utils.py << 'PYEOF'
\"\"\"Utility functions.\"\"\"


def slugify(text: str) -> str:
    \"\"\"Convert text to a URL-safe slug.\"\"\"
    return text.lower().replace(" ", "-")
PYEOF

cat > test_auth.py << 'PYEOF'
from auth import validate_email


def test_valid_email():
    assert validate_email("user@example.com") is True


def test_valid_subdomains():
    assert validate_email("user@mail.example.com") is True


def test_invalid_no_at():
    assert validate_email("notanemail") is False


def test_invalid_localhost():
    # no dot in the domain → must be invalid
    assert validate_email("user@localhost") is False


def test_invalid_no_dot_in_domain():
    assert validate_email("user@nodot") is False
PYEOF

git add auth.py api.py config.py utils.py test_auth.py
git commit -q -m "initial: add auth module, api handlers, config, utils"

# === Introduce changes — only auth.py has a bug; the rest are unrelated ===

# 1. Introduce regression bug in auth.py (tests will now fail)
cat > auth.py << 'PYEOF'
\"\"\"Authentication utilities.\"\"\"


def validate_email(email: str) -> bool:
    \"\"\"Return True if email looks valid.\"\"\"
    # BUG: only checks for @ sign; accepts 'user@localhost' (no dot in domain)
    return "@" in email and len(email) > 3
PYEOF

# 2. Half-finished feature in api.py (WIP — must NOT be committed)
cat >> api.py << 'PYEOF'


def create_user(name: str) -> dict:
    # TODO: add input validation
    return {"name": name, "active": True}
PYEOF

# 3. Temporary debug flag in config.py (must NOT be committed)
sed -i 's/DEBUG = False/DEBUG = True  # temporary debug/' config.py

# 4. Minor comment tweak in utils.py (must NOT be committed)
sed -i 's/Convert text to a URL-safe slug./Convert text to URL-safe slug./' utils.py
""",
    },
    "run": (
        "git log --oneline -3"
        " && echo __GPTME_SEP__"
        " && git show HEAD --name-only --format=''"
        " && echo __GPTME_SEP__"
        " && python3 -m pytest test_auth.py -q 2>&1"
    ),
    "prompt": (
        "Run `bash setup.sh` to initialise the repository. "
        "The `validate_email` function in `auth.py` has a regression: "
        "it now accepts addresses without a dot in the domain "
        "(e.g. `user@localhost` returns True, but it should return False). "
        "Fix the bug so all tests in `test_auth.py` pass, "
        "then commit your fix to the repository."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_noisy_worktree_tests_pass,
        "auth.py committed": check_noisy_worktree_auth_committed,
        "api.py not committed": check_noisy_worktree_api_not_committed,
        "config.py not committed": check_noisy_worktree_config_not_committed,
        "fix is correct": check_noisy_worktree_fix_correct,
    },
}
