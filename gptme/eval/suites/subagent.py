"""Trajectory-focused evals for the subagent tool."""

from typing import TYPE_CHECKING

from gptme.message import Message

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec, ResultContext


# `expect`/`check_log` callables are stored in the EvalSpec dict, which gets
# pickled by ProcessPoolExecutor.submit() even when running with --parallel 1
# (the executor always routes submissions through a picklable call queue).
# Lambdas can't be pickled ("attribute lookup <lambda> ... failed"), so every
# check here must be a named module-level function, not an inline lambda.
def _expect_words_marker(ctx: "ResultContext") -> bool:
    return "WORDS=6" in ctx.stdout


def _expect_lines_marker(ctx: "ResultContext") -> bool:
    return "LINES=4" in ctx.stdout


def _expect_exists_marker(ctx: "ResultContext") -> bool:
    return "EXISTS=yes" in ctx.stdout


def _expect_clean_exit(ctx: "ResultContext") -> bool:
    return ctx.exit_code == 0


def _expect_sum_marker(ctx: "ResultContext") -> bool:
    return "SUM=5050" in ctx.stdout


def _expect_greeting_marker(ctx: "ResultContext") -> bool:
    return "GREETING=" in ctx.stdout


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
        # subagent_batch manages its own parallelism without explicit waits
        return "subagent_batch(" in assistant_log
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


def check_subagent_complete_waited_before_result(messages: list[Message]) -> bool:
    """Parent must wait for (or be notified of) completion before stating the result.

    A trajectory-only guard: the outcome checks pass whenever ``SUM=5050`` lands
    in the final message or ``answer.txt``, even if the parent *fabricated* the
    answer before the subagent actually finished. This verifies ordering — a
    ``subagent_wait(...)`` call or the completion hook notification must appear
    before the first assistant message that states ``SUM=5050``.

    Tracking the *first* occurrence (not the last) ensures that fabricate-early
    trajectories fail even if the agent re-states the result after a later wait.
    """
    completion_idx = None
    result_idx = None
    for i, msg in enumerate(messages):
        if completion_idx is None and (
            (msg.role == "assistant" and "subagent_wait(" in msg.content)
            or (
                msg.role == "system"
                and "✅ Subagent 'sum-roundtrip' completed" in msg.content
            )
        ):
            completion_idx = i
        if result_idx is None and msg.role == "assistant" and "SUM=5050" in msg.content:
            result_idx = i  # first result-bearing message
    if completion_idx is None or result_idx is None:
        return False
    return completion_idx <= result_idx


def check_clarification_spawned(messages: list[Message]) -> bool:
    """Parent log should show the clarification subagent being started."""
    return _any_message_contains(
        messages, "assistant", 'subagent("greeter"'
    ) or _any_message_contains(messages, "assistant", "subagent('greeter'")


def check_clarification_hook_notification(messages: list[Message]) -> bool:
    """Parent should receive the clarification hook notification."""
    return _any_message_contains(messages, "system", "❓") and _any_message_contains(
        messages, "system", "greeter"
    )


def check_clarification_reply_called(messages: list[Message]) -> bool:
    """Parent must call subagent_reply to resume the clarifying subagent."""
    return _any_message_contains(messages, "assistant", "subagent_reply(")


def check_clarification_reply_with_language(messages: list[Message]) -> bool:
    """The subagent_reply call must supply a language answer."""
    return any(
        m.role == "assistant"
        and "subagent_reply(" in m.content
        and "English" in m.content
        for m in messages
    )


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
            "writes WORDS marker": _expect_words_marker,
            "writes LINES marker": _expect_lines_marker,
            "writes EXISTS marker": _expect_exists_marker,
            "clean exit": _expect_clean_exit,
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
            "writes SUM marker": _expect_sum_marker,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "spawned roundtrip subagent": check_subagent_complete_spawned,
            "received hook notification": check_subagent_complete_hook_notification,
            "roundtrip returned complete marker": check_subagent_complete_roundtrip_marker,
            "parent used delegated result": check_subagent_complete_parent_result,
            "waited before stating result": check_subagent_complete_waited_before_result,
        },
    },
    {
        "name": "subagent-clarification-roundtrip",
        "files": {
            "task.txt": "Write a greeting in the requested language.\n",
        },
        "run": "cat answer.txt",
        "prompt": (
            "Spawn a subagent with agent_id 'greeter' to write a greeting. "
            "The subagent's prompt must instruct it to use a `clarify` block "
            "asking which language to use (not `complete` — it genuinely does not know). "
            "After spawning, do a brief parent-side step (read task.txt) so the hook "
            "can deliver the clarification notification. "
            "When you receive the ❓ system message, call "
            "subagent_reply('greeter', 'English') to resume the subagent with the answer. "
            "Wait for the resumed subagent to finish. "
            "Write answer.txt containing the greeting the subagent produced, "
            "and include the word GREETING= followed by the greeting in your final message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes GREETING marker": _expect_greeting_marker,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "spawned greeter subagent": check_clarification_spawned,
            "received clarification hook notification": check_clarification_hook_notification,
            "called subagent_reply": check_clarification_reply_called,
            "replied with English": check_clarification_reply_with_language,
        },
    },
]
