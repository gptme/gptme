"""Error hook example.

This hook runs when an error occurs during skill execution.
"""

import logging

from gptme.lessons.hooks import HookContext

logger = logging.getLogger(__name__)


def execute(context: HookContext) -> None:
    """Error hook.

    Args:
        context: Hook execution context
    """
    logger.error(f"Error hook: Handling error in skill '{context.skill.title}'")

    # Example: Handle errors gracefully
    # - Log detailed error information
    # - Clean up partial resources
    # - Notify monitoring systems
    # - Attempt recovery actions

    if context.extra and "error" in context.extra:
        error = context.extra["error"]
        logger.error(f"Error details: {error}")
