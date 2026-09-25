"""
Helper for policy-block messages.

All five harness block sites route through here so the agent always sees:
  1. A consistent ``[policy-block]`` marker (countable by the episode walker).
  2. A FINALITY sentence that tells the agent the block is intentional and final.

Using a shared helper also decouples the marker string from call sites, so the
episode counter in ``complete.py`` only needs to search for the marker constant.
"""

import logging

from gptme.message import Message

logger = logging.getLogger(__name__)

POLICY_BLOCK_MARKER = "[policy-block]"

_FINALITY = (
    "This block is intentional and final, not a transient error. Do not retry it "
    "or reach the same outcome another way (other commands, encoding, scripts, "
    "wrappers, subagents). If a listed safer alternative fits, use it. Otherwise, "
    "continue with the rest of the task or finish with `complete` and report what "
    "was blocked."
)


def policy_block_message(source: str, detail: str) -> Message:
    """Return a system Message that marks a policy block.

    *source* is a short label for the block origin (e.g. "denylist", "guardrail",
    "read-guardrail").  It is logged at WARNING level but not shown to the agent —
    keeping agent-visible text concise and source-agnostic.
    """
    logger.warning("policy block (%s): %s", source, detail)
    return Message("system", f"{POLICY_BLOCK_MARKER} {detail}\n\n{_FINALITY}")
