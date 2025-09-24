"""
Simplified task system for prompt optimization using builders and templates.

Replaces the massive repetitive task definitions with a more maintainable approach.
"""

from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from ..types import EvalSpec, Files, ResultContext


@dataclass
class TaskBuilder:
    """Builder for creating evaluation tasks with common patterns."""

    name: str = ""
    files: Files = field(default_factory=dict)
    run_cmd: str = ""
    prompt: str = ""
    tools: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    expectations: dict[str, Callable[[ResultContext], bool]] = field(
        default_factory=dict
    )

    def with_name(self, name: str) -> "TaskBuilder":
        """Set task name."""
        self.name = name
        return self

    def with_files(self, files: dict[str, str]) -> "TaskBuilder":
        """Add files to the task."""
        self.files.update(files)
        return self

    def with_file(self, name: str, content: str) -> "TaskBuilder":
        """Add a single file."""
        self.files[name] = content
        return self

    def with_run(self, cmd: str) -> "TaskBuilder":
        """Set run command."""
        self.run_cmd = cmd
        return self

    def with_prompt(self, prompt: str) -> "TaskBuilder":
        """Set task prompt."""
        self.prompt = prompt
        return self

    def with_tools(self, tools: list[str]) -> "TaskBuilder":
        """Set required tools."""
        self.tools = tools
        return self

    def with_focus(self, areas: list[str]) -> "TaskBuilder":
        """Set focus areas."""
        self.focus_areas = areas
        return self

    def with_expectation(self, name: str, check: Callable) -> "TaskBuilder":
        """Add an expectation."""
        self.expectations[name] = check
        return self

    def expect_file_exists(self, filename: str) -> "TaskBuilder":
        """Expect a file to be created."""
        return self.with_expectation(
            f"file_{filename}_exists", lambda ctx: filename in ctx.files
        )

    def expect_output_contains(self, text: str) -> "TaskBuilder":
        """Expect output to contain specific text."""
        return self.with_expectation(
            f"output_contains_{text[:20]}", lambda ctx: text in ctx.stdout
        )

    def expect_success(self) -> "TaskBuilder":
        """Expect successful execution."""
        return self.with_expectation(
            "successful_execution", lambda ctx: ctx.returncode == 0
        )

    def build(self) -> EvalSpec:
        """Build the final task specification."""
        eval_spec: EvalSpec = {
            "name": self.name,
            "files": self.files,
            "run": self.run_cmd,
            "prompt": self.prompt,
            "expect": self.expectations,
        }

        # Add tools if specified
        if self.tools:
            eval_spec["tools"] = self.tools

        return eval_spec

    def build_with_metadata(self) -> tuple[EvalSpec, dict[str, Any]]:
        """Build EvalSpec with additional metadata like focus_areas."""
        eval_spec = self.build()
        metadata = {
            "focus_areas": self.focus_areas,
        }
        return eval_spec, metadata


class TaskTemplates:
    """Common task templates to reduce repetition."""

    @staticmethod
    def hello_world_task() -> TaskBuilder:
        """Basic hello world file creation task."""
        return (
            TaskBuilder()
            .with_name("hello-world-basic")
            .with_prompt(
                'Create a Python file called hello.py that prints "Hello, World!"'
            )
            .with_run("python hello.py")
            .with_tools(["save"])
            .with_focus(["file_creation", "basic_programming"])
            .expect_file_exists("hello.py")
            .expect_output_contains("Hello, World!")
            .expect_success()
        )

    @staticmethod
    def debug_task(filename: str, broken_content: str, description: str) -> TaskBuilder:
        """Template for debugging tasks."""
        return (
            TaskBuilder()
            .with_file(filename, broken_content)
            .with_run(f"python {filename}")
            .with_prompt(f"Fix the errors in {filename}. {description}")
            .with_tools(["read", "patch", "shell"])
            .with_focus(["debugging", "error_analysis"])
        )

    @staticmethod
    def tool_usage_task(tool_name: str, task_desc: str) -> TaskBuilder:
        """Template for testing specific tool usage."""
        return (
            TaskBuilder()
            .with_prompt(task_desc)
            .with_tools([tool_name])
            .with_focus(["tool_selection", f"{tool_name}_usage"])
        )

    @staticmethod
    def multi_file_project(base_name: str) -> TaskBuilder:
        """Template for multi-file projects."""
        return (
            TaskBuilder()
            .with_focus(["multi_file_coordination", "project_structure"])
            .with_tools(["save", "patch", "read", "shell"])
        )


# Global registry to track task metadata
_task_metadata: dict[str, dict[str, Any]] = {}


