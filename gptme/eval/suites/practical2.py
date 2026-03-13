"""Practical2 eval suite — data processing, templates, and validation tasks."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- sort-and-filter checks ---


def check_sort_filter_file(ctx):
    return "sort_filter.py" in ctx.files


def check_sort_filter_alice(ctx):
    """Alice (age 30, eng) should appear in output."""
    return "Alice" in ctx.stdout


def check_sort_filter_diana(ctx):
    """Diana (age 28, eng) should appear — boundary value for age >= 28."""
    return "Diana" in ctx.stdout


def check_sort_filter_bob_absent(ctx):
    """Bob (age 25) should be filtered out (age < 28)."""
    return "Bob" not in ctx.stdout


def check_sort_filter_order(ctx):
    """Engineering should come before Sales alphabetically; within eng, Alice before Diana."""
    lines = [line.strip() for line in ctx.stdout.splitlines() if line.strip()]
    eng_indices = [i for i, line in enumerate(lines) if "eng" in line.lower()]
    sales_indices = [i for i, line in enumerate(lines) if "sales" in line.lower()]
    if not eng_indices or not sales_indices:
        return False
    # All eng lines before all sales lines
    if max(eng_indices) >= min(sales_indices):
        return False
    # Within eng: Alice (index lower) before Diana (index higher)
    alice_idx = next(
        (i for i, line in enumerate(lines) if "alice" in line.lower()), None
    )
    diana_idx = next(
        (i for i, line in enumerate(lines) if "diana" in line.lower()), None
    )
    if alice_idx is None or diana_idx is None:
        return False
    return alice_idx < diana_idx


def check_sort_filter_exit(ctx):
    return ctx.exit_code == 0


# --- template-fill checks ---


def check_template_fill_file(ctx):
    return "fill_template.py" in ctx.files


def check_template_fill_name(ctx):
    """Output should contain the customer name."""
    return "Alice" in ctx.stdout


def check_template_fill_order_id(ctx):
    """Output should contain the order ID."""
    return "ORD-2024-001" in ctx.stdout


def check_template_fill_total(ctx):
    """Output should contain the formatted total price."""
    return "59.97" in ctx.stdout


def check_template_fill_exit(ctx):
    return ctx.exit_code == 0


# --- validate-csv checks ---


def check_validate_file(ctx):
    return "validate.py" in ctx.files


def _row_line(stdout: str, row_num: int) -> str | None:
    """Return the lowercased output line that reports on the given row number, or None."""
    prefix = f"row {row_num}:"
    for line in stdout.lower().splitlines():
        if line.lstrip().startswith(prefix):
            return line
    return None


def check_validate_row1_ok(ctx):
    """Row 1 (Alice, valid data) should be reported as OK."""
    line = _row_line(ctx.stdout, 1)
    return line is not None and "ok" in line


def check_validate_row2_email(ctx):
    """Row 2 has an invalid email (missing @) — should report an email error."""
    line = _row_line(ctx.stdout, 2)
    return line is not None and "email" in line


def check_validate_row3_name(ctx):
    """Row 3 has an empty name — should report a name/missing error."""
    line = _row_line(ctx.stdout, 3)
    return line is not None and ("name" in line or "missing" in line)


def check_validate_row4_age(ctx):
    """Row 4 has age -5 (out of range) — should report an age error."""
    line = _row_line(ctx.stdout, 4)
    return line is not None and "age" in line


def check_validate_exit(ctx):
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "sort-and-filter",
        "files": {
            "employees.json": (
                "[\n"
                '  {"name": "Alice", "age": 30, "dept": "eng"},\n'
                '  {"name": "Bob", "age": 25, "dept": "sales"},\n'
                '  {"name": "Carol", "age": 35, "dept": "sales"},\n'
                '  {"name": "Diana", "age": 28, "dept": "eng"},\n'
                '  {"name": "Eve", "age": 22, "dept": "eng"}\n'
                "]\n"
            ),
        },
        "run": "python sort_filter.py",
        "prompt": (
            "Read employees.json and write sort_filter.py that:\n"
            "1. Filters employees whose age is 28 or older\n"
            "2. Sorts the filtered list by department (alphabetically), "
            "then by name (alphabetically) within each department\n"
            "3. Prints each employee as 'Name (dept): age', one per line"
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "file created": check_sort_filter_file,
            "alice in output": check_sort_filter_alice,
            "diana in output (boundary age=28)": check_sort_filter_diana,
            "bob filtered out": check_sort_filter_bob_absent,
            "correct sort order": check_sort_filter_order,
            "clean exit": check_sort_filter_exit,
        },
    },
    {
        "name": "template-fill",
        "files": {
            "template.txt": (
                "Dear {name},\n"
                "\n"
                "Your order #{order_id} has been confirmed.\n"
                "Items: {items}\n"
                "Total: ${total:.2f}\n"
                "\n"
                "Thank you for shopping with us!\n"
            ),
            "order.json": (
                "{\n"
                '  "name": "Alice",\n'
                '  "order_id": "ORD-2024-001",\n'
                '  "items": "3x Widget",\n'
                '  "total": 59.97\n'
                "}\n"
            ),
        },
        "run": "python fill_template.py",
        "prompt": (
            "Read template.txt and order.json. "
            "Write fill_template.py that loads the order data from the JSON file, "
            "fills in the template using the order fields, and prints the result. "
            "The template uses {name}, {order_id}, {items}, and {total:.2f} as placeholders."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "file created": check_template_fill_file,
            "name in output": check_template_fill_name,
            "order id in output": check_template_fill_order_id,
            "total formatted": check_template_fill_total,
            "clean exit": check_template_fill_exit,
        },
    },
    {
        "name": "validate-csv",
        "files": {
            "users.csv": (
                "id,name,email,age\n"
                "1,Alice,alice@example.com,25\n"
                "2,Bob,bob-no-at-sign,30\n"
                "3,,carol@example.com,17\n"
                "4,Dave,dave@example.com,-5\n"
            ),
        },
        "run": "python validate.py",
        "prompt": (
            "Read users.csv. Write validate.py that checks each data row "
            "(skip the header) against these rules:\n"
            "- name must be non-empty\n"
            "- email must contain '@'\n"
            "- age must be between 0 and 120 (inclusive)\n"
            "For each row print 'Row N: OK' if valid, or "
            "'Row N: INVALID (reason)' listing the first failing rule. "
            "Use the row number as it appears in the file (row 1 = first data row)."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "file created": check_validate_file,
            "row 1 ok": check_validate_row1_ok,
            "row 2 email error": check_validate_row2_email,
            "row 3 name error": check_validate_row3_name,
            "row 4 age error": check_validate_row4_age,
            "clean exit": check_validate_exit,
        },
    },
]
