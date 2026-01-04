"""Hook system for extending gptme functionality at various lifecycle points."""

import logging
import threading
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from time import time
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    overload,
)

from ..message import Message
from ..plugins import register_plugin_hooks

if TYPE_CHECKING:
    from ..logmanager import Log, LogManager  # fmt: skip
    from ..tools.base import ToolUse  # fmt: skip

logger = logging.getLogger(__name__)


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

    Terminology (see docs/glossary.md for details):
    - Turn: Complete user-assistant exchange (may contain multiple steps)
    - Step: Single LLM generation + tool execution cycle
    """

    # Message lifecycle (per-turn hooks)
    MESSAGE_PRE_PROCESS = "message_pre_process"  # Before each step in a turn
    MESSAGE_POST_PROCESS = (
        "message_post_process"  # After all steps complete (once per turn)
    )
    MESSAGE_TRANSFORM = "message_transform"  # Transform message content

    # Tool lifecycle
    TOOL_PRE_EXECUTE = "tool_pre_execute"  # Before executing any tool
    TOOL_POST_EXECUTE = "tool_post_execute"  # After executing any tool
    TOOL_TRANSFORM = "tool_transform"  # Transform tool execution

    # File operations
    FILE_PRE_SAVE = "file_pre_save"  # Before saving a file
    FILE_POST_SAVE = "file_post_save"  # After saving a file
    FILE_PRE_PATCH = "file_pre_patch"  # Before patching a file
    FILE_POST_PATCH = "file_post_patch"  # After patching a file

    # Session lifecycle
    SESSION_START = "session_start"  # At session start
    SESSION_END = "session_end"  # At session end

    # Generation
    GENERATION_PRE = "generation_pre"  # Before generating response
    GENERATION_POST = "generation_post"  # After generating response
    GENERATION_INTERRUPT = "generation_interrupt"  # Interrupt generation

    # Loop control
    LOOP_CONTINUE = "loop_continue"  # Decide whether/how to continue the chat loop

    # Cache events
    CACHE_INVALIDATED = "cache_invalidated"  # Prompt cache was invalidated


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
    | MessageProcessHook
    | LoopContinueHook
    | GenerationPreHook
    | GenerationPostHook
    | FilePreSaveHook
    | FilePostSaveHook
    | CacheInvalidatedHook
)


@dataclass
class Hook:
    """A registered hook."""

    name: str
    hook_type: HookType
    func: HookFunc
    priority: int = 0  # Higher priority runs first
    enabled: bool = True

    def __lt__(self, other: "Hook") -> bool:
        """Sort by priority (higher first), then name."""
        return (self.priority, self.name) > (other.priority, other.name)


@dataclass
class HookRegistry:
    """Registry for managing hooks."""

    hooks: dict[HookType, list[Hook]] = field(default_factory=dict)
    _lock: Any = field(default_factory=lambda: __import__("threading").Lock())

    def register(
        self,
        name: str,
        hook_type: HookType,
        func: HookFunc,
        priority: int = 0,
        enabled: bool = True,
    ) -> None:
        """Register a hook."""
        with self._lock:
            if hook_type not in self.hooks:
                self.hooks[hook_type] = []

            hook = Hook(
                name=name,
                hook_type=hook_type,
                func=func,
                priority=priority,
                enabled=enabled,
            )

            # Check if hook with same name already exists
            existing = [h for h in self.hooks[hook_type] if h.name == name]
            if existing:
                logger.debug(f"Replacing existing hook '{name}' for {hook_type}")
                self.hooks[hook_type] = [
                    h for h in self.hooks[hook_type] if h.name != name
                ]

            self.hooks[hook_type].append(hook)
            self.hooks[hook_type].sort()  # Sort by priority

            logger.debug(
                f"Registered hook '{name}' for {hook_type} (priority={priority})"
            )

    def unregister(self, name: str, hook_type: HookType | None = None) -> None:
        """Unregister a hook by name, optionally filtering by type."""
        with self._lock:
            if hook_type:
                if hook_type in self.hooks:
                    self.hooks[hook_type] = [
                        h for h in self.hooks[hook_type] if h.name != name
                    ]
                    logger.debug(f"Unregistered hook '{name}' from {hook_type}")
            else:
                # Remove from all types
                for ht in self.hooks:
                    self.hooks[ht] = [h for h in self.hooks[ht] if h.name != name]
                logger.debug(f"Unregistered hook '{name}' from all types")

    def trigger(
        self, hook_type: HookType, *args, **kwargs
    ) -> Generator[Message, None, None]:
        """Trigger all hooks of a given type.

        Args:
            hook_type: The type of hook to trigger
            \\*args: Variable positional arguments to pass to hook functions
            \\*\\*kwargs: Variable keyword arguments to pass to hook functions

        Yields:
            Messages from hooks
        """
        if hook_type not in self.hooks:
            return

        hooks = [h for h in self.hooks[hook_type] if h.enabled]
        if not hooks:
            return

        logger.debug(
            f"Triggering {len(hooks)} hooks for {hook_type}: {[h.name for h in hooks]}"
        )

        # Collect all results
        for hook in hooks:
            try:
                # TODO: emit span for tracing
                t_start = time()
                result = hook.func(*args, **kwargs)
                t_end = time()
                t_delta = t_end - t_start
                logger.debug(f"Hook '{hook.name}' took {t_delta:.4f}s")
                if t_delta > 5.0:
                    logger.warning(
                        f"Hook '{hook.name}' is taking a long time ({t_delta:.4f}s)"
                    )

                # If hook returns a generator, yield from it
                if hasattr(result, "__iter__") and not isinstance(result, str | bytes):
                    try:
                        for msg in result:
                            if isinstance(msg, StopPropagation):
                                logger.debug(f"Hook '{hook.name}' stopped propagation")
                                return  # Stop processing remaining hooks
                            elif isinstance(msg, Message):
                                yield msg
                    except TypeError:
                        # Not actually iterable, continue
                        pass
                # If hook returns a Message, yield it
                elif isinstance(result, Message):
                    yield result
                # If hook returns StopPropagation, stop
                elif isinstance(result, StopPropagation):
                    logger.debug(f"Hook '{hook.name}' stopped propagation")
                    return

            except Exception as e:
                # Special handling for session termination
                if e.__class__.__name__ == "SessionCompleteException":
                    logger.info(f"Hook '{hook.name}' signaled session completion")
                    raise  # Propagate session complete signal

                # Log other exceptions with full traceback for debugging
                logger.exception(f"Error executing hook '{hook.name}'")
                continue  # Skip this hook but continue with others

    def get_hooks(self, hook_type: HookType | None = None) -> list[Hook]:
        """Get all registered hooks, optionally filtered by type."""
        if hook_type:
            return self.hooks.get(hook_type, [])
        return [hook for hooks in self.hooks.values() for hook in hooks]

    def enable_hook(self, name: str, hook_type: HookType | None = None) -> None:
        """Enable a hook by name."""
        with self._lock:
            hooks_to_enable = (
                self.get_hooks(hook_type) if hook_type else self.get_hooks()
            )
            for hook in hooks_to_enable:
                if hook.name == name:
                    hook.enabled = True
                    logger.debug(f"Enabled hook '{name}'")

    def disable_hook(self, name: str, hook_type: HookType | None = None) -> None:
        """Disable a hook by name."""
        with self._lock:
            hooks_to_disable = (
                self.get_hooks(hook_type) if hook_type else self.get_hooks()
            )
            for hook in hooks_to_disable:
                if hook.name == name:
                    hook.enabled = False
                    logger.debug(f"Disabled hook '{name}'")


# Context-local registry (each thread/context has its own)
_registry_var: ContextVar[HookRegistry | None] = ContextVar(
    "hook_registry", default=None
)

# Global lock for thread-safe hook initialization
_hooks_init_lock = threading.Lock()


def _thread_safe_init(func):
    """Decorator for thread-safe initialization."""

    def wrapper(*args, **kwargs):
        with _hooks_init_lock:
            return func(*args, **kwargs)

    return wrapper


def get_registry() -> HookRegistry:
    """Get the current hook registry, creating one if needed."""
    registry = _registry_var.get()
    if registry is None:
        registry = HookRegistry()
        _registry_var.set(registry)
    return registry


def set_registry(registry: HookRegistry) -> None:
    """Set the hook registry for this context."""
    _registry_var.set(registry)


# Type-safe overloads for register_hook
@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.SESSION_START],
    func: SessionStartHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.SESSION_END],
    func: SessionEndHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[
        HookType.TOOL_PRE_EXECUTE,
        HookType.TOOL_POST_EXECUTE,
    ],
    func: ToolExecuteHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.FILE_PRE_SAVE],
    func: FilePreSaveHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.FILE_POST_SAVE],
    func: FilePostSaveHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.LOOP_CONTINUE],
    func: LoopContinueHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.GENERATION_PRE],
    func: GenerationPreHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.GENERATION_POST],
    func: GenerationPostHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[
        HookType.MESSAGE_PRE_PROCESS,
        HookType.MESSAGE_POST_PROCESS,
        HookType.MESSAGE_TRANSFORM,
    ],
    func: MessageProcessHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.CACHE_INVALIDATED],
    func: CacheInvalidatedHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


# Implementation (catches all other cases)
# Fallback overload for dynamic registration (when hook_type is not a Literal)
@overload
def register_hook(
    name: str,
    hook_type: HookType,  # Non-Literal type
    func: HookFunc,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


def register_hook(
    name: str,
    hook_type: HookType,
    func: HookFunc,
    priority: int = 0,
    enabled: bool = True,
) -> None:
    """Register a hook with the global registry.

    Type-safe overloads ensure that registered hooks match their expected Protocol signatures.
    Direct calls with Literal hook types get strict type checking.
    Dynamic calls (non-Literal types) use the generic HookFunc fallback.
    """
    get_registry().register(name, hook_type, func, priority, enabled)


def unregister_hook(name: str, hook_type: HookType | None = None) -> None:
    """Unregister a hook from the registry."""
    get_registry().unregister(name, hook_type)


def trigger_hook(
    hook_type: HookType, *args, **kwargs
) -> Generator[Message, None, None]:
    """Trigger hooks of a given type."""
    try:
        yield from get_registry().trigger(hook_type, *args, **kwargs)
    except KeyboardInterrupt:
        logger.debug(f"Hook trigger {hook_type} interrupted by user")
        return


def get_hooks(hook_type: HookType | None = None) -> list[Hook]:
    """Get all registered hooks."""
    return get_registry().get_hooks(hook_type)


def enable_hook(name: str, hook_type: HookType | None = None) -> None:
    """Enable a hook."""
    get_registry().enable_hook(name, hook_type)


def disable_hook(name: str, hook_type: HookType | None = None) -> None:
    """Disable a hook."""
    get_registry().disable_hook(name, hook_type)


def clear_hooks(hook_type: HookType | None = None) -> None:
    """Clear all hooks, optionally filtered by type."""
    registry = get_registry()
    if hook_type:
        registry.hooks[hook_type] = []
    else:
        registry.hooks.clear()


@_thread_safe_init
def init_hooks(allowlist: list[str] | None = None) -> None:
    """Initialize and register hooks in a thread-safe manner.

    If allowlist is not provided, it will be loaded from the environment variable
    HOOK_ALLOWLIST or the chat config (if set).
    """
    from ..config import get_config  # fmt: skip

    config = get_config()

    # Get allowlist from parameter, environment, or config
    if allowlist is None:
        env_allowlist = config.get_env("HOOK_ALLOWLIST")
        if env_allowlist:
            allowlist = env_allowlist.split(",")
        # Note: hooks are not yet in chat config, but could be added later
        # elif config.chat and config.chat.hooks:
        #     allowlist = config.chat.hooks

    # Available hooks with their register functions
    available_hooks = {
        "cwd_tracking": lambda: __import__(
            "gptme.hooks.cwd_tracking", fromlist=["register"]
        ).register(),
        "markdown_validation": lambda: __import__(
            "gptme.hooks.markdown_validation", fromlist=["register"]
        ).register(),
        "time_awareness": lambda: __import__(
            "gptme.hooks.time_awareness", fromlist=["register"]
        ).register(),
        "token_awareness": lambda: __import__(
            "gptme.hooks.token_awareness", fromlist=["register"]
        ).register(),
        "active_context": lambda: __import__(
            "gptme.hooks.active_context", fromlist=["register"]
        ).register(),
        "form_autodetect": lambda: __import__(
            "gptme.hooks.form_autodetect", fromlist=["register"]
        ).register(),
        "cost_awareness": lambda: __import__(
            "gptme.hooks.cost_awareness", fromlist=["register"]
        ).register(),
        # NOTE: subagent_completion is now registered via ToolSpec in tools/subagent.py
        "test": lambda: __import__(
            "gptme.hooks.test", fromlist=["register_test_hooks"]
        ).register_test_hooks(),
    }

    # Determine which hooks to register
    if allowlist is not None:
        hooks_to_register = allowlist
    else:
        # Register all default hooks except test
        hooks_to_register = [h for h in available_hooks if h != "test"]

    # Register the hooks
    for hook_name in hooks_to_register:
        if hook_name in available_hooks:
            try:
                available_hooks[hook_name]()
                logger.debug(f"Registered hook: {hook_name}")
            except Exception as e:
                logger.warning(f"Failed to register hook '{hook_name}': {e}")
        else:
            logger.warning(f"Hook '{hook_name}' not found")

    # Register plugin hooks

    if config.project and config.project.plugins and config.project.plugins.paths:
        register_plugin_hooks(
            plugin_paths=[Path(p) for p in config.project.plugins.paths],
            enabled_plugins=config.project.plugins.enabled or None,
        )
