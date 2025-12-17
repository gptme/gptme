"""
A subagent tool for gptme

Lets gptme break down a task into smaller parts, and delegate them to subagents.
"""

import logging
import random
import string
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from ..message import Message
from . import get_tools
from .base import ToolSpec, ToolUse


class SubtaskDef(TypedDict):
    """Definition of a subtask for planner mode."""

    id: str
    description: str


# Callback type definitions for async subagent hooks
OnCompleteCallback = Callable[["str", "ReturnType"], None]
OnProgressCallback = Callable[["str", "str", "str"], None]  # agent_id, status, output


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


@dataclass
class Subagent:
    """Represents a running or completed subagent.

    Supports both thread-based (default) and subprocess-based execution modes.
    Subprocess mode provides better output isolation.
    """

    agent_id: str
    prompt: str
    thread: threading.Thread | None
    logdir: Path
    model: str | None
    output_schema: type | None = None
    # Subprocess mode fields
    process: subprocess.Popen | None = None
    execution_mode: Literal["thread", "subprocess"] = "thread"
    # Callback hooks
    on_complete: OnCompleteCallback | None = None
    on_progress: OnProgressCallback | None = None
    # Cached result for subprocess mode
    _cached_result: ReturnType | None = field(default=None, repr=False)

    def get_log(self) -> "LogManager":
        # noreorder
        from ..logmanager import LogManager  # fmt: skip

        return LogManager.load(self.logdir)

    def is_running(self) -> bool:
        """Check if the subagent is still running."""
        if self.execution_mode == "subprocess" and self.process:
            return self.process.poll() is None
        elif self.thread:
            return self.thread.is_alive()
        return False

    def status(self) -> ReturnType:
        # Return cached result if available (subprocess mode)
        if self._cached_result is not None:
            return self._cached_result

        if self.is_running():
            return ReturnType("running")

        # Check if executor used the complete tool
        log = self.get_log().log
        if not log:
            return ReturnType("failure", "No messages in log")

        last_msg = log[-1]

        # Check for complete tool call in last message
        # Try parsing as ToolUse first
        tool_uses = list(ToolUse.iter_from_content(last_msg.content))
        complete_tool = next((tu for tu in tool_uses if tu.tool == "complete"), None)

        if complete_tool:
            # Extract content from complete tool
            result = complete_tool.content or "Task completed"
            return ReturnType(
                "success",
                result + f"\n\nFull log: {self.logdir}",
            )

        # Fallback: Check for complete code block directly
        if "```complete" in last_msg.content:
            # Extract content between ```complete and ```
            import re

            match = re.search(
                r"```complete\s*\n(.*?)\n```", last_msg.content, re.DOTALL
            )
            if match:
                result = match.group(1).strip() or "Task completed"
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


def _create_subagent_thread(
    prompt: str,
    logdir: Path,
    model: str | None,
    context_mode: Literal["full", "instructions-only", "selective"],
    context_include: list[str] | None,
    workspace: Path,
    target: str = "parent",
    output_schema: type | None = None,
) -> None:
    """Shared function for running subagent threads.

    Args:
        prompt: Task prompt for the subagent
        logdir: Directory for conversation logs
        model: Model to use for the subagent
        context_mode: Controls what context is shared
        context_include: For selective mode, list of context components
        workspace: Workspace directory
        target: Who will review the results ("parent" or "planner")
    """
    # noreorder
    from gptme import chat  # fmt: skip
    from gptme.llm.models import set_default_model  # fmt: skip
    from gptme.tools import init_tools  # fmt: skip

    from ..prompts import get_prompt  # fmt: skip

    # Initialize model and tools for this thread
    if model:
        set_default_model(model)
    init_tools(allowlist=None)

    prompt_msgs = [Message("user", prompt)]

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

        # Type narrowing: context_include validated as not None by caller
        assert context_include is not None

        # Add components based on context_include
        if "agent" in context_include:
            initial_msgs.extend(list(prompt_gptme(False, None, agent_name=None)))
        if "tools" in context_include:
            initial_msgs.extend(
                list(prompt_tools(tools=get_tools(), tool_format="markdown"))
            )
    else:  # "full" mode (default)
        # Full context
        initial_msgs = get_prompt(get_tools(), interactive=False, workspace=workspace)

    # Add completion instruction as a system message
    complete_instruction = Message(
        "system",
        "When you have finished the task, use the `complete` tool to signal completion:\n"
        "```complete\n"
        "Brief summary of what was accomplished.\n"
        "```\n\n"
        f"This signals task completion. The full conversation log will be available to the {target} for review.",
    )
    initial_msgs.append(complete_instruction)

    # Note: workspace parameter is always passed to chat() (required parameter)
    # Workspace context in messages is controlled by initial_msgs
    chat(
        prompt_msgs,
        initial_msgs,
        logdir=logdir,
        workspace=workspace,
        model=model,
        stream=False,
        no_confirm=True,
        interactive=False,
        show_hidden=False,
        tool_format="markdown",
        output_schema=output_schema,
    )


