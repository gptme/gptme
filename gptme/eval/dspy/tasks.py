"""
Specialized evaluation tasks for prompt optimization.

These tasks are designed to test specific aspects of system prompts
including tool usage, reasoning, instruction following, and problem solving.
"""

from typing import Any


def create_tool_usage_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that specifically test tool usage patterns.

    These tasks evaluate whether the system prompt leads to appropriate
    tool selection and usage patterns.
    """
    return [
        {
            "name": "tool-save-simple",
            "files": {},
            "run": "python hello.py",
            "prompt": 'Create a Python file called hello.py that prints "Hello, World!"',
            "tools": ["save"],
            "expect": {
                "uses_save_tool": lambda ctx: "save" in str(ctx.messages),
                "correct_output": lambda ctx: "Hello, World!" in ctx.stdout,
            },
            "focus_areas": ["tool_selection", "file_creation"],
        },
        {
            "name": "tool-patch-vs-save",
            "files": {"greeting.py": 'print("Hi there")'},
            "run": "python greeting.py",
            "prompt": 'Change the greeting.py file to print "Hello, World!" instead',
            "tools": ["save", "patch"],
            "expect": {
                "prefers_patch": lambda ctx: "patch" in str(ctx.messages)
                and "save" not in str(ctx.messages),
                "correct_output": lambda ctx: "Hello, World!" in ctx.stdout,
            },
            "focus_areas": ["tool_selection", "incremental_changes"],
        },
        {
            "name": "tool-read-before-modify",
            "files": {"config.py": "DEBUG = True\nVERSION = '1.0'"},
            "run": "python -c 'import config; print(config.VERSION)'",
            "prompt": 'Update the VERSION in config.py to "2.0" but keep DEBUG setting unchanged',
            "tools": ["read", "patch", "save"],
            "expect": {
                "reads_first": lambda ctx: any(
                    "read" in str(msg) for msg in ctx.messages[:3]
                ),
                "correct_output": lambda ctx: "2.0" in ctx.stdout,
                "preserves_debug": lambda ctx: "DEBUG = True"
                in ctx.files.get("config.py", ""),
            },
            "focus_areas": ["tool_sequencing", "context_gathering"],
        },
        {
            "name": "tool-shell-exploration",
            "files": {},
            "run": "ls -la",
            "prompt": "List all files in the current directory including hidden files",
            "tools": ["shell"],
            "expect": {
                "uses_shell": lambda ctx: "shell" in str(ctx.messages),
                "correct_command": lambda ctx: "ls -la" in str(ctx.messages)
                or "ls -a" in str(ctx.messages),
            },
            "focus_areas": ["command_generation", "shell_usage"],
        },
    ]


def create_reasoning_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that test reasoning and problem-solving capabilities.

    These tasks evaluate whether the system prompt encourages good
    problem-solving approaches and step-by-step thinking.
    """
    return [
        {
            "name": "reasoning-debug-error",
            "files": {"broken.py": "print('Hello World')\nprint(undeclared_variable)"},
            "run": "python broken.py",
            "prompt": "Fix the error in broken.py and make it run successfully",
            "tools": ["shell", "patch", "read"],
            "expect": {
                "identifies_error": lambda ctx: "undeclared_variable"
                in str(ctx.messages).lower()
                or "not defined" in str(ctx.messages).lower(),
                "fixes_error": lambda ctx: ctx.return_code == 0,
                "explains_fix": lambda ctx: len(
                    [
                        m
                        for m in ctx.messages
                        if m.role == "assistant" and len(m.content) > 50
                    ]
                )
                > 0,
            },
            "focus_areas": ["error_analysis", "debugging", "explanation_quality"],
        },
        {
            "name": "reasoning-multi-step",
            "files": {},
            "run": "python fibonacci.py",
            "prompt": "Create a program that calculates and prints the first 10 Fibonacci numbers",
            "tools": ["save", "shell"],
            "expect": {
                "shows_planning": lambda ctx: any(
                    "step" in str(msg).lower() or "first" in str(msg).lower()
                    for msg in ctx.messages
                    if msg.role == "assistant"
                ),
                "correct_output": lambda ctx: "0, 1, 1, 2, 3, 5, 8, 13, 21, 34"
                in ctx.stdout.replace("\n", ", "),
                "tests_solution": lambda ctx: any(
                    "run" in str(msg) or "test" in str(msg) for msg in ctx.messages
                ),
            },
            "focus_areas": [
                "problem_decomposition",
                "verification",
                "algorithmic_thinking",
            ],
        },
        {
            "name": "reasoning-requirements-analysis",
            "files": {},
            "run": "python calculator.py",
            "prompt": "Build a calculator that can add, subtract, multiply, and divide two numbers provided as command line arguments",
            "tools": ["save", "shell"],
            "expect": {
                "analyzes_requirements": lambda ctx: any(
                    "command line" in str(msg).lower()
                    for msg in ctx.messages
                    if msg.role == "assistant"
                ),
                "handles_args": lambda ctx: "sys.argv"
                in ctx.files.get("calculator.py", "")
                or "argparse" in ctx.files.get("calculator.py", ""),
                "implements_operations": lambda ctx: all(
                    op in ctx.files.get("calculator.py", "")
                    for op in ["+", "-", "*", "/"]
                ),
            },
            "focus_areas": [
                "requirements_understanding",
                "comprehensive_implementation",
            ],
        },
    ]


