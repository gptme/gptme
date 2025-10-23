"""
Utilities for storing large tool outputs and creating compact references.

Implements the full/compact pattern: save full output to filesystem,
provide compact reference in conversation context.
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def save_large_output(
    content: str,
    logdir: Path | None,
    output_type: str = "tool-output",
    command_info: str | None = None,
    original_tokens: int | None = None,
    status: str | None = None,
) -> tuple[str, Path | None]:
    """
    Save large output to file and return compact summary with reference.

    Implements full/compact pattern: full content saved to file,
    compact reference returned for context.

    Args:
        content: The large output content to save
        logdir: Conversation directory for saving outputs
        output_type: Type of output (used for directory name, e.g., "shell", "python")
        command_info: Optional command/context information
        original_tokens: Optional token count for summary message
        status: Optional status (e.g., "completed", "failed")

    Returns:
        Tuple of (summary_text, saved_path)
        - summary_text: Compact reference message for context
        - saved_path: Path where full output was saved, or None if save failed
    """
    saved_path = None

    # Try to save content if logdir provided
    if logdir:
        output_dir = logdir / "tool-outputs" / output_type
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from timestamp and content hash
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
        filename = f"output-{timestamp}-{content_hash}.txt"
        saved_path = output_dir / filename

        # Try to save content
        try:
            saved_path.write_text(content)
            logger.info(f"Saved large output to {saved_path}")
        except Exception as e:
            logger.error(f"Failed to save large output: {e}")
            saved_path = None

    # Build summary message
    base_parts = ["[Large tool output removed"]

    if original_tokens:
        base_parts.append(f"- {original_tokens} tokens]:")
    else:
        base_parts.append("]:")

    base_parts.append("Tool execution")
    base_parts.append(status or "completed")

    if command_info:
        base_parts.append(f"({command_info})")

    base_msg = " ".join(base_parts) + "."

    # Add reference to file if saved
    if saved_path:
        reference = f"\nFull output saved to: {saved_path}\nYou can read or grep this file if needed."
        return f"{base_msg}{reference}", saved_path
    else:
        reference = "\nOutput was automatically removed due to size to allow conversation continuation."
        return f"{base_msg}{reference}", None


def create_tool_result_summary(
    content: str,
    original_tokens: int,
    logdir: Path | None,
    tool_name: str = "tool",
) -> str:
    """
    Create compact summary for a tool result, saving full content to file.

    This is a convenience wrapper around save_large_output for tool results,
    extracting command information and status from content.

    Args:
        content: Tool result content
        original_tokens: Number of tokens in original content
        logdir: Conversation directory for saving
        tool_name: Name of the tool that generated the result

    Returns:
        Compact summary message with reference to saved file
    """
    # Try to extract command information from content
    lines = content.split("\n")
    command_info = None

    for line in lines[:10]:  # Check first 10 lines
        if (
            line.startswith("Ran command:")
            or line.startswith("Executed:")
            or line.startswith("Running:")
        ):
            command_info = line.strip()
            break

    # Detect status from content
    status = "completed"
    if any(
        word in content.lower()
        for word in ["error", "failed", "exception", "traceback"]
    ):
        status = "failed"

    summary, _ = save_large_output(
        content=content,
        logdir=logdir,
        output_type=tool_name,
        command_info=command_info,
        original_tokens=original_tokens,
        status=status,
    )

    return summary
