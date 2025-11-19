"""
Simplified task system for prompt optimization using builders and templates.

Replaces the massive repetitive task definitions with a more maintainable approach.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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

    # Phase 1 Basic Tasks (6 new tasks)

    # 1. simple-data-processing
    spec, metadata = (
        TaskBuilder()
        .with_name("simple-data-processing")
        .with_file(
            "data.csv", "name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago"
        )
        .with_prompt(
            "Parse data.csv, filter entries where age > 28, and save results to filtered.csv"
        )
        .with_run("python process.py && cat filtered.csv")
        .with_tools(["read", "save", "python", "shell"])
        .with_focus(["file_io", "data_manipulation"])
        .expect_file_exists("filtered.csv")
        .expect_file_exists("process.py")
        .expect_output_contains("Alice")
        .expect_output_contains("Charlie")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 2. git-workflow-basic
    spec, metadata = (
        TaskBuilder()
        .with_name("git-workflow-basic")
        .with_prompt(
            "Initialize a git repository, create a file called README.md with 'Hello Git', and commit it"
        )
        .with_run("git log --oneline && cat README.md")
        .with_tools(["shell", "save"])
        .with_focus(["version_control", "git_basics"])
        .expect_file_exists("README.md")
        .expect_output_contains("Hello Git")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 3. json-api-mock
    spec, metadata = (
        TaskBuilder()
        .with_name("json-api-mock")
        .with_prompt(
            "Create a simple Flask API with a /hello endpoint that returns JSON {'message': 'Hello, World!'}"
        )
        .with_run("python api.py &\nsleep 2\ncurl http://localhost:5000/hello")
        .with_tools(["save", "shell"])
        .with_focus(["web_development", "api_basics"])
        .expect_file_exists("api.py")
        .expect_output_contains("Hello, World!")
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 4. test-driven-simple
    spec, metadata = (
        TaskBuilder()
        .with_name("test-driven-simple")
        .with_prompt(
            "Write a test for a function is_palindrome(), then implement the function to make the test pass"
        )
        .with_run("python -m pytest test_palindrome.py -v")
        .with_tools(["save", "shell"])
        .with_focus(["tdd", "testing"])
        .expect_file_exists("test_palindrome.py")
        .expect_file_exists("palindrome.py")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 5. refactor-duplicate-code
    spec, metadata = (
        TaskBuilder()
        .with_name("refactor-duplicate-code")
        .with_file(
            "duplicates.py",
            """
def calculate_area_rectangle(width, height):
    area = width * height
    print(f"Area: {area}")
    return area

def calculate_area_square(side):
    area = side * side
    print(f"Area: {area}")
    return area

def calculate_area_triangle(base, height):
    area = (base * height) / 2
    print(f"Area: {area}")
    return area
""",
        )
        .with_prompt("Extract the common 'print area' logic into a reusable function")
        .with_run("python duplicates.py")
        .with_tools(["read", "patch"])
        .with_focus(["code_organization", "refactoring"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 6. document-generation
    spec, metadata = (
        TaskBuilder()
        .with_name("document-generation")
        .with_file(
            "calc.py",
            """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
""",
        )
        .with_prompt("Generate a README.md documenting the functions in calc.py")
        .with_run("cat README.md")
        .with_tools(["read", "save", "python"])
        .with_focus(["documentation", "parsing"])
        .expect_file_exists("README.md")
        .expect_output_contains("add")
        .expect_output_contains("subtract")
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

    # Phase 1 Debugging Tasks (3 new tasks)

    # 1. debug-web-scraper
    scraper_files = {
        "scraper.py": """import requests
from utils import parse_html
import config

def fetch_data(url):
    response = requests.get(url)
    return parse_html(response.txt)  # Bug: should be response.text

def main():
    data = fetch_data(config.URL)
    print(data)

if __name__ == "__main__":
    main()
""",
        "utils.py": """def parse_html(html):
    # Bug: KeyError if 'title' not found
    return html.split('<title>')[1].split('</title>')[0]
