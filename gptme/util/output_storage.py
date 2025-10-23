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

    # Create compact reference using schema if available
    schema = get_schema(tool)
    if schema:
        summary = schema.summarize(full_result)
    else:
        # Fallback to basic summary
        summary = {
            "tool": tool,
            "command": command,
            "status": meta.get("status", "completed"),
            "lines": len(output.split("\n")) if output else 0,
        }

    compact = CompactReference(
        type="reference",
        path=str(result_path),
        summary=summary,
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


def _parse_tool_result_content(
    content: str,
) -> tuple[str | None, str | None, str, dict[str, Any]]:
    """
    Parse tool result content to extract tool, command, output, and metadata.

    Args:
        content: Tool result message content

    Returns:
        Tuple of (tool_name, command, output, meta)
        - tool_name: Detected tool or None if unknown
        - command: Extracted command or None if not found
        - output: Full output text (may be same as content if parsing fails)
        - meta: Metadata dictionary with status, exit_code, etc.
    """
    lines = content.split("\n")
    tool_name = None
    command = None
    output = content  # Default to full content
    meta: dict[str, Any] = {}

    # Detect tool from header patterns
    if lines:
        header = lines[0]
        if "Ran command:" in header or "Ran allowlisted command:" in header:
            tool_name = "shell"
        elif "Executed:" in header or "Running:" in header:
            # Could be python or other tools
            if "python" in header.lower():
                tool_name = "python"

    # Extract command from code blocks
    in_command_block = False
    command_lines = []
    for line in lines:
        if line.strip().startswith("```bash") or line.strip().startswith("```python"):
            in_command_block = True
            continue
        if in_command_block:
            if line.strip() == "```":
                break
            command_lines.append(line)

    if command_lines:
        command = "\n".join(command_lines).strip()

    # Extract return code/exit code from end of content
    for line in reversed(lines[-10:]):
        if "Return code:" in line:
            try:
                code_str = line.split("Return code:")[-1].strip()
                meta["exit_code"] = int(code_str)
                meta["status"] = "success" if meta["exit_code"] == 0 else "failed"
            except (ValueError, IndexError):
                pass
            break
        elif "exit_code" in line.lower():
            # Try to extract from various formats
            try:
                import re

                match = re.search(r"exit[_ ]code[:\s]+(\d+)", line, re.IGNORECASE)
                if match:
                    meta["exit_code"] = int(match.group(1))
                    meta["status"] = "success" if meta["exit_code"] == 0 else "failed"
            except (ValueError, AttributeError):
                pass

    # Fallback status detection
    if "status" not in meta:
        if any(
            word in content.lower()
            for word in ["error", "failed", "exception", "traceback"]
        ):
            meta["status"] = "failed"
        else:
            meta["status"] = "completed"

    return tool_name, command, output, meta


def create_tool_result_summary(
    content: str,
    original_tokens: int,
    logdir: Path | None,
    tool_name: str = "tool",
) -> str:
    """
    Create compact summary for a tool result, saving full content to file.

    Now uses schema-based summarization when tool can be detected from content.
    Falls back to legacy approach if parsing fails.

    Args:
        content: Tool result content
        original_tokens: Number of tokens in original content
        logdir: Conversation directory for saving
        tool_name: Name of the tool that generated the result (may be detected)

    Returns:
        Compact summary message with reference to saved file
    """
    # Try schema-based approach if we have logdir
    if logdir:
        try:
            # Detect tool and parse content
            detected_tool, command, output, meta = _parse_tool_result_content(content)
            if detected_tool:
                tool_name = detected_tool

            # Use structured result with schema-based summarization
            compact_ref, _full_result = save_structured_result(
                tool=tool_name,
                command=command,
                output=output,
                meta=meta,
                logdir=logdir,
            )
            return compact_ref.to_text()
        except Exception as e:
            # Log but don't fail - fall through to legacy approach
            logger.debug(
                f"Schema-based summarization failed, using legacy: {e}", exc_info=True
            )

    # Fallback to legacy approach
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


# Result Schema System for Tool-Specific Summarization


class ResultSchema:
    """
    Base class for result schemas.

    Schemas define how to create compact summaries from full tool results.
    Each tool can register a custom schema for optimal summarization.
    """

    @staticmethod
    def summarize(full_result: FullResult) -> dict[str, Any]:
        """
        Create compact summary from full result.

        Args:
            full_result: Complete result with all output

        Returns:
            Dictionary with essential fields for compact representation
        """
        raise NotImplementedError("Subclasses must implement summarize()")


class ShellResultSchema(ResultSchema):
    """Schema for shell command results."""

    @staticmethod
    def summarize(full_result: FullResult) -> dict[str, Any]:
        """Summarize shell command result with essential fields."""
        lines = full_result.output.split("\n") if full_result.output else []
        exit_code = full_result.meta.get("exit_code", 0)

        # Get preview: first 2 lines and last 1 line
        preview_lines = []
        if len(lines) > 3:
            preview_lines = lines[:2] + ["..."] + lines[-1:]
        elif lines:
            preview_lines = lines

        return {
            "tool": "shell",
            "command": full_result.command,
            "exit_code": exit_code,
            "status": "success" if exit_code == 0 else "failed",
            "duration_ms": full_result.meta.get("duration_ms"),
            "lines": len(lines),
            "preview": "\n".join(preview_lines),
        }


class PythonResultSchema(ResultSchema):
    """Schema for Python execution results."""

    @staticmethod
    def summarize(full_result: FullResult) -> dict[str, Any]:
        """Summarize Python execution result."""
        lines = full_result.output.split("\n") if full_result.output else []

        # Check for exceptions in output
        has_exception = any("Traceback" in line or "Error:" in line for line in lines)

        # Get result value if present
        result_value = full_result.meta.get("result")

        return {
            "tool": "python",
            "status": "error" if has_exception else "success",
            "result": str(result_value)[:100] if result_value else None,
            "lines": len(lines),
            "has_exception": has_exception,
            "duration_ms": full_result.meta.get("duration_ms"),
        }


class BrowserResultSchema(ResultSchema):
    """Schema for browser operation results."""

    @staticmethod
    def summarize(full_result: FullResult) -> dict[str, Any]:
        """Summarize browser operation result."""
        return {
            "tool": "browser",
            "operation": full_result.meta.get("operation", "unknown"),
            "url": full_result.meta.get("url"),
            "status_code": full_result.meta.get("status_code"),
            "content_size": len(full_result.output) if full_result.output else 0,
            "duration_ms": full_result.meta.get("duration_ms"),
        }


class FileResultSchema(ResultSchema):
    """Schema for file operation results."""

    @staticmethod
    def summarize(full_result: FullResult) -> dict[str, Any]:
        """Summarize file operation result."""
        return {
            "tool": "file",
            "operation": full_result.meta.get("operation", "unknown"),
            "path": full_result.meta.get("path"),
            "size_bytes": len(full_result.output) if full_result.output else 0,
            "status": full_result.meta.get("status", "completed"),
        }


# Schema Registry

_SCHEMA_REGISTRY: dict[str, type[ResultSchema]] = {
    "shell": ShellResultSchema,
    "python": PythonResultSchema,
    "ipython": PythonResultSchema,  # Use same schema as python
    "browser": BrowserResultSchema,
    "file": FileResultSchema,
}


def register_schema(tool_name: str, schema: type[ResultSchema]) -> None:
    """
    Register a custom schema for a tool.

    Args:
        tool_name: Name of the tool
        schema: Schema class implementing summarize()
    """
    _SCHEMA_REGISTRY[tool_name] = schema


def get_schema(tool_name: str) -> type[ResultSchema] | None:
    """
    Get registered schema for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Schema class or None if not registered
    """
    return _SCHEMA_REGISTRY.get(tool_name)
