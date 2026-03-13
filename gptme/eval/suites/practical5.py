"""Practical eval tests (batch 5) — refactoring, pipelines, and text processing.

Tests capabilities not covered by earlier practical suites:
- Function renaming across multiple files (imports, calls, and tests)
- Data transformation pipelines (filter → transform → aggregate)
- PII redaction with regex (emails, phone numbers, SSNs)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- multi-file-refactor checks ---


def check_refactor_utils(ctx):
    """utils.py should have calculate_total instead of calc_total."""
    content = ctx.files.get("utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    has_new = "def calculate_total" in content
    no_old = "def calc_total" not in content
    return has_new and no_old


def check_refactor_main(ctx):
    """main.py should import and call calculate_total, not calc_total."""
    content = ctx.files.get("main.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    has_new = "calculate_total" in content
    no_old = "calc_total" not in content
    return has_new and no_old


def check_refactor_tests(ctx):
    """test_utils.py should import and call calculate_total, not calc_total.

    Test function names (e.g. test_calc_total_simple) are out of scope per the prompt
    ("function definition, all imports, and all call sites"), so we only check that
    the import and call sites are updated.
    """
    content = ctx.files.get("test_utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    has_new = "calculate_total" in content
    no_old_import = "import calc_total" not in content
    no_old_calls = "calc_total(" not in content
    return has_new and no_old_import and no_old_calls


def check_refactor_output(ctx):
    """Program should still produce correct output after rename."""
    # calc_total([25*2, 50*1, 25*2]) = 150.0, with 10% tax = 165.0
    return "150" in ctx.stdout and "165" in ctx.stdout


def check_refactor_exit(ctx):
    return ctx.exit_code == 0


# --- data-pipeline checks ---


def check_pipeline_file(ctx):
    """pipeline.py should exist."""
    return "pipeline.py" in ctx.files


def check_pipeline_count(ctx):
    """Output should show 3 filtered employees (age >= 30 with 5+ years)."""
    return bool(re.search(r"\b3\b", ctx.stdout))


def check_pipeline_total_salary(ctx):
    """Total salary of senior employees: 75000 + 82000 + 91000 = 248000."""
    return "248000" in ctx.stdout


def check_pipeline_avg_experience(ctx):
    """Average experience of seniors: (8 + 12 + 15) / 3 = 11.667 (or 11.7)."""
    # Accept various reasonable roundings: 11.7, 11.67, 11.667, 11.6667, ...
    return bool(re.search(r"11\.(?:6[67]\d*|7\b)", ctx.stdout))


def check_pipeline_exit(ctx):
    return ctx.exit_code == 0


# --- regex-scrub checks ---


def check_scrub_file(ctx):
    """scrub.py should exist."""
    return "scrub.py" in ctx.files


def check_scrub_emails(ctx):
    """Emails should be redacted — no alice@example.com or bob@corp.net in output."""
    output = ctx.stdout
    return "alice@example.com" not in output and "bob@corp.net" not in output


def check_scrub_phones(ctx):
    """Phone numbers should be redacted — no 555-123-4567 or (555) 987-6543."""
    output = ctx.stdout
    return "555-123-4567" not in output and "(555) 987-6543" not in output


def check_scrub_ssns(ctx):
    """SSNs should be redacted — no 123-45-6789 or 987-65-4321."""
    output = ctx.stdout
    return "123-45-6789" not in output and "987-65-4321" not in output


def check_scrub_names_preserved(ctx):
    """Non-PII content should be preserved — names should still appear."""
    output = ctx.stdout
    return "Alice" in output and "Bob" in output


def check_scrub_exit(ctx):
    return ctx.exit_code == 0


# --- test data ---

_UTILS_PY = """\
def calc_total(items, tax_rate=0.0):
    \"\"\"Calculate total price of items with optional tax.\"\"\"
    subtotal = sum(item['price'] * item['quantity'] for item in items)
    return subtotal * (1 + tax_rate)


def format_currency(amount):
    \"\"\"Format amount as currency string.\"\"\"
    return f"${amount:.2f}"
"""

_MAIN_PY = """\
from utils import calc_total, format_currency

orders = [
    {'price': 25.0, 'quantity': 2},
    {'price': 50.0, 'quantity': 1},
    {'price': 25.0, 'quantity': 2},
]

total = calc_total(orders)
total_with_tax = calc_total(orders, tax_rate=0.1)
print(f"Subtotal: {format_currency(total)}")
print(f"With tax: {format_currency(total_with_tax)}")
print(f"Raw: {total}")
"""

_TEST_UTILS_PY = """\
from utils import calc_total, format_currency


