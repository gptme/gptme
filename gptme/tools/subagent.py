"""
A subagent tool for gptme

Lets gptme break down a task into smaller parts, and delegate them to subagents.
"""

import logging
import random
import string
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from ..message import Message
from . import get_tools
from .base import ToolSpec, ToolUse


class SubtaskDef(TypedDict):
    """Definition of a subtask for planner mode."""

    id: str
    description: str


if TYPE_CHECKING:
    # noreorder
    from ..logmanager import LogManager  # fmt: skip

logger = logging.getLogger(__name__)

Status = Literal["running", "success", "failure"]

_subagents: list["Subagent"] = []


@dataclass(frozen=True)
class ReturnType:
    status: Status
    result: str | None = None


@dataclass(frozen=True)
class Subagent:
    agent_id: str
    prompt: str
    thread: threading.Thread
    logdir: Path

    def get_log(self) -> "LogManager":
        # noreorder
        from ..logmanager import LogManager  # fmt: skip

        return LogManager.load(self.logdir)

    def status(self) -> ReturnType:
        if self.thread.is_alive():
            return ReturnType("running")

        # Check if executor used the complete tool
        log = self.get_log().log
        if not log:
            return ReturnType("failure", "No messages in log")

        last_msg = log[-1]

        # Check for complete tool call in last message
        tool_uses = list(ToolUse.iter_from_content(last_msg.content))
        complete_tool = next((tu for tu in tool_uses if tu.tool == "complete"), None)

        if complete_tool:
            # Extract content from complete tool
            result = complete_tool.content or "Task completed"
            return ReturnType(
                "success",
                result + f"\n\nFull log: {self.logdir}",
            )

        # Check if session ended with system completion message
        if last_msg.role == "system" and "Task complete" in last_msg.content:
            return ReturnType(
                "success",
                f"Task completed successfully. Full log: {self.logdir}",
            )

        # Task didn't complete properly
        return ReturnType(
            "failure",
            f"Task did not complete properly. Check log: {self.logdir}",
        )


def _run_planner(
    agent_id: str,
    prompt: str,
    subtasks: list[SubtaskDef],
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "instructions-only", "selective"] = "full",
    context_include: list[str] | None = None,
) -> None:
    """Run a planner that delegates work to multiple executor subagents.

    Args:
        agent_id: Identifier for the planner
        prompt: Context prompt shared with all executors
        subtasks: List of subtask definitions to execute
        execution_mode: "parallel" (all at once) or "sequential" (one by one)
        context_mode: Controls what context is shared with executors (see subagent() docs)
        context_include: For selective mode, list of context components to include
    """
    from gptme import chat
    from gptme.cli import get_logdir

    from ..prompts import get_prompt

    logger.info(
        f"Starting planner {agent_id} with {len(subtasks)} subtasks "
        f"in {execution_mode} mode"
    )

    def random_string(n):
        s = string.ascii_lowercase + string.digits
        return "".join(random.choice(s) for _ in range(n))

    threads = []
    for subtask in subtasks:
        executor_id = f"{agent_id}-{subtask['id']}"
        executor_prompt = f"Context: {prompt}\n\nSubtask: {subtask['description']}"
        name = f"subagent-{executor_id}"
        logdir = get_logdir(name + "-" + random_string(4))

        def run_executor(prompt=executor_prompt, log_dir=logdir):
            prompt_msgs = [Message("user", prompt)]
            workspace = Path.cwd()

            # Build initial messages based on context_mode
            if context_mode == "instructions-only":
                # Minimal system context - just basic instruction
                initial_msgs = [
                    Message(
                        "system",
                        "You are a helpful AI assistant. Complete the task described by the user. Use the `complete` tool when finished with a summary of your work.",
                    )
                ]
                # Add complete tool for instructions-only mode
                from ..prompts import prompt_tools

                initial_msgs.extend(
                    list(
                        prompt_tools(
                            tools=[t for t in get_tools() if t.name == "complete"],
                            tool_format="markdown",
                        )
                    )
                )
            elif context_mode == "selective":
                # Selective context - build from specified components
                from ..prompts import prompt_gptme, prompt_tools

                initial_msgs = []

                # Add components based on context_include
                if context_include and "agent" in context_include:
                    initial_msgs.extend(
                        list(prompt_gptme(False, None, agent_name=None))
                    )
                if context_include and "tools" in context_include:
                    initial_msgs.extend(
                        list(prompt_tools(tools=get_tools(), tool_format="markdown"))
                    )
                # workspace handled by passing workspace parameter to chat() if included
            else:  # "full" mode (default)
                # Full context
                initial_msgs = get_prompt(
                    get_tools(), interactive=False, workspace=workspace
                )

            complete_prompt = (
                "When you have finished the task, use the `complete` tool:\n"
                "```complete\n"
                "Brief summary of what was accomplished.\n"
                "```\n\n"
                "This signals task completion. The full conversation log will be "
                "available to the planner for review."
            )
            prompt_msgs.append(Message("user", complete_prompt))
            chat(
                prompt_msgs,
                initial_msgs,
                logdir=log_dir,
                workspace=workspace,
                model=None,
                stream=False,
                no_confirm=True,
                interactive=False,
                show_hidden=False,
                tool_format="markdown",
            )

        t = threading.Thread(target=run_executor, daemon=True)
        t.start()
        threads.append(t)
        _subagents.append(Subagent(executor_id, executor_prompt, t, logdir))

        # Sequential mode: wait for each task to complete before starting next
        if execution_mode == "sequential":
            logger.info(f"Waiting for {executor_id} to complete (sequential mode)")
            t.join()
            logger.info(f"Executor {executor_id} completed")

    # Parallel mode: all threads already started
    if execution_mode == "parallel":
        logger.info(f"Planner {agent_id} spawned {len(subtasks)} executor subagents")
    else:
        logger.info(
            f"Planner {agent_id} completed {len(subtasks)} subtasks sequentially"
        )


