"""Interactive tutorial mode — learn gptme by doing.

Walks new users through 3 beginner tasks with guided prompts,
scaffolded hints, and automated completion validators.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Callable

README = """\
# Tutorial Demo

This is a sample project for the gptme tutorial.

## What is gptme?

gptme is a terminal-based AI assistant that can read files, write code,
run commands, and help you build software — from your terminal.

## Getting Started

Complete the tutorial tasks to learn the basics.
"""

BUGGY_SCRIPT = """\
# This script has a bug — can you find and fix it?
def greet(name):
    return f"Hello, {name}!"

names = ["Alice", "Bob", "Charlie"]
for i in range(len(names) + 1):  # off-by-one error
    print(greet(names[i]))
"""


@dataclass
class TutorialTask:
    title: str
    prompt: str
    hint: str
    validate_fn: Callable[[Path], tuple[bool, str]]


def _validate_summarize(tmpdir: Path) -> tuple[bool, str]:
    """README.md must exist; gptme's run is itself the completion signal."""
    if not (tmpdir / "README.md").exists():
        return False, "README.md missing from working directory"
    return True, "File read and summarized!"


def _validate_write_test(tmpdir: Path) -> tuple[bool, str]:
    """A test_*.py file containing a test function with an assertion must exist."""
    test_files = list(tmpdir.glob("test_*.py")) + list(tmpdir.glob("*_test.py"))
    if not test_files:
        return False, "No test file (test_*.py) found in the directory"
    content = test_files[0].read_text()
    if "def test_" not in content:
        return False, f"{test_files[0].name}: no test function (def test_…)"
    if "assert" not in content:
        return False, f"{test_files[0].name}: no assertions found"
    return True, f"Test file created: {test_files[0].name}"


def _validate_fix_bug(tmpdir: Path) -> tuple[bool, str]:
    """buggy.py must run without errors after the fix."""
    script = tmpdir / "buggy.py"
    if not script.exists():
        return False, "buggy.py not found"
    try:
        result = subprocess.run(
            ["python3", str(script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Script timed out (infinite loop?)"
    if result.returncode != 0:
        return False, f"Script still fails: {result.stderr.strip()[:120]}"
    return True, "Bug fixed — script runs successfully!"


TASKS: list[TutorialTask] = [
    TutorialTask(
        title="Summarize a file",
        prompt="Summarize the contents of README.md",
        hint='Try: gptme "Summarize the contents of README.md"',
        validate_fn=_validate_summarize,
    ),
    TutorialTask(
        title="Write a test",
        prompt=(
            "Write a pytest test for this function and save it to test_add.py:\n\n"
            "def add(a, b):\n"
            "    return a + b"
        ),
        hint='Try: gptme "Write a pytest test for add(a, b) and save to test_add.py"',
        validate_fn=_validate_write_test,
    ),
    TutorialTask(
        title="Fix a bug",
        prompt="Find and fix the bug in buggy.py so it runs without errors",
        hint='Try: gptme "Fix the bug in buggy.py"',
        validate_fn=_validate_fix_bug,
    ),
]


class TaskResult(Enum):
    COMPLETED = auto()
    SKIPPED = auto()
    QUIT = auto()


def _run_task(task: TutorialTask, tmpdir: Path, num: int, total: int) -> TaskResult:
    """Run one tutorial task interactively and return its outcome."""
    click.echo(f"\n{'=' * 55}")
    click.echo(f"Task {num}/{total}: {task.title}")
    click.echo(f"{'=' * 55}")
    click.echo(f"\nGoal:  {task.prompt!r}")
    click.echo(f"Hint:  {task.hint}\n")

    while True:
        click.echo("[Enter] Run with gptme  [s] Skip  [q] Quit: ", nl=False)
        choice = click.getchar()
        click.echo()

        if choice in ("q", "Q"):
            return TaskResult.QUIT
        if choice in ("s", "S"):
            click.echo("Skipping task.")
            return TaskResult.SKIPPED

        # Run gptme non-interactively in the tutorial directory
        click.echo(f'\n$ gptme --non-interactive "{task.prompt}"\n')
        result = subprocess.run(
            ["gptme", "--non-interactive", task.prompt],
            cwd=str(tmpdir),
            check=False,
        )
        if result.returncode != 0:
            click.echo(f"\n✗ gptme exited with status {result.returncode}.")
            click.echo("Try again (or press 's' to skip).\n")
            continue

        passed, message = task.validate_fn(tmpdir)
        if passed:
            click.echo(f"\n✓ {message}")
            return TaskResult.COMPLETED
        click.echo(f"\n✗ Not quite: {message}")
        click.echo("Try again (or press 's' to skip).\n")


@click.command("tutorial")
@click.option(
    "--task",
    "task_num",
    type=int,
    default=None,
    metavar="N",
    help="Run a specific task (1-3) instead of all.",
)
def main(task_num: int | None) -> None:
    """Interactive tutorial: learn gptme by doing.

    Walks you through 3 hands-on tasks that teach gptme's core capabilities:
    reading files, writing code, and fixing bugs. Each task runs gptme with a
    curated prompt, then checks whether the goal was achieved.

    \b
    Tasks:
      1. Summarize a file    — file reading and summarization
      2. Write a test        — code generation and file creation
      3. Fix a bug           — debugging and editing

    Run a specific task: gptme-tutorial --task 2
    """
    click.echo("\nWelcome to the gptme interactive tutorial!")
    click.echo("Complete 3 hands-on tasks to learn the basics.\n")

    if task_num is not None and (task_num < 1 or task_num > len(TASKS)):
        raise click.BadParameter(
            f"Must be between 1 and {len(TASKS)}.",
            param_hint="--task",
        )

    tasks = [TASKS[task_num - 1]] if task_num is not None else TASKS
    start_num = task_num or 1

    with tempfile.TemporaryDirectory(prefix="gptme-tutorial-") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        (tmpdir / "README.md").write_text(README)
        (tmpdir / "buggy.py").write_text(BUGGY_SCRIPT)

        completed = 0
        for i, task in enumerate(tasks, start=start_num):
            result = _run_task(task, tmpdir, i, len(TASKS))
            if result is TaskResult.QUIT:
                break
            if result is TaskResult.COMPLETED:
                completed += 1

    click.echo(f"\n{'=' * 55}")
    click.echo(f"Tutorial complete! {completed}/{len(tasks)} task(s) done.")
    click.echo("\nWhat's next?")
    click.echo("  gptme --help       See all options")
    click.echo("  gptme-doctor       Check your setup")
    click.echo("  https://gptme.org  Full documentation")
    click.echo(f"{'=' * 55}\n")