def create_essential_tasks() -> list[EvalSpec]:
    """Create a curated set of essential tasks covering key areas."""

    tasks = []

    # Basic file operations
    spec, metadata = TaskTemplates.hello_world_task().build_with_metadata()
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Tool selection - patch vs save
    spec, metadata = (
        TaskBuilder()
        .with_name("patch-vs-save")
        .with_file("greeting.py", 'print("Hi there")')
        .with_run("python greeting.py")
        .with_prompt('Change greeting.py to print "Hello, World!" instead')
        .with_tools(["save", "patch"])
        .with_focus(["tool_selection", "incremental_changes"])
        .expect_output_contains("Hello, World!")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Debugging task
    spec, metadata = (
        TaskBuilder()
        .with_name("debug-undefined-variable")
        .with_file("broken.py", "print('Hello World')\nprint(undeclared_variable)")
        .with_run("python broken.py")
        .with_prompt("Fix the undefined variable error in broken.py")
        .with_tools(["read", "patch", "shell"])
        .with_focus(["debugging", "error_analysis"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Multi-step reasoning
    spec, metadata = (
        TaskBuilder()
        .with_name("fibonacci-sequence")
        .with_prompt(
            "Create a program that calculates and prints the first 10 Fibonacci numbers"
        )
        .with_run("python fibonacci.py")
        .with_tools(["save", "shell"])
        .with_focus(["algorithmic_thinking", "problem_decomposition"])
        .expect_file_exists("fibonacci.py")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Error handling
    spec, metadata = (
        TaskBuilder()
        .with_name("command-error-handling")
        .with_prompt(
            "Run 'nonexistent_command --help' and create a script that handles the error gracefully"
        )
        .with_run("python error_handler.py")
        .with_tools(["shell", "save"])
        .with_focus(["error_recovery", "alternative_solutions"])
        .expect_file_exists("error_handler.py")
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Research and implementation
    spec, metadata = (
        TaskBuilder()
        .with_name("research-simple-cache")
        .with_file(
            "slow_calc.py",
            """
def expensive_calculation(n):
    import time
    time.sleep(0.1)  # Simulate slow operation
    return n * n

if __name__ == "__main__":
    print(expensive_calculation(5))
    print(expensive_calculation(5))  # Same calculation
""",
        )
        .with_prompt(
            "Research caching strategies and add simple caching to improve performance"
        )
        .with_run("python slow_calc.py")
        .with_tools(["read", "patch", "browser", "shell"])
        .with_focus(["web_research", "performance_optimization", "caching_strategies"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    return tasks


def create_advanced_tasks() -> list[EvalSpec]:
    """Create advanced multi-step tasks for comprehensive evaluation."""

    tasks = []

    # Complex debugging project
    calculator_files = {
        "calculator.py": """#!/usr/bin/env python3
import sys
from utility import calculate_average  # Wrong import name

def main()  # Missing colon
    numbers = [1, 2, 3, 4, 5]
    print(f"Average: {calculate_average(numbers)}")

if __name__ == "__main__":
    main()
""",
        "utils.py": """def calculate_average(numbers):
    # Bug: dividing by wrong value
    return sum(numbers) / (len(numbers) + 1)
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("multi-bug-calculator")
        .with_files(calculator_files)
        .with_run("python calculator.py")
        .with_prompt("Fix all the bugs in this calculator project so it runs correctly")
        .with_tools(["read", "patch", "shell"])
        .with_focus(
            ["multi_step_debugging", "systematic_analysis", "file_coordination"]
        )
        .expect_success()
        .expect_output_contains("Average: 3.0")
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    return tasks


def get_prompt_optimization_tasks() -> list[EvalSpec]:
    """Get all tasks for prompt optimization."""
    tasks = []
    tasks.extend(create_essential_tasks())
    tasks.extend(create_advanced_tasks())
    return tasks


def get_tasks_by_focus_area(focus_area: str) -> list[EvalSpec]:
    """Get tasks that focus on a specific area."""
    all_tasks = get_prompt_optimization_tasks()
    filtered_tasks = []

    for task in all_tasks:
        task_name = task["name"]
        metadata = _task_metadata.get(task_name, {})
        focus_areas = metadata.get("focus_areas", [])

        if focus_area in focus_areas:
            filtered_tasks.append(task)

    return filtered_tasks


def analyze_task_coverage() -> dict[str, list[str]]:
    """Analyze what focus areas are covered by the available tasks."""
    all_tasks = get_prompt_optimization_tasks()
    coverage: dict[str, list[str]] = {}

    for task in all_tasks:
        task_name = task["name"]
        metadata = _task_metadata.get(task_name, {})
        focus_areas = metadata.get("focus_areas", [])

        for area in focus_areas:
            if area not in coverage:
                coverage[area] = []
            coverage[area].append(task_name)

    return coverage


def get_task_metadata(task_name: str) -> dict[str, Any]:
    """Get metadata for a specific task."""
    return _task_metadata.get(task_name, {})


if __name__ == "__main__":
    # Print task coverage analysis
    print("=== Simplified Task System Coverage ===")
    coverage = analyze_task_coverage()

    for area, tasks in sorted(coverage.items()):
        print(f"\n{area}:")
        for task in tasks:
            print(f"  - {task}")

    print(f"\nTotal tasks: {len(get_prompt_optimization_tasks())}")
    print(f"Focus areas covered: {len(coverage)}")
