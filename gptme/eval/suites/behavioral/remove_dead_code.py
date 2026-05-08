"""Behavioral scenario: remove-dead-code."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_dead_code_removed(ctx):
    """_batch_normalize function should be removed from utils.py."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def _batch_normalize(" not in content


def check_live_functions_intact(ctx):
    """parse_record, validate_record, and _normalize_value must still exist."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return (
        "def parse_record(" in content
        and "def validate_record(" in content
        and "def _normalize_value(" in content
    )


def check_dead_code_tests_pass(ctx):
    """All tests should still pass after removing dead code."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_processor_unchanged(ctx):
    """processor.py should not be modified — it doesn't use the dead function."""
    content = ctx.files.get("processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Should still import parse_record and have process_records
    return (
        "from utils import parse_record" in content
        and "def process_records(" in content
    )


test: "EvalSpec" = {
    "name": "remove-dead-code",
    "task_type": "structured_process",
    "files": {
        "utils.py": """\
\"\"\"Record processing utilities.\"\"\"


def parse_record(data: dict) -> dict:
    \"\"\"Parse and normalize a record from raw input.\"\"\"
    return {
        "id": data["id"],
        "value": _normalize_value(data.get("value", "")),
        "valid": validate_record(data),
    }


def validate_record(data: dict) -> bool:
    \"\"\"Return True if *data* contains the required fields.\"\"\"
    return "id" in data and "value" in data


def _normalize_value(value: str) -> str:
    \"\"\"Normalize a string value for storage.\"\"\"
    return value.strip().lower()


def _batch_normalize(items: list[str]) -> list[str]:
    \"\"\"Normalize a batch of string values.

    Originally used by the bulk-import pipeline, which was removed in v2.0.
    This function is no longer called anywhere in the codebase.
    \"\"\"
    return [_normalize_value(item) for item in items]
""",
        "processor.py": """\
\"\"\"High-level record processing pipeline.\"\"\"

from utils import parse_record


def process_records(raw_records: list[dict]) -> list[dict]:
    \"\"\"Parse and filter a list of raw records, keeping only valid ones.\"\"\"
    results = []
    for record in raw_records:
        parsed = parse_record(record)
        if parsed["valid"]:
            results.append(parsed)
    return results
""",
        "test_utils.py": """\
from utils import _normalize_value, parse_record, validate_record


def test_parse_record_basic():
    record = {"id": 1, "value": "  Hello World  "}
    result = parse_record(record)
    assert result["id"] == 1
    assert result["value"] == "hello world"
    assert result["valid"] is True


def test_parse_record_missing_value():
    record = {"id": 2}
    result = parse_record(record)
    assert result["value"] == ""
    assert result["valid"] is False


def test_validate_record_valid():
    assert validate_record({"id": 1, "value": "x"}) is True


def test_validate_record_missing_id():
    assert validate_record({"value": "x"}) is False


def test_validate_record_missing_value():
    assert validate_record({"id": 1}) is False


def test_normalize_value_strips_whitespace():
    assert _normalize_value("  hello  ") == "hello"


def test_normalize_value_lowercases():
    assert _normalize_value("UPPER") == "upper"


def test_normalize_value_empty():
    assert _normalize_value("") == ""
""",
    },
    "run": "python3 -m pytest test_utils.py -v --tb=short 2>&1",
    "prompt": (
        "The `utils.py` module contains dead code: a function that is no longer "
        "called anywhere in the codebase. Find it and remove it. "
        "Keep all other functions intact — `processor.py` and the tests must "
        "continue to work without modification."
    ),
    "tools": ["shell", "read", "patch"],
    "expect": {
        "dead code removed": check_dead_code_removed,
        "live functions intact": check_live_functions_intact,
        "tests pass": check_dead_code_tests_pass,
        "processor unchanged": check_processor_unchanged,
    },
}
