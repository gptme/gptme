"""Practical eval tests (batch 4) — analytics, scheduling, and algorithms.

Tests capabilities not covered by earlier practical suites:
- Grouping and aggregating structured JSON data
- Detecting schedule conflicts (interval overlap with datetime parsing)
- Topological sorting of dependency graphs
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- group-by checks ---


def check_groupby_file(ctx):
    """report.py should exist."""
    return "report.py" in ctx.files


def check_groupby_electronics(ctx):
    """Electronics total should be 1200 (500 + 700)."""
    return "1200" in ctx.stdout


def check_groupby_clothing(ctx):
    """Clothing total should be 550 (200 + 350)."""
    return "550" in ctx.stdout


def check_groupby_food(ctx):
    """Food total should be 180 (80 + 100)."""
    return "180" in ctx.stdout


def check_groupby_exit(ctx):
    return ctx.exit_code == 0


# --- schedule-overlaps checks ---


def check_schedule_file(ctx):
    """check_schedule.py should exist."""
    return "check_schedule.py" in ctx.files


def check_schedule_finds_meeting_a(ctx):
    """Meeting A should be reported as part of an overlap."""
    return bool(re.search(r"meeting\s*a", ctx.stdout, re.IGNORECASE))


def check_schedule_finds_meeting_b(ctx):
    """Meeting B should be reported as part of an overlap."""
    return bool(re.search(r"meeting\s*b", ctx.stdout, re.IGNORECASE))


def check_schedule_exit(ctx):
    return ctx.exit_code == 0


# --- topo-sort checks ---


def check_topo_file(ctx):
    """topo_sort.py should exist."""
    return "topo_sort.py" in ctx.files


def check_topo_all_tasks(ctx):
    """All 6 tasks (A–F) should appear in the output."""
    return all(re.search(rf"\b{task}\b", ctx.stdout) for task in "ABCDEF")


def check_topo_order_valid(ctx):
    """Each task's dependencies must appear earlier in the output.

    Graph: B->A, C->A, D->B, D->C, E->D, F->E
    """
    lines = [line.strip() for line in ctx.stdout.splitlines() if line.strip()]
    # Find first line index where each task appears
    positions: dict[str, int] = {}
    for i, line in enumerate(lines):
        for task in "ABCDEF":
            if task not in positions and re.search(rf"\b{task}\b", line):
                positions[task] = i

    if len(positions) < 6:
        return False

    # deps[task] = list of tasks that must come BEFORE task
    deps = {"B": ["A"], "C": ["A"], "D": ["B", "C"], "E": ["D"], "F": ["E"]}
    for task, prerequisites in deps.items():
        for prereq in prerequisites:
            if positions.get(prereq, 999) >= positions.get(task, -1):
                return False
    return True


def check_topo_exit(ctx):
    return ctx.exit_code == 0


# --- data ---

_SALES_JSON = json.dumps(
    [
        {
            "product": "Laptop",
            "category": "Electronics",
            "region": "North",
            "amount": 500,
        },
        {
            "product": "Phone",
            "category": "Electronics",
            "region": "South",
            "amount": 700,
        },
        {"product": "Shirt", "category": "Clothing", "region": "North", "amount": 200},
        {"product": "Jeans", "category": "Clothing", "region": "South", "amount": 350},
        {"product": "Bread", "category": "Food", "region": "North", "amount": 80},
        {"product": "Milk", "category": "Food", "region": "South", "amount": 100},
    ],
    indent=2,
)

_EVENTS_JSON = json.dumps(
    [
        {"name": "Meeting A", "start": "2024-01-15 10:00", "end": "2024-01-15 11:00"},
        {"name": "Meeting B", "start": "2024-01-15 10:30", "end": "2024-01-15 11:30"},
        {"name": "Lunch", "start": "2024-01-15 12:00", "end": "2024-01-15 13:00"},
        {"name": "Review", "start": "2024-01-15 14:00", "end": "2024-01-15 15:00"},
        {"name": "Team Sync", "start": "2024-01-15 13:00", "end": "2024-01-15 13:30"},
    ],
    indent=2,
)

_DEPS_JSON = json.dumps(
    {
        "A": [],
        "B": ["A"],
        "C": ["A"],
        "D": ["B", "C"],
        "E": ["D"],
        "F": ["E"],
    },
    indent=2,
)


tests: list["EvalSpec"] = [
    {
        "name": "group-by",
        "files": {"sales.json": _SALES_JSON},
        "run": "python report.py sales.json",
        "prompt": (
            "Write report.py that reads sales.json (a list of records with "
            "'product', 'category', 'region', and 'amount' fields) and prints "
            "total sales amount grouped by category, sorted by category name. "
            "The script should accept the filename as a command-line argument. "
            "Output one line per category in the format: "
            "'<Category>: <total>' (e.g. 'Electronics: 1200')."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "report.py exists": check_groupby_file,
            "Electronics total correct": check_groupby_electronics,
            "Clothing total correct": check_groupby_clothing,
            "Food total correct": check_groupby_food,
            "clean exit": check_groupby_exit,
        },
    },
    {
        "name": "schedule-overlaps",
        "files": {"events.json": _EVENTS_JSON},
        "run": "python check_schedule.py events.json",
        "prompt": (
            "Write check_schedule.py that reads events.json (a list of events "
            "with 'name', 'start', and 'end' fields in 'YYYY-MM-DD HH:MM' format) "
            "and prints any pairs of events that overlap in time. "
            "Two events overlap if one starts before the other ends AND the other "
            "starts before the first ends (touching at a single point is NOT an overlap). "
            "The script should accept the filename as a command-line argument. "
            "Print each overlapping pair on one line, e.g. "
            "'Meeting A and Meeting B overlap'. "
            "If no overlaps are found, print 'No overlaps found'."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "check_schedule.py exists": check_schedule_file,
            "Meeting A detected": check_schedule_finds_meeting_a,
            "Meeting B detected": check_schedule_finds_meeting_b,
            "clean exit": check_schedule_exit,
        },
    },
    {
        "name": "topo-sort",
        "files": {"deps.json": _DEPS_JSON},
        "run": "python topo_sort.py deps.json",
        "prompt": (
            "Write topo_sort.py that reads deps.json (a dict mapping task names to "
            "their list of prerequisite task names) and prints a valid execution order — "
            "one task name per line — such that every task appears only after all its "
            "prerequisites. The script should accept the filename as a command-line "
            "argument. If a cycle is detected, print 'Error: cycle detected' and exit "
            "with code 1."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "topo_sort.py exists": check_topo_file,
            "all tasks present": check_topo_all_tasks,
            "valid topological order": check_topo_order_valid,
            "clean exit": check_topo_exit,
        },
    },
]
