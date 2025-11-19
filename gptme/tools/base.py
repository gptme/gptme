from __future__ import annotations

import functools
import importlib
import json
import logging
import re
import sys
import types
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import indent
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeAlias,
    cast,
    get_origin,
)

import json_repair
from lxml import etree

from ..codeblock import Codeblock
from ..hooks import HookFunc
from ..message import Message
from ..util import clean_example, transform_examples_to_chat_directives

if TYPE_CHECKING:
    from ..logmanager import Log

logger = logging.getLogger(__name__)

InitFunc: TypeAlias = Callable[[], "ToolSpec"]

ToolFormat: TypeAlias = Literal["markdown", "xml", "tool"]

# tooluse format
tool_format: ToolFormat = "markdown"

# Match tool name and start of JSON
toolcall_re = re.compile(r"^@(\w+)\(([\w\-:]+)\):\s*({.*)", re.M | re.S)


def find_json_end(s: str, start: int) -> int | None:
    """Find the end of a JSON object by counting braces"""
    stack = []
    in_string = False
    escape = False

    for i, c in enumerate(s[start:], start):
        if escape:
            escape = False
            continue

        if c == "\\":
            escape = True
        elif c == '"' and not escape:
            in_string = not in_string
        elif not in_string:
            if c == "{":
                stack.append(c)
            elif c == "}":
                if not stack:
                    return None
                stack.pop()
                if not stack:
                    return i + 1
    return None


def extract_json(content: str, match: re.Match) -> str | None:
    """Extract complete JSON object starting from a regex match"""
    json_start = match.start(3)  # start of the JSON content
    json_end = find_json_end(content, json_start)
    if json_end is None:
        return None
    return content[json_start:json_end]


ConfirmFunc = Callable[[str], bool]


def set_tool_format(new_format: ToolFormat):
    global tool_format
    tool_format = new_format


def get_tool_format():
    return tool_format


class ExecuteFuncGen(Protocol):
    def __call__(
        self,
        code: str | None,
        args: list[str] | None,
        kwargs: dict[str, str] | None,
        confirm: ConfirmFunc,
    ) -> Generator[Message, None, None]: ...


class ExecuteFuncMsg(Protocol):
    def __call__(
        self,
        code: str | None,
        args: list[str] | None,
        kwargs: dict[str, str] | None,
        confirm: ConfirmFunc,
    ) -> Message: ...


ExecuteFunc: TypeAlias = ExecuteFuncGen | ExecuteFuncMsg


@dataclass(frozen=True)
class Parameter:
    """A wrapper for function parameters to convert them to JSON schema."""

    name: str
    type: str
    description: str | None = None
    enum: list[Any] | None = None
    required: bool = False


# TODO: there must be a better way?
def derive_type(t) -> str:
    if get_origin(t) == Literal:
        v = ", ".join(f'"{a}"' for a in t.__args__)
        return f"Literal[{v}]"
    elif get_origin(t) == types.UnionType:
        v = ", ".join(derive_type(a) for a in t.__args__)
        return f"Union[{v}]"
    else:
        return t.__name__


def callable_signature(func: Callable) -> str:
    # returns a signature f(arg1: type1, arg2: type2, ...) -> return_type
    args = ", ".join(
        f"{k}: {derive_type(v)}"
        for k, v in func.__annotations__.items()
        if k != "return"
    )
    ret_type = func.__annotations__.get("return")
    ret = f" -> {derive_type(ret_type)}" if ret_type else ""
    return f"{func.__name__}({args}){ret}"


