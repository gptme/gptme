import logging
from collections.abc import Generator
from pathlib import Path

from ..context.selector.file_selector import select_relevant_files
from ..message import Message
from ..util.context import (
    file_to_display_path,
    get_mentioned_files,
    git_status,
    md_codeblock,
)
from . import HookType, StopPropagation, register_hook

logger = logging.getLogger(__name__)


def context_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message | StopPropagation, None, None]:
    """Active Context Discovery hook.

    Scans the workspace for relevant files based on recent messages and
    injects them into the context.

    Args:
        messages: List of conversation messages
        **kwargs: Includes workspace and manager (optional)
    """
    from ..util.context import use_fresh_context

    # Check if fresh context mode is enabled (opt-in)
    if not use_fresh_context():
        return

    workspace = kwargs.get("workspace")
    if not workspace:
        return

    # Run active discovery
    try:
        files = select_relevant_files(
            messages, workspace, max_files=10, use_selector=True
        )
    except Exception as e:
        logger.error(f"Failed to select files with context selector: {e}")
        # Fallback to simple mention counting if selector fails
        files = get_mentioned_files(messages, workspace)[:10]

    if not files:
        return

    sections = []

    # Include git status
    if status := git_status():
        sections.append(status)

    # Read contents of selected files
    for f in files[:10]:
        if f.exists():
            try:
                display_path = file_to_display_path(f, workspace)
                with open(f) as file:
                    content = file.read()
                logger.info(f"Read file: {display_path}")
                sections.append(md_codeblock(display_path, content))
            except UnicodeDecodeError:
                logger.debug(f"Skipping binary file: {f}")
                sections.append(md_codeblock(str(display_path), "<binary file>"))
            except OSError as e:
                logger.warning(f"Error reading file {f}: {e}")
        else:
            logger.info(f"File not found: {f}")

    if not sections:
        return

    cwd = Path.cwd()
    content = f"""# Context
Working directory: {cwd}

This context message is always inserted before the last user message.
It contains the current state of relevant files at the time of processing.
The file contents shown in this context message are the source of truth.
Any file contents shown elsewhere in the conversation history may be outdated.
This context message will be removed and replaced with fresh context on every new message.

""" + "\n\n".join(sections)

    yield Message("system", content)


def register():
    register_hook("active_context", HookType.GENERATION_PRE, context_hook)
