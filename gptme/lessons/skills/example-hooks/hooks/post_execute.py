"""Post-execute hook example.

This hook runs after the skill's bundled scripts execute successfully.
"""

import logging

from gptme.lessons.hooks import HookContext

logger = logging.getLogger(__name__)


def execute(context: HookContext) -> None:
    """Post-execute hook.

    Args:
        context: Hook execution context
    """
    logger.info(f"Post-execute hook: Completed skill '{context.skill.title}'")

    # Example: Clean up after execution
    # - Release resources
    # - Save results
    # - Update state
    # - Log execution completion

    if context.extra:
        logger.debug(f"Extra context: {context.extra}")
