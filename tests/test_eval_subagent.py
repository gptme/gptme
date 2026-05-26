from gptme.eval.suites.subagent import (
    check_subagent_complete_hook_notification,
    check_subagent_complete_parent_result,
    check_subagent_complete_roundtrip_marker,
    check_subagent_complete_spawned,
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


def test_roundtrip_hook_notification_is_required():
    messages = [
        Message(
            "assistant",
            '```ipython\nsubagent("sum-roundtrip", "Return COMPLETE_SUM: 5050 via complete")\n```',
        ),
        Message("assistant", "Finished. SUM=5050"),
    ]

    assert not check_subagent_complete_hook_notification(messages)
