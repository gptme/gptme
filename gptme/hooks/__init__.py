"""Hook system for extending gptme functionality at various lifecycle points.

This package provides a hook registry for registering and triggering hooks at
various points in the gptme lifecycle. The system is split into:

- ``types``: Type definitions (Protocol classes, HookType enum, Hook dataclass)
- ``registry``: Hook registry, registration, and execution infrastructure
- ``confirm``: Tool confirmation hooks
- ``elicitation``: Structured user input hooks

Individual hook implementations live in their own modules (e.g., ``cwd_changed``,
``time_awareness``, ``workspace_agents``).
"""

import logging
from pathlib import Path

from ..plugins import register_plugin_hooks

# Re-export confirm and elicitation types
from .confirm import ConfirmAction as ConfirmAction
from .confirm import ConfirmationResult as ConfirmationResult
from .confirm import ToolConfirmHook as ToolConfirmHook
from .confirm import confirm as confirm
from .confirm import get_confirmation as get_confirmation
from .elicitation import ElicitationHook as ElicitationHook
from .elicitation import ElicitationRequest as ElicitationRequest
from .elicitation import ElicitationResponse as ElicitationResponse
from .elicitation import FormField as FormField
from .elicitation import elicit as elicit

# Re-export registry functions
from .registry import (
    HookRegistry as HookRegistry,
)
from .registry import _thread_safe_init
from .registry import (
    clear_hooks as clear_hooks,
)
from .registry import (
    disable_hook as disable_hook,
)
from .registry import (
    enable_hook as enable_hook,
)
from .registry import (
    get_hooks as get_hooks,
)
from .registry import (
    get_registry as get_registry,
)
from .registry import (
    register_hook as register_hook,
)
from .registry import (
    set_registry as set_registry,
)
from .registry import (
    trigger_hook as trigger_hook,
)
from .registry import (
    unregister_hook as unregister_hook,
)
from .server_confirm import current_conversation_id as current_conversation_id
from .server_confirm import current_session_id as current_session_id

# Re-export types (Protocol classes, enums, dataclasses)
from .types import (
    CacheInvalidatedHook as CacheInvalidatedHook,
)
from .types import (
    CwdChangedHook as CwdChangedHook,
)
from .types import (
    FilePostSaveHook as FilePostSaveHook,
)
from .types import (
    FilePreSaveHook as FilePreSaveHook,
)
from .types import (
    GenerationPostHook as GenerationPostHook,
)
from .types import (
    GenerationPreHook as GenerationPreHook,
)
from .types import (
    Hook as Hook,
)
from .types import (
    HookFunc as HookFunc,
)
from .types import (
    HookType as HookType,
)
from .types import (
    LoopContinueHook as LoopContinueHook,
)
from .types import (
    MessageProcessHook as MessageProcessHook,
)
from .types import (
    SessionEndHook as SessionEndHook,
)
from .types import (
    SessionStartHook as SessionStartHook,
)
from .types import (
    StopPropagation as StopPropagation,
)
from .types import (
    ToolExecuteHook as ToolExecuteHook,
)

logger = logging.getLogger(__name__)


@_thread_safe_init
def init_hooks(
    allowlist: list[str] | None = None,
    interactive: bool = False,
    no_confirm: bool = False,
    server: bool = False,
) -> None:
    """Initialize and register hooks in a thread-safe manner.

    Mode detection for confirmation hooks:
    - Interactive CLI mode with confirmation: Registers cli_confirm hook
    - Server mode with confirmation: Registers server_confirm hook
    - Non-interactive mode: No confirmation hook (autonomous/auto-confirm)

    Args:
        allowlist: Explicit list of hooks to register (replaces defaults).
                   If not provided, defaults will be loaded from env/config.
        interactive: Whether running in interactive mode (CLI).
        no_confirm: Whether to skip tool confirmations.
        server: Whether running in server mode (API/WebUI).
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
        "cwd_changed": lambda: __import__(
            "gptme.hooks.cwd_changed", fromlist=["register"]
        ).register(),
        "cwd_awareness": lambda: __import__(
            "gptme.hooks.cwd_awareness", fromlist=["register"]
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
        "cache_awareness": lambda: __import__(
            "gptme.hooks.cache_awareness", fromlist=["register"]
        ).register(),
        "workspace_agents": lambda: __import__(
            "gptme.hooks.workspace_agents", fromlist=["register"]
        ).register(),
        "agents_md_inject": lambda: __import__(
            "gptme.hooks.agents_md_inject", fromlist=["register"]
        ).register(),
        # Tool confirmation hooks (mode-specific, not registered by default)
        "cli_confirm": lambda: __import__(
            "gptme.hooks.cli_confirm", fromlist=["register"]
        ).register(),
        "auto_confirm": lambda: __import__(
            "gptme.hooks.auto_confirm", fromlist=["register"]
        ).register(),
        "server_confirm": lambda: __import__(
            "gptme.hooks.server_confirm", fromlist=["register"]
        ).register(),
        "server_elicit": lambda: __import__(
            "gptme.hooks.server_elicit", fromlist=["register"]
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
        # Register all default hooks except test and mode-specific confirmation hooks
        # Confirmation hooks (cli_confirm, auto_confirm, server_confirm) should be
        # registered explicitly based on the mode (CLI, server, autonomous)
        mode_specific_hooks = {
            "test",
            "cli_confirm",
            "auto_confirm",
            "server_confirm",
            "server_elicit",
        }
        hooks_to_register = [h for h in available_hooks if h not in mode_specific_hooks]

        # Mode-based hook selection:
        # - Server mode with confirmation: server_confirm + server_elicit
        # - CLI interactive with confirmation enabled: cli_confirm
        # - Non-interactive (autonomous): no confirmation hook (auto-confirm behavior)
        if server and not no_confirm:
            hooks_to_register.append("server_confirm")
            hooks_to_register.append("server_elicit")
        elif interactive and not no_confirm:
            hooks_to_register.append("cli_confirm")

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