""",
        "config.py": """URL = "http://example.com"
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("debug-web-scraper")
        .with_files(scraper_files)
        .with_run("python scraper.py")
        .with_prompt("Fix all bugs in the web scraper to make it work correctly")
        .with_tools(["read", "patch", "shell"])
        .with_focus(["debugging", "error_handling", "multi_file"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 2. debug-database-queries
    db_files = {
        "app.py": """from models import get_user
import queries

def fetch_user_data(user_id):
    # Bug: SQL injection vulnerability
    return queries.get_user_by_id(user_id)

if __name__ == "__main__":
    print(fetch_user_data("1"))
""",
        "models.py": """def get_user(user_id):
    return {"id": user_id, "name": "Test User"}
""",
        "queries.py": """def get_user_by_id(user_id):
    # Bug: String formatting allows SQL injection
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return query
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("debug-database-queries")
        .with_files(db_files)
        .with_run("python app.py")
        .with_prompt("Fix SQL injection vulnerability and query bugs")
        .with_tools(["read", "patch", "shell"])
        .with_focus(["debugging", "security", "database"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 3. debug-async-race-condition
    async_files = {
        "async_worker.py": """import asyncio

counter = 0

async def increment():
    global counter
    temp = counter
    await asyncio.sleep(0.001)  # Race condition window
    counter = temp + 1

async def main():
    await asyncio.gather(*[increment() for _ in range(10)])
    print(f"Counter: {counter}")  # Should be 10, but likely less

if __name__ == "__main__":
    asyncio.run(main())
""",
        "test_race.py": """import asyncio
from async_worker import main

async def test():
    await main()
    # Expected: 10, but race condition causes incorrect value

if __name__ == "__main__":
    asyncio.run(test())
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("debug-async-race-condition")
        .with_files(async_files)
        .with_run("python async_worker.py")
        .with_prompt(
            "Fix the race condition in the async code to ensure counter reaches 10"
        )
        .with_tools(["read", "patch", "shell"])
        .with_focus(["debugging", "concurrency", "async"])
        .expect_output_contains("Counter: 10")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Phase 1 Complex Tasks (3 new tasks)

    # 1. optimize-data-processing
    opt_files = {
        "slow_processor.py": """def process_data(data):
    # Inefficient: O(n^2) when O(n) possible
    results = []
    for i in range(len(data)):
        for j in range(len(data)):
            if i != j and data[i] == data[j]:
                results.append(data[i])
    return list(set(results))

if __name__ == "__main__":
    data = list(range(1000)) * 2
    result = process_data(data)
    print(f"Found {len(result)} duplicates")
""",
        "benchmark.py": """import time
from slow_processor import process_data

def benchmark():
    data = list(range(1000)) * 2
    start = time.time()
    result = process_data(data)
    elapsed = time.time() - start
    print(f"Time: {elapsed:.3f}s")
    return elapsed

if __name__ == "__main__":
    benchmark()
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("optimize-data-processing")
        .with_files(opt_files)
        .with_run("python benchmark.py")
        .with_prompt(
            "Analyze and optimize the slow data processing code for better performance"
        )
        .with_tools(["read", "patch", "shell", "python"])
        .with_focus(["performance_optimization", "algorithmic_improvement", "analysis"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 2. research-implement-auth
    spec, metadata = (
        TaskBuilder()
        .with_name("research-implement-auth")
        .with_prompt(
            "Research JWT authentication strategies and implement a simple JWT auth system with login and token verification"
        )
        .with_run("python auth.py")
        .with_tools(["browser", "read", "save", "patch", "shell"])
        .with_focus(["research", "implementation", "security", "authentication"])
        .expect_file_exists("auth.py")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # 3. refactor-architecture
    arch_files = {
        "monolith.py": """import json
import sqlite3

class App:
    def __init__(self):
        self.db = sqlite3.connect('app.db')
        self.db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER, name TEXT)')

    def add_user(self, user_id, name):
        self.db.execute('INSERT INTO users VALUES (?, ?)', (user_id, name))
        self.db.commit()

    def get_user(self, user_id):
        cursor = self.db.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        return cursor.fetchone()

    def api_create_user(self, data):
        user_id = data['id']
        name = data['name']
        self.add_user(user_id, name)
        return json.dumps({'status': 'created'})

    def api_get_user(self, user_id):
        user = self.get_user(user_id)
        return json.dumps({'id': user[0], 'name': user[1]})

if __name__ == "__main__":
    app = App()
    print(app.api_create_user({'id': 1, 'name': 'Alice'}))
    print(app.api_get_user(1))
"""
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("refactor-architecture")
        .with_files(arch_files)
        .with_run(
            "python -c 'import api.routes; import models.user; print(\"Refactored!\")'"
        )
        .with_prompt(
            "Refactor monolith.py into a modular structure with separate api/, models/, and utils/ directories"
        )
        .with_tools(["read", "save", "patch", "shell"])
        .with_focus(["architectural_refactoring", "modularity", "multi_file"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Phase 2 Debugging Tasks

    # debug-memory-leak
    memory_leak_files = {
        "leak_example.py": """import gc

data_store = []

def process_data(size):
    # Bug: appending to global list causes memory leak
    chunk = [0] * size
    data_store.append(chunk)
    return len(chunk)

def main():
    for i in range(100):
        process_data(10000)
    print(f"Processed data, store size: {len(data_store)}")

if __name__ == "__main__":
    main()
""",
        "profiler.py": """import tracemalloc
import leak_example

tracemalloc.start()
leak_example.main()
current, peak = tracemalloc.get_traced_memory()
print(f"Memory: current={current/1024/1024:.1f}MB peak={peak/1024/1024:.1f}MB")
tracemalloc.stop()
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("debug-memory-leak")
        .with_files(memory_leak_files)
        .with_run("python3 profiler.py")
        .with_prompt(
            "Fix the memory leak in leak_example.py. The data_store shouldn't grow unbounded."
        )
        .with_tools(["read", "patch", "shell", "python"])
        .with_focus(["memory_debugging", "profiling", "performance"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # debug-test-failures
    test_failure_files = {
        "app.py": """def add(a, b):
    return a + b

def multiply(a, b):
    # Bug: should multiply not add
    return a + b

def divide(a, b):
    # Bug: no zero check
    return a / b
""",
        "test_app.py": """import pytest
from app import add, multiply, divide

def test_add():
    assert add(2, 3) == 5

def test_multiply():
    assert multiply(2, 3) == 6

def test_divide():
    assert divide(6, 2) == 3

def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
""",
        "fixtures.py": """import pytest

@pytest.fixture
def sample_numbers()
    # Bug: missing colon
    return [1, 2, 3, 4, 5]
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("debug-test-failures")
        .with_files(test_failure_files)
        .with_run("python3 -m pytest test_app.py -v")
        .with_prompt("Fix all the bugs causing test failures in this project")
        .with_tools(["read", "patch", "shell"])
        .with_focus(["test_debugging", "systematic_fixing", "error_handling"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # debug-cli-tool
    cli_tool_files = {
        "cli.py": """#!/usr/bin/env python3
import argparse
from commands import run_command

def main():
    parser = argparse.ArgumentParser(description='CLI Tool')
    parser.add_argument('command', help='Command to run')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--count', type=int, default=1)  # Bug: missing help

    args = parser.parse_args()

    # Bug: not passing verbose flag
    run_command(args.command, args.count)

if __name__ == "__main__":
    main()
""",
        "commands.py": """def run_command(command, count, verbose=False):
    for i in range(count):
        if verbose:
            print(f"[{i+1}/{count}] Executing: {command}")
        print(f"Result: {command.upper()}")
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("debug-cli-tool")
        .with_files(cli_tool_files)
        .with_run("python3 cli.py test --verbose --count 3")
        .with_prompt(
            "Fix the CLI tool so verbose mode works correctly and all arguments are passed"
        )
        .with_tools(["read", "patch", "shell"])
        .with_focus(["cli_debugging", "argument_parsing", "user_interface"])
        .expect_output_contains("Executing: test")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # Phase 2 Complex Tasks

    # implement-caching-strategy
    caching_files = {
        "app.py": """import time

def expensive_computation(x):
    time.sleep(0.1)  # Simulate expensive operation
    return x ** 2

def process_requests(requests):
    results = []
    for req in requests:
        result = expensive_computation(req)
        results.append(result)
    return results

if __name__ == "__main__":
    reqs = [1, 2, 3, 1, 2, 3, 1, 2, 3]  # Many duplicates
    start = time.time()
    results = process_requests(reqs)
    duration = time.time() - start
    print(f"Results: {results}")
    print(f"Duration: {duration:.2f}s")
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("implement-caching-strategy")
        .with_files(caching_files)
        .with_run("python3 app.py")
        .with_prompt(
            "Research caching strategies and implement a multi-layer cache (memory + disk) to optimize this code. The output should complete in <0.5s."
        )
        .with_tools(["browser", "read", "save", "patch", "shell"])
        .with_focus(["research", "caching", "performance_optimization", "architecture"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # code-review-enhance
    code_review_files = {
        "calculator.py": """def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    return a / b
""",
        "test_calculator.py": """from calculator import add, subtract

def test_add():
    assert add(2, 3) == 5

def test_subtract():
    assert subtract(5, 3) == 2
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("code-review-enhance")
        .with_files(code_review_files)
        .with_run("python3 -m pytest test_calculator.py -v")
        .with_prompt(
            "Review calculator.py and enhance it: 1) Add input validation, 2) Add tests for multiply/divide, 3) Handle edge cases like division by zero"
        )
        .with_tools(["read", "patch", "save", "shell"])
        .with_focus(["code_review", "testing", "error_handling", "enhancement"])
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # migration-sql-to-orm
    sql_migration_files = {
        "db.py": """import sqlite3

def get_users():
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users')
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_user(name, email):
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()
    cursor.execute(f"INSERT INTO users (name, email) VALUES ('{name}', '{email}')")  # SQL injection risk
    conn.commit()
    conn.close()

def init_db():
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
""",
        "queries.py": """# Raw SQL queries
GET_USER_BY_EMAIL = "SELECT * FROM users WHERE email = '%s'"
DELETE_USER = "DELETE FROM users WHERE id = %d"
UPDATE_USER = "UPDATE users SET name = '%s', email = '%s' WHERE id = %d"
""",
    }

    spec, metadata = (
        TaskBuilder()
        .with_name("migration-sql-to-orm")
        .with_files(sql_migration_files)
        .with_run(
            'python3 -c \'from models import User, init_db; init_db(); u = User(name="Test", email="test@example.com"); print("ORM working")\''
        )
        .with_prompt(
            "Migrate this raw SQL code to use SQLAlchemy ORM. Create models.py with proper ORM models and refactor db.py to use them. Fix SQL injection vulnerabilities."
        )
        .with_tools(["read", "save", "patch", "shell"])
        .with_focus(["migration", "orm", "security", "architecture"])
        .expect_output_contains("ORM working")
        .expect_success()
        .build_with_metadata()
    )
    _task_metadata[spec["name"]] = metadata
    tasks.append(spec)

    # build-mini-framework
    spec, metadata = (
        TaskBuilder()
        .with_name("build-mini-framework")
        .with_run(
            'python3 -c \'from framework import App; app = App(); @app.route("/"); def index(): return "Hello"; print(app.routes); print("Framework works")\''
        )
        .with_prompt(
            "Build a mini web framework from scratch with: 1) App class with routing decorator, 2) Request/Response handling, 3) Middleware support. Create framework.py with the implementation."
        )
        .with_tools(["save", "shell", "python"])
        .with_focus(
            ["architecture", "framework_design", "api_design", "implementation"]
        )
        .expect_output_contains("Framework works")
        .expect_success()
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