def create_instruction_following_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that test adherence to specific instructions and constraints.

    These tasks evaluate whether the system prompt helps follow detailed
    instructions and respect constraints.
    """
    return [
        {
            "name": "instruction-no-placeholders",
            "files": {},
            "run": "bash deploy.sh",
            "prompt": "Create a deployment script deploy.sh that echoes 'Deploying to production'. Do not use any placeholder variables.",
            "tools": ["save"],
            "expect": {
                "no_placeholders": lambda ctx: "$" not in ctx.files.get("deploy.sh", "")
                and "placeholder" not in ctx.files.get("deploy.sh", "").lower(),
                "correct_output": lambda ctx: "Deploying to production" in ctx.stdout,
                "executable": lambda ctx: ctx.return_code == 0,
            },
            "focus_areas": ["constraint_following", "avoiding_placeholders"],
        },
        {
            "name": "instruction-absolute-paths",
            "files": {},
            "run": "python path_example.py",
            "prompt": "Create a Python script that prints the absolute path of the current working directory. Use absolute paths in your solution.",
            "tools": ["save", "shell"],
            "expect": {
                "uses_absolute_paths": lambda ctx: any(
                    "/" in str(msg) and not msg.content.startswith("```")
                    for msg in ctx.messages
                    if msg.role == "assistant"
                ),
                "correct_logic": lambda ctx: "os.getcwd()"
                in ctx.files.get("path_example.py", "")
                or "pathlib" in ctx.files.get("path_example.py", ""),
            },
            "focus_areas": ["path_handling", "instruction_adherence"],
        },
        {
            "name": "instruction-preserve-comments",
            "files": {
                "commented.py": "# Important configuration\nDEBUG = True  # Keep this for development\n# Production settings\nPORT = 8080"
            },
            "run": "python -c 'import commented; print(commented.PORT)'",
            "prompt": "Change the PORT to 3000 in commented.py but preserve all existing comments",
            "tools": ["patch", "read"],
            "expect": {
                "preserves_comments": lambda ctx: "# Important configuration"
                in ctx.files.get("commented.py", "")
                and "# Production settings" in ctx.files.get("commented.py", ""),
                "changes_port": lambda ctx: "PORT = 3000"
                in ctx.files.get("commented.py", ""),
                "correct_output": lambda ctx: "3000" in ctx.stdout,
            },
            "focus_areas": ["comment_preservation", "selective_modification"],
        },
    ]


def create_error_handling_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that test error handling and recovery capabilities.

    These tasks evaluate whether the system prompt leads to good
    error handling and self-correction behavior.
    """
    return [
        {
            "name": "error-command-failure",
            "files": {},
            "run": "python test_error.py",
            "prompt": "Run the command 'nonexistent_command --help' and then create a Python script that prints 'Command not found' when that happens",
            "tools": ["shell", "save"],
            "expect": {
                "attempts_command": lambda ctx: "nonexistent_command"
                in str(ctx.messages),
                "handles_failure": lambda ctx: "not found" in str(ctx.messages).lower()
                or "error" in str(ctx.messages).lower(),
                "creates_alternative": lambda ctx: "test_error.py" in ctx.files,
                "correct_output": lambda ctx: "Command not found" in ctx.stdout,
            },
            "focus_areas": ["error_recovery", "alternative_solutions"],
        },
        {
            "name": "error-syntax-correction",
            "files": {
                "broken_syntax.py": "def greet(name\n    print(f'Hello, {name}!')\n\ngreet('World')"
            },
            "run": "python broken_syntax.py",
            "prompt": "Fix the syntax errors in broken_syntax.py and make it run correctly",
            "tools": ["shell", "patch", "read"],
            "expect": {
                "identifies_syntax_errors": lambda ctx: "syntax"
                in str(ctx.messages).lower(),
                "fixes_missing_paren": lambda ctx: "def greet(name):"
                in ctx.files.get("broken_syntax.py", ""),
                "fixes_indentation": lambda ctx: "    print("
                in ctx.files.get("broken_syntax.py", ""),
                "correct_output": lambda ctx: "Hello, World!" in ctx.stdout,
            },
            "focus_areas": ["syntax_error_handling", "code_correction"],
        },
    ]


