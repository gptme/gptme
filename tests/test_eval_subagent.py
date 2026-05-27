from gptme.eval.suites.subagent import (
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
