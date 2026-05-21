"""Behavioral scenario: root-cause-pipeline-debug.

Agent must trace a data-flow bug across multiple files and fix the upstream
root cause instead of patching the downstream symptom.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_pipeline_tests_pass(ctx):
    """All pipeline tests should pass after the fix."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_root_cause_fixed(ctx):
    """normalize_amounts should not silently default missing amounts to 0.0."""
    content = ctx.files.get("normalize.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Verify the original fixture file exists (agent worked in correct repo)
    if not content:
        return False
    # The bug assigned t["amount"] = 0.0 when amount is None
    # Acceptable fixes: filter the record, raise ValueError, or any
    # other transformation that removes the 0.0 default.
    has_zero_default = (
        't["amount"] = 0.0' in content
        or "t['amount'] = 0.0" in content
        or '["amount"] = 0.0' in content
    )
    return not has_zero_default


def check_sink_unchanged(ctx):
    """report.py should not be modified (root cause is upstream in normalize.py)."""
    content = ctx.files.get("report.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Verify original report.py structure is preserved
    # Reject modifications that add workarounds for missing amounts
    # (that would be patching the symptom instead of the root cause)
    return (
        "def generate_report" in content
        and "sum(" in content
        and '"count"' in content
        and '"average"' in content
        and 't.get("amount")' not in content
        and "t.get('amount')" not in content
    )


def check_no_blanket_except(ctx):
    """No bare except or except Exception in normalize.py."""
    content = ctx.files.get("normalize.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    if not content:
        return False
    return "except:" not in content and "except Exception:" not in content


def check_regression_test_added(ctx):
    """At least 3 test functions in test_pipeline.py (original 2 + at least 1 new)."""
    content = ctx.files.get("test_pipeline.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    if not content:
        return False
    # Count test function definitions
    test_names = re.findall(r"def (test_\w+)", content)
    # Original fixtures had exactly 2 test functions
    return len(test_names) >= 3


test: "EvalSpec" = {
    "name": "root-cause-pipeline-debug",
    "task_type": "structured_process",
    "files": {
        "loader.py": """\
\"\"\"Loads transaction data from a JSON file.\"\"\"
import json


def load_transactions(path):
    \"\"\"Load transaction records from a JSON file.\"\"\"
    with open(path) as f:
        return json.load(f)
""",
        "normalize.py": """\
\"\"\"Normalizes transaction records for reporting.\"\"\"


def normalize_amounts(transactions):
    \"\"\"Convert amount fields to float; default missing amounts to 0.0.\"\"\"
    result = []
    for t in transactions:
        amount = t.get("amount")
        if amount is None:
            t["amount"] = 0.0  # BUG: should filter out, not silently default
        else:
            t["amount"] = float(t["amount"])
        result.append(t)
    return result


def normalize(transactions):
    \"\"\"Apply all normalization steps.\"\"\"
    return normalize_amounts(transactions)
""",
        "report.py": """\
\"\"\"Generates summary reports from normalized transaction data.\"\"\"


def generate_report(transactions):
    \"\"\"Build a summary dict with total, count, and average amount.\"\"\"
    amounts = [t["amount"] for t in transactions]
    total = sum(amounts)
    count = len(amounts)
    avg = total / count if count > 0 else 0.0
    return {"total": total, "count": count, "average": avg}
""",
        "pipeline.py": """\
\"\"\"Pipeline runner combining loader, normalize, and report.\"\"\"
from loader import load_transactions
from normalize import normalize
from report import generate_report


def run_pipeline(path):
    \"\"\"Load → normalize → report.\"\"\"
    data = load_transactions(path)
    normalized = normalize(data)
    return generate_report(normalized)
""",
        "test_pipeline.py": """\
import json

import pytest
from pipeline import run_pipeline


@pytest.fixture
def sample_data(tmp_path):
    data = [
        {"id": 1, "name": "Widget A", "amount": 100.0},
        {"id": 2, "name": "Widget B", "amount": 50.0},
        {"id": 3, "name": "Widget C", "amount": 25.0},
        {"id": 4, "name": "Setup Fee", "amount": 200.0},
    ]
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_pipeline_normal(sample_data):
    result = run_pipeline(sample_data)
    assert result["total"] == 375.0
    assert result["count"] == 4
    assert result["average"] == 93.75


def test_pipeline_missing_amount(tmp_path):
    data = [
        {"id": 1, "name": "Item A", "amount": 50.0},
        {"id": 2, "name": "Item B"},  # missing amount field
        {"id": 3, "name": "Item C", "amount": 100.0},
    ]
    p = tmp_path / "test.json"
    p.write_text(json.dumps(data))
    result = run_pipeline(str(p))
    assert result["total"] == 150.0  # Item B should be dropped
    assert result["count"] == 2
    assert result["average"] == 75.0
""",
    },
    "run": "python3 -m pytest test_pipeline.py -v --tb=short 2>&1",
    "prompt": (
        "The pipeline in `pipeline.py` is producing wrong report results "
        "when some transaction records are missing the `amount` field. "
        "Run the tests to see the failure, then trace the data flow "
        "through each stage (`load_transactions` → `normalize` → "
        "`generate_report`) to find where the value corruption occurs. "
        "Fix the root cause so all tests pass and add one targeted "
        "regression test or unit assertion covering the corrected data path."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_pipeline_tests_pass,
        "root cause fixed": check_root_cause_fixed,
        "report.py unchanged": check_sink_unchanged,
        "no blanket except": check_no_blanket_except,
        "regression test added": check_regression_test_added,
    },
}
