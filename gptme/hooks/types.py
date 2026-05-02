"""Type definitions for the hook system.

Contains Protocol classes, enums, dataclasses, and type aliases used
throughout the hook infrastructure.
"""

from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
)

from ..message import Message
from .confirm import ToolConfirmHook
from .elicitation import ElicitationHook

if TYPE_CHECKING:
    from ..logmanager import Log, LogManager  # fmt: skip
    from ..tools.base import ToolUse  # fmt: skip


class StopPropagation:
    """Sentinel class that hooks can yield to stop execution of lower-priority hooks.

    Usage::

        def my_hook():
            if some_condition_failed:
                yield Message("system", "Error occurred")
                yield StopPropagation()  # Stop remaining hooks
    """


class HookType(str, Enum):
    """Types of hooks that can be registered.

    Hook names follow OpenCode-style dot-notation for namespacing:
    - <category>.<event> or <category>.<action>.<event>

    Terminology (see docs/glossary.md for details):
    - Turn: Complete user-assistant exchange (may contain multiple steps)
    - Step: Single LLM generation + tool execution cycle

    Naming conventions:
    - PRE/POST: Used consistently for timing around events
    - START/END: Reserved for session lifecycle only

    Examples:
    - step.pre: Before each step in a turn
    - step.post: After each step (before next step)
    - turn.pre: Before turn begins (once per turn)
    - turn.post: After all steps complete (once per turn)
    - tool.execute.pre: Before executing any tool
    - session.start: At session start
    """

    # Step/Turn lifecycle (formerly MESSAGE_* hooks)
    # Step: Single LLM generation + tool execution cycle within a turn
    STEP_PRE = "step.pre"  # Before each step in a turn
    STEP_POST = "step.post"  # After each step (before next step)
    # Turn: Complete user-assistant exchange
    TURN_PRE = "turn.pre"  # Before turn begins (once per turn)
    TURN_POST = "turn.post"  # After all steps complete (once per turn)
    # Transform: Modify message content before storage/display
    # Applied after assistant response, allows hook to rewrite content.
    # The transform persists (modifies the stored message).
    MESSAGE_TRANSFORM = "message.transform"

    # Tool lifecycle
    TOOL_EXECUTE_PRE = "tool.execute.pre"  # Before executing any tool
    TOOL_EXECUTE_POST = "tool.execute.post"  # After executing any tool
    # Transform: Modify tool execution (input/output)
    TOOL_TRANSFORM = "tool.transform"

    # File operations
    FILE_SAVE_PRE = "file.save.pre"  # Before saving a file
    FILE_SAVE_POST = "file.save.post"  # After saving a file
    FILE_PATCH_PRE = "file.patch.pre"  # Before patching a file
    FILE_PATCH_POST = "file.patch.post"  # After patching a file

    # Session lifecycle (START/END for long-duration events)
    SESSION_START = "session.start"  # At session start
    SESSION_END = "session.end"  # At session end

    # Generation
    GENERATION_PRE = "generation.pre"  # Before generating response
    GENERATION_POST = "generation.post"  # After generating response
    GENERATION_INTERRUPT = "generation.interrupt"  # Interrupt generation

    # Loop control
    LOOP_CONTINUE = "loop.continue"  # Decide whether/how to continue the chat loop

    # Directory tracking
    CWD_CHANGED = "cwd.changed"  # Working directory changed during tool execution

    # Cache events
    CACHE_INVALIDATED = "cache.invalidated"  # Prompt cache was invalidated

    # Tool confirmation (different from other hooks - returns data, not yields Messages)
    TOOL_CONFIRM = "tool.confirm"  # Confirm tool execution before running

    # Elicitation (agent requests structured input from user)
    ELICIT = "elicit"  # Agent requests user input (text, choice, secret, form, etc.)


# Protocol classes for different hook signatures
class SessionStartHook(Protocol):
    """Hook called at session start with logdir, workspace, and initial messages."""

    def __call__(
        self,
        logdir: Path,
        workspace: Path | None,
        initial_msgs: list[Message],
    ) -> Generator[Message | StopPropagation, None, None]: ...


class SessionEndHook(Protocol):
    """Hook called at session end with logdir and manager."""

    def __call__(
        self, manager: "LogManager"
    ) -> Generator[Message | StopPropagation, None, None]: ...


