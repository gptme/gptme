"""Trajectory-focused evals for the subagent tool."""

import re
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


def _expect_score_marker(ctx: "ResultContext") -> bool:
    return "SCORE=" in ctx.stdout


def _expect_reviewed_marker(ctx: "ResultContext") -> bool:
    return "REVIEWED=" in ctx.stdout


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
    # Accept both positional and keyword argument syntax
    return (
        'subagent("sum-roundtrip"' in assistant_log
        or "subagent('sum-roundtrip'" in assistant_log
        or 'subagent(agent_id="sum-roundtrip"' in assistant_log
        or "subagent(agent_id='sum-roundtrip'" in assistant_log
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
    # Accept both positional and keyword argument syntax
    assistant_log = _role_contents(messages, "assistant")
    return (
        'subagent("greeter"' in assistant_log
        or "subagent('greeter'" in assistant_log
        or 'subagent(agent_id="greeter"' in assistant_log
        or "subagent(agent_id='greeter'" in assistant_log
    )


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


def check_output_schema_used(messages: list[Message]) -> bool:
    """Parent should pass output_schema= when spawning the subagent."""
    assistant_log = _role_contents(messages, "assistant")
    return "output_schema=" in assistant_log


def check_output_schema_result_is_structured(messages: list[Message]) -> bool:
    """The result retrieved via subagent_wait should look like parsed JSON/dict.

    A trajectory-level check: the parent's final assistant message must contain
    a Python dict literal or JSON object that includes the 'score' key, proving
    the parent received and used a structured (not free-text) result.

    Pattern: matches `'score': <value>` (with dict/list delimiters before the key)
    where <value> is NOT a type keyword (int, str, etc), but IS a concrete value
    (number, string literal, object/array, or JSON constant).
    """
    final_msg = _last_assistant_content(messages)
    return (
        re.search(
            r"[\{\[,]\s*['\"]score['\"]\s*:\s*"
            r"(?!(?:int|str|float|bool|list|dict|tuple|set)(?:\b|[,\}\]]))"
            r"(?:-?\d+(?:\.\d+)?|['\"]|\{|\[|true\b|false\b|null\b|None\b)",
            final_msg,
        )
        is not None
    )


def check_output_schema_wait_called(messages: list[Message]) -> bool:
    """Parent must call subagent_wait after spawning the output_schema subagent."""
    return _any_message_contains(messages, "assistant", "subagent_wait(")


def check_pipeline_used(messages: list[Message]) -> bool:
    """Parent should use subagent_pipeline for staged fan-out."""
    return _any_message_contains(messages, "assistant", "subagent_pipeline(")


def check_pipeline_results_integrated(messages: list[Message]) -> bool:
    """Final assistant message should include results from the pipeline."""
    final_msg = _last_assistant_content(messages)
    return "REVIEWED=" in final_msg or "SUMMARY=" in final_msg


def check_pipeline_no_explicit_waits(messages: list[Message]) -> bool:
    """subagent_pipeline manages its own waits; the parent should not call subagent_wait.

    The point of subagent_pipeline is barrier-free fan-out — the helper handles
    scheduling internally. If the parent still calls subagent_wait() manually it
    defeats the purpose and suggests the agent misunderstood the API.
    """
    assistant_log = _role_contents(messages, "assistant")
    return "subagent_wait(" not in assistant_log


def _expect_race_marker(ctx: "ResultContext") -> bool:
    return (
        "RACE=done" in ctx.stdout
        and re.search(r"\bWINNER=(fast|thorough)\b", ctx.stdout) is not None
    )


def check_wait_any_used(messages: list[Message]) -> bool:
    """Parent should call subagent_wait_any to race two subagents."""
    return _any_message_contains(messages, "assistant", "subagent_wait_any(")


def check_wait_any_loser_cancelled(messages: list[Message]) -> bool:
    """Parent should cancel the agent that did not win the race.

    Accepts any of several common patterns for cancelling the non-winner:
    - Conditional: ``if var != first_id: subagent_cancel(var)``
    - Named loser/other/slower variable referencing first_id
    - Conditional on first_id value: ``if first_id == "fast": subagent_cancel("thorough")``
    - Ternary or comprehension: ``other = ...; subagent_cancel(other)``
    """
    assistant_log = _role_contents(messages, "assistant")
    if "subagent_cancel(" not in assistant_log:
        return False
    # Reject the obvious wrong pattern: directly cancelling the winner by name
    if re.search(r"subagent_cancel\(\s*first_id\s*\)", assistant_log):
        return False
    return any(
        re.search(pattern, assistant_log, re.DOTALL) is not None
        for pattern in (
            # if var != first_id: ... subagent_cancel(var)
            # Allows multi-line bodies and indented cancel calls
            r"if\s+\w+\s*!=\s*first_id\s*:.*?subagent_cancel\(",
            # Named loser/other/slower/remaining variable derived from first_id
            r"(?:loser|other|slower|remaining|winner_id)\s*=.*first_id.*\n.*subagent_cancel\(",
            # subagent_cancel(loser/other/slower/remaining) — named non-winner variable
            r"subagent_cancel\(\s*(?:loser|other|slower|remaining)\s*\)",
            # if first_id == <value>: subagent_cancel(<other>) — direct branch on winner
            r"if\s+first_id\s*==.*subagent_cancel\(",
        )
    )


def check_wait_any_result_used(messages: list[Message]) -> bool:
    """Final assistant message should reference the winning subagent result."""
    final_msg = _last_assistant_content(messages)
    return "WINNER=" in final_msg and "RACE=" in final_msg


def check_workdir_subagent_spawned(messages: list[Message]) -> bool:
    """Parent should spawn a subagent referencing the subdirectory."""
    assistant_log = _role_contents(messages, "assistant")
    return "subagent(" in assistant_log and "subproject" in assistant_log


def check_workdir_subprocess_params(messages: list[Message]) -> bool:
    """Parent should use subprocess mode and workdir parameter together.

    The whole point of the workdir eval is that subprocess-mode subagents run
    in a fresh process rooted at the given directory, so the subagent sees that
    directory's files without path prefixes.
    """
    assistant_log = _role_contents(messages, "assistant")
    return "use_subprocess=True" in assistant_log and "workdir=" in assistant_log


def check_workdir_result_returned(messages: list[Message]) -> bool:
    """Final assistant message should include the task marker from the subdir file."""
    final_msg = _last_assistant_content(messages)
    return "TASK_MARKER" in final_msg or "WORKDIR_OK" in final_msg


def _expect_task_marker(ctx: "ResultContext") -> bool:
    return "TASK_MARKER" in ctx.stdout


def _expect_workdir_ok_content(ctx: "ResultContext") -> bool:
    return "WORKDIR_OK=confirmed" in ctx.stdout


_PARALLEL_A = "alpha beta gamma delta epsilon zeta\n"
_PARALLEL_B = "one\ntwo\nthree\nfour\n"
_NOTES = "Keep this brief. The parent can read this between spawn and wait.\n"
_RACE_FILE = "The answer is FORTYTWO.\n"
_SUBPROJECT_TASK = "WORKDIR_OK=confirmed\n"


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
    {
        "name": "subagent-output-schema",
        "files": {
            "reviews.txt": (
                "Product A: excellent build quality, fast shipping\n"
                "Product B: poor packaging, slow delivery\n"
            ),
        },
        "run": "cat answer.txt",
        "prompt": (
            "Spawn a subagent with agent_id 'reviewer' to evaluate reviews.txt. "
            "Pass output_schema={'score': int, 'summary': str} so the subagent returns "
            "structured data. In the subagent prompt tell it to:\n"
            "  - Read reviews.txt\n"
            "  - Compute an overall quality score from 1-10\n"
            "  - Write a one-sentence summary\n"
            "  - Return the result via complete() as JSON: "
            '{"score": <int>, "summary": "<text>"}\n'
            "After spawning, wait for the subagent. "
            "The result from subagent_wait should be a parsed dict, not raw text. "
            "Write answer.txt containing:\n"
            "SCORE=<the integer score>\n"
            "SUMMARY=<the one-sentence summary>\n"
            "Include SCORE= in your final assistant message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes SCORE marker": _expect_score_marker,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "passed output_schema to subagent": check_output_schema_used,
            "called subagent_wait for result": check_output_schema_wait_called,
            "result contains structured score field": check_output_schema_result_is_structured,
        },
    },
    {
        "name": "subagent-pipeline-staged",
        "files": {
            "items.txt": "apple\nbanana\ncherry\n",
        },
        "run": "cat answer.txt",
        "prompt": (
            "Use subagent_pipeline to process items.txt in two stages without a barrier:\n"
            "Stage 1: for each fruit name, spawn a subagent that converts it to UPPERCASE.\n"
            "Stage 2: for each uppercased result, spawn a subagent that wraps it in "
            "'REVIEWED: <name>'.\n"
            "subagent_pipeline runs stage 2 on item A while item B is still in stage 1 — "
            "do NOT call subagent_wait manually; let subagent_pipeline manage scheduling.\n"
            "When finished, write answer.txt containing the three REVIEWED= lines, one per fruit.\n"
            "Include 'REVIEWED=' in your final assistant message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes REVIEWED marker": _expect_reviewed_marker,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "used subagent_pipeline": check_pipeline_used,
            "integrated pipeline results": check_pipeline_results_integrated,
            "did not call subagent_wait manually": check_pipeline_no_explicit_waits,
        },
    },
    {
        "name": "subagent-wait-any-race",
        "files": {
            "data.txt": _RACE_FILE,
        },
        "run": "cat answer.txt",
        "prompt": (
            "Demonstrate the race/hedging pattern with subagent_wait_any:\n"
            "1. Spawn TWO subagents concurrently — agent_id 'fast' and 'thorough' — "
            "each instructed to read data.txt and return its content via the complete tool.\n"
            "2. Call subagent_wait_any(['fast', 'thorough']) to wait for whichever "
            "finishes first.\n"
            "3. Cancel the slower agent with subagent_cancel().\n"
            "4. Write answer.txt containing:\n"
            "WINNER=<the agent_id that won>\n"
            "RACE=done\n"
            "Include both WINNER= and RACE= in your final assistant message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes RACE marker": _expect_race_marker,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "called subagent_wait_any": check_wait_any_used,
            "cancelled the losing agent": check_wait_any_loser_cancelled,
            "used winner result": check_wait_any_result_used,
        },
    },
    {
        "name": "subagent-workdir-subprocess",
        "files": {
            "subproject/task.txt": _SUBPROJECT_TASK,
        },
        "run": "cat answer.txt",
        "prompt": (
            "There is a subdirectory 'subproject/' in the current workspace that contains "
            "a file called 'task.txt'. "
            "Spawn a subprocess-mode subagent (use_subprocess=True) with "
            "workdir='./subproject' so the subagent's working directory is 'subproject/'. "
            "In the subagent's prompt, instruct it to read 'task.txt' (just the filename, "
            "not the full path — it will be in the subagent's working directory) and "
            "return the exact file content via the complete tool. "
            "Wait for the subagent to finish, then write answer.txt with exactly:\n"
            "TASK_MARKER=<the content the subagent returned>\n"
            "Include 'TASK_MARKER' in your final assistant message."
        ),
        "tools": ["read", "save", "shell", "ipython", "subagent"],
        "expect": {
            "writes TASK_MARKER": _expect_task_marker,
            "content contains WORKDIR_OK": _expect_workdir_ok_content,
            "clean exit": _expect_clean_exit,
        },
        "check_log": {
            "spawned subagent referencing subproject": check_workdir_subagent_spawned,
            "used subprocess mode and workdir": check_workdir_subprocess_params,
            "result returned to parent": check_workdir_result_returned,
        },
    },
]
