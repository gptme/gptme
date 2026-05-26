"""Trajectory-focused evals for the subagent tool."""

from typing import TYPE_CHECKING

from gptme.message import Message

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _role_contents(messages: list[Message], role: str) -> str:
    return "\n".join(msg.content for msg in messages if msg.role == role)


def _any_message_contains(messages: list[Message], role: str, needle: str) -> bool:
    return any(msg.role == role and needle in msg.content for msg in messages)


def _last_assistant_content(messages: list[Message]) -> str:
    assistants = [msg.content for msg in messages if msg.role == "assistant"]
    return assistants[-1] if assistants else ""


def check_subagent_parallel_used(messages: list[Message]) -> bool:
    """Parent log should show subagent delegation."""
    assistant_log = _role_contents(messages, "assistant")
    return "subagent(" in assistant_log or "subagent_batch(" in assistant_log


def check_subagent_parallel_started_before_wait(messages: list[Message]) -> bool:
    """Parallel work should be launched before any wait call."""
    assistant_log = _role_contents(messages, "assistant")
    first_wait = assistant_log.find("subagent_wait(")
    if first_wait == -1:
        return False
    before_wait = assistant_log[:first_wait]
    return (
        "subagent_batch(" in before_wait
        or 'mode="planner"' in before_wait
        or "mode='planner'" in before_wait
        or before_wait.count("subagent(") >= 2
    )


def check_subagent_parallel_integrated_results(messages: list[Message]) -> bool:
    """Final assistant reply should integrate all delegated results."""
    final_msg = _last_assistant_content(messages)
    return all(marker in final_msg for marker in ("WORDS=6", "LINES=4", "EXISTS=yes"))


def check_subagent_complete_spawned(messages: list[Message]) -> bool:
    """Parent log should show the roundtrip subagent being started."""
    assistant_log = _role_contents(messages, "assistant")
    return (
        'subagent("sum-roundtrip"' in assistant_log
        or "subagent('sum-roundtrip'" in assistant_log
    )


def check_subagent_complete_hook_notification(messages: list[Message]) -> bool:
    """Parent should receive the completion hook notification."""
    return _any_message_contains(
        messages,
        "system",
        "✅ Subagent 'sum-roundtrip' completed: COMPLETE_SUM: 5050",
    )


def check_subagent_complete_roundtrip_marker(messages: list[Message]) -> bool:
    """The completion marker should make it back to the parent log."""
    return _any_message_contains(messages, "system", "COMPLETE_SUM: 5050")


def check_subagent_complete_parent_result(messages: list[Message]) -> bool:
    """Final assistant reply should use the delegated result."""
    final_msg = _last_assistant_content(messages)
    return "SUM=5050" in final_msg or "5050" in final_msg


_PARALLEL_A = "alpha beta gamma delta epsilon zeta\n"
_PARALLEL_B = "one\ntwo\nthree\nfour\n"
_NOTES = "Keep this brief. The parent can read this between spawn and wait.\n"


tests: list["EvalSpec"] = [
    {
        "name": "subagent-parallel-delegation",
        "files": {
            "a.txt": _PARALLEL_A,
            "b.txt": _PARALLEL_B,
            "c.txt": "present\n",
        },
        "run": "cat answer.txt",
        "prompt": (
            "Use subagents, not parent-side direct computation, to solve three "
            "independent tasks concurrently: "
            "(1) count the whitespace-separated words in a.txt, "
            "(2) count the newline-delimited lines in b.txt, and "
            "(3) check whether c.txt exists. "
            "Start all delegated work before waiting on any single result. "
            "A planner-mode subagent or subagent_batch is fine; sequential one-at-a-time waiting is not. "
            "When finished, write answer.txt containing exactly:\n"
            "WORDS=6\nLINES=4\nEXISTS=yes\n"
            "and include those same three markers in your final assistant message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes WORDS marker": lambda ctx: "WORDS=6" in ctx.stdout,
            "writes LINES marker": lambda ctx: "LINES=4" in ctx.stdout,
            "writes EXISTS marker": lambda ctx: "EXISTS=yes" in ctx.stdout,
            "clean exit": lambda ctx: ctx.exit_code == 0,
        },
        "check_log": {
            "used subagent delegation": check_subagent_parallel_used,
            "started parallel work before waiting": check_subagent_parallel_started_before_wait,
            "integrated delegated results": check_subagent_parallel_integrated_results,
        },
    },
    {
        "name": "subagent-complete-roundtrip",
        "files": {
            "notes.txt": _NOTES,
        },
        "run": "cat answer.txt",
        "prompt": (
            "Delegate the computation to a subagent with agent_id 'sum-roundtrip'. "
            "In the subagent prompt, require it to use the complete tool and return exactly:\n"
            "COMPLETE_SUM: 5050\n"
            "Do not compute the sum in the parent. "
            "After spawning the subagent, do one brief parent-side step before waiting "
            "(for example, read notes.txt) so the hook system has a chance to deliver "
            "the completion notification. Then wait for the subagent, write answer.txt "
            "containing exactly:\nSUM=5050\n"
            "and mention 5050 in your final assistant message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes SUM marker": lambda ctx: "SUM=5050" in ctx.stdout,
            "clean exit": lambda ctx: ctx.exit_code == 0,
        },
        "check_log": {
            "spawned roundtrip subagent": check_subagent_complete_spawned,
            "received hook notification": check_subagent_complete_hook_notification,
            "roundtrip returned complete marker": check_subagent_complete_roundtrip_marker,
            "parent used delegated result": check_subagent_complete_parent_result,
        },
    },
]