@dataclass(frozen=True, eq=False)
class ToolSpec:
    """
    Tool specification. Defines a tool that can be used by the agent.

    Args:
        name: The name of the tool.
        desc: A description of the tool.
        instructions: Instructions for the agent on how to use the tool. This will be included in the prompt.
        instructions_format: Per tool format instructions when needed.
        examples: Example usage of the tool.
        functions: Functions registered in the IPython REPL.
        init: An optional function that is called when the tool is first loaded.
        execute: An optional function that is called when the tool executes a block.
        block_types: A list of block types that the tool will execute.
        available: Whether the tool is available for use.
        parameters: Descriptor of parameters use by this tool.
        load_priority: Influence the loading order of this tool. The higher the later.
        disabled_by_default: Whether this tool should be disabled by default.
        hooks: Hooks to register when this tool is loaded.
        commands: User slash-commands (/example) to register when this tool is loaded.
    """

    name: str
    desc: str
    instructions: str = ""
    instructions_format: dict[str, str] = field(default_factory=dict)
    examples: str | Callable[[str], str] = ""
    functions: list[Callable] | None = None
    init: InitFunc | None = None
    execute: ExecuteFunc | None = None
    block_types: list[str] = field(default_factory=list)
    available: bool | Callable[[], bool] = True
    parameters: list[Parameter] = field(default_factory=list)
    load_priority: int = 0
    disabled_by_default: bool = False
    is_mcp: bool = False
    hooks: dict[str, tuple[str, HookFunc, int]] = field(default_factory=dict)
    commands: dict[str, Callable] = field(default_factory=dict)

    def __repr__(self):
        return f"ToolSpec({self.name})"

    def register_hooks(self) -> None:
        """Register all hooks defined in this tool with the global hook registry."""
        # Avoid circular import
        from ..hooks import HookType, register_hook

        for hook_name, (hook_type_str, func, priority) in self.hooks.items():
            try:
                hook_type = HookType(hook_type_str)
                full_hook_name = f"{self.name}.{hook_name}"
                register_hook(full_hook_name, hook_type, func, priority)
            except (ValueError, KeyError) as e:
                logger.warning(
                    f"Failed to register hook '{hook_name}' for tool '{self.name}': {e}"
                )

    def register_commands(self) -> None:
        """Register all commands defined in this tool with the global command registry."""
        # Avoid circular import
        from ..commands import register_command

        for cmd_name, handler in self.commands.items():
            try:
                register_command(cmd_name, handler)
            except Exception as e:
                logger.warning(
                    f"Failed to register command '{cmd_name}' for tool '{self.name}': {e}"
                )

    def get_doc(self, doc: str | None = None) -> str:
        """Returns an updated docstring with examples."""
        if not doc:
            doc = ""
        else:
            doc += "\n\n"
        if self.instructions:
            doc += f"""
.. rubric:: Instructions

.. code-block:: markdown

{indent(self.instructions, "    ")}\n\n"""
        if self.get_examples():
            doc += f"""
.. rubric:: Examples

{transform_examples_to_chat_directives(self.get_examples())}\n\n
"""
        # doc += """.. rubric:: Members"""
        return doc.strip()

    def __eq__(self, other):
        if not isinstance(other, ToolSpec):
            return False
        return self.name == other.name

    def __lt__(self, other):
        if not isinstance(other, ToolSpec):
            return NotImplemented
        return (self.load_priority, self.name) < (other.load_priority, other.name)

    @property
    def is_available(self) -> bool:
        """Check if the tool is available for use."""
        if callable(self.available):
            return self.available()
        return self.available

    @property
    def is_runnable(self) -> bool:
        """Check if the tool can be executed."""
        return bool(self.execute)

    def get_instructions(self, tool_format: ToolFormat):
        instructions = []

        if self.instructions:
            instructions.append(self.instructions)

        if tool_format in self.instructions_format:
            instructions.append(self.instructions_format[tool_format])

        if self.functions:
            instructions.append(self.get_functions_description())

        return "\n\n".join(instructions)

    def get_tool_prompt(self, examples: bool, tool_format: ToolFormat):
        prompt = ""
        prompt += f"\n\n## {self.name}"
        prompt += f"\n\n**Description:** {self.desc}" if self.desc else ""
        instructions = self.get_instructions(tool_format)
        if instructions:
            prompt += f"\n\n**Instructions:** {instructions}"
        # TODO: Don't include examples for reasoning models: https://platform.openai.com/docs/guides/function-calling?api-mode=chat&example=get-weather#best-practices-for-defining-functions
        if examples and (
            examples_content := self.get_examples(tool_format, quote=True).strip()
        ):
            prompt += f"\n\n### Examples\n\n{examples_content}"
        return prompt

    def get_examples(self, tool_format: ToolFormat = "markdown", quote=False):
        if callable(self.examples):
            examples = self.examples(tool_format)
        else:
            examples = self.examples
        # make sure headers have exactly two newlines after them
        examples = re.sub(r"\n*(\n#+.*?)\n+", r"\n\1\n\n", examples)
        return clean_example(examples, quote=quote)

    def get_functions_description(self) -> str:
        # return a prompt with a brief description of the available functions
        if self.functions:
            description = "The following Python functions are available using the `ipython` tool:\n\n```txt\n"
            return (
                description
                + "\n".join(
                    f"{callable_signature(func)}: {func.__doc__ or 'No description'}"
                    for func in self.functions
                )
                + "\n```"
            )
        else:
            return "None"