def _run_subagent_subprocess(
    prompt: str,
    logdir: Path,
    model: str | None,
    workspace: Path,
) -> subprocess.Popen:
    """Run a subagent in a subprocess for output isolation.

    This provides better isolation than threads - subprocess stdout/stderr
    doesn't mix with the parent's output.

    Args:
        prompt: Task prompt for the subagent
        logdir: Directory for conversation logs
        model: Model to use (or None for default)
        workspace: Workspace directory

    Returns:
        The subprocess.Popen object for monitoring
    """
    cmd = [
        sys.executable,
        "-m",
        "gptme",
        "-n",  # Non-interactive
        "--no-confirm",
        f"--logdir={logdir}",
    ]

    if model:
        cmd.extend(["--model", model])

    # Add the prompt as the final argument
    cmd.append(prompt)

    # Start subprocess with captured output
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=workspace,
        text=True,
    )

    return process


def _monitor_subprocess(
    subagent: "Subagent",
) -> None:
    """Monitor a subprocess and invoke callbacks when it completes.

    Runs in a background thread to enable non-blocking operation.
    """
    if not subagent.process:
        return

    # Wait for process to complete
    stdout, stderr = subagent.process.communicate()

    # Determine status based on return code
    if subagent.process.returncode == 0:
        status: Status = "success"
        # Try to extract result from stdout or log
        result = stdout.strip() if stdout else None
        if not result:
            # Try to get result from conversation log
            try:
                log_status = subagent.status()
                result = log_status.result
            except Exception:
                result = "Task completed (check log for details)"
    else:
        status = "failure"
        result = f"Process exited with code {subagent.process.returncode}"
        if stderr:
            result += f"\nStderr: {stderr[:500]}"

    # Cache the result
    final_result = ReturnType(status, result)
    object.__setattr__(subagent, "_cached_result", final_result)

    # Invoke on_complete callback if set
    if subagent.on_complete:
        try:
            subagent.on_complete(subagent.agent_id, final_result)
        except Exception as e:
            logger.warning(f"on_complete callback failed: {e}")


