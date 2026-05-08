"""Behavioral scenario: add-logging."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_processor_source(ctx) -> str:
    content = ctx.files.get("processor.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_logging_tests_pass(ctx):
    """Tests should pass — function behavior must be unchanged."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_logging_module_imported(ctx):
    """The logging module should be imported in processor.py."""
    content = _get_processor_source(ctx)
    return "import logging" in content or "from logging import" in content


def check_logging_error_level_used(ctx):
    """logging.error/exception/warning used for failure cases (direct or via named logger)."""
    content = _get_processor_source(ctx)
    return bool(
        re.search(
            r"\blogging\.(error|exception|warning)\s*\("  # logging.error(
            r"|"
            r"\b\w+\.(error|exception|warning)\s*\(",  # logger.error(
            content,
        )
    )


def check_logging_debug_or_info_used(ctx):
    """logging.debug/info used for the success path (direct or via named logger)."""
    content = _get_processor_source(ctx)
    return bool(
        re.search(
            r"\blogging\.(debug|info)\s*\("  # logging.debug(
            r"|"
            r"\b\w+\.(debug|info)\s*\(",  # logger.debug(
            content,
        )
    )


def check_logging_no_print(ctx):
    """print() should not be used — the prompt explicitly requires the logging module."""
    return "print(" not in _get_processor_source(ctx)


test: "EvalSpec" = {
    "name": "add-logging",
    "task_type": "structured_process",
    "files": {
        "processor.py": """\
\"\"\"Data record processor.\"\"\"


def process_record(record):
    \"\"\"Process a single data record.

    Returns True on success, None if the record is invalid or negative.
    \"\"\"
    try:
        value = int(record["value"])
        if value < 0:
            return None
        record["processed"] = value * 2
        return True
    except (KeyError, ValueError):
        return None
""",
        "test_processor.py": """\
from processor import process_record


def test_valid_record():
    record = {"value": "5"}
    result = process_record(record)
    assert result is True
    assert record["processed"] == 10


def test_invalid_value():
    record = {"value": "not_a_number"}
    result = process_record(record)
    assert result is None


def test_negative_value():
    record = {"value": "-3"}
    result = process_record(record)
    assert result is None


def test_missing_key():
    record = {}
    result = process_record(record)
    assert result is None
""",
    },
    "run": "python3 -m pytest test_processor.py -v --tb=short 2>&1",
    "prompt": (
        "Add appropriate logging to `process_record` in `processor.py` "
        "using Python's `logging` module — do not use `print`. "
        "Use ERROR level when a record is invalid (missing key or bad value) "
        "or has a negative value, and DEBUG level when processing succeeds. "
        "Do not change the function's return values or overall behaviour — "
        "all existing tests must still pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_logging_tests_pass,
        "logging module imported": check_logging_module_imported,
        "error level used for failures": check_logging_error_level_used,
        "no print statements": check_logging_no_print,
        "debug/info level used for success": check_logging_debug_or_info_used,
    },
}