@dataclass(frozen=True)
class ToolUse:
    tool: str
    args: list[str] | None
    content: str | None
    kwargs: dict[str, str] | None = None
    call_id: str | None = None
    start: int | None = None
    _format: ToolFormat | None = "markdown"

    def execute(
        self,
        confirm: ConfirmFunc,
        log: Log | None = None,
        workspace: Path | None = None,
    ) -> Generator[Message, None, None]:
        """Executes a tool-use tag and returns the output."""
        # noreorder
        from ..hooks import HookType, trigger_hook  # fmt: skip
        from ..telemetry import record_tool_call, trace_function  # fmt: skip
        from . import get_tool  # fmt: skip

        # wrap confirm in trace_function
        @functools.wraps(confirm)
        def _confirm(content: str) -> bool:
            return trace_function(
                name=f"tool.{self.tool}.confirm",
                attributes={"tool_name": self.tool},
            )(confirm)(content)

        @trace_function(name=f"tool.{self.tool}", attributes={"tool_name": self.tool})
        def _execute_tool():
            tool = get_tool(self.tool)
            if tool and tool.execute:
                try:
                    # Trigger pre-execution hooks
                    if pre_hook_msgs := trigger_hook(
                        HookType.TOOL_PRE_EXECUTE,
                        log=log,
                        workspace=workspace,
                        tool_use=self,
                    ):
                        yield from pre_hook_msgs

                    # Play tool sound if enabled
                    from ..util.sound import get_tool_sound_for_tool, play_tool_sound

                    if sound_type := get_tool_sound_for_tool(self.tool):
                        play_tool_sound(sound_type)

                    # Measure tool execution time
                    import time

                    start_time = time.time()

                    ex = tool.execute(
                        self.content,
                        self.args,
                        self.kwargs,
                        _confirm,
                    )
                    if isinstance(ex, Generator):
                        # Convert generator to list to measure execution time properly
                        results = list(ex)
                        yield from results
                    else:
                        yield ex

                    # Calculate duration
                    duration = time.time() - start_time

                    # Record successful tool call with duration
                    record_tool_call(
                        self.tool,
                        duration=duration,
                        success=True,
                        tool_format=self._format,
                    )

                    # Trigger post-execution hooks
                    if post_hook_msgs := trigger_hook(
                        HookType.TOOL_POST_EXECUTE,
                        log=log,
                        workspace=workspace,
                        tool_use=self,
                    ):
                        yield from post_hook_msgs

                except Exception as e:
                    # Calculate duration even for failed calls
                    duration = time.time() - start_time

                    # Record failed tool call with error details and duration
                    record_tool_call(
                        self.tool,
                        duration=duration,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        tool_format=self._format,
                    )

                    # if we are testing, raise the exception
                    logger.exception(e)
                    if "pytest" in globals():
                        raise e
                    yield Message("system", f"Error executing tool '{self.tool}': {e}")
            else:
                logger.warning(f"Tool '{self.tool}' is not available for execution.")

        yield from _execute_tool()

    @property
    def is_runnable(self) -> bool:
        # noreorder
        from . import get_tool  # fmt: skip

        tool = get_tool(self.tool)
        return bool(tool.execute) if tool else False

    @classmethod
    def _from_codeblock(cls, codeblock: Codeblock) -> ToolUse | None:
        """Parses a codeblock into a ToolUse. Codeblock must be a supported type.

        Example:
          ```lang
          content
          ```
        """
        # noreorder
        from . import get_tool_for_langtag  # fmt: skip

        if tool := get_tool_for_langtag(codeblock.lang):
            # NOTE: special case
            args = (
                codeblock.lang.split(" ")[1:]
                if tool.name not in ["save", "append", "patch"]
                else [codeblock.lang]
            )
            return ToolUse(
                tool.name,
                args,
                codeblock.content,
                start=codeblock.start,
                _format="markdown",
            )
        else:
            # no_op_langs = ["csv", "json", "html", "xml", "stdout", "stderr", "result"]
            # if codeblock.lang and codeblock.lang not in no_op_langs:
            #     logger.warning(
            #         f"Unknown codeblock type '{codeblock.lang}', neither supported language or filename."
            #     )
            return None

    @classmethod
    def iter_from_content(
        cls,
        content: str,
        tool_format_override: ToolFormat | None = None,
        streaming: bool = False,
    ) -> Generator[ToolUse, None, None]:
        """Returns all ToolUse in a message, markdown or XML, in order.

        Args:
            content: The message content to parse
            tool_format_override: Optional tool format override
            streaming: If True, requires blank line after code blocks for completion
        """
        # Use override if provided, otherwise use global tool_format
        active_format = tool_format_override or tool_format

        # collect all tool uses
        tool_uses = []
        if active_format == "xml":
            for tool_use in cls._iter_from_xml(content):
                tool_uses.append(tool_use)
        if active_format == "markdown":
            for tool_use in cls._iter_from_markdown(content, streaming=streaming):
                tool_uses.append(tool_use)

        # return them in the order they appear
        assert all(x.start is not None for x in tool_uses)
        tool_uses.sort(key=lambda x: x.start or 0)
        for tool_use in tool_uses:
            yield tool_use

        # don't continue unless tool format (or override allows it)
        if active_format != "tool":
            return

        # check if its a toolcall and extract valid JSON
        if match := toolcall_re.search(content):
            tool_name = match.group(1)
            call_id = match.group(2)
            if (json_str := extract_json(content, match)) is not None:
                try:
                    kwargs = json_repair.loads(json_str)
                    if not isinstance(kwargs, dict):
                        logger.debug(f"JSON repair result is not a dict: {kwargs}")
                        return
                    start_pos = content.find(f"@{tool_name}(")
                    yield ToolUse(
                        tool_name,
                        None,
                        None,
                        kwargs=cast(dict[str, str], kwargs),
                        call_id=call_id,
                        start=start_pos,
                        _format="tool",
                    )
                except json.JSONDecodeError:
                    logger.debug(f"Failed to parse JSON: {json_str}")

    @classmethod
    def _iter_from_markdown(
        cls, content: str, streaming: bool = False
    ) -> Generator[ToolUse, None, None]:
        """Returns all markdown-style ToolUse in a message.

        Args:
            content: The message content to parse
            streaming: If True, requires blank line after code blocks for completion

        Example:
          ```ipython
          print("Hello, world!")
          ```
        """
        for codeblock in Codeblock.iter_from_markdown(content, streaming=streaming):
            if tool_use := cls._from_codeblock(codeblock):
                yield tool_use

    @classmethod
    def _iter_from_xml(cls, content: str) -> Generator[ToolUse, None, None]:
        """Returns all XML-style ToolUse in a message.

        Supports two formats:
        1. gptme format:
          <tool-use>
          <ipython>
          print("Hello, world!")
          </ipython>
          </tool-use>

        2. Haiku format:
          <function_calls>
          <invoke name="ipython">
          print("Hello, world!")
          </invoke>
          </function_calls>
        """
        # Check for either format
        has_tool_use = "<tool-use>" in content and "</tool-use>" in content
        has_function_calls = (
            "<function_calls>" in content and "</function_calls>" in content
        )

        if not (has_tool_use or has_function_calls):
            return

        try:
            # Parse the content as HTML to be more lenient with malformed XML
            parser = etree.HTMLParser()
            tree = etree.fromstring(content, parser)

            # Handle gptme format: <tool-use><toolname>...</toolname></tool-use>
            for tooluse in tree.xpath("//tool-use"):
                for child in tooluse.getchildren():
                    tool_name = child.tag
                    args = list(child.attrib.values())
                    tool_content = (child.text or "").strip()

                    # Find the start position of the tool in the original content
                    start_pos = content.find(f"<{tool_name}")

                    yield ToolUse(
                        tool_name,
                        args,
                        tool_content,
                        start=start_pos,
                        _format="xml",
                    )

            # Handle Haiku format: <function_calls><invoke name="toolname">...</invoke></function_calls>
            for function_calls in tree.xpath("//function_calls"):
                for invoke in function_calls.xpath(".//invoke"):
                    # Get tool name from 'name' attribute
                    tool_name = invoke.get("name")
                    if not tool_name:
                        continue

                    # Get any other attributes as args (excluding 'name')
                    args = [v for k, v in invoke.attrib.items() if k != "name"]
                    tool_content = (invoke.text or "").strip()

                    # Find the start position of the invoke in the original content
                    start_pos = content.find(f'<invoke name="{tool_name}"')

                    yield ToolUse(
                        tool_name,
                        args,
                        tool_content,
                        start=start_pos,
                        _format="xml",
                    )
        except etree.ParseError as e:
            logger.warning(f"Failed to parse XML content: {e}")
            return

    def to_output(self, tool_format: ToolFormat = "markdown") -> str:
        if tool_format == "markdown":
            return self._to_markdown()
        elif tool_format == "xml":
            return self._to_xml()
        elif tool_format == "tool":
            return self._to_toolcall()

    def _to_markdown(self) -> str:
        assert self.args is not None
        args = " ".join(self.args)
        return f"```{self.tool}{' ' if args else ''}{args}\n{self.content}\n```"

    def _to_xml(self) -> str:
        assert self.args is not None
        wrapper_tag = "tool-use"
        args = " ".join(self.args)
        args_str = "" if not args else f" args='{args}'"
        # Special case for Haiku format (testing purposes)
        haiku_adapted = False
        if haiku_adapted:
            wrapper_tag = "function_calls"
            args_str = f' name="{self.tool}"' + args_str
            call = f'<invoke name="{self.tool}"{args_str}>\n{self.content}\n</invoke>'
        else:
            call = f"<{self.tool}{args_str}>\n{self.content}\n</{self.tool}>"
        return f"<{wrapper_tag}>\n{call}\n</{wrapper_tag}>"

    def _to_params(self) -> dict:
        # noreorder
        from . import get_tool  # fmt: skip

        if self.kwargs is not None:
            return self.kwargs
        elif self.args is not None and self.content is not None:
            # match positional args with kwargs
            if tool := get_tool(self.tool):
                if self.args:
                    args = [*self.args, self.content]
                else:
                    args = [self.content]

                json_parameters: dict[str, str] = {}
                for index, param in enumerate(tool.parameters):
                    json_parameters[param.name] = args[index]

                return json_parameters
        return {}

    def _to_json(self) -> str:
        return json.dumps({"name": self.tool, "parameters": self._to_params()})

    def _to_toolcall(self) -> str:
        self._to_json()
        return f"@{self.tool}: {json.dumps(self._to_params(), indent=2)}"


def get_path(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Path:
    """Get the path from args/kwargs for save, append, and patch."""
    if code is not None and args is not None:
        fn = " ".join(args)
        if (
            fn.startswith("save ")
            or fn.startswith("append ")
            or fn.startswith("patch ")
        ):
            fn = fn.split(" ", 1)[1]
    elif kwargs is not None:
        fn = kwargs.get("path", "")
    else:
        raise ValueError("No filename provided")

    return Path(fn).expanduser()


# TODO: allow using via specifying .py paths with --tools flag
def load_from_file(path: Path) -> list[ToolSpec]:
    """Import a tool from a Python file and register the ToolSpec."""
    from . import get_tool, get_tools

    tools_before = set([t.name for t in get_tools()])

    # import the python file
    script_dir = path.resolve().parent
    if script_dir not in sys.path:
        sys.path.append(str(script_dir))
    importlib.import_module(path.stem)

    tools_after = set([t.name for t in get_tools()])
    tools_new = tools_after - tools_before
    print(f"Loaded tools {tools_new} from {path}")
    return [tool for tool_name in tools_new if (tool := get_tool(tool_name))]
