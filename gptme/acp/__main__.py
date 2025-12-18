#!/usr/bin/env python
"""Run gptme as an ACP agent.

This is the entry point for running gptme as an ACP-compatible agent.
It can be invoked as:

    python -m gptme.acp

Or via the CLI:

    gptme --acp
"""

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the gptme ACP agent."""
    # Configure logging to stderr (stdout is used for ACP communication)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    try:
        from acp import run_agent  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "agent-client-protocol package not installed.\n"
            "Install with: pip install agent-client-protocol"
        )
        return 1

    from .agent import GptmeAgent

    logger.info("Starting gptme ACP agent...")

    try:
        asyncio.run(run_agent(GptmeAgent()))
        return 0
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Agent error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
