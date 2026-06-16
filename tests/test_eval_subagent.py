from gptme.eval.suites.subagent import (
    check_clarification_hook_notification,
    check_clarification_reply_called,
    check_clarification_reply_with_language,
    check_clarification_spawned,
    check_subagent_complete_hook_notification,
    check_subagent_complete_parent_result,
    check_subagent_complete_roundtrip_marker,
    check_subagent_complete_spawned,
    check_subagent_complete_waited_before_result,
    check_subagent_parallel_integrated_results,
    check_subagent_parallel_started_before_wait,
    check_subagent_parallel_used,
)
from gptme.message import Message


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
