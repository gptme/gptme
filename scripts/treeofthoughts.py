"""
Tree-branching conversations for gptme with branch evaluation/prediction.

The idea is to evaluate if we are on the right track by checking if the current branch is "good"/making progress, and otherwise backtracking to the last good branch and trying a different prompt/approach.

The goal is to have a more autonomous agent which can self-supervise and make several branching attempts to find the right path to the solution.

TODO:
- [ ] add a "feedback" action which lets the critic give feedback as a user to the agent
- [ ] fix so that we undo to a meaningful point
- [ ] ask the agent wether changes are good before applying (tricky)
"""

import logging
import sys
from pathlib import Path
from typing import Literal

from gptme.chat import step as _step
from gptme.init import init
from gptme.logmanager import Log
from gptme.message import Message
from gptme.prompts import get_prompt
from gptme.tools import get_tools
from gptme.util.context import (
    gather_fresh_context,
    get_changed_files,
)
from gptme.tools.precommit import run_precommit_checks
from lxml import etree

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


EvalAction = Literal["continue", "undo", "done"]


def gather_changed_context() -> Message:
    """Gather fresh context focused on changed files."""
    # Create a dummy message with changed files
    dummy_msg = Message("system", "", files=get_changed_files())

    # Use gather_fresh_context with only the changed files
    return gather_fresh_context([dummy_msg], Path.cwd())


def llm_confirm(msg: str) -> bool:
    # TODO: asks a LLM if we should confirm
    return True


def step(log: Log) -> Log:
    # Steps the conversation forward
    for msg in _step(log, stream=True, confirm=llm_confirm):
        log = log.append(msg)
    return log


def recommendation(log: Log) -> EvalAction:
    # Returns a LLM-guided recommendation for the next action
    system_msg = Message(
        "system",
        """
We are evaluating the agent in the following conversation to determine the next action.

Please evaluate the current state of the conversation,
if the agent is making progress or if we should undo,
and then recommend the next action within <action></action> tags.

For example:
<action>continue</action> to let the agent continue (making progress)
<action>undo</action> to backtrack until last user prompt (made a mistake)
<action>done</action> if the agent has completed the task (e.g. answered the question)
""",
    )
    log_xml = "Here is the conversation to evaluate:\n"
    for msg in log:
        log_xml += msg.to_xml() + "\n"
    log = Log(
        [system_msg]
        + [Message("system", log_xml)]
        + [Message("user", "evaluate the agent")]
    )
    log = step(log)
    parser = etree.HTMLParser()
    tree = etree.fromstring(log[-1].content, parser)
    return tree.xpath("//action")[0].text


def update_context(log: Log) -> Log:
    """Update context in the conversation log."""
    # Remove any previous context messages
    msgs = [msg for msg in log if not msg.content.startswith("# Context")]
    # Add fresh context
    return Log(msgs + [gather_changed_context()])


def main():
    print("Initializing the autonomous agent...")
    init(
        model="anthropic",
        interactive=False,
        tool_allowlist=["python", "shell", "save", "patch"],
    )

    # Set up the conversation
    prompt = sys.argv[1] if len(sys.argv) > 1 else "What is fib(10)?"
    prompts = [Message("user", prompt)]
    tools = get_tools()
    initial_msgs = get_prompt(tools, prompt="full", interactive=False)
    log = Log(initial_msgs + prompts)

    # Main loop for autonomous operation
    max_iterations = 50
    iteration = 0
    progress = 0
    while iteration < max_iterations:
        iteration += 1

        # Check for pre-commit issues
        if precommit_output := run_precommit_checks():
            print("Pre-commit checks found issues:")
            print(precommit_output)
            system_msg = Message(
                "system",
                f"Pre-commit checks found issues:\n\n{precommit_output}",
            )
            log = log.append(system_msg)

        # Gather and update context
        log = update_context(log)
        print(f"Context updated. Iteration: {iteration}/{max_iterations}")

        # Step the conversation forward
        log = step(log)
        print("Conversation stepped forward.")

        # Get recommendation for next action
        action = recommendation(log)
        print(f"Recommended action: {action}")

        # Execute the recommended action
        if action == "continue":
            progress += 1
            print(f"Progress: {progress}")
            continue
        elif action == "undo":
            log = log.pop()
            progress = max(0, progress - 1)
            print(f"Undoing last step. Progress: {progress}")
        elif action == "done":
            print(f"Task completed successfully. Total iterations: {iteration}")
            break
        else:
            print(f"Unexpected action: {action}")
            break

    print(f"Exiting. Final progress: {progress}/{iteration}")


if __name__ == "__main__":
    main()
