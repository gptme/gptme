"""Behavioral scenario: debug-data-pipeline."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_pipeline_tests_pass(ctx):
    """All pipeline tests should pass after the fix."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_pipeline_extract_emails_fixed(ctx):
    """extract_emails should iterate the list, not subscript it as a dict."""
    content = ctx.files.get("pipeline.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Must not still contain the broken subscript access
    if 'users["emails"]' in content or "users['emails']" in content:
        return False
    # Must iterate over users list (accept any loop variable name)
    return bool(re.search(r"for \w+ in users", content))


def check_pipeline_source_unchanged(ctx):
    """test_pipeline.py should not be modified."""
    content = ctx.files.get("test_pipeline.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return (
        "run_pipeline" in content
        and "assert result" in content
        and '"count"' in content
    )


test: "EvalSpec" = {
    "name": "debug-data-pipeline",
    "task_type": "structured_process",
    "files": {
        "pipeline.py": """\
\"\"\"User data processing pipeline.\"\"\"
import json


def load_data(path):
    \"\"\"Load user records from a JSON file.\"\"\"
    with open(path) as f:
        return json.load(f)


def filter_active(data):
    \"\"\"Keep only active user records.\"\"\"
    return [user for user in data if user.get("active")]


def extract_emails(users):
    \"\"\"Extract email addresses from user records.\"\"\"
    return users["emails"]  # bug: users is a list, not a dict


def format_output(emails):
    \"\"\"Return a summary dict with count and sorted emails.\"\"\"
    return {"count": len(emails), "emails": sorted(emails)}


def run_pipeline(path):
    data = load_data(path)
    active = filter_active(data)
    emails = extract_emails(active)
    return format_output(emails)
""",
        "test_pipeline.py": """\
import json
import os
import tempfile

import pytest

from pipeline import run_pipeline


@pytest.fixture
def users_file(tmp_path):
    data = [
        {"active": True, "email": "alice@example.com"},
        {"active": False, "email": "bob@example.com"},
        {"active": True, "email": "carol@example.com"},
    ]
    p = tmp_path / "users.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_pipeline_basic(users_file):
    result = run_pipeline(users_file)
    assert result == {
        "count": 2,
        "emails": ["alice@example.com", "carol@example.com"],
    }


def test_pipeline_all_inactive(tmp_path):
    data = [{"active": False, "email": "x@example.com"}]
    p = tmp_path / "empty.json"
    p.write_text(json.dumps(data))
    result = run_pipeline(str(p))
    assert result == {"count": 0, "emails": []}


def test_pipeline_single_active(tmp_path):
    data = [{"active": True, "email": "only@example.com"}]
    p = tmp_path / "single.json"
    p.write_text(json.dumps(data))
    result = run_pipeline(str(p))
    assert result == {"count": 1, "emails": ["only@example.com"]}
""",
    },
    "run": "python3 -m pytest test_pipeline.py -v --tb=short 2>&1",
    "prompt": (
        "The pipeline in `pipeline.py` is failing with a TypeError. "
        "Run the tests to see the failure, then trace the data flow "
        "through each stage (`load_data` → `filter_active` → "
        "`extract_emails` → `format_output`) to find where the format "
        "mismatch occurs. Fix `pipeline.py` so all tests pass. "
        "Do not modify `test_pipeline.py`."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_pipeline_tests_pass,
        "extract_emails iterates list": check_pipeline_extract_emails_fixed,
        "test file unchanged": check_pipeline_source_unchanged,
    },
}
