"""LLM-powered conversation summarization (resume generation).

Creates structured summaries of conversations using LLM, extracts
context files, and manages conversation resumption.
"""

import logging
import re
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ... import llm
from ...llm.models import get_default_model
from ...logmanager import Log, prepare_messages
from ...message import Message, len_tokens
from ...util.context import md_codeblock

if TYPE_CHECKING:
    from ...logmanager import LogManager

logger = logging.getLogger(__name__)


def _parse_context_files(content: str) -> list[str]:
    """
    Parse file paths from the LLM-generated resume.

    Looks for a "Context Files" or "Files to Include" section and extracts
    file paths that start with / or ./ or are relative paths.

    Args:
        content: The LLM-generated resume content

    Returns:
        List of file paths found in the response
    """
    file_paths: list[str] = []

    # Find the Context Files section (case-insensitive)
    # Look for patterns like "## Context Files" or "### Files to Include"
    context_section_pattern = r"(?:#{1,4}\s*(?:Context Files|Files to Include|Recommended Files|Key Files)[^\n]*\n)([\s\S]*?)(?=\n#{1,4}\s|\Z)"
    match = re.search(context_section_pattern, content, re.IGNORECASE)

    if match:
        section_content = match.group(1)
    else:
        # If no explicit section, scan the whole content
        section_content = content

    # Extract file paths from markdown list items
    # Matches: - `/path/to/file.py` or - `./relative/path.md` or - path/to/file.txt
    # Also matches: - `/path/to/file.py` - description
    path_patterns = [
        # Backtick-wrapped paths: - `path/to/file`
        r"[-*]\s*`([^`]+)`",
        # Paths starting with / or ./ or ~/
        r"[-*]\s*([/~.][\w./\-]+(?:\.\w+)?)",
        # Relative paths without leading dot (common patterns)
        r"[-*]\s*((?:src|docs|tests|config|scripts|tasks|journal|knowledge|lessons)/[\w./\-]+(?:\.\w+)?)",
    ]

    for pattern in path_patterns:
        matches = re.findall(pattern, section_content)
        for m in matches:
            path = m.strip()
            # Filter out common false positives
            if path and not path.startswith("http") and not path.startswith("#"):
                # Normalize path
                path = path.removeprefix("./")
                file_paths.append(path)

    # Remove duplicates while preserving order
    seen = set()
    unique_paths = []
    for p in file_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    return unique_paths


def _load_context_files(
    file_paths: list[str],
    workspace: Path | None = None,
    max_tokens_per_file: int = 2000,
) -> list[tuple[str, str]]:
    """
    Load contents of specified files that exist.

    Args:
        file_paths: List of file paths to load
        workspace: Workspace directory for resolving relative paths
        max_tokens_per_file: Maximum tokens to include per file

    Returns:
        List of (path, content) tuples for files that exist and are readable
    """
    loaded_files: list[tuple[str, str]] = []
    workspace_path = workspace or Path.cwd()

    for file_path in file_paths:
        # Resolve path
        if file_path.startswith("/"):
            full_path = Path(file_path)
        elif file_path.startswith("~"):
            full_path = Path(file_path).expanduser()
        else:
            full_path = workspace_path / file_path

        try:
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")

                # Truncate if too long
                tokens = len_tokens(content, "gpt-4")
                if tokens > max_tokens_per_file:
                    # Simple truncation with note
                    lines = content.split("\n")
                    truncated_lines = []
                    current_tokens = 0
                    for line in lines:
                        line_tokens = len_tokens(line, "gpt-4")
                        if current_tokens + line_tokens > max_tokens_per_file - 50:
                            truncated_lines.append(
                                f"\n... (truncated, {tokens - current_tokens} tokens remaining)"
                            )
                            break
                        truncated_lines.append(line)
                        current_tokens += line_tokens
                    content = "\n".join(truncated_lines)

                loaded_files.append((str(file_path), content))
                logger.info(
                    f"Loaded context file: {file_path} ({len_tokens(content, 'gpt-4')} tokens)"
                )
        except Exception as e:
            logger.warning(f"Could not load context file {file_path}: {e}")

    return loaded_files