def get_prompt_optimization_tasks() -> list[dict[str, Any]]:
    """
    Get all tasks designed for prompt optimization.

    Returns a comprehensive set of tasks that test different aspects
    of system prompt effectiveness.
    """
    all_tasks = []
    all_tasks.extend(create_tool_usage_tasks())
    all_tasks.extend(create_reasoning_tasks())
    all_tasks.extend(create_instruction_following_tasks())
    all_tasks.extend(create_error_handling_tasks())

    return all_tasks


def get_tasks_by_focus_area(focus_area: str) -> list[dict[str, Any]]:
    """
    Get tasks that focus on a specific area.

    Args:
        focus_area: The area to focus on (e.g., "tool_selection", "reasoning")

    Returns:
        List of tasks that test the specified focus area
    """
    all_tasks = get_prompt_optimization_tasks()

    matching_tasks = []
    for task in all_tasks:
        focus_areas = task.get("focus_areas", [])
        if focus_area in focus_areas:
            matching_tasks.append(task)

    return matching_tasks


def analyze_task_coverage() -> dict[str, list[str]]:
    """
    Analyze what focus areas are covered by the available tasks.

    Returns:
        Dictionary mapping focus areas to task names that cover them
    """
    all_tasks = get_prompt_optimization_tasks()
    coverage: dict[str, list[str]] = {}

    for task in all_tasks:
        focus_areas = task.get("focus_areas", [])
        task_name = task.get("name", "unknown")

        for area in focus_areas:
            if area not in coverage:
                coverage[area] = []
            coverage[area].append(task_name)

    return coverage


if __name__ == "__main__":
    # Print task coverage analysis
    print("=== Prompt Optimization Task Coverage ===")
    coverage = analyze_task_coverage()

    for area, tasks in sorted(coverage.items()):
        print(f"\n{area}:")
        for task in tasks:
            print(f"  - {task}")

    print(f"\nTotal tasks: {len(get_prompt_optimization_tasks())}")
    print(f"Focus areas covered: {len(coverage)}")
