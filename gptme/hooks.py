"""Hook system for extending gptme functionality at various lifecycle points."""

import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .message import Message

logger = logging.getLogger(__name__)


class StopPropagation:
    """Sentinel class that hooks can yield to stop execution of lower-priority hooks.

    Usage::

        def my_hook():
            if some_condition_failed:
                yield Message("system", "Error occurred")
                yield StopPropagation()  # Stop remaining hooks
    """

    pass


class HookType(str, Enum):
    """Types of hooks that can be registered."""

    # Message lifecycle
    MESSAGE_PRE_PROCESS = "message_pre_process"  # Before processing a message
    MESSAGE_POST_PROCESS = "message_post_process"  # After processing a message
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


# Hook function signatures for different hook types
HookFunc = Callable[..., Any | Generator[Message, None, None]]


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
                result = hook.func(*args, **kwargs)

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

            except Exception:
                # logger.exception already includes exception info and traceback
                logger.error(f"Error executing hook '{hook.name}'")
                continue  # Skip this hook but continue with others
                # Disable printing system message to prevent infinite loops
                # yield Message("system", f"Hook '{hook.name}' failed: {e}", hide=True)

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


# Global hook registry
_registry = HookRegistry()


def register_hook(
    name: str,
    hook_type: HookType,
    func: HookFunc,
    priority: int = 0,
    enabled: bool = True,
) -> None:
    """Register a hook with the global registry."""
    _registry.register(name, hook_type, func, priority, enabled)


def unregister_hook(name: str, hook_type: HookType | None = None) -> None:
    """Unregister a hook from the global registry."""
    _registry.unregister(name, hook_type)


def trigger_hook(
    hook_type: HookType, *args, **kwargs
) -> Generator[Message, None, None]:
    """Trigger hooks of a given type."""
    yield from _registry.trigger(hook_type, *args, **kwargs)


def get_hooks(hook_type: HookType | None = None) -> list[Hook]:
    """Get all registered hooks."""
    return _registry.get_hooks(hook_type)


def enable_hook(name: str, hook_type: HookType | None = None) -> None:
    """Enable a hook."""
    _registry.enable_hook(name, hook_type)


def disable_hook(name: str, hook_type: HookType | None = None) -> None:
    """Disable a hook."""
    _registry.disable_hook(name, hook_type)


def clear_hooks(hook_type: HookType | None = None) -> None:
    """Clear all hooks, optionally filtered by type."""
    if hook_type:
        _registry.hooks[hook_type] = []
    else:
        _registry.hooks.clear()
