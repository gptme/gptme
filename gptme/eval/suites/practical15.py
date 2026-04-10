"""Practical eval tests (batch 15) — async pipelines, advanced SQL, SQL injection fixes.

Tests capabilities not covered by earlier practical suites:
- Async/await concurrent task processing with asyncio (concurrency)
- Advanced SQL analytics with JOINs, GROUP BY, subqueries (database)
- SQL injection detection and parameterized-query remediation (security)
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- async-pipeline checks ---

_TASKS_JSON = json.dumps(
    [
        {"name": "compress", "items": ["a", "b", "c"], "delay": 0.3},
        {"name": "upload", "items": ["x", "y"], "delay": 0.5},
        {"name": "notify", "items": ["m1"], "delay": 0.1},
    ],
    indent=2,
)


def check_async_file(ctx):
    """async_pipeline.py should exist."""
    return "async_pipeline.py" in ctx.files


def check_async_uses_asyncio(ctx):
    """Script must import asyncio."""
    src = ctx.files.get("async_pipeline.py", "")
    return "import asyncio" in src


def check_async_uses_await(ctx):
    """Script must use async/await syntax."""
    src = ctx.files.get("async_pipeline.py", "")
    return "await " in src and "async " in src


def check_async_all_tasks_complete(ctx):
    """Output should mention all three task names completing."""
    out = ctx.stdout.lower()
    return "compress" in out and "upload" in out and "notify" in out


def check_async_total_items(ctx):
    """Output should report 6 total items processed (3+2+1)."""
    return bool(
        re.search(
            r"\b6\b.*\bitem|\bitem.*\b6\b|\btotal.*\b6\b|\b6\b.*\bprocess",
            ctx.stdout,
            re.IGNORECASE,
        )
    )


def check_async_concurrent(ctx):
    """Script should use asyncio.gather or create_task for concurrent execution."""
    src = ctx.files.get("async_pipeline.py", "")
    return bool(re.search(r"asyncio\.(gather|create_task)", src))


def check_async_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- sql-analytics checks ---

_SETUP_SQL = """\
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER NOT NULL,
    order_date TEXT NOT NULL
);

INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob'), (3, 'Carol');
INSERT INTO products VALUES (1, 'Widget', 9.99), (2, 'Gadget', 24.99), (3, 'Doohickey', 4.99);
INSERT INTO orders VALUES
    (1, 1, 1, 5, '2024-01-15'),
    (2, 1, 2, 2, '2024-01-20'),
    (3, 2, 1, 10, '2024-02-01'),
    (4, 2, 1, 3, '2024-02-15'),
    (5, 1, 1, 1, '2024-03-01');
