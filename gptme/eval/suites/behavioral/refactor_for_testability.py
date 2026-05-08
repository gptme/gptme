"""Behavioral scenario: refactor-for-testability."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_testability_tests_pass(ctx):
    """All tests should pass after the refactoring."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_testability_has_pure_function(ctx):
    """report.py should have a function that accepts data directly (not a filepath)."""
    content = ctx.files.get("report.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # A pure function takes rows/data, not a file path.  We look for a function
    # whose parameter list mentions rows, records, data, or items — NOT filepath.
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef):
            params = [a.arg for a in node.args.args]
            # Skip the I/O function (generate_report) which takes a path
            if "filepath" in params or "path" in params or "filename" in params:
                continue
            # A pure stats function should take structured data
            if any(
                p in params for p in ("rows", "records", "data", "items", "entries")
            ):
                return True
    return False


def check_testability_pure_function_tested(ctx):
    """test_report.py should call the pure computation function directly."""
    content = ctx.files.get("test_report.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Find the pure function name from report.py
    report_content = ctx.files.get("report.py", "")
    if isinstance(report_content, bytes):
        report_content = report_content.decode()
    module = parse_python_source(report_content)
    pure_names = []
    if module is not None:
        for node in ast.walk(module):
            if isinstance(node, ast.FunctionDef):
                params = [a.arg for a in node.args.args]
                if "filepath" not in params and "path" not in params:
                    if any(
                        p in params
                        for p in ("rows", "records", "data", "items", "entries")
                    ):
                        pure_names.append(node.name)
    if not pure_names:
        return False
    return any(name in content for name in pure_names)


def check_testability_generate_report_preserved(ctx):
    """generate_report function should still exist and use the extracted function."""
    content = ctx.files.get("report.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "generate_report" in content


def check_testability_no_file_io_in_unit_tests(ctx):
    """Unit tests for the pure function should not create/open files."""
    content = ctx.files.get("test_report.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # A well-factored test suite will have at least one test that does NOT
    # use tmp_path / NamedTemporaryFile / open( for testing the pure function.
    # We check that there exists a test function without file I/O calls.
    import re as _re

    test_fns = _re.findall(
        r"def (test_\w+)\(.*?\):(.*?)(?=\ndef |\Z)", content, _re.DOTALL
    )
    for _, body in test_fns:
        if (
            "tmp_path" not in body
            and "NamedTemporaryFile" not in body
            and "open(" not in body
        ):
            return True
    return False


test: "EvalSpec" = {
    "name": "refactor-for-testability",
    "task_type": "creative_restructuring",
    "files": {
        "report.py": """\
import csv


def generate_report(filepath):
    \"\"\"Generate summary statistics from a CSV file.

    Reads a CSV with columns: name, score, hours.
    Returns a dict with per-column stats: total, average, min, max.
    \"\"\"
    rows = []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return {"total_rows": 0}

    scores = [int(r["score"]) for r in rows]
    hours = [float(r["hours"]) for r in rows]

    return {
        "total_rows": len(rows),
        "score_total": sum(scores),
        "score_average": round(sum(scores) / len(scores), 2),
        "score_min": min(scores),
        "score_max": max(scores),
        "hours_total": round(sum(hours), 2),
        "hours_average": round(sum(hours) / len(hours), 2),
        "hours_min": min(hours),
        "hours_max": max(hours),
    }
""",
        "test_report.py": """\
# Tests for report.py
# Currently empty — the generate_report function reads files directly,
# making it hard to test without creating CSV fixtures.
""",
    },
    "run": "python3 -m pytest test_report.py -v --tb=short 2>&1",
    "prompt": (
        "The `generate_report` function in `report.py` reads a CSV file and "
        "computes summary statistics, but the computation is tightly coupled "
        "with file I/O inside a single function. This makes the statistics "
        "logic impossible to unit-test without creating real CSV files.\n\n"
        "Refactor `report.py` by extracting the pure statistics computation "
        "into a standalone function (e.g. `compute_stats`) that accepts a "
        "list of row dicts directly instead of a filepath. The original "
        "`generate_report` should still work by reading the file and "
        "delegating to the extracted function.\n\n"
        "Then write thorough tests in `test_report.py`: test the pure "
        "computation function with plain data (lists of dicts, no file I/O "
        "needed) covering normal data, empty input, single rows, and edge "
        "cases. Do NOT use `tmp_path` or file creation for testing the "
        "pure function — that's the whole point of the refactoring."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_testability_tests_pass,
        "has pure computation function": check_testability_has_pure_function,
        "pure function tested directly": check_testability_pure_function_tested,
        "generate_report preserved": check_testability_generate_report_preserved,
        "no file I/O in unit tests": check_testability_no_file_io_in_unit_tests,
    },
}
