from .base import ToolSpec


class Completed:
    """Signal that the task has been completed."""

    def __init__(self, message: str, value: str | None) -> None:
        self.message = message
        self.value = value


def attempt_complete(value: str | None) -> Completed | None:
    """Attempt to complete the current task given by user."""
    print("Attempting to complete the current task.")
    if not check_completion():
        print("Task not completed.")
    # TODO: ask assistant "are you sure you want to complete the task?" to give it a chance to think some more
    # TODO: ask assistant "was the task completed successfully?" to get feedback

    # raise Completed to signal task finished, will gracefully exit
    return Completed("Task completed successfully", value)


def check_completion() -> bool:
    """Check if the current task has been completed."""
    # TODO: run actual completion checks, like pre-commit hooks, tests, etc.
    return True


tool = ToolSpec(
    "attempt_complete",
    desc="Completes the request. Used as part of autonomous operation to let the assistant signal that the request has completed.",
    instructions="Call this to attempt to complete the current task, after running final checks. Must ALWAYS be called to signal completion when done with all tasks.",
    functions=[attempt_complete],
    disabled_by_default=True,
)
