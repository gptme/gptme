import pickle

from gptme.eval.suites.subagent import (
    check_clarification_hook_notification,
    check_clarification_reply_called,
    check_clarification_reply_with_language,
    check_clarification_spawned,
    check_output_schema_result_is_structured,
    check_output_schema_used,
    check_output_schema_wait_called,
    check_pipeline_no_explicit_waits,
    check_pipeline_results_integrated,
    check_pipeline_used,
    check_subagent_complete_hook_notification,
    check_subagent_complete_parent_result,
    check_subagent_complete_roundtrip_marker,
    check_subagent_complete_spawned,
    check_subagent_complete_waited_before_result,
    check_subagent_parallel_integrated_results,
    check_subagent_parallel_started_before_wait,
    check_subagent_parallel_used,
    check_wait_any_loser_cancelled,
    check_wait_any_result_used,
    check_wait_any_used,
)
from gptme.eval.suites.subagent import tests as subagent_evals
from gptme.message import Message


def test_subagent_eval_specs_are_picklable():
    """run_evals() submits each EvalSpec through a ProcessPoolExecutor, which
    pickles the submitted args even at --parallel 1 (it always routes work
    through a picklable call queue). A lambda in `expect`/`check_log` crashes
    every run with a PicklingError before the model is ever invoked — the
    exact reason the subagent trajectory evals had never completed a live
    run. Guard against reintroducing inline lambdas here."""
    for spec in subagent_evals:
        for checks in (spec.get("expect", {}), spec.get("check_log", {})):
            for check in checks.values():
                pickle.dumps(check)


def test_parallel_checks_pass_for_planner_style_trajectory():
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("parallel-demo", "run tasks", mode="planner", execution_mode="parallel")\n```',
        ),
        Message(
            "assistant",
            '```ipython\nsubagent_wait("parallel-demo-a")\nsubagent_wait("parallel-demo-b")\nsubagent_wait("parallel-demo-c")\n```',
        ),
        Message("assistant", "Done. WORDS=6 LINES=4 EXISTS=yes"),
    ]

    assert check_subagent_parallel_used(messages)
    assert check_subagent_parallel_started_before_wait(messages)
    assert check_subagent_parallel_integrated_results(messages)


def test_parallel_checks_pass_for_subagent_batch_only_trajectory():
    """subagent_batch without explicit subagent_wait should still pass."""
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent_batch([{"agent_id": "a"}, {"agent_id": "b"}, {"agent_id": "c"}])\n```',
        ),
        Message("assistant", "Done. WORDS=6 LINES=4 EXISTS=yes"),
    ]

    assert check_subagent_parallel_used(messages)
    assert check_subagent_parallel_started_before_wait(messages)


def test_parallel_started_before_wait_rejects_sequential_launch():
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("only-one", "task one")\nsubagent_wait("only-one")\n```',
        ),
        Message("assistant", "Done. WORDS=6 LINES=4 EXISTS=yes"),
    ]

    assert not check_subagent_parallel_started_before_wait(messages)


def test_roundtrip_checks_pass_for_hook_completion_trajectory():
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("sum-roundtrip", "Return COMPLETE_SUM: 5050 via complete")\n```',
        ),
        Message("assistant", "I will read notes.txt before waiting."),
        Message(
            "system",
            "✅ Subagent 'sum-roundtrip' completed: COMPLETE_SUM: 5050",
        ),
        Message(
            "assistant",
            '```ipython\nsubagent_wait("sum-roundtrip")\n```',
        ),
        Message("assistant", "Finished. SUM=5050"),
    ]

    assert check_subagent_complete_spawned(messages)
    assert check_subagent_complete_hook_notification(messages)
    assert check_subagent_complete_roundtrip_marker(messages)
    assert check_subagent_complete_parent_result(messages)
    assert check_subagent_complete_waited_before_result(messages)


def test_roundtrip_hook_notification_is_required():
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("sum-roundtrip", "Return COMPLETE_SUM: 5050 via complete")\n```',
        ),
        Message("assistant", "Finished. SUM=5050"),
    ]

    assert not check_subagent_complete_hook_notification(messages)


def test_waited_before_result_accepts_explicit_wait_ordering():
    """An explicit subagent_wait before the result satisfies the ordering check
    even without the hook notification message."""
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("sum-roundtrip", "Return COMPLETE_SUM: 5050 via complete")\n```',
        ),
        Message(
            "assistant",
            '```ipython\nsubagent_wait("sum-roundtrip")\n```',
        ),
        Message("assistant", "Finished. SUM=5050"),
    ]

    assert check_subagent_complete_waited_before_result(messages)


