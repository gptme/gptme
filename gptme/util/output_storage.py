"""
Utilities for storing large tool outputs and creating compact references.

Implements the full/compact pattern: save full output to filesystem,
provide compact reference in conversation context.
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Any
import json

logger = logging.getLogger(__name__)


@dataclass
class FullResult:
    """
    Complete tool result with all output and metadata.

    Stored in filesystem for later retrieval, not kept in conversation context.
    """

    tool: str
    timestamp: str  # ISO 8601 format
    command: str | None
    output: str
    meta: dict[str, Any]

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "FullResult":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(**data)

    def save(self, path: Path) -> None:
        """Save to file as JSON."""
        path.write_text(self.to_json())


@dataclass
class CompactReference:
    """
    Compact reference to a full result stored in filesystem.

    Included in conversation context instead of full output.
    """

    type: str  # Always "reference"
    path: str
    summary: dict[str, Any]

    def to_text(self) -> str:
        """Convert to human-readable text for context."""
        lines = [
            f"[Tool result reference: {self.summary.get('tool', 'unknown')}]",
            f"Status: {self.summary.get('status', 'unknown')}",
        ]

        if "command" in self.summary:
            lines.append(f"Command: {self.summary['command']}")

        if "lines" in self.summary:
            lines.append(f"Lines: {self.summary['lines']}")

        lines.append(f"Full output: {self.path}")

        return "\n".join(lines)


def save_structured_result(
    tool: str,
    command: str | None,
    output: str,
    meta: dict[str, Any],
    logdir: Path,
) -> tuple[CompactReference, FullResult]:
    """
    Save a structured tool result and return compact reference.

    Args:
        tool: Tool name (e.g., "shell", "python")
        command: Command that was executed
        output: Full output text
        meta: Metadata dictionary (duration, exit_code, etc.)
        logdir: Conversation directory for saving

    Returns:
        Tuple of (compact_reference, full_result)
    """
    # Create full result
    full_result = FullResult(
        tool=tool,
        timestamp=datetime.now().isoformat(),
        command=command,
        output=output,
        meta=meta,
    )

    # Create storage path
    output_dir = logdir / "tool-outputs" / tool
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    content_hash = hashlib.sha256(output.encode()).hexdigest()[:8]
    filename = f"result-{timestamp_str}-{content_hash}.json"
    result_path = output_dir / filename

    # Save full result
    full_result.save(result_path)
    logger.info(f"Saved structured result to {result_path}")

    # Create compact reference
    compact = CompactReference(
        type="reference",
        path=str(result_path),
        summary={
            "tool": tool,
            "command": command,
            "status": meta.get("status", "completed"),
            "lines": len(output.split("\n")),
        },
    )

    return compact, full_result


def load_full_result(path: Path) -> FullResult:
    """
    Load a full result from filesystem.

    Args:
        path: Path to the JSON result file

    Returns:
        FullResult object
    """
    json_str = path.read_text()
    return FullResult.from_json(json_str)


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