def test_calc_total_simple():
    items = [{'price': 10.0, 'quantity': 1}]
    assert calc_total(items) == 10.0


def test_calc_total_with_tax():
    items = [{'price': 100.0, 'quantity': 1}]
    assert calc_total(items, tax_rate=0.1) == 110.0


def test_calc_total_empty():
    assert calc_total([]) == 0.0


def test_format_currency():
    assert format_currency(10.5) == "$10.50"
"""

_EMPLOYEES_JSON = """\
[
  {"name": "Alice", "age": 35, "department": "Engineering", "salary": 75000, "years_experience": 8},
  {"name": "Bob", "age": 28, "department": "Marketing", "salary": 55000, "years_experience": 3},
  {"name": "Charlie", "age": 42, "department": "Engineering", "salary": 82000, "years_experience": 12},
  {"name": "Diana", "age": 25, "department": "Marketing", "salary": 48000, "years_experience": 2},
  {"name": "Eve", "age": 38, "department": "Engineering", "salary": 91000, "years_experience": 15},
  {"name": "Frank", "age": 31, "department": "Sales", "salary": 62000, "years_experience": 4},
  {"name": "Grace", "age": 29, "department": "Sales", "salary": 58000, "years_experience": 6}
]
"""

_PII_TEXT = """\
Customer Report - Q4 2024

Contact Alice at alice@example.com or call 555-123-4567 for account details.
Her SSN on file is 123-45-6789.

Bob from the partner team can be reached at bob@corp.net.
His direct line is (555) 987-6543 and SSN 987-65-4321.

Meeting scheduled for January 15, 2025 at 10:00 AM.
Total outstanding balance: $4,250.00

Notes: Both Alice and Bob confirmed attendance for the Q1 review.
"""


tests: list["EvalSpec"] = [
    {
        "name": "rename-function",
        "files": {
            "utils.py": _UTILS_PY,
            "main.py": _MAIN_PY,
            "test_utils.py": _TEST_UTILS_PY,
        },
        "run": "python main.py",
        "prompt": (
            "Rename the function `calc_total` to `calculate_total` across the entire "
            "codebase. This function is defined in utils.py and used in main.py and "
            "test_utils.py. Update the function definition, all imports, and all call "
            "sites. Do not change any other logic — only rename `calc_total` to "
            "`calculate_total`. After renaming, the program should produce the same output."
        ),
        "tools": ["read", "save", "patch", "shell"],
        "expect": {
            "utils.py renamed": check_refactor_utils,
            "main.py updated": check_refactor_main,
            "test_utils.py updated": check_refactor_tests,
            "correct output": check_refactor_output,
            "clean exit": check_refactor_exit,
        },
    },
    {
        "name": "data-pipeline",
        "files": {"employees.json": _EMPLOYEES_JSON},
        "run": "python pipeline.py employees.json",
        "prompt": (
            "Write pipeline.py that reads employees.json and processes the data through "
            "a pipeline:\n"
            "1. Filter: keep only employees with age >= 30 AND years_experience >= 5\n"
            "2. Transform: add a 'seniority' field = 'senior' for experience >= 10, "
            "'mid' otherwise\n"
            "3. Aggregate: compute count, total salary, and average years_experience "
            "of the filtered employees\n\n"
            "The script should accept the filename as a command-line argument.\n"
            "Print output in this format:\n"
            "Filtered employees: <count>\n"
            "Total salary: <amount>\n"
            "Avg experience: <value> years"
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "pipeline.py exists": check_pipeline_file,
            "correct count": check_pipeline_count,
            "correct total salary": check_pipeline_total_salary,
            "correct avg experience": check_pipeline_avg_experience,
            "clean exit": check_pipeline_exit,
        },
    },
    {
        "name": "regex-scrub",
        "files": {"report.txt": _PII_TEXT},
        "run": "python scrub.py report.txt",
        "prompt": (
            "Write scrub.py that reads a text file and redacts personally identifiable "
            "information (PII), printing the scrubbed text to stdout. The script should "
            "accept the filename as a command-line argument.\n\n"
            "Redact the following PII types:\n"
            "- Email addresses → replace with [EMAIL]\n"
            "- US phone numbers (formats: 555-123-4567 or (555) 987-6543) → replace with [PHONE]\n"
            "- US Social Security Numbers (format: 123-45-6789) → replace with [SSN]\n\n"
            "Preserve all other text exactly as-is, including names, dates, and amounts."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "scrub.py exists": check_scrub_file,
            "emails redacted": check_scrub_emails,
            "phones redacted": check_scrub_phones,
            "SSNs redacted": check_scrub_ssns,
            "names preserved": check_scrub_names_preserved,
            "clean exit": check_scrub_exit,
        },
    },
]