def test_waited_before_result_rejects_fabricated_answer_before_completion():
    """Stating the result before any wait/completion is a fabricated trajectory
    that the outcome checks alone cannot catch."""
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("sum-roundtrip", "Return COMPLETE_SUM: 5050 via complete")\n```',
        ),
        # Parent states the answer immediately, before waiting or any completion.
        Message("assistant", "Finished. SUM=5050"),
        Message(
            "assistant",
            '```ipython\nsubagent_wait("sum-roundtrip")\n```',
        ),
    ]

    assert not check_subagent_complete_waited_before_result(messages)


def test_waited_before_result_rejects_fabricate_then_repeat():
    """Fabricating the result early then re-stating it after a real wait must fail.

    Without first-occurrence tracking, this trajectory would bypass the check
    because the *last* SUM=5050 appears after the wait.
    """
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("sum-roundtrip", "Return COMPLETE_SUM: 5050 via complete")\n```',
        ),
        # Parent fabricates the answer before waiting.
        Message("assistant", "I already know the answer is SUM=5050."),
        Message(
            "assistant",
            '```ipython\nsubagent_wait("sum-roundtrip")\n```',
        ),
        # Re-states the result after the wait — last occurrence would pass with
        # a naive "track last" strategy, but first occurrence already failed.
        Message("assistant", "Confirmed. SUM=5050"),
    ]

    assert not check_subagent_complete_waited_before_result(messages)


def test_clarification_checks_pass_for_full_roundtrip():
    """Clarification eval: spawn → hook notification → subagent_reply → completion."""
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("greeter", "Write a greeting. Ask for language via clarify.")\n```',
        ),
        Message("assistant", "I will read task.txt while waiting."),
        Message(
            "system",
            "❓ Subagent 'greeter' needs clarification: Which language should I use?\n"
            "Call subagent_reply('greeter', '<your answer>') to continue.",
        ),
        Message(
            "assistant",
            "```ipython\nsubagent_reply('greeter', 'English')\n```",
        ),
        Message(
            "system",
            "✅ Subagent 'greeter' completed: Hello, world!",
        ),
        Message("assistant", "GREETING=Hello, world!"),
    ]

    assert check_clarification_spawned(messages)
    assert check_clarification_hook_notification(messages)
    assert check_clarification_reply_called(messages)
    assert check_clarification_reply_with_language(messages)


def test_clarification_checks_fail_without_reply():
    """Missing subagent_reply should fail the reply check."""
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("greeter", "Write a greeting.")\n```',
        ),
        Message(
            "system",
            "❓ Subagent 'greeter' needs clarification: Which language?\n"
            "Call subagent_reply('greeter', '<your answer>') to continue.",
        ),
        # Parent ignores the clarification and just writes the answer itself
        Message("assistant", "GREETING=Hello!"),
    ]

    assert not check_clarification_reply_called(messages)


def test_output_schema_checks_pass_for_full_trajectory():
    """output_schema eval: spawn with schema → wait → use structured result."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            "subagent('reviewer', 'Evaluate reviews.txt', output_schema={'score': int, 'summary': str})\n"
            "```",
        ),
        Message(
            "assistant",
            "```ipython\nresult = subagent_wait('reviewer')\n```",
        ),
        Message(
            "assistant",
            "The structured result was {'score': 7, 'summary': 'Generally positive'}. SCORE=7",
        ),
    ]

    assert check_output_schema_used(messages)
    assert check_output_schema_wait_called(messages)
    assert check_output_schema_result_is_structured(messages)


def test_output_schema_check_fails_without_schema_param():
    """Spawning without output_schema= should fail the schema check."""
    messages = [
        Message(
            "assistant",
            "```ipython\nsubagent('reviewer', 'Evaluate reviews.txt')\n```",
        ),
        Message(
            "assistant",
            "```ipython\nresult = subagent_wait('reviewer')\n```",
        ),
        Message("assistant", "SCORE=7"),
    ]

    assert not check_output_schema_used(messages)


def test_output_schema_wait_check_fails_without_wait_call():
    """Omitting subagent_wait should fail the wait-call check."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            "subagent('reviewer', 'Evaluate reviews.txt', output_schema={'score': int, 'summary': str})\n"
            "```",
        ),
        Message(
            "assistant",
            "The structured result was {'score': 7, 'summary': 'Positive'}.",
        ),
    ]

    assert check_output_schema_used(messages)
    assert not check_output_schema_wait_called(messages)


