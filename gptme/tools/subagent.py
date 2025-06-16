"""
A subagent tool for gptme

Lets gptme break down a task into smaller parts, and delegate them to subagents.
"""

import json
import logging
import random
import string
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..message import Message
from . import get_tools
from .base import ToolSpec, ToolUse

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
        # check if the last message contains the return JSON
        msg = self.get_log().log[-1].content.strip()
        json_response = _extract_json(msg)
        if not json_response:
            print(f"FAILED to find JSON in message: {msg}")
            return ReturnType("failure")
        elif not json_response.strip().startswith("{"):
            print(f"FAILED to parse JSON: {json_response}")
            return ReturnType("failure")
        else:
            return ReturnType(**json.loads(json_response))  # type: ignore


def _extract_json(s: str) -> str:
    first_brace = s.find("{")
    last_brace = s.rfind("}")
    return s[first_brace : last_brace + 1]


def subagent(agent_id: str, prompt: str):
    """Runs a subagent and returns the resulting JSON output."""
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
        initial_msgs = get_prompt(get_tools(), interactive=False, workspace=workspace)

        # add the return prompt
        return_prompt = """Thank you for doing the task, please reply with a JSON codeblock on the format:

```json
{
    result: 'A description of the task result/outcome',
    status: 'success' | 'failure',
}
```"""
        prompt_msgs.append(Message("user", return_prompt))

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

    print("Waiting for the subagent to finish...")
    subagent.thread.join(timeout=60)
    status = subagent.status()
    return asdict(status)


def examples(tool_format):
    return f"""
User: compute fib 13 using a subagent
Assistant: Starting a subagent to compute the 13th Fibonacci number.
{ToolUse("ipython", [], 'subagent("fib-13", "compute the 13th Fibonacci number")').to_output(tool_format)}
System: Subagent started successfully.
Assistant: Now we need to wait for the subagent to finish the task.
{ToolUse("ipython", [], 'subagent_wait("fib-13")').to_output(tool_format)}
System: {{"status": "success", "result": "The 13th Fibonacci number is 233"}}.
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