def subagent(
    agent_id: str,
    prompt: str,
    mode: Literal["executor", "planner"] = "executor",
    subtasks: list[SubtaskDef] | None = None,
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "instructions-only", "selective"] = "full",
    context_include: list[str] | None = None,
):
    """Starts an asynchronous subagent. Returns None immediately; output is retrieved later via subagent_wait().

    Args:
        agent_id: Unique identifier for the subagent
        prompt: Task prompt for the subagent (used as context for planner mode)
        mode: "executor" for single task, "planner" for delegating to multiple executors
        subtasks: List of subtask definitions for planner mode (required when mode="planner")
        execution_mode: "parallel" (default) runs all subtasks concurrently,
                       "sequential" runs subtasks one after another.
                       Only applies to planner mode.
        context_mode: Controls what context is shared with the subagent:
            - "full" (default): Share complete context (agent identity, tools, workspace)
            - "instructions-only": Minimal context, only the user prompt
            - "selective": Share only specified context components (requires context_include)
        context_include: For selective mode, list of context components to include:
            - "agent": Agent identity and capabilities
            - "tools": Tool descriptions and usage
            - "workspace": Workspace files and structure

    Returns:
        None: Starts asynchronous execution. Use subagent_wait() to retrieve output.
            In executor mode, starts a single task execution.
            In planner mode, starts execution of all subtasks using the specified execution_mode.

            Executors use the `complete` tool to signal completion with a summary.
            The full conversation log is available at the logdir path.
    """
    if mode == "planner":
        if not subtasks:
            raise ValueError("Planner mode requires subtasks parameter")
        return _run_planner(
            agent_id, prompt, subtasks, execution_mode, context_mode, context_include
        )

    # Validate context_mode parameters
    if context_mode == "selective" and not context_include:
        raise ValueError(
            "context_include parameter required when context_mode='selective'"
        )

    # noreorder
    from gptme import chat  # fmt: skip
    from gptme.cli import get_logdir  # fmt: skip

    from ..prompts import get_prompt  # fmt: skip

    def random_string(n):
        s = string.ascii_lowercase + string.digits
        return "".join(random.choice(s) for _ in range(n))

    name = f"subagent-{agent_id}"
    logdir = get_logdir(name + "-" + random_string(4))

    def run_subagent():
        prompt_msgs = [Message("user", prompt)]
        workspace = Path.cwd()

        # Build initial messages based on context_mode
        if context_mode == "instructions-only":
            # Minimal system context - just basic instruction
            initial_msgs = [
                Message(
                    "system",
                    "You are a helpful AI assistant. Complete the task described by the user. Use the `complete` tool when finished with a summary of your work.",
                )
            ]
            # Add complete tool for instructions-only mode
            from ..prompts import prompt_tools

            initial_msgs.extend(
                list(
                    prompt_tools(
                        tools=[t for t in get_tools() if t.name == "complete"],
                        tool_format="markdown",
                    )
                )
            )
        elif context_mode == "selective":
            # Selective context - build from specified components
            from ..prompts import prompt_gptme, prompt_tools

            initial_msgs = []

            # Type narrowing: context_include validated as not None earlier
            assert context_include is not None

            # Add components based on context_include
            if "agent" in context_include:
                initial_msgs.extend(list(prompt_gptme(False, None, agent_name=None)))
            if "tools" in context_include:
                initial_msgs.extend(
                    list(prompt_tools(tools=get_tools(), tool_format="markdown"))
                )
            # workspace handled by passing workspace parameter to chat() if included
        else:  # "full" mode (default)
            # Current behavior - full context
            initial_msgs = get_prompt(
                get_tools(), interactive=False, workspace=workspace
            )

        # add the return prompt
        return_prompt = """Thank you for doing the task, please reply with a JSON codeblock on the format:

```json
{
    result: 'A description of the task result/outcome',
    status: 'success' | 'failure',
}
```"""
        prompt_msgs.append(Message("user", return_prompt))

        # Note: workspace parameter is always passed to chat() (required parameter)
        # Workspace context in messages is controlled by initial_msgs
        chat(
            prompt_msgs,
            initial_msgs,
            logdir=logdir,
            workspace=workspace,
            model=None,
            stream=False,
            no_confirm=True,
            interactive=False,
            show_hidden=False,
            tool_format="markdown",
        )

    # start a thread with a subagent
    t = threading.Thread(
        target=run_subagent,
        daemon=True,
    )
    t.start()
    _subagents.append(Subagent(agent_id, prompt, t, logdir))


