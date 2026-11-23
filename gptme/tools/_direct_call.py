"""Direct calls to function-only tools when ``ipython`` is not loaded (#4964).

Function-only tools (``ToolSpec.is_function_only``, e.g. ``browser``,
``chats``, ``subagent``) register Python functions in the IPython REPL and have
no ``execute`` handler of their own. Without ``ipython`` — a read-only subagent
profile such as ``researcher``, or an allowlist like ``-t read,browser`` — the
model has no way to call them. Granting ``ipython`` is not an option: it is
arbitrary code execution, which an allowlist or read-only profile must never
gain implicitly.

Instead, such a tool gets its own ``execute`` handler that accepts exactly one
call to one of *its own* registered functions, with literal arguments::

    ```browser
    read_url("https://example.com")
    ```

Arguments are evaluated with :func:`ast.literal_eval`. Names, attribute access,
nested calls, operators, ``*args`` and ``**kwargs`` are rejected, so nothing
runs except the registered function itself.
"""

from __future__ import annotations

import ast
import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..constants import DECLINED_CONTENT
from ..hooks import ConfirmAction, get_confirmation
from ..message import Message
from ..util.context import md_codeblock
from .base import Parameter, ToolFunction, ToolSpec, ToolUse

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class DirectCallError(ValueError):
    """The code is not a single direct call to one of the tool's functions."""


def parse_direct_call(
    code: str, functions: dict[str, ToolFunction]
) -> tuple[ToolFunction, list[Any], dict[str, Any]]:
    """Parse *code* as one call to a function in *functions* with literal args.

    Returns the function and its evaluated positional and keyword arguments.
    Raises :class:`DirectCallError` for anything else.
    """
    try:
        tree = ast.parse(code.strip(), mode="eval")
    except SyntaxError as e:
        raise DirectCallError(
            f"Expected exactly one function call, got invalid syntax: {e.msg}"
        ) from None
    call = tree.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        raise DirectCallError("Expected exactly one function call, e.g. name(...)")
    tf = functions.get(call.func.id)
    if tf is None:
        raise DirectCallError(
            f"Unknown function '{call.func.id}'. "
            f"Available: {', '.join(sorted(functions))}"
        )

    args: list[Any] = []
    for node in call.args:
        if isinstance(node, ast.Starred):
            raise DirectCallError("*args is not supported in direct calls")
        args.append(_literal(node))
    kwargs: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise DirectCallError("**kwargs is not supported in direct calls")
        if kw.arg in kwargs:
            raise DirectCallError(f"Repeated keyword argument: {kw.arg}")
        kwargs[kw.arg] = _literal(kw.value)
    return tf, args, kwargs


def _literal(node: ast.expr) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        raise DirectCallError(
            f"Argument `{ast.unparse(node)}` is not a literal. Direct calls only "
            "accept literal arguments (strings, numbers, booleans, None, and "
            "lists/tuples/dicts of those): no variables, expressions or nested calls."
        ) from None


def _example_call(tool: ToolSpec) -> str:
    tf = (tool.functions or [])[0]
    required = [p for p in tf.parameters if p.required]
    args = ", ".join(f'{p.name}="..."' for p in required)
    return f"{tf.fn.__name__}({args})"


def _usage(tool: ToolSpec) -> str:
    return (
        f"The '{tool.name}' tool takes exactly one call to one of its functions, "
        "with literal arguments (strings, numbers, booleans, None, and "
        f"lists/tuples/dicts of those), e.g. `{_example_call(tool)}`. "
        "Variables, expressions, nested calls and multiple statements are not "
        "supported. The function's return value and printed output are returned."
    )


