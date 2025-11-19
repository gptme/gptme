"""
Utilities for storing large tool outputs before truncation.

Simplified implementation that saves full output to filesystem
and provides path reference for later retrieval.
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def save_large_output(
    content: str,
    logdir: Path,
    output_type: str = "tool",
    command_info: str | None = None,
    original_tokens: int | None = None,
) -> tuple[str, Path]:
    """
    Save large output to filesystem before truncation.

    Args:
        content: Full output text to save
        logdir: Conversation directory for saving
        output_type: Type of tool output (e.g., "shell", "python")
        command_info: Optional command that generated output
        original_tokens: Optional token count of original content

    Returns:
        Tuple of (summary_text, saved_path)
    """
    # Create output directory
    output_dir = logdir / "tool-outputs" / output_type
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename with timestamp and content hash
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
    filename = f"{timestamp}-{content_hash}.txt"
    saved_path = output_dir / filename

    # Save content to file
    saved_path.write_text(content)
    logger.info(f"Saved large output to {saved_path}")

    # Create summary text
    summary_parts = [f"[Large {output_type} output saved]"]

    if command_info:
        summary_parts.append(f"Command: {command_info}")

    if original_tokens:
        summary_parts.append(f"Original: {original_tokens} tokens")

    summary_parts.append(f"Full output: {saved_path}")

    summary_text = "\n".join(summary_parts)

    return (summary_text, saved_path)


def create_tool_result_summary(
    content: str,
    original_tokens: int,
    logdir: Path | None,
    tool_name: str = "tool",
) -> str:
    """
    Create a summary of a large tool result, with content saved to file.

    This is used by autocompact to summarize massive tool outputs.

    Args:
        content: Full tool result content
        original_tokens: Number of tokens in original content
        logdir: Path to conversation directory for saving removed output
        tool_name: Name of tool that generated output

    Returns:
        Summary message with reference to saved file
    """
    # Try to extract command info from content
    lines = content.split("\n")
    command_info = None

    for line in lines[:10]:  # Check first 10 lines
        if (
            line.startswith("Ran command:")
            or line.startswith("Executed:")
            or line.startswith("Command:")
        ):
            command_info = line.strip()
            break

    # Create base message without status inference
    base_msg = f"[Large tool output removed - {original_tokens} tokens]"

    if command_info:
        base_msg += f" ({command_info})"

    # If no logdir, return simple message
    if not logdir:
        base_msg += "."
        return f"{base_msg} Output was automatically removed due to size to allow conversation continuation."

    # Save the output
    _, saved_path = save_large_output(
        content=content,
        logdir=logdir,
        output_type=tool_name,
        command_info=command_info,
        original_tokens=original_tokens,
    )

    # Return message with file reference
    return f"{base_msg}. Full output saved to: {saved_path}\nYou can read or grep this file if needed."