class ToolExecuteHook(Protocol):
    """Hook called before/after tool execution.

    Note: Receives log/workspace separately since manager isn't available in ToolUse.execute() context.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        tool_use: The tool being executed
    """

    def __call__(
        self,
        log: "Log",
        workspace: Path | None,
        tool_use: "ToolUse",
    ) -> Generator[Message | StopPropagation, None, None]: ...


class MessageProcessHook(Protocol):
    """Hook called before/after message processing.

    Args:
        manager: Conversation manager with log and workspace
    """

    def __call__(
        self, manager: "LogManager"
    ) -> Generator[Message | StopPropagation, None, None]: ...


class LoopContinueHook(Protocol):
    """Hook called to decide whether to continue the chat loop.

    Args:
        manager: Conversation manager with log and workspace
        interactive: Whether in interactive mode
        prompt_queue: Queue of pending prompts
    """

    def __call__(
        self,
        manager: "LogManager",
        interactive: bool,
        prompt_queue: Any,
    ) -> Generator[Message | StopPropagation, None, None]: ...


class GenerationPreHook(Protocol):
    """Hook called before generating response.

    Args:
        messages: List of conversation messages
        workspace: Workspace directory path (optional)
        manager: Conversation manager (optional, currently always None)
        model: Fully-qualified model name used for this generation (optional)
    """

    def __call__(
        self,
        messages: list[Message],
        **kwargs: Any,
    ) -> Generator[Message | StopPropagation, None, None]: ...


class GenerationPostHook(Protocol):
    """Hook called after generating response.

    Args:
        message: The generated message
        workspace: Workspace directory path (optional)
    """

    def __call__(
        self,
        message: Message,
        **kwargs: Any,
    ) -> Generator[Message | StopPropagation, None, None]: ...


class CacheInvalidatedHook(Protocol):
    """Hook called when prompt cache is invalidated.

    This is triggered after operations that invalidate the prompt cache,
    such as auto-compaction. Plugins can use this hook to update their
    state (e.g., re-evaluate attention tiers) at the optimal time.

    Args:
        manager: Conversation manager with log and workspace
        reason: Reason for cache invalidation (e.g., "compact", "edit")
        tokens_before: Token count before the operation (optional)
        tokens_after: Token count after the operation (optional)
    """

    def __call__(
        self,
        manager: "LogManager",
        reason: str,
        tokens_before: int | None = None,
        tokens_after: int | None = None,
    ) -> Generator[Message | StopPropagation, None, None]: ...


class CwdChangedHook(Protocol):
    """Hook called when the working directory changes during tool execution.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        old_cwd: Previous working directory
        new_cwd: New working directory (current os.getcwd())
        tool_use: The tool that caused the change
    """

    def __call__(
        self,
        log: "Log",
        workspace: Path | None,
        old_cwd: str,
        new_cwd: str,
        tool_use: "ToolUse",
    ) -> Generator[Message | StopPropagation, None, None]: ...


class FilePreSaveHook(Protocol):
    """Hook called before saving a file.

    Args:
        log: Conversation log (optional, may be None)
        workspace: Workspace directory path (optional, may be None)
        path: Path to file being saved
        content: Content to be saved
    """

    def __call__(
        self,
        log: "Log | None",
        workspace: Path | None,
        path: Path,
        content: str,
    ) -> Generator[Message | StopPropagation, None, None]: ...


class FilePostSaveHook(Protocol):
    """Hook called after saving a file.

    Args:
        log: Conversation log (optional, may be None)
        workspace: Workspace directory path (optional, may be None)
        path: Path to file that was saved
        content: Content that was saved
        created: Whether file was newly created (vs overwritten)
    """

    def __call__(
        self,
        log: "Log | None",
        workspace: Path | None,
        path: Path,
        content: str,
        created: bool,
    ) -> Generator[Message | StopPropagation, None, None]: ...


# Union of all hook types
HookFunc = (
    SessionStartHook
    | SessionEndHook
    | ToolExecuteHook
    | CwdChangedHook
    | MessageProcessHook
    | LoopContinueHook
    | GenerationPreHook
    | GenerationPostHook
    | FilePreSaveHook
    | FilePostSaveHook
    | CacheInvalidatedHook
    | ToolConfirmHook
    | ElicitationHook
)


@dataclass
class Hook:
    """A registered hook."""

    name: str
    hook_type: HookType
    func: HookFunc
    priority: int = 0  # Higher priority runs first
    enabled: bool = True
    async_mode: bool = False  # If True, run in background thread without blocking

    def __lt__(self, other: "Hook") -> bool:
        """Sort by priority (higher first), then name."""
        return (self.priority, self.name) > (other.priority, other.name)