def test_output_schema_structured_check_requires_score_field():
    """Final message without a score field fails the structured-result check."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            "subagent('reviewer', 'Evaluate', output_schema={'score': int, 'summary': str})\n"
            "```",
        ),
        Message(
            "assistant",
            "```ipython\nresult = subagent_wait('reviewer')\n```",
        ),
        # Parent uses the result but doesn't show any structured field
        Message("assistant", "The review looks mostly positive. SCORE=7 (from text)"),
    ]

    assert check_output_schema_used(messages)
    # Prose markers (uppercase, no delimiters) do not count as a structured field reference.
    # The check requires a score key inside a dict or list, not just text with "score=".
    assert not check_output_schema_result_is_structured(messages)


def test_output_schema_structured_check_rejects_lowercase_prose_score():
    """A lowercase prose score marker is still not a structured result."""
    messages = [
        Message("assistant", "The score=7 based on my analysis."),
    ]

    assert not check_output_schema_result_is_structured(messages)


def test_output_schema_structured_check_rejects_schema_definition_only():
    """Re-mentioning output_schema={'score': int} is not using the result."""
    messages = [
        Message(
            "assistant",
            "I used output_schema={'score': int, 'summary': str} and the overall rating was 8.",
        ),
    ]

    assert not check_output_schema_result_is_structured(messages)


def test_pipeline_checks_pass_for_staged_trajectory():
    """pipeline eval: subagent_pipeline call → results integrated → no manual waits."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            "results = subagent_pipeline(\n"
            "    ['apple', 'banana', 'cherry'],\n"
            "    lambda item, _: subagent(f'upper-{item}', f'Uppercase {item}'),\n"
            "    lambda item, prev: subagent(f'review-{item}', f'Wrap {prev} as REVIEWED: {prev}'),\n"
            ")\n"
            "```",
        ),
        Message("assistant", "Done. REVIEWED=APPLE REVIEWED=BANANA REVIEWED=CHERRY"),
    ]

    assert check_pipeline_used(messages)
    assert check_pipeline_results_integrated(messages)
    assert check_pipeline_no_explicit_waits(messages)


def test_pipeline_check_fails_when_subagent_wait_called_manually():
    """Using subagent_wait manually defeats pipeline's barrier-free contract."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            "results = subagent_pipeline(['apple'], stage1, stage2)\n"
            "subagent_wait('apple-stage1')\n"
            "```",
        ),
        Message("assistant", "Done. REVIEWED=APPLE"),
    ]

    assert check_pipeline_used(messages)
    assert not check_pipeline_no_explicit_waits(messages)


def test_pipeline_check_fails_when_pipeline_not_used():
    """Manually launching subagents without subagent_pipeline should not pass."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            "subagent('upper-apple', 'Uppercase apple')\n"
            "subagent_wait('upper-apple')\n"
            "subagent('review-apple', 'Wrap APPLE as REVIEWED')\n"
            "subagent_wait('review-apple')\n"
            "```",
        ),
        Message("assistant", "Done. REVIEWED=APPLE"),
    ]

    assert not check_pipeline_used(messages)


def test_wait_any_checks_pass_for_race_trajectory():
    """wait_any eval: spawn two racers → subagent_wait_any → cancel loser → report winner."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            'subagent("fast", "Read data.txt and return its content")\n'
            'subagent("thorough", "Read data.txt carefully and return its content")\n'
            "```",
        ),
        Message(
            "assistant",
            '```ipython\nfirst_id, result = subagent_wait_any(["fast", "thorough"])\n```',
        ),
        Message(
            "assistant",
            "```ipython\n"
            'for aid in ("fast", "thorough"):\n'
            "    if aid != first_id:\n"
            "        subagent_cancel(aid)\n"
            "```",
        ),
        Message("assistant", "WINNER=fast RACE=done"),
    ]

    assert check_wait_any_used(messages)
    assert check_wait_any_loser_cancelled(messages)
    assert check_wait_any_result_used(messages)


def test_wait_any_check_fails_without_wait_any_call():
    """Using subagent_wait instead of subagent_wait_any should fail the check."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            'subagent("fast", "Read data.txt")\n'
            'subagent("thorough", "Read data.txt")\n'
            'result = subagent_wait("fast")\n'
            "```",
        ),
        Message("assistant", "WINNER=fast RACE=done"),
    ]

    assert not check_wait_any_used(messages)


def test_wait_any_check_fails_without_cancel():
    """Omitting subagent_cancel on the loser should fail the cancel check."""
    messages = [
        Message(
            "assistant",
            "```ipython\n"
            'subagent("fast", "Read data.txt")\n'
            'subagent("thorough", "Read data.txt")\n'
            'first_id, result = subagent_wait_any(["fast", "thorough"])\n'
            "```",
        ),
        # Parent does not cancel the loser
        Message("assistant", "WINNER=fast RACE=done"),
    ]

    assert check_wait_any_used(messages)
    assert not check_wait_any_loser_cancelled(messages)
