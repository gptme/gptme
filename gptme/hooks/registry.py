"""Hook registry and execution infrastructure.

Contains the HookRegistry class, context-local registry management,
and module-level API functions (register_hook, trigger_hook, etc.).
"""

import functools
import logging
import threading
from collections.abc import Generator
from contextvars import ContextVar
from time import time
from typing import (
    Any,
    Literal,
    overload,
)

from ..message import Message
from .confirm import ToolConfirmHook
from .elicitation import ElicitationHook
from .types import (
    CacheInvalidatedHook,
    CwdChangedHook,
    FilePostSaveHook,
    FilePreSaveHook,
    GenerationPostHook,
    GenerationPreHook,
    Hook,
    HookFunc,
    HookType,
    LoopContinueHook,
    MessageProcessHook,
    SessionEndHook,
    SessionStartHook,
    StopPropagation,
    ToolExecuteHook,
)

logger = logging.getLogger(__name__)


class HookRegistry:
    """Registry for managing hooks."""

    def __init__(self) -> None:
        self.hooks: dict[HookType, list[Hook]] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        hook_type: HookType,
        func: HookFunc,
        priority: int = 0,
        enabled: bool = True,
        async_mode: bool = False,
    ) -> None:
        """Register a hook.

        Args:
            name: Unique name for the hook
            hook_type: Type of hook (when it should be triggered)
            func: The hook function to call
            priority: Higher priority runs first (default 0)
            enabled: Whether the hook is enabled (default True)
            async_mode: If True, run in background thread without blocking (default False)
        """
        with self._lock:
            if hook_type not in self.hooks:
                self.hooks[hook_type] = []

            hook = Hook(
                name=name,
                hook_type=hook_type,
                func=func,
                priority=priority,
                enabled=enabled,
                async_mode=async_mode,
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
        self, hook_type: HookType, *args: Any, **kwargs: Any
    ) -> Generator[Message, None, None]:
        """Trigger all hooks of a given type.

        Args:
            hook_type: The type of hook to trigger
            *args: Variable positional arguments to pass to hook functions
            **kwargs: Variable keyword arguments to pass to hook functions

        Yields:
            Messages from sync hooks (async hooks run in background and don't yield)
        """
        if hook_type not in self.hooks:
            return

        hooks = [h for h in self.hooks[hook_type] if h.enabled]
        if not hooks:
            return

        # Separate sync and async hooks
        sync_hooks = [h for h in hooks if not h.async_mode]
        async_hooks = [h for h in hooks if h.async_mode]

        logger.debug(
            f"Triggering {len(hooks)} hooks for {hook_type}: "
            f"{len(sync_hooks)} sync, {len(async_hooks)} async"
        )

        # Start async hooks in background threads (fire-and-forget)
        # Note: daemon=True is intentional - async hooks are for non-blocking
        # side effects (logging, telemetry) where completion on exit is not required
        for hook in async_hooks:
            thread = threading.Thread(
                target=self._run_async_hook,
                args=(hook, args, kwargs),
                daemon=True,
                name=f"async-hook-{hook.name}",
            )
            thread.start()
            logger.debug(f"Started async hook '{hook.name}' in background thread")

        # Process sync hooks as before (yielding messages)
        for hook in sync_hooks:
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
                # Note: ToolConfirmHooks may return None (fall-through) or ConfirmationResult
                if (
                    result is not None
                    and hasattr(result, "__iter__")
                    and not isinstance(result, str | bytes)
                ):
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

    def _run_async_hook(self, hook: Hook, args: tuple, kwargs: dict) -> None:
        """Run an async hook in the background.

        Async hooks run without blocking and their results (messages) are logged
        but not returned to the main execution flow. They're ideal for:
        - Logging and telemetry
        - Notifications
        - External service calls
        - Any non-blocking side effects
        """
        try:
            t_start = time()
            result = hook.func(*args, **kwargs)
            t_end = time()
            t_delta = t_end - t_start

            # Process the result (log messages instead of yielding)
            if (
                result is not None
                and hasattr(result, "__iter__")
                and not isinstance(result, str | bytes)
            ):
                try:
                    for msg in result:
                        if isinstance(msg, Message):
                            content = str(msg.content) if msg.content else ""
                            preview = (
                                content[:100] + "..." if len(content) > 100 else content
                            )
                            logger.debug(
                                f"Async hook '{hook.name}' produced message: {preview}"
                            )
                        elif isinstance(msg, StopPropagation):
                            logger.debug(
                                f"Async hook '{hook.name}' signaled StopPropagation "
                                "(ignored in async mode)"
                            )
                except TypeError:
                    pass
            elif isinstance(result, Message):
                content = str(result.content) if result.content else ""
                preview = content[:100] + "..." if len(content) > 100 else content
                logger.debug(f"Async hook '{hook.name}' produced message: {preview}")

            logger.debug(f"Async hook '{hook.name}' completed in {t_delta:.4f}s")

        except Exception as e:
            # Special handling for session termination
            if e.__class__.__name__ == "SessionCompleteException":
                logger.info(
                    f"Async hook '{hook.name}' signaled session completion "
                    "(note: cannot propagate in async mode)"
                )
                return
            logger.exception(f"Error in async hook '{hook.name}'")

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

    @functools.wraps(func)
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
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.SESSION_END],
    func: SessionEndHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[
        HookType.TOOL_EXECUTE_PRE,
        HookType.TOOL_EXECUTE_POST,
    ],
    func: ToolExecuteHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.CWD_CHANGED],
    func: CwdChangedHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.FILE_SAVE_PRE],
    func: FilePreSaveHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.FILE_SAVE_POST],
    func: FilePostSaveHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.LOOP_CONTINUE],
    func: LoopContinueHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.GENERATION_PRE],
    func: GenerationPreHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.GENERATION_POST],
    func: GenerationPostHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[
        HookType.STEP_PRE,
        HookType.TURN_POST,
        HookType.MESSAGE_TRANSFORM,
    ],
    func: MessageProcessHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.CACHE_INVALIDATED],
    func: CacheInvalidatedHook,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.TOOL_CONFIRM],
    func: ToolConfirmHook,
    priority: int = 0,
    enabled: bool = True,
) -> None: ...


@overload
def register_hook(
    name: str,
    hook_type: Literal[HookType.ELICIT],
    func: ElicitationHook,
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
    async_mode: bool = False,
) -> None: ...


def register_hook(
    name: str,
    hook_type: HookType,
    func: HookFunc,
    priority: int = 0,
    enabled: bool = True,
    async_mode: bool = False,
) -> None:
    """Register a hook with the global registry.

    Type-safe overloads ensure that registered hooks match their expected Protocol signatures.
    Direct calls with Literal hook types get strict type checking.
    Dynamic calls (non-Literal types) use the generic HookFunc fallback.

    Args:
        name: Unique name for the hook
        hook_type: Type of hook (when it should be triggered)
        func: The hook function to call
        priority: Higher priority runs first (default 0)
        enabled: Whether the hook is enabled (default True)
        async_mode: If True, run in background thread without blocking (default False).
            Async hooks are ideal for logging, notifications, and external service calls.
    """
    get_registry().register(name, hook_type, func, priority, enabled, async_mode)


def unregister_hook(name: str, hook_type: HookType | None = None) -> None:
    """Unregister a hook from the registry."""
    get_registry().unregister(name, hook_type)


def trigger_hook(
    hook_type: HookType, *args: Any, **kwargs: Any
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
