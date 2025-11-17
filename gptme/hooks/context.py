"""Context management hooks for compression and selection."""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..message import Message

from ..config import get_config

logger = logging.getLogger(__name__)


def compression_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message, None, None]:
    """Compress context before generation if enabled.

    This hook runs in the GENERATION_PRE stage to compress system messages
    before they are sent to the LLM, reducing token usage and costs.

    Configuration:
        [context.compression]
        enabled = true          # Enable compression
        ratio = 0.15           # Target compression ratio (0.15 = keep 15% of sentences)
        min_length = 100       # Minimum content length to compress
    """
    config = get_config()

    # Check if compression is enabled (project-level config)
    if not config.project or not config.project.context.compression.enabled:
        # Pass through unchanged
        yield from messages
        return

    # Get compression parameters
    compression_config = config.project.context.compression
    ratio = compression_config.target_ratio
    min_length = compression_config.min_section_length

    # Import compression logic (lazy import to avoid circular dependencies)
    try:
        from ..context_compression.extractive import ExtractiveSummarizer
    except ImportError:
        logger.warning("Context compression module not available, skipping compression")
        yield from messages
        return

    # Create compressor with config
    compressor = ExtractiveSummarizer(compression_config)

    # Process each message
    for msg in messages:
        # Only compress long system messages
        if msg.role == "system" and len(msg.content) > min_length:
            try:
                result = compressor.compress(msg.content, target_ratio=ratio)
                # Replace message with compressed version
                compressed_msg = msg.replace(content=result.compressed)
                logger.info(
                    f"Compressed system message: {len(msg.content)} → {len(compressed_msg.content)} chars "
                    f"({result.compression_ratio:.1%} compression)"
                )
                yield compressed_msg
            except Exception as e:
                logger.error(f"Compression failed: {e}, using original message")
                yield msg
        else:
            # Pass through other messages unchanged
            yield msg


def register() -> None:
    """Register context management hooks."""
    from . import HookType, register_hook

    # Register compression hook
    register_hook(
        name="context_compression",
        hook_type=HookType.GENERATION_PRE,
        func=compression_hook,
        priority=10,  # Run early to transform context before generation
        enabled=True,  # Will be controlled by config at runtime
    )

    logger.info("Registered context management hooks")
