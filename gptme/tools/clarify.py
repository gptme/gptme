"""Clarify tool — subagents use this to pause and ask the parent for more information.

Registers the ``clarify`` block type so the executor recognizes it as a valid
tool call and stops the session cleanly (instead of letting auto_reply_hook fire
and append follow-up messages that push the clarify block away from log[-1]).

Only enabled in autonomous/subagent sessions (disabled_by_default=True).
The parent receives a ❓ hook notification via subagent hooks and can answer
with ``subagent_reply(agent_id, reply)`` to re-spawn with full Q&A context.
"""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from ..message import Message
from .base import ToolSpec, ToolUse
from .complete import SessionCompleteException

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def clarify_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message, None, None]:
    """Detect a clarify block and stop the session immediately.

    Runs at GENERATION_PRE (before generating the next response) to halt the
    subagent session the moment it writes a clarify block, preventing
    auto_reply_hook from injecting a follow-up user message that would push the
    clarify block away from log[-1] and cause _read_log() to miss it.
    """
    # Make function a generator for type checking
    if False:
        yield

    if not messages:
        return

    # Only check the most recent assistant message in the current turn.
    last_user_idx = next(
        (
            len(messages) - 1 - i
            for i, m in enumerate(reversed(messages))
            if m.role == "user"
        ),
        None,
    )
    current_turn = (
        messages[last_user_idx + 1 :] if last_user_idx is not None else messages
    )

    last_assistant_msg = next(
        (m for m in reversed(current_turn) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    for tool_use in tool_uses:
        if tool_use.tool == "clarify":
            logger.info(
                "Clarify block detected — stopping subagent session for parent clarification"
            )
            raise SessionCompleteException(
                "Session paused: subagent needs clarification from parent"
            )


tool = ToolSpec(
    name="clarify",
    desc="Signal that the subagent needs clarification from the parent before continuing",
    disabled_by_default=True,
    instructions="""
Use this block when you cannot proceed without more information from the parent.
Write a specific, concise, answerable question inside the block.

```clarify
Your specific question here.
```

The parent will receive your question via a hook notification and can answer
it with ``subagent_reply(agent_id, reply)``. Do not use ``complete`` — using
``clarify`` pauses the session so the parent can answer and re-spawn you with
full context.
""".strip(),
    block_types=["clarify"],
    available=True,
    hooks={
        "clarify": (
            "generation.pre",  # HookType.GENERATION_PRE.value
            clarify_hook,
            999,  # Just below complete_hook priority (1000) so complete wins on ties
        )
    },
)