def _run(tf: ToolFunction, args: list[Any], kwargs: dict[str, Any]) -> list[Message]:
    # Reuse ipython's output capture and caps, preserving diagnostics even
    # when the function returns structured messages rather than plain text.
    from .python import (  # fmt: skip
        _cap_output,
        _redact_cell_output,
        capture_and_display,
    )

    error: Exception | None = None
    result: Any = None
    with capture_and_display() as (stdout_capture, stderr_capture):
        try:
            result = tf.fn(*args, **kwargs)
        except Exception as e:
            logger.debug("Direct call to %s failed", tf.fn.__name__, exc_info=True)
            error = e

    messages: list[Message] = []
    if error is None:
        if isinstance(result, Path) and result.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
        }:
            # Reuse vision's path boundary and image normalization/scaling.
            from .vision import view_image

            try:
                result = view_image(result)
            except Exception as e:
                logger.debug("Direct call image result failed", exc_info=True)
                error = e
                result = None
        if isinstance(result, Message):
            messages = [result]
        elif (
            isinstance(result, list)
            and result
            and all(isinstance(m, Message) for m in result)
        ):
            messages = list(result)

    output = ""
    if stdout := stdout_capture.get_captured():
        output += md_codeblock("stdout", _cap_output(stdout).rstrip()) + "\n\n"
    if error is None and result is not None and not messages:
        output += f"Result:\n{md_codeblock('', _cap_output(str(result)))}\n\n"
    if stderr := stderr_capture.get_captured():
        output += md_codeblock("stderr", _cap_output(stderr).rstrip()) + "\n\n"
    if error is not None:
        # The exception text is inserted directly (unlike stdout/stderr/result,
        # which pass through _cap_output), so redact it here. Redaction only:
        # capping here would insert an omission marker mid-error, changing a
        # message this path previously passed through verbatim.
        output += f"Error: {type(error).__name__}: {_redact_cell_output(str(error))}\n"
    if messages:
        return ([Message("system", output.strip())] if output else []) + messages
    if not output:
        output = f"Called {tf.fn.__name__}() (no output)"
    return [Message("system", output.strip())]


def make_direct_call_tool(tool: ToolSpec) -> ToolSpec:
    """Return a copy of a function-only *tool* that can be called without ipython."""
    if not tool.is_function_only:
        raise ValueError(f"Tool '{tool.name}' is not function-only")
    # Key by the callable's own name: that is what the prompt's function listing
    # shows and what ipython registers (not an optional ToolFunction.name alias).
    functions = {tf.fn.__name__: tf for tf in tool.functions or []}

    def execute(
        code: str | None,
        args: list[str] | None,
        kwargs: dict[str, str] | None,
    ) -> Generator[Message, None, None]:
        if code is None and kwargs is not None:
            code = kwargs.get("code")
        if not isinstance(code, str) or not code.strip():
            yield Message("system", f"No function call given. {_usage(tool)}")
            return
        try:
            tf, call_args, call_kwargs = parse_direct_call(code, functions)
        except DirectCallError as e:
            yield Message("system", f"{e}\n\n{_usage(tool)}")
            return

        confirm = get_confirmation()
        if confirm.action != ConfirmAction.CONFIRM:
            yield Message("system", DECLINED_CONTENT)
            return
        yield from _run(tf, call_args, call_kwargs)

    def examples(tool_format: Any) -> str:
        call = ToolUse(tool.name, [], _example_call(tool))
        return f"To call `{_example_call(tool)}`:\n\n{call.to_output(tool_format)}"

    note = (
        f"The 'ipython' tool is not loaded: call these functions directly through "
        f"the '{tool.name}' tool. " + _usage(tool)
    )
    return dataclasses.replace(
        tool,
        execute=execute,
        block_types=[*tool.block_types, tool.name]
        if tool.name not in tool.block_types
        else list(tool.block_types),
        parameters=[
            Parameter(
                name="code",
                type="string",
                description=(
                    "Exactly one call to one of this tool's functions, with "
                    f"literal arguments, e.g. {_example_call(tool)}"
                ),
                required=True,
            )
        ],
        instructions=f"{tool.instructions}\n\n{note}".strip(),
        # Old overrides may recommend ipython and native builders use the
        # compact tool override verbatim, without the base instructions.
        instructions_format={
            **{
                fmt: tool.get_functions_description()
                for fmt in tool.instructions_format
                if fmt != "tool"
            },
            "tool": note + " Functions: " + ", ".join(functions),
        },
        examples=examples,
    )


def with_direct_calls(tools: list[ToolSpec], function_call_tool: str) -> list[ToolSpec]:
    """Make function-only tools directly callable unless *function_call_tool* is loaded."""
    if any(t.name == function_call_tool for t in tools):
        return tools
    return [make_direct_call_tool(t) if t.is_function_only else t for t in tools]