def subagent_status(agent_id: str) -> dict:
    """Returns the status of a subagent."""
    for subagent in _subagents:
        if subagent.agent_id == agent_id:
            return asdict(subagent.status())
    raise ValueError(f"Subagent with ID {agent_id} not found.")


def subagent_wait(agent_id: str) -> dict:
    """Waits for a subagent to finish. Timeout is 1 minute."""
    subagent = None
    for subagent in _subagents:
        if subagent.agent_id == agent_id:
            break

    if subagent is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")

    logger.info("Waiting for the subagent to finish...")
    subagent.thread.join(timeout=60)
    status = subagent.status()
    return asdict(status)


def examples(tool_format):
    return f"""
### Executor Mode (single task)
User: compute fib 13 using a subagent
Assistant: Starting a subagent to compute the 13th Fibonacci number.
{ToolUse("ipython", [], 'subagent("fib-13", "compute the 13th Fibonacci number")').to_output(tool_format)}
System: Subagent started successfully.
Assistant: Now we need to wait for the subagent to finish the task.
{ToolUse("ipython", [], 'subagent_wait("fib-13")').to_output(tool_format)}
System: {{"status": "success", "result": "The 13th Fibonacci number is 233"}}.

### Planner Mode (multi-task delegation)
User: implement feature X with tests
Assistant: I'll use planner mode to delegate implementation and testing to separate subagents.
{ToolUse("ipython", [], '''subtasks = [
    {{"id": "implement", "description": "Write implementation for feature X"}},
    {{"id": "test", "description": "Write comprehensive tests"}},
]
subagent("feature-planner", "Feature X adds new functionality", mode="planner", subtasks=subtasks)''').to_output(tool_format)}
System: Planner spawned 2 executor subagents.
Assistant: Now I'll wait for both subtasks to complete.
{ToolUse("ipython", [], 'subagent_wait("feature-planner-implement")').to_output(tool_format)}
System: {{"status": "success", "result": "Implementation complete in feature_x.py"}}.
{ToolUse("ipython", [], 'subagent_wait("feature-planner-test")').to_output(tool_format)}
System: {{"status": "success", "result": "Tests complete in test_feature_x.py, all passing"}}.

### Context Modes

#### Full Context (default)
User: analyze this codebase
Assistant: I'll use full context mode for comprehensive analysis.
{ToolUse("ipython", [], 'subagent("analyze", "Analyze code quality and suggest improvements", context_mode="full")').to_output(tool_format)}

#### Instructions-Only Mode (minimal context)
User: compute the sum of 1 to 100
Assistant: For a simple computation, I'll use instructions-only mode with minimal context.
{ToolUse("ipython", [], 'subagent("sum", "Compute sum of integers from 1 to 100", context_mode="instructions-only")').to_output(tool_format)}

#### Selective Context (choose specific components)
User: write tests using pytest
Assistant: I'll use selective mode to share only tool descriptions, not workspace files.
{ToolUse("ipython", [], 'subagent("tests", "Write pytest tests for the calculate function", context_mode="selective", context_include=["tools"])').to_output(tool_format)}
""".strip()


instructions = """
You can create, check status and wait for subagents.
""".strip()

tool = ToolSpec(
    name="subagent",
    desc="Create and manage subagents",
    examples=examples,
    functions=[subagent, subagent_status, subagent_wait],
    disabled_by_default=True,
)
__doc__ = tool.get_doc(__doc__)