def _run_planner(
    agent_id: str,
    prompt: str,
    subtasks: list[SubtaskDef],
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "instructions-only", "selective"] = "full",
    context_include: list[str] | None = None,
    model: str | None = None,
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
    from gptme.cli import get_logdir

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
            _create_subagent_thread(
                prompt=prompt,
                logdir=log_dir,
                model=model,
                context_mode=context_mode,
                context_include=context_include,
                workspace=Path.cwd(),
                target="planner",
            )

        t = threading.Thread(target=run_executor, daemon=True)
        t.start()
        threads.append(t)
        _subagents.append(Subagent(executor_id, executor_prompt, t, logdir, model))

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
    output_schema: type | None = None,
    # New Phase 1 parameters
    use_subprocess: bool = False,
    on_complete: OnCompleteCallback | None = None,
    on_progress: OnProgressCallback | None = None,
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
        use_subprocess: If True, run subagent in subprocess for output isolation.
            Subprocess mode captures stdout/stderr separately from the parent.
        on_complete: Callback function(agent_id, result) invoked when subagent completes.
        on_progress: Callback function(agent_id, status, output) for progress updates.

    Returns:
        None: Starts asynchronous execution. Use subagent_wait() to retrieve output.
            In executor mode, starts a single task execution.
            In planner mode, starts execution of all subtasks using the specified execution_mode.

            Executors use the `complete` tool to signal completion with a summary.
            The full conversation log is available at the logdir path.
    """
    # noreorder
    from gptme.cli import get_logdir  # fmt: skip
    from gptme.llm.models import get_default_model  # fmt: skip

    # Get current model from parent conversation (needed for both executor and planner modes)
    current_model = get_default_model()
    model_name = current_model.full if current_model else None

    if mode == "planner":
        if not subtasks:
            raise ValueError("Planner mode requires subtasks parameter")
        return _run_planner(
            agent_id,
            prompt,
            subtasks,
            execution_mode,
            context_mode,
            context_include,
            model_name,
        )

    # Validate context_mode parameters
    if context_mode == "selective" and not context_include:
        raise ValueError(
            "context_include parameter required when context_mode='selective'"
        )

    def random_string(n):
        s = string.ascii_lowercase + string.digits
        return "".join(random.choice(s) for _ in range(n))

    name = f"subagent-{agent_id}"
    logdir = get_logdir(name + "-" + random_string(4))
    workspace = Path.cwd()

    if use_subprocess:
        # Subprocess mode: better output isolation
        logger.info(f"Starting subagent {agent_id} in subprocess mode")
        process = _run_subagent_subprocess(
            prompt=prompt,
            logdir=logdir,
            model=model_name,
            workspace=workspace,
        )

        # Create Subagent with subprocess reference
        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=None,
            logdir=logdir,
            model=model_name,
            output_schema=output_schema,
            process=process,
            execution_mode="subprocess",
            on_complete=on_complete,
            on_progress=on_progress,
        )
        _subagents.append(sa)

        # Start monitor thread to handle callbacks
        if on_complete or on_progress:
            monitor_thread = threading.Thread(
                target=_monitor_subprocess,
                args=(sa,),
                daemon=True,
            )
            monitor_thread.start()
    else:
        # Thread mode: original behavior
        def run_subagent():
            _create_subagent_thread(
                prompt=prompt,
                logdir=logdir,
                model=model_name,
                context_mode=context_mode,
                context_include=context_include,
                workspace=workspace,
                target="parent",
                output_schema=output_schema,
            )

            # Invoke callback after completion
            if on_complete:
                sa = next((s for s in _subagents if s.agent_id == agent_id), None)
                if sa:
                    try:
                        on_complete(agent_id, sa.status())
                    except Exception as e:
                        logger.warning(f"on_complete callback failed: {e}")

        # Start a thread with a subagent
        t = threading.Thread(
            target=run_subagent,
            daemon=True,
        )
        t.start()

        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=t,
            logdir=logdir,
            model=model_name,
            output_schema=output_schema,
            process=None,
            execution_mode="thread",
            on_complete=on_complete,
            on_progress=on_progress,
        )
        _subagents.append(sa)


def subagent_status(agent_id: str) -> dict:
    """Returns the status of a subagent."""
    for subagent in _subagents:
        if subagent.agent_id == agent_id:
            return asdict(subagent.status())
    raise ValueError(f"Subagent with ID {agent_id} not found.")


def subagent_wait(agent_id: str, timeout: int = 60) -> dict:
    """Waits for a subagent to finish.

    Args:
        agent_id: The subagent to wait for
        timeout: Maximum seconds to wait (default 60)

    Returns:
        Status dict with 'status' and 'result' keys
    """
    sa = None
    for s in _subagents:
        if s.agent_id == agent_id:
            sa = s
            break

    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")

    logger.info(f"Waiting for subagent {agent_id} to finish...")

    if sa.execution_mode == "subprocess" and sa.process:
        # Subprocess mode: wait for process
        try:
            sa.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(f"Subagent {agent_id} timed out after {timeout}s")
            sa.process.kill()
    elif sa.thread:
        # Thread mode: join thread
        sa.thread.join(timeout=timeout)

    status = sa.status()
    return asdict(status)


@dataclass
class BatchJob:
    """Manages a batch of subagents for parallel execution."""

    agent_ids: list[str]
    results: dict[str, ReturnType] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _on_complete(self, agent_id: str, result: ReturnType) -> None:
        """Internal callback to collect results."""
        with self._lock:
            self.results[agent_id] = result

    def wait_all(self, timeout: int = 300) -> dict[str, dict]:
        """Wait for all subagents to complete.

        Args:
            timeout: Maximum seconds to wait for all subagents

        Returns:
            Dict mapping agent_id to status dict
        """
        import time

        start_time = time.time()
        for agent_id in self.agent_ids:
            remaining = max(1, timeout - int(time.time() - start_time))
            try:
                result = subagent_wait(agent_id, timeout=remaining)
                with self._lock:
                    if agent_id not in self.results:
                        self.results[agent_id] = ReturnType(
                            result.get("status", "failure"),
                            result.get("result"),
                        )
            except Exception as e:
                logger.warning(f"Error waiting for {agent_id}: {e}")
                with self._lock:
                    self.results[agent_id] = ReturnType("failure", str(e))

        return {aid: asdict(r) for aid, r in self.results.items()}

    def is_complete(self) -> bool:
        """Check if all subagents have completed."""
        return len(self.results) == len(self.agent_ids)

    def get_completed(self) -> dict[str, dict]:
        """Get results of completed subagents so far."""
        with self._lock:
            return {aid: asdict(r) for aid, r in self.results.items()}


def subagent_batch(
    tasks: list[tuple[str, str]],
    use_subprocess: bool = False,
    on_complete: OnCompleteCallback | None = None,
) -> BatchJob:
    """Start multiple subagents in parallel and return a BatchJob to manage them.

    This is a convenience function for fire-and-gather patterns where you want
    to run multiple independent tasks concurrently.

    Args:
        tasks: List of (agent_id, prompt) tuples
        use_subprocess: If True, run subagents in subprocesses for output isolation
        on_complete: Optional callback invoked when each subagent completes

    Returns:
        BatchJob object with methods:
        - wait_all(timeout): Wait for all subagents, returns dict of results
        - is_complete(): Check if all subagents finished
        - get_completed(): Get results so far

    Example:
        job = subagent_batch([
            ("impl", "Implement feature X"),
            ("test", "Write tests for feature X"),
            ("docs", "Document feature X"),
        ])
        # Do other work while subagents run...
        results = job.wait_all(timeout=300)
        for agent_id, result in results.items():
            print(f"{agent_id}: {result['status']}")
    """
    job = BatchJob(agent_ids=[t[0] for t in tasks])

    def combined_callback(agent_id: str, result: ReturnType) -> None:
        """Collect results and call user callback."""
        job._on_complete(agent_id, result)
        if on_complete:
            on_complete(agent_id, result)

    # Start all subagents
    for agent_id, prompt in tasks:
        subagent(
            agent_id=agent_id,
            prompt=prompt,
            use_subprocess=use_subprocess,
            on_complete=combined_callback,
        )

    logger.info(f"Started batch of {len(tasks)} subagents: {job.agent_ids}")
    return job


def subagent_read_log(
    agent_id: str,
    max_messages: int = 50,
    include_system: bool = False,
    message_filter: str | None = None,
) -> str:
    """Read the conversation log of a subagent.

    Args:
        agent_id: The subagent to read logs from
        max_messages: Maximum number of messages to return
        include_system: Whether to include system messages
        message_filter: Filter messages by role (user/assistant/system) or None for all

    Returns:
        Formatted log output showing the conversation
    """
    subagent = None
    for sa in _subagents:
        if sa.agent_id == agent_id:
            subagent = sa
            break

    if subagent is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")

    try:
        log_manager = subagent.get_log()
        messages = log_manager.log.messages

        # Filter messages
        if not include_system:
            messages = [m for m in messages if m.role != "system" or not m.hide]
        if message_filter:
            messages = [m for m in messages if m.role == message_filter]

        # Limit number of messages
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        # Format output
        output = f"=== Subagent Log: {agent_id} ===\n"
        output += f"Total messages: {len(messages)}\n"
        output += f"Logdir: {subagent.logdir}\n\n"

        for msg in messages:
            timestamp = msg.timestamp.strftime("%H:%M:%S") if msg.timestamp else "N/A"
            content_preview = (
                msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
            )
            output += f"[{timestamp}] {msg.role}:\n{content_preview}\n\n"

        return output
    except Exception as e:
        return f"Error reading log: {e}\nLogdir: {subagent.logdir}"


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

### Subprocess Mode (output isolation)
User: run a subagent without output mixing with parent
Assistant: I'll use subprocess mode for better output isolation.
{ToolUse("ipython", [], 'subagent("isolated", "Compute complex calculation", use_subprocess=True)').to_output(tool_format)}
System: Subagent started in subprocess mode.

### Batch Execution (parallel tasks)
User: implement, test, and document a feature in parallel
Assistant: I'll use subagent_batch for parallel execution with fire-and-gather pattern.
{ToolUse("ipython", [], '''job = subagent_batch([
    ("impl", "Implement the user authentication feature"),
    ("test", "Write tests for authentication"),
    ("docs", "Document the authentication API"),
])
# Do other work while subagents run...
results = job.wait_all(timeout=300)
for agent_id, result in results.items():
    print(f"{{agent_id}}: {{result['status']}}")''').to_output(tool_format)}
System: Started batch of 3 subagents: ['impl', 'test', 'docs']
impl: success
test: success
docs: success

### Callbacks for Async Progress
User: start a subagent with completion notification
Assistant: I'll use a callback to be notified when the subagent completes.
{ToolUse("ipython", [], '''def on_done(agent_id, result):
    print(f"Agent {{agent_id}} finished with {{result.status}}")

subagent("callback-demo", "Compute pi to 100 digits", on_complete=on_done)''').to_output(tool_format)}
System: Subagent started. Callback will be invoked on completion.
""".strip()


instructions = """
You can create, check status, wait for, and read logs from subagents.

Use subagent_read_log() to inspect a subagent's conversation log for debugging or detailed understanding of what happened.

New in Phase 1:
- use_subprocess=True: Run subagent in subprocess for output isolation
- on_complete callback: Get notified when subagent finishes
- subagent_batch(): Start multiple subagents in parallel with fire-and-gather pattern
""".strip()

tool = ToolSpec(
    name="subagent",
    desc="Create and manage subagents",
    examples=examples,
    functions=[
        subagent,
        subagent_status,
        subagent_wait,
        subagent_read_log,
        subagent_batch,
    ],
    disabled_by_default=True,
)
__doc__ = tool.get_doc(__doc__)