def _resume_via_llm(
    manager: "LogManager",
    msgs: list[Message],
    use_view_branch: bool = False,
) -> Generator[Message, None, None]:
    """Core LLM-powered resume logic: summarize conversation and replace history.

    Args:
        manager: LogManager that owns the conversation.
        msgs: Messages to summarize.
        use_view_branch: If True, create a view branch (for auto-triggered resume)
            and mark status messages as hidden. If False, replace the log directly
            (for user-invoked /compact resume).
    """

    # Prepare messages for summarization
    prepared_msgs = prepare_messages(msgs)

    if len(prepared_msgs) < 3:
        yield Message(
            "system",
            "Not enough conversation history to create a meaningful resume.",
            hide=use_view_branch,
        )
        return

    # Generate conversation summary using LLM
    yield Message(
        "system",
        "🔄 Generating conversation resume with LLM...",
        hide=use_view_branch,
    )

    resume_prompt = """Please create a comprehensive resume of this conversation that includes:

1. **Conversation Summary**: Key topics, decisions made, and progress achieved
2. **Technical Context**: Important code changes, configurations, or technical details
3. **Current State**: What was accomplished and what remains to be done
4. **Context Files**: List the specific files that should be included in future context

For the Context Files section, use this format:
## Context Files

List each file on its own line with a bullet point and backticks:
- `path/to/file.py` - Brief rationale for including this file
- `docs/spec.md` - Contains the specification being implemented

Include files such as:
- Specs, PRDs, or design documents being referenced
- Source files being actively modified
- Configuration files relevant to the work
- Task or plan files tracking progress

Format the response as a structured document that could serve as a RESUME.md file."""

    # Create a temporary message for the LLM prompt
    resume_request = Message("user", resume_prompt)
    # Use full prepared messages for prompt caching friendliness
    llm_msgs = prepared_msgs + [resume_request]

    # Generate the resume using LLM
    m = get_default_model()
    if not m:
        yield Message(
            "system",
            "❌ Failed to generate resume: No default model configured. "
            "Set OPENAI_API_KEY or ANTHROPIC_API_KEY environment variable.",
            hide=use_view_branch,
        )
        return
    resume_response = llm.reply(llm_msgs, model=m.full, tools=[], workspace=None)
    resume_content = resume_response.content

    # Save RESUME.md to logdir (not workspace) for reference/debugging
    resume_path: Path | None = None
    if manager.logdir:
        resume_path = manager.logdir / "RESUME.md"
        try:
            with open(resume_path, "w") as f:
                f.write(resume_content)
            logger.info(f"Saved resume to {resume_path}")
        except Exception as e:
            logger.warning(f"Failed to save resume file: {e}")
            resume_path = None

    # Parse and load context files suggested by the LLM
    suggested_files = _parse_context_files(resume_content)
    workspace = manager.workspace
    loaded_files = _load_context_files(suggested_files, workspace=workspace)

    # Extract original system messages (before any user/assistant messages)
    # These contain essential context: core prompt, tool instructions, workspace info
    original_system_msgs = []
    for msg in msgs:
        if msg.role == "system":
            original_system_msgs.append(msg)
        elif msg.role in ("user", "assistant"):
            # Stop when we hit the first non-system message
            break

    # Create file context messages for each loaded file
    file_context_msgs = []
    for file_path, file_content in loaded_files:
        file_msg = Message(
            "system",
            f"Context file `{file_path}`:\n{md_codeblock('', file_content)}",
        )
        file_context_msgs.append(file_msg)

    # Create the resume intro message
    files_note = ""
    if loaded_files:
        files_note = f" (with {len(loaded_files)} context files)"
    resume_source = str(resume_path) if resume_path else "LLM-generated summary"
    resume_intro_msg = Message(
        "system", f"Previous conversation resumed from {resume_source}{files_note}:"
    )
    resume_msg = Message("assistant", resume_content)

    new_log = original_system_msgs + file_context_msgs + [resume_intro_msg, resume_msg]

    if use_view_branch:
        view_name = manager.get_next_view_name()
        manager.create_view(view_name, new_log)
        manager.switch_view(view_name)
    else:
        # Replace the log directly (user-invoked /compact resume)
        manager.log = Log(new_log)
        manager.write()

    # Build status message
    files_loaded_str = ""
    if loaded_files:
        files_loaded_str = f"• Context files loaded: {len(loaded_files)}\n"
        for fp, _ in loaded_files[:5]:  # Show first 5
            files_loaded_str += f"  - {fp}\n"
        if len(loaded_files) > 5:
            files_loaded_str += f"  ... and {len(loaded_files) - 5} more\n"
    elif suggested_files:
        files_loaded_str = f"• Suggested files not found: {len(suggested_files)}\n"

    view_note = ""
    if use_view_branch:
        view_note = f"• View: {view_name} (master branch preserved with full history)\n"

    resume_note = ""
    if resume_path:
        resume_note = f"• Resume saved to: {resume_path.absolute()}\n"

    yield Message(
        "system",
        f"✅ LLM-powered resume completed:\n"
        f"• Original conversation ({len(prepared_msgs)} messages) compressed to resume\n"
        f"{resume_note}"
        f"{files_loaded_str}"
        f"{view_note}"
        f"• Conversation history replaced with resume",
        hide=use_view_branch,
    )