"""


def check_sql_file(ctx):
    """analytics.py should exist."""
    return "analytics.py" in ctx.files


def check_sql_revenue_widget(ctx):
    """Widget revenue: (5+10+3+1)*9.99 = 189.81."""
    return bool(re.search(r"189\.81", ctx.stdout))


def check_sql_revenue_gadget(ctx):
    """Gadget revenue: 2*24.99 = 49.98."""
    return bool(re.search(r"49\.98", ctx.stdout))


def check_sql_top_customer(ctx):
    """Top customer by total spend should be Bob (10+3)*9.99 = 129.87 or Alice (5+1)*9.99+2*24.99 = 109.92.

    Actually: Alice = 5*9.99 + 2*24.99 + 1*9.99 = 49.95+49.98+9.99 = 109.92
              Bob   = 10*9.99 + 3*9.99 = 99.90+29.97 = 129.87
    Bob is top customer."""
    # Check that Bob appears as top customer
    lines = ctx.stdout.lower().splitlines()
    for line in lines:
        if "bob" in line and re.search(r"(top|highest|most|1[st]*\b)", line):
            return True
    # Also accept if Bob's total appears near "top" heading
    out = ctx.stdout.lower()
    return "bob" in out and "129.87" in out


def check_sql_never_ordered(ctx):
    """Doohickey (product 3) was never ordered."""
    return bool(re.search(r"\bdoohickey\b", ctx.stdout, re.IGNORECASE))


def check_sql_uses_join(ctx):
    """Script should use JOIN in queries."""
    src = ctx.files.get("analytics.py", "")
    return bool(re.search(r"\bJOIN\b", src, re.IGNORECASE))


def check_sql_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- fix-sql-injection checks ---

_VULNERABLE_PY = '''\
import sqlite3

def get_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, role TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'admin', 'admin')")
    conn.execute("INSERT INTO users VALUES (2, 'alice', 'user')")
    conn.execute("INSERT INTO users VALUES (3, 'bob', 'user')")
    conn.commit()
    return conn

def find_user(conn, username):
    """VULNERABLE: SQL injection via string formatting."""
    query = "SELECT * FROM users WHERE name = '%s'" % username
    return conn.execute(query).fetchall()

def find_by_role(conn, role):
    """VULNERABLE: SQL injection via f-string."""
    query = f"SELECT * FROM users WHERE role = '{role}'"
    return conn.execute(query).fetchall()

def delete_user(conn, user_id):
    """VULNERABLE: SQL injection via concatenation."""
    query = "DELETE FROM users WHERE id = " + str(user_id)
    conn.execute(query)
    conn.commit()

if __name__ == "__main__":
    conn = get_db()
    print("Find admin:", find_user(conn, "admin"))
    print("Find users:", find_by_role(conn, "user"))
    delete_user(conn, 3)
    print("After delete:", conn.execute("SELECT * FROM users").fetchall())
'''


def check_injection_file(ctx):
    """secure_app.py should exist."""
    return "secure_app.py" in ctx.files


def check_injection_no_percent_format(ctx):
    """Fixed code should not use % string formatting for SQL."""
    src = ctx.files.get("secure_app.py", "")
    # Should not have any % format specifiers (%s, %d, %r, etc.) in SQL strings
    return not bool(re.search(r"""['"][^'"]*%[sdrifge][^'"]*['"]""", src))


def check_injection_no_fstring(ctx):
    """Fixed code should not use f-strings for SQL queries."""
    src = ctx.files.get("secure_app.py", "")
    # Look for f-string SQL patterns — match f"...WHERE..." or f'...WHERE...'
    return not bool(re.search(r'f["\'].*(?:SELECT|INSERT|UPDATE|DELETE|WHERE)', src))


def check_injection_no_concat(ctx):
    """Fixed code should not use string concatenation for SQL."""
    src = ctx.files.get("secure_app.py", "")
    # Should not have query = "..." + str(...)
    return not bool(re.search(r'"\s*\+\s*str\(', src))


def check_injection_uses_params(ctx):
    """Fixed code should use parameterized queries (? placeholders)."""
    src = ctx.files.get("secure_app.py", "")
    return src.count("?") >= 3  # At least 3 parameterized queries


def check_injection_still_works(ctx):
    """Fixed code should still produce correct output."""
    out = ctx.stdout
    return "admin" in out and "alice" in out


def check_injection_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "async-pipeline",
        "files": {"tasks.json": _TASKS_JSON},
        "run": "python async_pipeline.py",
        "prompt": (
            "Write a Python script `async_pipeline.py` that reads tasks from "
            "`tasks.json`. Each task has a 'name', a list of 'items', and a "
            "'delay' (seconds per item). Process ALL tasks concurrently using "
            "asyncio: for each task, iterate its items with an `await asyncio.sleep(delay)` "
            "per item. Print a line when each task completes (e.g. 'compress: 3 items done'). "
            "After all tasks finish, print the total number of items processed. "
            "Use `asyncio.gather` or `asyncio.create_task` for concurrency."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "async_pipeline.py exists": check_async_file,
            "imports asyncio": check_async_uses_asyncio,
            "uses async/await": check_async_uses_await,
            "all tasks complete": check_async_all_tasks_complete,
            "6 total items": check_async_total_items,
            "concurrent execution": check_async_concurrent,
            "clean exit": check_async_exit,
        },
    },
    {
        "name": "sql-analytics",
        "files": {"setup.sql": _SETUP_SQL},
        "run": "python analytics.py",
        "prompt": (
            "Write a Python script `analytics.py` that creates a SQLite in-memory "
            "database, runs the SQL in `setup.sql` to create and populate tables "
            "(customers, products, orders), then prints three analytics reports:\n"
            "1. **Revenue per product**: For each product, show name and total revenue "
            "(price × total quantity ordered). Use a JOIN between orders and products.\n"
            "2. **Top customer**: Show the customer name with the highest total spend "
            "and their total. Use JOINs across all three tables.\n"
            "3. **Never ordered**: List product names that have zero orders. "
            "Use a subquery or LEFT JOIN to find them.\n"
            "Print clear section headers and format currency to 2 decimal places."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "analytics.py exists": check_sql_file,
            "Widget revenue 189.81": check_sql_revenue_widget,
            "Gadget revenue 49.98": check_sql_revenue_gadget,
            "Bob is top customer": check_sql_top_customer,
            "Doohickey never ordered": check_sql_never_ordered,
            "uses JOIN": check_sql_uses_join,
            "clean exit": check_sql_exit,
        },
    },
    {
        "name": "fix-sql-injection",
        "files": {"vulnerable_app.py": _VULNERABLE_PY},
        "run": "python secure_app.py",
        "prompt": (
            "The file `vulnerable_app.py` contains three SQL injection vulnerabilities: "
            "one using % string formatting, one using an f-string, and one using "
            "string concatenation. Create `secure_app.py` — a fixed version that "
            "replaces ALL vulnerable query construction with parameterized queries "
            "(using ? placeholders and passing parameters as tuples to execute()). "
            "The fixed script must produce the same functional output as the original."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "secure_app.py exists": check_injection_file,
            "no % formatting": check_injection_no_percent_format,
            "no f-string SQL": check_injection_no_fstring,
            "no string concat SQL": check_injection_no_concat,
            "uses ? params": check_injection_uses_params,
            "still works correctly": check_injection_still_works,
            "clean exit": check_injection_exit,
        },
    },
]
