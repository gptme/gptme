"""Behavioral scenario: extract-function-refactor."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


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


test: "EvalSpec" = {
    "name": "extract-function-refactor",
    "task_type": "creative_restructuring",
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
    "run": "python3 -m pytest test_services.py -q 2>&1",
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
}
