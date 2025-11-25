"""Pre-execute hook example.

This hook runs before the skill's bundled scripts execute.
"""

import logging

from gptme.lessons.hooks import HookContext

logger = logging.getLogger(__name__)


def execute(context: HookContext) -> None:
    """Pre-execute hook.

    Args:
        context: Hook execution context
    """
    logger.info(f"Pre-execute hook: Preparing skill '{context.skill.title}'")

    # Example: Set up prerequisites
    # - Check dependencies
    # - Initialize resources
    # - Validate environment
    # - Log execution start

    if context.extra:
        logger.debug(f"Extra context: {context.extra}")
