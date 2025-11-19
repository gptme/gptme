"""
The assistant can execute Python code blocks.

It uses IPython to do so, and persists the IPython instance between calls to give a REPL-like experience.
"""

import dataclasses
import functools
import importlib.util
import io
import re
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from logging import getLogger
from typing import TYPE_CHECKING, TypeVar

from ..message import Message
from ..util.ask_execute import print_preview
from . import get_tools
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
    callable_signature,
)

if TYPE_CHECKING:
    from IPython.core.interactiveshell import InteractiveShell  # fmt: skip


logger = getLogger(__name__)

# TODO: launch the IPython session in the current venv, if any, instead of the pipx-managed gptme venv (for example) in which gptme itself runs
#       would let us use libraries installed with `pip install` in the current venv
#       https://github.com/gptme/gptme/issues/29

# IPython instance
_ipython: "InteractiveShell | None" = None


registered_functions: dict[str, Callable] = {}

T = TypeVar("T", bound=Callable)


def register_function(func: T) -> T:
    """Decorator to register a function to be available in the IPython instance."""
    registered_functions[func.__name__] = func
    # if ipython is already initialized, push the function to it to make it available
    if _ipython is not None:
        _ipython.push({func.__name__: func})
    return func


def _get_ipython():
    global _ipython
    from IPython.core.interactiveshell import InteractiveShell  # fmt: skip

    if _ipython is None:
        _ipython = InteractiveShell()
        _ipython.push(registered_functions)

    return _ipython


class TeeIO(io.StringIO):
    def __init__(self, original_stream):
        super().__init__()
        self.original_stream = original_stream
        self.in_result_block = False

    def write(self, s):
        # hack to get rid of ipython result-prompt ("Out[0]: ...") and everything after it
        if s.startswith("Out["):
            self.in_result_block = True
        if self.in_result_block:
            if s.startswith("\n"):
                self.in_result_block = False
            else:
                s = ""
        self.original_stream.write(s)
        self.original_stream.flush()  # Ensure immediate display
        return super().write(s)


@contextmanager
def capture_and_display():
    stdout_capture = TeeIO(sys.stdout)
    stderr_capture = TeeIO(sys.stderr)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_capture, stderr_capture
    try:
        yield stdout_capture, stderr_capture
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr


def execute_python(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc = lambda _: True,
) -> Generator[Message, None, None]:
    """Executes a python codeblock and returns the output."""
    from IPython.core.interactiveshell import ExecutionResult  # fmt: skip

    if code is not None and args is not None:
        code = code.strip()
    elif kwargs is not None:
        code = kwargs.get("code", "").strip()

    assert code is not None

    print_preview(code, "python")
    if not confirm("Execute this code?"):
        # early return
        yield Message("system", "Execution aborted: user chose not to run this code.")
        return

    # Create an IPython instance if it doesn't exist yet
    _ipython = _get_ipython()

    # Capture and display output in real-time
    with capture_and_display() as (stdout_capture, stderr_capture):
        # Execute the code (output will be displayed in real-time)
        result: ExecutionResult = _ipython.run_cell(
            code, silent=False, store_history=False
        )

    captured_stdout = stdout_capture.getvalue()
    captured_stderr = stderr_capture.getvalue()

    output = ""
    # TODO: should we include captured stdout with messages like these?
    # used by vision tool
    if isinstance(result.result, Message):
        yield result.result
        return

    if result.result is not None:
        output += f"Result:\n```\n{result.result}\n```\n\n"

    # only show stdout if there is no result
    elif captured_stdout:
        output += f"```stdout\n{captured_stdout.rstrip()}\n```\n\n"
    if captured_stderr:
        output += f"```stderr\n{captured_stderr.rstrip()}\n```\n\n"
    if result.error_in_exec:
        tb = result.error_in_exec.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        if tb:
            output += f"Exception during execution on line {tb.tb_lineno}:\n  {result.error_in_exec.__class__.__name__}: {result.error_in_exec}"

    # strip ANSI escape sequences
    # TODO: better to signal to the terminal that we don't want colors?
    output = re.sub(r"\x1b[^m]*m", "", output)
    yield Message("system", "Executed code block.\n\n" + output)


@functools.lru_cache
def get_installed_python_libraries() -> list[str]:
    """Check if a select list of Python libraries are installed."""
    candidates = [
        "numpy",
        "pandas",
        "matplotlib",
        "seaborn",
        "scipy",
        "sklearn",
        "statsmodels",
        "PIL",
    ]
    installed = set()
    for candidate in candidates:
        if importlib.util.find_spec(candidate):
            installed.add(candidate)

    return list(sorted(installed))


def get_functions():
    return "\n".join(
        [f"- {callable_signature(func)}" for func in registered_functions.values()]
    )


instructions = """
Use this tool to execute Python code in an interactive IPython session.
It will respond with the output and result of the execution.
""".strip()

instructions_format = {
    "markdown": """
To use it, send a codeblock using the `ipython` language tag.
If you first write the code in a normal python codeblock, remember to also execute it with the ipython codeblock.
"""
}


def examples(tool_format):
    return f"""
#### Result of the last expression will be returned

> User: What is 2 + 2?
> Assistant:
{ToolUse("ipython", [], "2 + 2").to_output(tool_format)}
> System: Executed code block.
{ToolUse("result", [], "4").to_output()}

#### Write a function and call it

> User: compute fib 10
> Assistant: To compute the 10th Fibonacci number, we can run the following code:
{ToolUse("ipython", [], '''
def fib(n):
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)
fib(10)
'''.strip()).to_output(tool_format)}
> System: Executed code block.
{ToolUse("result", [], "55").to_output()}
""".strip()


def init() -> ToolSpec:
    # Register python functions from other tools
    for loaded_tool in get_tools():
        if loaded_tool.functions:
            for func in loaded_tool.functions:
                register_function(func)

    python_libraries = get_installed_python_libraries()
    python_libraries_str = (
        "\n".join(f"- {lib}" for lib in python_libraries)
        or "- no common libraries found"
    )

    _instructions = f"""{instructions}

Available libraries:
{python_libraries_str}

Available functions:
{get_functions()}
""".strip()

    # create a copy with the updated instructions
    return dataclasses.replace(tool, instructions=_instructions)


tool = ToolSpec(
    name="ipython",
    desc="Execute Python code",
    instructions=instructions,
    examples=examples,
    execute=execute_python,
    init=init,
    block_types=[
        # "python",
        "ipython",
        "py",
    ],
    parameters=[
        Parameter(
            name="code",
            type="string",
            description="The code to execute in the IPython shell.",
            required=True,
        ),
    ],
    load_priority=10,
)
__doc__ = tool.get_doc(__doc__)
