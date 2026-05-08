"""Behavioral scenario: use-existing-helper."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_reuse_tests_pass(ctx):
    """All tests should pass after the refactor."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_reuse_imports_utils(ctx):
    """users.py must import from utils (normalize_email must be reachable)."""
    content = ctx.files.get("users.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return bool(re.search(r"from utils import|import utils", content))


def check_reuse_uses_normalize(ctx):
    """users.py must call normalize_email instead of duplicating strip/lower logic."""
    content = ctx.files.get("users.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "normalize_email" in content


def check_reuse_no_inline_strip(ctx):
    """Inline .strip().lower() duplication should be removed from create_user."""
    content = ctx.files.get("users.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return ".strip().lower()" not in content


def check_reuse_utils_unchanged(ctx):
    """utils.py should not be modified — only users.py should change."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def normalize_email" in content and "def is_valid_email" in content


test: "EvalSpec" = {
    "name": "use-existing-helper",
    "task_type": "structured_process",
    "files": {
        "utils.py": """\
\"\"\"Shared utility functions.\"\"\"

import re


def normalize_email(email: str) -> str:
    \"\"\"Normalize an email address: strip surrounding whitespace and lowercase.\"\"\"
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    \"\"\"Return True if *email* looks like a valid address (basic RFC check).\"\"\"
    return bool(re.match(r"[^@]+@[^@]+\\.[^@]+", email))
""",
        "users.py": """\
\"\"\"User management module.\"\"\"


def create_user(email: str) -> dict:
    \"\"\"Create a new user record with a normalized email address.

    Raises ValueError for obviously invalid addresses.
    Returns a dict with keys 'email' (normalized) and 'active'.
    \"\"\"
    # TODO: this duplicates normalization logic that already exists in utils.py
    normalized = email.strip().lower()
    if "@" not in normalized or "." not in normalized.split("@")[-1]:
        raise ValueError(f"Invalid email: {email!r}")
    return {"email": normalized, "active": True}
""",
        "test_users.py": """\
import pytest
from users import create_user


def test_create_user_normalizes_whitespace():
    user = create_user("  Alice@Example.COM  ")
    assert user["email"] == "alice@example.com"


def test_create_user_lowercases():
    user = create_user("BOB@DOMAIN.ORG")
    assert user["email"] == "bob@domain.org"


def test_create_user_invalid_no_at():
    with pytest.raises(ValueError):
        create_user("notanemail")


def test_create_user_invalid_no_dot():
    with pytest.raises(ValueError):
        create_user("user@nodot")


def test_create_user_active_flag():
    user = create_user("test@example.com")
    assert user["active"] is True
""",
    },
    "run": "python3 -m pytest test_users.py -v --tb=short 2>&1",
    "prompt": (
        "Refactor `create_user` in `users.py` to use the helper functions "
        "from `utils.py` instead of duplicating their logic inline. "
        "All existing tests must still pass after your change."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_reuse_tests_pass,
        "imports utils": check_reuse_imports_utils,
        "uses normalize_email": check_reuse_uses_normalize,
        "no inline strip/lower": check_reuse_no_inline_strip,
        "utils.py unchanged": check_reuse_utils_unchanged,
    },
}
